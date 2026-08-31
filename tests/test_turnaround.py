from models.schemas import QuarterlyFinancials, EarningsEvent
from services.turnaround import detect_turnaround, build_turnaround_message

def test_detects_beaten_down_inflection():
    f=QuarterlyFinancials(ticker="TEST",yf_ticker="TEST",company_name="Test Co",market="US",report_date="2026-08-31",
        current_price=60,fifty_two_week_high=100,revenue_history=[110,100,105,110],
        operating_income_history=[8,4,2,3],net_margin_history=[.06,.03,.01,.02])
    e=EarningsEvent("TEST","Test Co","2026-08-31","test",market="US",eps_actual=1.1,eps_estimate=1.0)
    s=detect_turnaround(f,e)
    assert s is not None
    assert s.score >= 4
    assert "TURNAROUND RADAR" in build_turnaround_message([s],"2026-08-31")

def test_rejects_weak_signal():
    f=QuarterlyFinancials(ticker="FLAT",yf_ticker="FLAT",company_name="Flat",market="US",report_date="2026-08-31",
        current_price=95,fifty_two_week_high=100,revenue_history=[90,100,110],operating_income_history=[5,6,7],net_margin_history=[.03,.04,.05])
    assert detect_turnaround(f,None) is None
