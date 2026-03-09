"""4-필터 스크리닝 엔진."""

from __future__ import annotations

import logging

from models.schemas import EarningsEvent, QuarterlyFinancials, ScreenResult, FilterResult

logger = logging.getLogger(__name__)


def _build_eps_surprise(event: EarningsEvent | None) -> str:
    """EPS 서프라이즈 문자열 생성."""
    if not event or event.eps_actual is None:
        return ""
    actual = event.eps_actual
    estimate = event.eps_estimate
    if estimate is not None and estimate != 0:
        diff_pct = (actual - estimate) / abs(estimate) * 100
        beat_miss = "Beat" if actual >= estimate else "Miss"
        return f"EPS ${actual:.2f} {beat_miss} (est ${estimate:.2f}, {diff_pct:+.1f}%)"
    return f"EPS ${actual:.2f}"


def _build_context(financials: QuarterlyFinancials) -> str:
    """실적 변동 원인 컨텍스트 자동 생성."""
    parts = []

    # 매출 변동
    if financials.revenue is not None and financials.revenue_prev is not None and financials.revenue_prev != 0:
        rev_g = (financials.revenue - financials.revenue_prev) / abs(financials.revenue_prev)
        if rev_g > 0.15:
            parts.append("매출 고성장")
        elif rev_g > 0:
            parts.append("매출 소폭 증가")
        elif rev_g > -0.05:
            parts.append("매출 보합")
        else:
            parts.append("매출 감소")

    # 영업이익 vs 매출 성장 → 레버리지 / 비용 구조
    if (financials.revenue is not None and financials.revenue_prev is not None
            and financials.operating_income is not None and financials.operating_income_prev is not None
            and financials.revenue_prev != 0 and financials.operating_income_prev != 0):
        rev_g = (financials.revenue - financials.revenue_prev) / abs(financials.revenue_prev)
        op_g = (financials.operating_income - financials.operating_income_prev) / abs(financials.operating_income_prev)
        if op_g > rev_g * 2 and op_g > 0.1:
            parts.append("영업레버리지 효과 (비용 효율화)")
        elif op_g > rev_g and op_g > 0:
            parts.append("수익성 개선")
        elif op_g < 0 and rev_g > 0:
            parts.append("비용 증가 압박")
        elif op_g < rev_g < 0:
            parts.append("수익성 악화")

    # 순이익률 변동
    if (financials.revenue is not None and financials.revenue_prev is not None
            and financials.net_income is not None and financials.net_income_prev is not None
            and financials.revenue != 0 and financials.revenue_prev != 0):
        margin = financials.net_income / financials.revenue
        margin_prev = financials.net_income_prev / financials.revenue_prev
        diff = margin - margin_prev
        if diff > 0.03:
            parts.append("이익률 큰 폭 개선")
        elif diff < -0.03:
            parts.append("이익률 큰 폭 하락")

    # 52주 저가 근접 원인
    if (financials.current_price and financials.fifty_two_week_low
            and financials.fifty_two_week_low > 0):
        pct = (financials.current_price - financials.fifty_two_week_low) / financials.fifty_two_week_low
        if pct <= 0.05:
            if financials.fifty_two_week_high and financials.fifty_two_week_high > 0:
                drop = (financials.current_price - financials.fifty_two_week_high) / financials.fifty_two_week_high
                parts.append(f"52주 고점 대비 {drop:.0%} 하락")

    return " / ".join(parts) if parts else ""


def screen(financials: QuarterlyFinancials, low_52w_threshold: float = 0.05,
           event: EarningsEvent | None = None) -> ScreenResult:
    """4가지 독립 필터로 스크리닝."""
    result = ScreenResult(
        ticker=financials.ticker,
        yf_ticker=financials.yf_ticker,
        company_name=financials.company_name,
        market=financials.market,
        report_date=financials.report_date,
        current_price=financials.current_price,
        currency=financials.currency,
        industry=financials.industry,
        description=financials.description,
        eps_actual=event.eps_actual if event else None,
        eps_estimate=event.eps_estimate if event else None,
        eps_surprise=_build_eps_surprise(event),
        context=_build_context(financials),
    )

    # ── 필터 1: 52주 신저가 근접 ──
    result.near_52w_low = _check_near_52w_low(
        financials.current_price,
        financials.fifty_two_week_low,
        low_52w_threshold,
        financials.currency,
    )

    # ── 필터 2: 매출액 성장 QoQ ──
    result.revenue_growth = _check_revenue_growth(
        financials.revenue,
        financials.revenue_prev,
    )

    # ── 필터 3: 영업이익 성장률 > 매출 성장률 ──
    result.op_income_growth = _check_op_income_growth(
        financials.revenue,
        financials.revenue_prev,
        financials.operating_income,
        financials.operating_income_prev,
    )

    # ── 필터 4: 순이익률 개선 QoQ ──
    result.net_margin_improvement = _check_net_margin_improvement(
        financials.revenue,
        financials.revenue_prev,
        financials.net_income,
        financials.net_income_prev,
        financials.net_margin_history,
    )

    return result


def _check_near_52w_low(price: float | None, low_52w: float | None,
                         threshold: float, currency: str) -> FilterResult:
    if not price or not low_52w or low_52w <= 0:
        return FilterResult(passed=False, detail="데이터 없음")

    pct = (price - low_52w) / low_52w
    passed = pct <= threshold
    symbol = "₩" if currency == "KRW" else "$"

    if price >= 1000 and currency == "KRW":
        price_str = f"{symbol}{price:,.0f}"
        low_str = f"{symbol}{low_52w:,.0f}"
    else:
        price_str = f"{symbol}{price:,.2f}"
        low_str = f"{symbol}{low_52w:,.2f}"

    return FilterResult(
        passed=passed,
        value=pct,
        detail=f"{price_str} | 52W Low {low_str} (+{pct:.1%})",
    )


def _check_revenue_growth(rev: float | None, rev_prev: float | None) -> FilterResult:
    if rev is None or rev_prev is None or rev_prev == 0:
        return FilterResult(passed=False, detail="데이터 없음")

    growth = (rev - rev_prev) / abs(rev_prev)
    passed = growth > 0
    return FilterResult(
        passed=passed,
        value=growth,
        detail=f"Rev {growth:+.1%} QoQ",
    )


def _check_op_income_growth(rev: float | None, rev_prev: float | None,
                              op: float | None, op_prev: float | None) -> FilterResult:
    if rev is None or rev_prev is None or rev_prev == 0:
        return FilterResult(passed=False, detail="매출 데이터 없음")
    if op is None or op_prev is None or op_prev == 0:
        return FilterResult(passed=False, detail="영업이익 데이터 없음")

    rev_growth = (rev - rev_prev) / abs(rev_prev)
    op_growth = (op - op_prev) / abs(op_prev)
    passed = op_growth > rev_growth
    return FilterResult(
        passed=passed,
        value=op_growth,
        detail=f"Rev {rev_growth:+.1%} / OpInc {op_growth:+.1%} QoQ",
    )


def _check_net_margin_improvement(rev: float | None, rev_prev: float | None,
                                    ni: float | None, ni_prev: float | None,
                                    margin_history: list[float] | None = None) -> FilterResult:
    if rev is None or rev_prev is None or ni is None or ni_prev is None:
        return FilterResult(passed=False, detail="데이터 없음")
    if rev == 0 or rev_prev == 0:
        return FilterResult(passed=False, detail="매출 0")

    margin = ni / rev
    margin_prev = ni_prev / rev_prev
    passed = margin > margin_prev
    diff_pp = (margin - margin_prev) * 100  # percentage points

    # 연속 개선 분기 수 계산
    streak_str = ""
    if margin_history and len(margin_history) >= 2:
        consecutive = 0
        for i in range(len(margin_history) - 1):
            if margin_history[i] > margin_history[i + 1]:
                consecutive += 1
            else:
                break
        if consecutive >= 2:
            streak_str = f" | {consecutive}분기 연속 개선"
        elif consecutive == 1:
            streak_str = " | 1분기만 개선"

    return FilterResult(
        passed=passed,
        value=margin,
        detail=f"Net Margin {margin_prev:.1%} → {margin:.1%} ({diff_pp:+.1f}pp){streak_str}",
    )
