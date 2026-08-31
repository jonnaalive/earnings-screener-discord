"""Early turnaround signal detection for beaten-down earnings names."""
from __future__ import annotations
from dataclasses import dataclass, field
from models.schemas import QuarterlyFinancials, EarningsEvent

@dataclass
class TurnaroundSignal:
    ticker: str
    company_name: str
    score: int
    max_score: int = 6
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    current_price: float | None = None
    drawdown_from_high: float | None = None

    @property
    def strong(self) -> bool:
        return self.score >= 4


def _growth_series(values: list[float | None]) -> list[float]:
    clean = [v for v in values if v is not None]
    out = []
    for i in range(len(clean) - 1):
        prev, cur = clean[i + 1], clean[i]
        if prev != 0:
            out.append((cur - prev) / abs(prev))
    return out


def detect_turnaround(fin: QuarterlyFinancials, event: EarningsEvent | None = None) -> TurnaroundSignal | None:
    """Score early inflection, not a completed turnaround.

    Six independent signals: beaten-down price, revenue inflection, operating-income
    inflection, margin streak, EPS surprise/beat, and accelerating improvement.
    Require >=4 and at least one fundamental inflection signal.
    """
    score, reasons, cautions = 0, [], []
    drawdown = None

    if fin.current_price and fin.fifty_two_week_high and fin.fifty_two_week_high > 0:
        drawdown = fin.current_price / fin.fifty_two_week_high - 1
        if drawdown <= -0.25:
            score += 1
            reasons.append(f"주가가 52주 고점 대비 {drawdown:.0%} 하락")

    rev_g = _growth_series(fin.revenue_history)
    revenue_inflection = False
    if len(rev_g) >= 2:
        if rev_g[0] > rev_g[1] and (rev_g[0] > 0 or rev_g[1] < 0):
            score += 1; revenue_inflection = True
            reasons.append(f"매출 성장률 개선 {rev_g[1]:+.1%} → {rev_g[0]:+.1%}")
    elif fin.revenue is not None and fin.revenue_prev not in (None, 0):
        g = (fin.revenue-fin.revenue_prev)/abs(fin.revenue_prev)
        if g > 0:
            score += 1; revenue_inflection = True
            reasons.append(f"매출 QoQ 성장 전환 {g:+.1%}")

    op_g = _growth_series(fin.operating_income_history)
    op_inflection = False
    if len(op_g) >= 2 and op_g[0] > op_g[1]:
        score += 1; op_inflection = True
        reasons.append(f"영업이익 모멘텀 개선 {op_g[1]:+.1%} → {op_g[0]:+.1%}")
    elif fin.operating_income is not None and fin.operating_income_prev is not None:
        if fin.operating_income > fin.operating_income_prev:
            score += 1; op_inflection = True
            reasons.append("영업이익 QoQ 개선")

    margins = fin.net_margin_history
    margin_inflection = False
    if len(margins) >= 3 and margins[0] > margins[1] > margins[2]:
        score += 1; margin_inflection = True
        reasons.append(f"순이익률 2분기 연속 개선 {margins[2]:.1%} → {margins[1]:.1%} → {margins[0]:.1%}")

    if event and event.eps_actual is not None and event.eps_estimate is not None:
        if event.eps_actual >= event.eps_estimate:
            score += 1
            reasons.append("EPS 컨센서스 Beat")
        else:
            cautions.append("EPS는 아직 컨센서스 Miss")

    # Acceleration: latest revenue or margin improvement is larger than prior step.
    accelerated = False
    if len(rev_g) >= 3 and (rev_g[0]-rev_g[1]) > (rev_g[1]-rev_g[2]):
        accelerated = True
    if len(margins) >= 4 and (margins[0]-margins[1]) > (margins[1]-margins[2]):
        accelerated = True
    if accelerated:
        score += 1
        reasons.append("개선 속도까지 가속")

    fundamental = revenue_inflection or op_inflection or margin_inflection
    if score < 4 or not fundamental:
        return None

    if not (fin.current_price and fin.fifty_two_week_high):
        cautions.append("가격 낙폭 데이터 확인 필요")
    return TurnaroundSignal(fin.ticker, fin.company_name, score, reasons=reasons,
                            cautions=cautions, current_price=fin.current_price,
                            drawdown_from_high=drawdown)


def build_turnaround_message(signals: list[TurnaroundSignal], run_date: str) -> str:
    lines = ["# 🔄 TURNAROUND RADAR", f"**{run_date} · 초기 반전 후보 {len(signals)}개**", "",
             "> 아직 턴어라운드가 확정된 종목이 아닙니다. 실적 방향이 꺾이기 시작한 후보를 먼저 분석하라는 신호입니다.", ""]
    for s in sorted(signals, key=lambda x: x.score, reverse=True):
        badge = "🔥 HIGH" if s.score >= 5 else "🟡 WATCH"
        lines += [f"## {badge} · {s.ticker} — {s.company_name}", f"**Turnaround Score: {s.score}/{s.max_score}**"]
        for reason in s.reasons:
            lines.append(f"• {reason}")
        for caution in s.cautions:
            lines.append(f"• ⚠️ {caution}")
        lines += ["", "**→ 턴어라운드 가능성이 커지고 있습니다. 기업 분석을 검토해보세요.**", ""]
    return "\n".join(lines)
