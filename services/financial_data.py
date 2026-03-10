"""재무데이터 보강 (yfinance 1차 → EDGAR XBRL / DART 폴백)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import requests
import yfinance as yf

from models.schemas import EarningsEvent, QuarterlyFinancials

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── 한국 종목 매핑 (supply-chain-alpha 기반 + 확장) ──
KR_TICKER_MAP = {
    "SK하이닉스": "000660.KS",
    "삼성전자": "005930.KS",
    "삼성바이오로직스": "207940.KS",
    "현대일렉트릭": "267260.KS",
    "제룡전기": "033100.KS",
    "두산에너빌리티": "034020.KS",
    "풍산": "103140.KS",
    "고려아연": "010130.KS",
    "LS": "006260.KS",
    "LS ELECTRIC": "010120.KS",
    "한국항공우주": "047810.KS",
    "한화에어로스페이스": "012450.KS",
    "LIG넥스원": "079550.KS",
    "한미반도체": "042700.KS",
    "한전": "015760.KS",
    "현대차": "005380.KS",
    "POSCO홀딩스": "005490.KS",
    "HD현대중공업": "329180.KS",
    "NAVER": "035420.KS",
    "카카오": "035720.KS",
    "기아": "000270.KS",
    "셀트리온": "068270.KS",
    "LG에너지솔루션": "373220.KS",
}


def resolve_ticker(raw_ticker: str, market: str) -> str | None:
    """원본 티커를 yfinance 호환 티커로 변환."""
    raw = raw_ticker.strip()

    if market == "US":
        match = re.match(r"^([A-Z]{1,5})", raw.upper())
        return match.group(1) if match else None

    # 한국: 6자리 코드
    if re.match(r"^\d{6}$", raw):
        return raw + ".KS"

    # 이름으로 매핑
    if raw in KR_TICKER_MAP:
        return KR_TICKER_MAP[raw]

    # 부분 매칭
    for name, ticker in KR_TICKER_MAP.items():
        if raw in name or name in raw:
            return ticker

    # ticker_map.json에서 찾기
    ticker_map_path = DATA_DIR / "ticker_map.json"
    if ticker_map_path.exists():
        try:
            with open(ticker_map_path, "r", encoding="utf-8") as f:
                tmap = json.load(f)
            if raw in tmap:
                code = tmap[raw]["ticker"]
                mkt = tmap[raw].get("market", "")
                if mkt in ("KOSPI", "KOSDAQ"):
                    return code + (".KS" if mkt == "KOSPI" else ".KQ")
                return code
            # 코드 역매핑
            for name, info in tmap.items():
                if info["ticker"] == raw:
                    mkt = info.get("market", "")
                    if mkt in ("KOSPI", "KOSDAQ"):
                        return raw + (".KS" if mkt == "KOSPI" else ".KQ")
                    return raw
        except Exception:
            pass

    return None


def enrich_from_yfinance(event: EarningsEvent) -> QuarterlyFinancials | None:
    """yfinance에서 분기 재무데이터 조회."""
    yf_ticker = resolve_ticker(event.ticker, event.market)
    if not yf_ticker:
        logger.debug("Cannot resolve ticker: %s (market=%s)", event.ticker, event.market)
        return None

    try:
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        if not info or not info.get("regularMarketPrice"):
            logger.debug("No market data for %s", yf_ticker)
            return None

        price = info.get("regularMarketPrice") or info.get("currentPrice", 0)
        currency = "KRW" if event.market == "KR" else "USD"

        # 회사 정보 추출
        industry = info.get("industry", "")
        sector = info.get("sector", "")
        long_name = info.get("longName", "") or info.get("shortName", "")
        summary = info.get("longBusinessSummary", "")
        description = _build_description(long_name, sector, industry, summary)

        # company_name 보강 (Finnhub은 티커만 제공)
        display_name = event.company_name
        if display_name == event.ticker and long_name:
            display_name = long_name

        financials = QuarterlyFinancials(
            ticker=event.ticker,
            yf_ticker=yf_ticker,
            company_name=display_name,
            market=event.market,
            report_date=event.report_date,
            current_price=price,
            market_cap=info.get("marketCap"),
            fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            currency=currency,
            industry=industry,
            description=description,
            data_source="yfinance",
        )

        # 분기 손익계산서
        try:
            income = stock.quarterly_income_stmt
            if income is not None and not income.empty and len(income.columns) >= 2:
                # 최신 분기 (Q0) = columns[0], 이전 분기 (Q-1) = columns[1]
                q0 = income.columns[0]
                q1 = income.columns[1]

                rev_values = []  # 분기별 매출 (최신→과거)
                ni_values = []   # 분기별 순이익 (최신→과거)

                for label in ["Total Revenue", "Revenue"]:
                    if label in income.index:
                        financials.revenue = _safe_float(income.loc[label, q0])
                        financials.revenue_prev = _safe_float(income.loc[label, q1])
                        # 전체 분기 매출 추출
                        for col in income.columns:
                            rev_values.append(_safe_float(income.loc[label, col]))
                        break

                for label in ["Operating Income", "Operating Income Loss"]:
                    if label in income.index:
                        financials.operating_income = _safe_float(income.loc[label, q0])
                        financials.operating_income_prev = _safe_float(income.loc[label, q1])
                        break

                for label in ["Net Income", "Net Income Common Stockholders"]:
                    if label in income.index:
                        financials.net_income = _safe_float(income.loc[label, q0])
                        financials.net_income_prev = _safe_float(income.loc[label, q1])
                        # 전체 분기 순이익 추출
                        for col in income.columns:
                            ni_values.append(_safe_float(income.loc[label, col]))
                        break

                # 순이익률 히스토리 계산 (최신→과거)
                if rev_values and ni_values and len(rev_values) == len(ni_values):
                    margins = []
                    for rv, ni in zip(rev_values, ni_values):
                        if rv and ni and rv != 0:
                            margins.append(ni / rv)
                    financials.net_margin_history = margins
        except Exception as e:
            logger.debug("Failed to get quarterly income for %s: %s", yf_ticker, e)

        return financials

    except Exception as e:
        logger.warning("yfinance failed for %s: %s", yf_ticker, e)
        return None


def enrich_from_edgar_xbrl(event: EarningsEvent, user_agent: str) -> QuarterlyFinancials | None:
    """EDGAR XBRL CompanyFacts API에서 재무데이터 조회 (US 폴백)."""
    yf_ticker = resolve_ticker(event.ticker, "US")
    if not yf_ticker:
        return None

    # company_tickers.json에서 CIK 찾기
    cache_path = DATA_DIR / "company_tickers.json"
    if not cache_path.exists():
        return None

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cik = None
        for _, info in data.items():
            if info["ticker"] == yf_ticker:
                cik = str(info["cik_str"])
                break
        if not cik:
            return None

        padded_cik = cik.zfill(10)
        resp = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json",
            headers={"User-Agent": user_agent},
            timeout=30,
        )
        resp.raise_for_status()
        facts = resp.json()

        us_gaap = facts.get("facts", {}).get("us-gaap", {})

        financials = QuarterlyFinancials(
            ticker=event.ticker,
            yf_ticker=yf_ticker,
            company_name=event.company_name,
            market="US",
            report_date=event.report_date,
            data_source="edgar_xbrl",
        )

        # 매출
        rev_data = _get_recent_quarterly(us_gaap, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"])
        if rev_data and len(rev_data) >= 2:
            financials.revenue = rev_data[0]
            financials.revenue_prev = rev_data[1]

        # 영업이익
        op_data = _get_recent_quarterly(us_gaap, ["OperatingIncomeLoss"])
        if op_data and len(op_data) >= 2:
            financials.operating_income = op_data[0]
            financials.operating_income_prev = op_data[1]

        # 순이익
        ni_data = _get_recent_quarterly(us_gaap, ["NetIncomeLoss"])
        if ni_data and len(ni_data) >= 2:
            financials.net_income = ni_data[0]
            financials.net_income_prev = ni_data[1]

        return financials

    except Exception as e:
        logger.debug("EDGAR XBRL failed for %s: %s", event.ticker, e)
        return None


def enrich_from_dart(event: EarningsEvent, api_key: str) -> QuarterlyFinancials | None:
    """OpenDART 단일회사 재무제표 API (KR 폴백)."""
    yf_ticker = resolve_ticker(event.ticker, "KR")
    if not yf_ticker:
        return None

    # corp_code 찾기
    corp_code_path = DATA_DIR / "corpcode.xml"
    if not corp_code_path.exists():
        return None

    try:
        from xml.etree import ElementTree
        root = ElementTree.fromstring(corp_code_path.read_bytes())
        corp_code = None
        for corp in root.findall("list"):
            sc = corp.findtext("stock_code", "").strip()
            if sc == event.ticker:
                corp_code = corp.findtext("corp_code", "")
                break
        if not corp_code:
            return None

        # 최근 분기 보고서
        import re
        year = event.report_date[:4]
        resp = requests.get(
            "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bsns_year": year,
                "reprt_code": "11013",  # 1분기보고서
                "fs_div": "OFS",  # 개별재무제표
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "000":
            return None

        financials = QuarterlyFinancials(
            ticker=event.ticker,
            yf_ticker=yf_ticker,
            company_name=event.company_name,
            market="KR",
            report_date=event.report_date,
            currency="KRW",
            data_source="dart",
        )

        for item in data.get("list", []):
            acct = item.get("account_nm", "")
            amt_str = item.get("thstrm_amount", "").replace(",", "")
            prev_str = item.get("frmtrm_amount", "").replace(",", "")

            try:
                amt = float(amt_str) if amt_str else None
                prev = float(prev_str) if prev_str else None
            except ValueError:
                continue

            if "매출액" in acct:
                financials.revenue = amt
                financials.revenue_prev = prev
            elif "영업이익" in acct:
                financials.operating_income = amt
                financials.operating_income_prev = prev
            elif "당기순이익" in acct:
                financials.net_income = amt
                financials.net_income_prev = prev

        return financials

    except Exception as e:
        logger.debug("DART financials failed for %s: %s", event.ticker, e)
        return None


def enrich_event(event: EarningsEvent, edgar_user_agent: str = "",
                 dart_api_key: str = "") -> QuarterlyFinancials | None:
    """이벤트에 재무데이터를 보강. yfinance 1차 → EDGAR/DART 폴백."""
    # 1차: yfinance
    result = enrich_from_yfinance(event)
    if result and result.revenue is not None:
        return result

    # 2차: 폴백
    if event.market == "US" and edgar_user_agent:
        fallback = enrich_from_edgar_xbrl(event, edgar_user_agent)
        if fallback:
            # yfinance에서 가격/회사정보 가져온 경우 병합
            if result and result.current_price:
                fallback.current_price = result.current_price
                fallback.fifty_two_week_high = result.fifty_two_week_high
                fallback.fifty_two_week_low = result.fifty_two_week_low
            if result and result.description:
                fallback.industry = result.industry
                fallback.description = result.description
                fallback.company_name = result.company_name
            return fallback

    if event.market == "KR" and dart_api_key:
        fallback = enrich_from_dart(event, dart_api_key)
        if fallback:
            if result and result.current_price:
                fallback.current_price = result.current_price
                fallback.fifty_two_week_high = result.fifty_two_week_high
                fallback.fifty_two_week_low = result.fifty_two_week_low
            if result and result.description:
                fallback.industry = result.industry
                fallback.description = result.description
                fallback.company_name = result.company_name
            return fallback

    # yfinance에서 가격만이라도 있으면 반환
    return result


# ── 업종 영한 매핑 ──
SECTOR_KR = {
    "Technology": "기술",
    "Financial Services": "금융",
    "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재",
    "Consumer Defensive": "필수소비재",
    "Industrials": "산업재",
    "Energy": "에너지",
    "Basic Materials": "소재",
    "Communication Services": "커뮤니케이션",
    "Real Estate": "부동산",
    "Utilities": "유틸리티",
}

INDUSTRY_KR = {
    # Technology
    "Semiconductors": "반도체",
    "Software - Application": "응용 소프트웨어",
    "Software - Infrastructure": "인프라 소프트웨어",
    "Consumer Electronics": "가전",
    "Electronic Components": "전자부품",
    "Information Technology Services": "IT 서비스",
    "Internet Content & Information": "인터넷 콘텐츠",
    "Computer Hardware": "컴퓨터 하드웨어",
    "Scientific & Technical Instruments": "과학/기술 장비",
    "Communication Equipment": "통신장비",
    # Financial
    "Banks - Regional": "지방은행",
    "Banks - Diversified": "종합은행",
    "Capital Markets": "자본시장/증권",
    "Insurance - Property & Casualty": "손해보험",
    "Insurance - Life": "생명보험",
    "Insurance - Diversified": "종합보험",
    "Asset Management": "자산운용",
    "Financial Data & Stock Exchanges": "금융 데이터/거래소",
    "Credit Services": "신용/대출",
    # Healthcare
    "Biotechnology": "바이오텍",
    "Drug Manufacturers - General": "종합 제약",
    "Drug Manufacturers - Specialty & Generic": "전문/제네릭 제약",
    "Medical Devices": "의료기기",
    "Medical Instruments & Supplies": "의료 기기/소모품",
    "Health Information Services": "헬스케어 IT",
    "Diagnostics & Research": "진단/연구",
    # Industrials
    "Aerospace & Defense": "항공우주/방위산업",
    "Marine Shipping": "해운",
    "Railroads": "철도",
    "Trucking": "육운",
    "Airlines": "항공",
    "Specialty Industrial Machinery": "특수 산업기계",
    "Electrical Equipment & Parts": "전기장비/부품",
    "Staffing & Employment Services": "인력파견/채용",
    "Industrial Distribution": "산업재 유통",
    "Building Products & Equipment": "건설자재/장비",
    "Engineering & Construction": "엔지니어링/건설",
    "Farm & Heavy Construction Machinery": "농기계/중장비",
    "Conglomerates": "복합기업",
    "Waste Management": "폐기물 처리",
    # Consumer Cyclical
    "Auto Manufacturers": "자동차",
    "Auto Parts": "자동차 부품",
    "Apparel Retail": "의류 유통",
    "Internet Retail": "온라인 유통",
    "Specialty Retail": "전문 소매",
    "Home Improvement Retail": "홈인테리어",
    "Restaurants": "외식업",
    "Leisure": "레저",
    "Residential Construction": "주택건설",
    "Luxury Goods": "명품",
    "Footwear & Accessories": "신발/액세서리",
    "Apparel Manufacturing": "의류 제조",
    # Consumer Defensive
    "Packaged Foods": "가공식품",
    "Beverages - Non-Alcoholic": "비알코올 음료",
    "Beverages - Brewers": "주류",
    "Household & Personal Products": "생활용품",
    "Discount Stores": "할인점",
    "Grocery Stores": "식료품점",
    "Tobacco": "담배",
    # Energy
    "Oil & Gas E&P": "석유/가스 탐사/생산",
    "Oil & Gas Integrated": "종합 석유/가스",
    "Oil & Gas Equipment & Services": "석유/가스 장비",
    "Solar": "태양광",
    "Uranium": "우라늄",
    # Basic Materials
    "Steel": "철강",
    "Copper": "구리",
    "Gold": "금",
    "Aluminum": "알루미늄",
    "Specialty Chemicals": "특수화학",
    "Agricultural Inputs": "농업 소재",
    "Lumber & Wood Production": "목재",
    "Business Equipment & Supplies": "사무기기/용품",
    # Communication Services
    "Entertainment": "엔터테인먼트",
    "Electronic Gaming & Multimedia": "게임/멀티미디어",
    "Telecom Services": "통신서비스",
    "Advertising Agencies": "광고",
    "Publishing": "출판",
    # Real Estate
    "REIT - Industrial": "산업용 리츠",
    "REIT - Residential": "주거용 리츠",
    "REIT - Retail": "상업용 리츠",
    "Real Estate Services": "부동산 서비스",
    # Utilities
    "Utilities - Regulated Electric": "전력 (규제)",
    "Utilities - Renewable": "신재생 에너지",
    "Utilities - Diversified": "종합 유틸리티",
}


def _build_description(long_name: str, sector: str, industry: str, summary: str) -> str:
    """회사 한 줄 설명 (한국어)."""
    # 1) summary에서 한국어 요약 시도
    if summary:
        kr_desc = _summarize_to_korean(summary, long_name, industry)
        if kr_desc:
            return kr_desc

    # 2) industry만이라도 한국어로
    if industry:
        return INDUSTRY_KR.get(industry, industry)
    if sector:
        return SECTOR_KR.get(sector, sector)
    return ""


# ── 영→한 비즈니스 키워드 매핑 ──
_BIZ_TRANSLATIONS = {
    # 제품/서비스
    "consulting services": "컨설팅 서비스",
    "consulting firm": "컨설팅",
    "insurance products": "보험 상품",
    "insurance services": "보험 서비스",
    "container shipping": "컨테이너 해운",
    "shipping services": "해운 서비스",
    "3d printing": "3D 프린팅",
    "digital manufacturing": "디지털 제조",
    "fuel cell": "연료전지",
    "hydrogen": "수소",
    "electric vehicle": "전기차",
    "autonomous driving": "자율주행",
    "cloud computing": "클라우드 컴퓨팅",
    "artificial intelligence": "AI",
    "machine learning": "머신러닝",
    "cybersecurity": "사이버보안",
    "semiconductor": "반도체",
    "data center": "데이터센터",
    "e-commerce": "이커머스",
    "social media": "소셜 미디어",
    "streaming": "스트리밍",
    "biotechnology": "바이오테크",
    "pharmaceutical": "제약",
    "medical device": "의료기기",
    "clinical trial": "임상시험",
    "investment banking": "투자은행",
    "asset management": "자산운용",
    "wealth management": "자산관리",
    "real estate": "부동산",
    "oil and gas": "석유/가스",
    "renewable energy": "신재생에너지",
    "solar energy": "태양광",
    "wind energy": "풍력",
    "defense": "방위산업",
    "aerospace": "항공우주",
    "staffing": "인력파견",
    "recruitment": "채용",
    "office products": "사무용품",
    "consumer products": "소비재",
    "food products": "식품",
    "software": "소프트웨어",
    "platform": "플랫폼",
    "marketplace": "마켓플레이스",
    "logistics": "물류",
    "supply chain": "공급망",
    "mining": "광업",
    "construction": "건설",
    # 지역
    "worldwide": "글로벌",
    "globally": "글로벌",
    "internationally": "글로벌",
    "north america": "북미",
    "europe": "유럽",
    "asia": "아시아",
    "united states": "미국",
    "israel": "이스라엘",
    "china": "중국",
    "japan": "일본",
    "korea": "한국",
    # 동사/행위 → 한국어
    "manufactures": "제조",
    "develops": "개발",
    "designs": "설계",
    "distributes": "유통",
    "markets": "판매",
    "operates": "운영",
    "sells": "판매",
    "produces": "생산",
}

# 비즈니스 유형 패턴 → 한국어 프레임
_BIZ_PATTERNS = [
    # (regex, 한국어 템플릿) — group(1)을 {biz}에 넣음
    (r"container shipping.*?(?:services?)?(?:\s+(?:in|across|throughout)\s+(.+))?$",
     lambda m: f"컨테이너 해운사" + (f" ({_kr_region(m.group(1))})" if m.group(1) else "")),
    (r"(?:personal|commercial|property|casualty|life|residential).*?insurance",
     lambda m: "손해/재물보험"),
    (r"organizational consulting|management consulting|consulting services",
     lambda m: "조직/경영 컨설팅"),
    (r"3d printing|additive manufacturing",
     lambda m: "3D 프린팅/디지털 제조 솔루션"),
    (r"fuel cell|hydrogen.*?(?:power|energy)",
     lambda m: "연료전지/수소 에너지"),
    (r"biopharmaceutical|biotechnology",
     lambda m: "바이오 제약"),
    (r"investment\s+(?:banking|management)|capital\s+markets|brokerage",
     lambda m: "투자은행/자본시장"),
    (r"staffing|employment|recruiting|recruitment|talent",
     lambda m: "인력파견/채용 서비스"),
    (r"(?:computerized|industrial).*?machine|cnc|automation",
     lambda m: "산업용 기계/자동화 장비"),
    (r"printed circuit|pcb|electronic.*?component",
     lambda m: "전자부품/PCB 제조"),
    (r"office.*?product|school.*?product|business.*?suppli",
     lambda m: "사무/학용품 제조/유통"),
    (r"footwear|shoe|apparel.*?retail",
     lambda m: "의류/신발 유통"),
    (r"oilfield|oil.*?gas.*?(?:service|equipment)",
     lambda m: "석유/가스 장비/서비스"),
    (r"(?:diversified|conglomerate|holding)",
     lambda m: "복합기업"),
]


def _kr(text: str) -> str:
    """영문 텍스트를 키워드 매핑으로 부분 한국어화."""
    if not text:
        return ""
    result = text.lower().strip().rstrip(".")
    for eng, kor in _BIZ_TRANSLATIONS.items():
        result = result.replace(eng.lower(), kor)
    # and → /
    result = re.sub(r"\s+and\s+", "/", result)
    return result.strip()


def _kr_region(text: str) -> str:
    """지역 텍스트를 한국어로."""
    if not text:
        return ""
    result = text.lower().strip().rstrip(".")
    for eng, kor in _BIZ_TRANSLATIONS.items():
        result = result.replace(eng.lower(), kor)
    result = re.sub(r"\s+and\s+", "/", result)
    # 쓸모없는 잔여 영어 제거
    result = re.sub(r"\s+(?:the|in|across|throughout)\s+", " ", result)
    return result.strip()


def _summarize_to_korean(summary: str, company_name: str, industry: str) -> str:
    """영문 longBusinessSummary → 한국어 한 줄 요약."""
    import re

    # 첫 문장 추출
    first = summary.split(". ")[0].strip()
    if not first:
        return ""

    # 불필요한 서두 제거
    first = re.sub(
        r"^(?:together with (?:its )?subsidiaries,?\s*|through its subsidiaries,?\s*)",
        "", first, flags=re.IGNORECASE
    ).strip()

    # 동사 뒤 비즈니스 내용 추출
    verbs = (
        r"provides?|offers?|delivers?|supplies|develops?|manufactures?|produces?"
        r"|designs?|operates? as|engages? in|is an?|distributes?"
        r"|focuses on|specializes? in|involved in"
    )
    m = re.search(rf"\b(?:{verbs})\s+(.*)", first, re.IGNORECASE)
    biz_part = m.group(1).strip().rstrip(".") if m else first
    biz_part = re.sub(r"^(?:a |an |the )", "", biz_part, flags=re.IGNORECASE)

    if len(biz_part) < 5:
        return ""

    # 패턴 매칭으로 한국어 변환 시도
    biz_lower = biz_part.lower()
    for pattern, template_fn in _BIZ_PATTERNS:
        pm = re.search(pattern, biz_lower)
        if pm:
            result = template_fn(pm)
            # 업종 접두사
            ind_kr = INDUSTRY_KR.get(industry, "")
            if ind_kr and ind_kr not in result:
                return f"{ind_kr} - {result}"
            return result

    # 패턴 미매칭 → 키워드 번역 + 업종
    translated = _kr(biz_part)
    # 번역이 의미 있게 됐는지 확인 (한글이 들어있으면 성공)
    has_korean = any('\uac00' <= c <= '\ud7a3' for c in translated)
    if has_korean:
        ind_kr = INDUSTRY_KR.get(industry, "")
        if ind_kr:
            return f"{ind_kr} - {translated}"
        return translated

    # 키워드 번역 실패 → 업종만
    ind_kr = INDUSTRY_KR.get(industry, "")
    if ind_kr:
        return ind_kr
    return ""


def _safe_float(val) -> float | None:
    """pandas 값을 안전하게 float으로 변환."""
    try:
        import math
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _get_recent_quarterly(us_gaap: dict, concept_names: list[str]) -> list[float] | None:
    """XBRL facts에서 최근 2개 분기 데이터 추출."""
    for concept in concept_names:
        if concept not in us_gaap:
            continue
        units = us_gaap[concept].get("units", {})
        usd_data = units.get("USD", [])
        # 10-Q filing (quarterly) 데이터만 필터
        quarterly = [d for d in usd_data if d.get("form") == "10-Q"]
        if len(quarterly) < 2:
            continue
        # end date 기준 최신 순 정렬
        quarterly.sort(key=lambda x: x.get("end", ""), reverse=True)
        return [quarterly[0]["val"], quarterly[1]["val"]]
    return None
