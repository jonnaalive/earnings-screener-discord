"""데이터 모델 정의."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class EarningsEvent:
    ticker: str
    company_name: str
    report_date: str
    source: str
    market: str = ""
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None

@dataclass
class QuarterlyFinancials:
    ticker: str
    yf_ticker: str
    company_name: str
    market: str
    report_date: str
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    currency: str = "USD"
    revenue: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    revenue_prev: Optional[float] = None
    operating_income_prev: Optional[float] = None
    net_income_prev: Optional[float] = None
    # 최신→과거. 턴어라운드의 '방향 변화'를 보기 위한 시계열.
    revenue_history: list[Optional[float]] = field(default_factory=list)
    operating_income_history: list[Optional[float]] = field(default_factory=list)
    net_income_history: list[Optional[float]] = field(default_factory=list)
    net_margin_history: list[float] = field(default_factory=list)
    industry: str = ""
    description: str = ""
    data_source: str = "yfinance"

@dataclass
class FilterResult:
    passed: bool = False
    value: Optional[float] = None
    detail: str = ""

@dataclass
class ScreenResult:
    ticker: str
    yf_ticker: str
    company_name: str
    market: str
    report_date: str
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    currency: str = "USD"
    industry: str = ""
    description: str = ""
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_surprise: str = ""
    context: str = ""
    near_52w_low: FilterResult = field(default_factory=FilterResult)
    revenue_growth: FilterResult = field(default_factory=FilterResult)
    op_income_growth: FilterResult = field(default_factory=FilterResult)
    net_margin_improvement: FilterResult = field(default_factory=FilterResult)

    @property
    def all_pass(self) -> bool:
        return (self.near_52w_low.passed and self.revenue_growth.passed
                and self.op_income_growth.passed and self.net_margin_improvement.passed)

    @property
    def pass_count(self) -> int:
        return sum([self.near_52w_low.passed, self.revenue_growth.passed,
                    self.op_income_growth.passed, self.net_margin_improvement.passed])
