"""52주 신저가 근접 종목 스캐너 (S&P 500 대상)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from models.schemas import ScreenResult, FilterResult
from services.financial_data import _build_description, INDUSTRY_KR, SECTOR_KR

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SP500_CACHE = DATA_DIR / "sp500_tickers.json"
CACHE_MAX_AGE = 7 * 86400  # 7일


def _load_sp500_tickers() -> list[str]:
    """S&P 500 구성종목 로드 (Wikipedia, 7일 캐시)."""
    # 캐시 확인
    if SP500_CACHE.exists():
        age = time.time() - SP500_CACHE.stat().st_mtime
        if age < CACHE_MAX_AGE:
            try:
                return json.loads(SP500_CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass

    # Wikipedia에서 가져오기
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "EarningsScreener/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"id": "constituents"})
        tickers = []
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if cols:
                ticker = cols[0].text.strip().replace(".", "-")  # BRK.B → BRK-B
                tickers.append(ticker)

        SP500_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SP500_CACHE.write_text(json.dumps(tickers), encoding="utf-8")
        logger.info("[52w_scanner] Loaded %d S&P 500 tickers from Wikipedia", len(tickers))
        return tickers
    except Exception as e:
        logger.error("[52w_scanner] Failed to load S&P 500: %s", e)
        return _fallback_tickers()


def _fallback_tickers() -> list[str]:
    """S&P 500 로드 실패 시 주요 종목."""
    return [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
        "JPM", "V", "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "HD", "CVX",
        "MRK", "ABBV", "PEP", "KO", "COST", "AVGO", "LLY", "TMO", "MCD",
        "CSCO", "ACN", "ABT", "DHR", "NEE", "TXN", "PM", "UNP", "CMCSA",
        "INTC", "AMD", "QCOM", "BA", "CAT", "GE", "RTX", "IBM", "GS",
        "AMGN", "SBUX", "INTU", "BLK", "ISRG", "ADI", "MDLZ", "GILD",
        "SYK", "BKNG", "REGN", "VRTX", "MMC", "CB", "CI", "SO", "DUK",
        "PGR", "SCHW", "ZTS", "LRCX", "CME", "ITW", "BSX", "MO", "EQIX",
        "FIS", "PYPL", "CL", "SHW", "MCO", "NFLX", "NOW", "PANW", "CRM",
        "SNOW", "UBER", "ABNB", "PLTR", "COIN", "RIVN", "LCID",
        "NKE", "DIS", "F", "GM", "T", "VZ", "WFC", "BAC", "C",
    ]


def scan_52w_low(threshold: float = 0.05) -> list[ScreenResult]:
    """S&P 500에서 52주 신저가 근접 종목 스캔."""
    tickers = _load_sp500_tickers()
    if not tickers:
        return []

    logger.info("[52w_scanner] Scanning %d tickers for 52-week lows...", len(tickers))

    # 1년 주간 데이터 배치 다운로드 (한 번에)
    try:
        data = yf.download(
            tickers,
            period="1y",
            interval="1wk",
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.error("[52w_scanner] yf.download failed: %s", e)
        return []

    if data.empty:
        return []

    # 52주 최저가 & 현재가 계산
    try:
        low_52w = data["Low"].min()
        current = data["Close"].iloc[-1]
    except Exception as e:
        logger.error("[52w_scanner] Data processing failed: %s", e)
        return []

    pct_from_low = (current - low_52w) / low_52w
    near_low = pct_from_low[pct_from_low <= threshold].dropna()

    if near_low.empty:
        logger.info("[52w_scanner] No stocks near 52-week low (threshold=%.0f%%)", threshold * 100)
        return []

    logger.info("[52w_scanner] Found %d stocks near 52-week low", len(near_low))

    # 매칭 종목 상세 정보 조회
    today = datetime.now().strftime("%Y-%m-%d")
    results = []
    for ticker in near_low.index:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info or {}

            price = float(current[ticker])
            low = float(low_52w[ticker])
            high = info.get("fiftyTwoWeekHigh", 0)
            pct = float(near_low[ticker])

            long_name = info.get("longName", "") or info.get("shortName", "")
            industry = info.get("industry", "")
            sector = info.get("sector", "")
            summary = info.get("longBusinessSummary", "")
            description = _build_description(long_name, sector, industry, summary)

            # 고점 대비 하락률
            drop_from_high = ""
            if high and high > 0:
                drop_pct = (price - high) / high
                drop_from_high = f"고점 대비 {drop_pct:.0%} 하락"

            result = ScreenResult(
                ticker=ticker,
                yf_ticker=ticker,
                company_name=long_name or ticker,
                market="US",
                report_date=today,
                current_price=price,
                market_cap=info.get("marketCap"),
                currency="USD",
                industry=industry,
                description=description,
                context=drop_from_high,
                near_52w_low=FilterResult(
                    passed=True,
                    value=pct,
                    detail=f"${price:,.2f} | 52W Low ${low:,.2f} (+{pct:.1%})",
                ),
            )
            results.append(result)
        except Exception as e:
            logger.debug("[52w_scanner] Failed to get info for %s: %s", ticker, e)

    # 저가 근접 순 정렬
    results.sort(key=lambda r: r.near_52w_low.value or 999)
    logger.info("[52w_scanner] Returning %d results", len(results))
    return results
