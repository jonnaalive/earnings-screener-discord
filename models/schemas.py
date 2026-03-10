"""데이터 모델 정의."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EarningsEvent:
    """실적 발표 이벤트 (수집 단계)."""
    ticker: str                    # 원본 티커 (e.g., "AAPL", "005930")
    company_name: str
    report_date: str               # YYYY-MM-DD
    source: str                    # "finnhub", "edgar", "dart", "telegram"
    market: str = ""               # "US", "KR"
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None


@dataclass
class QuarterlyFinancials:
    """분기 재무데이터 (enrichment 단계)."""
    ticker: str
    yf_ticker: str                 # yfinance 호환 티커
    company_name: str
    market: str
    report_date: str

    # 가격 데이터
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    currency: str = "USD"

    # 최신 분기 (Q0)
    revenue: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None

    # 이전 분기 (Q-1)
    revenue_prev: Optional[float] = None
    operating_income_prev: Optional[float] = None
    net_income_prev: Optional[float] = None

    # 순이익률 히스토리 (최신→과거 순, 최대 8분기)
    net_margin_history: list[float] = field(default_factory=list)

    # 회사 정보
    industry: str = ""             # e.g., "Semiconductors", "인터넷 서비스"
    description: str = ""          # 한 줄 요약

    # 데이터 소스
    data_source: str = "yfinance"  # "yfinance", "edgar_xbrl", "dart"


@dataclass
class FilterResult:
    """개별 필터 결과."""
    passed: bool = False
    value: Optional[float] = None
    detail: str = ""


@dataclass
class ScreenResult:
    """4-필터 스크리닝 결과."""
    ticker: str
    yf_ticker: str
    company_name: str
    market: str
    report_date: str

    # 가격
    current_price: Optional[float] = None
    market_cap: Optional[float] = None    # 시가총액 (USD or KRW)
    currency: str = "USD"

    # 회사 정보
    industry: str = ""
    description: str = ""          # 한 줄 요약 (업종 + 사업 내용)

    # EPS 서프라이즈
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    eps_surprise: str = ""         # e.g., "EPS $1.28 Beat (est $1.25, +2.4%)"

    # 컨텍스트 (왜 이런 결과가 나왔는지)
    context: str = ""              # e.g., "영업레버리지 효과로 이익률 급개선"

    # 필터 1: 52주 신저가
    near_52w_low: FilterResult = field(default_factory=FilterResult)
    # 필터 2: 매출 성장 QoQ
    revenue_growth: FilterResult = field(default_factory=FilterResult)
    # 필터 3: 영업이익 성장 > 매출 성장
    op_income_growth: FilterResult = field(default_factory=FilterResult)
    # 필터 4: 순이익률 개선
    net_margin_improvement: FilterResult = field(default_factory=FilterResult)

    @property
    def all_pass(self) -> bool:
        return (self.near_52w_low.passed and self.revenue_growth.passed
                and self.op_income_growth.passed and self.net_margin_improvement.passed)

    @property
    def pass_count(self) -> int:
        return sum([
            self.near_52w_low.passed,
            self.revenue_growth.passed,
            self.op_income_growth.passed,
            self.net_margin_improvement.passed,
        ])
