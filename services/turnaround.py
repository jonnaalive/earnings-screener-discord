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


def _safe(v):
    try:
        if v != v: return None
        return float(v)
    except Exception:
        return None


def hydrate_history(fin: QuarterlyFinancials) -> None:
    """Populate up to 8 quarters from yfinance for slope/inflection analysis.
    Non-fatal: the detector falls back to Q0/Q-1 fields when unavailable.
    """
    if fin.revenue_history and fin.operating_income_history:
        return
    try:
        import yfinance as yf
        income = yf.Ticker(fin.yf_ticker).quarterly_income_stmt
        if income is None or income.empty:
            return
        def row(labels):
            for label in labels:
                if label in income.index:
                    return [_safe(income.loc[label, c]) for c in list(income.columns)[:8]]
            return []
        fin.revenue_history = row(["Total Revenue", "Revenue"])
        fin.operating_income_history = row(["Operating Income", "Operating Income Loss"])
        fin.net_income_history = row(["Net Income", "Net Income Common Stockholders"])
        if fin.revenue_history and fin.net_income_history:
            fin.net_margin_history = [ni/rev for rev, ni in zip(fin.revenue_history, fin.net_income_history)
                                      if rev not in (None, 0) and ni is not None]
    except Exception:
        return


def _growth_series(values):
    out = []
    for i in range(len(values)-1):
        cur, prev = values[i], values[i+1]
        if cur is not None and prev not in (None, 0):
            out.append((cur-prev)/abs(prev))
    return out


def detect_turnaround(fin: QuarterlyFinancials, event: EarningsEvent | None = None) -> TurnaroundSignal | None:
    hydrate_history(fin)
    score, reasons, cautions = 0, [], []
    drawdown = None
    if fin.current_price and fin.fifty_two_week_high and fin.fifty_two_week_high > 0:
        drawdown = fin.current_price/fin.fifty_two_week_high-1
        if drawdown <= -0.25:
            score += 1; reasons.append(f"주가가 52주 고점 대비 {drawdown:.0%} 하락")

    rev_g = _growth_series(fin.revenue_history)
    revenue_inflection = False
    if len(rev_g) >= 2 and rev_g[0] > rev_g[1] and (rev_g[0] > 0 or rev_g[1] < 0):
        score += 1; revenue_inflection = True
        reasons.append(f"매출 성장률 개선 {rev_g[1]:+.1%} → {rev_g[0]:+.1%}")
    elif fin.revenue is not None and fin.revenue_prev not in (None,0):
        g=(fin.revenue-fin.revenue_prev)/abs(fin.revenue_prev)
        if g>0: score+=1; revenue_inflection=True; reasons.append(f"매출 QoQ 성장 {g:+.1%}")

    op_g = _growth_series(fin.operating_income_history)
    op_inflection = False
    if len(op_g)>=2 and op_g[0]>op_g[1]:
        score+=1; op_inflection=True; reasons.append(f"영업이익 모멘텀 개선 {op_g[1]:+.1%} → {op_g[0]:+.1%}")
    elif fin.operating_income is not None and fin.operating_income_prev is not None and fin.operating_income>fin.operating_income_prev:
        score+=1; op_inflection=True; reasons.append("영업이익 QoQ 개선")

    margins=fin.net_margin_history
    margin_inflection=False
    if len(margins)>=3 and margins[0]>margins[1]>margins[2]:
        score+=1; margin_inflection=True
        reasons.append(f"순이익률 2분기 연속 개선 {margins[2]:.1%} → {margins[1]:.1%} → {margins[0]:.1%}")

    if event and event.eps_actual is not None and event.eps_estimate is not None:
        if event.eps_actual>=event.eps_estimate:
            score+=1; reasons.append("EPS 컨센서스 Beat")
        else: cautions.append("EPS는 아직 컨센서스 Miss")

    accelerated=False
    if len(rev_g)>=3 and rev_g[0]-rev_g[1] > rev_g[1]-rev_g[2]: accelerated=True
    if len(margins)>=4 and margins[0]-margins[1] > margins[1]-margins[2]: accelerated=True
    if accelerated: score+=1; reasons.append("개선 속도까지 가속")

    if score<4 or not (revenue_inflection or op_inflection or margin_inflection): return None
    return TurnaroundSignal(fin.ticker,fin.company_name,score,reasons=reasons,cautions=cautions,
                            current_price=fin.current_price,drawdown_from_high=drawdown)


def build_turnaround_message(signals, run_date):
    lines=["# 🔄 TURNAROUND RADAR",f"**{run_date} · 초기 반전 후보 {len(signals)}개**","",
           "> 아직 턴어라운드 확정이 아닙니다. 숫자의 방향이 먼저 꺾이는 종목을 조기 포착합니다.",""]
    for s in sorted(signals,key=lambda x:x.score,reverse=True):
        badge="🔥 HIGH" if s.score>=5 else "🟡 WATCH"
        lines += [f"## {badge} · {s.ticker} — {s.company_name}",f"**Turnaround Score {s.score}/{s.max_score}**"]
        lines += [f"• {r}" for r in s.reasons]
        lines += [f"• ⚠️ {c}" for c in s.cautions]
        lines += ["","**→ 턴어라운드 가능성이 커지고 있습니다. 지금 기업 분석을 검토해보세요.**",""]
    return "\n".join(lines)
