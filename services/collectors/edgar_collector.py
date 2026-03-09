"""SEC EDGAR 10-Q/10-K 제출 수집기."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from models.schemas import EarningsEvent
from services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
COMPANY_TICKERS_CACHE = DATA_DIR / "company_tickers.json"


class EdgarCollector(BaseCollector):
    name = "edgar"

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._cik_map: dict[str, str] = {}  # CIK -> ticker
        self._ticker_to_name: dict[str, str] = {}  # ticker -> company name

    def _load_company_tickers(self):
        """SEC company_tickers.json 로드 (캐시 사용)."""
        # 캐시가 최근 7일 이내면 재사용
        if COMPANY_TICKERS_CACHE.exists():
            age = time.time() - COMPANY_TICKERS_CACHE.stat().st_mtime
            if age < 7 * 86400:
                try:
                    data = json.loads(COMPANY_TICKERS_CACHE.read_text(encoding="utf-8"))
                    self._build_maps(data)
                    return
                except Exception:
                    pass

        try:
            resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            COMPANY_TICKERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            COMPANY_TICKERS_CACHE.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            self._build_maps(data)
        except Exception as e:
            logger.error("[edgar] Failed to load company_tickers: %s", e)

    def _build_maps(self, data: dict):
        for _, info in data.items():
            cik = str(info["cik_str"])
            ticker = info["ticker"]
            name = info["title"]
            self._cik_map[cik] = ticker
            self._ticker_to_name[ticker] = name

    async def collect(self, lookback_days: int = 3) -> list[EarningsEvent]:
        self._load_company_tickers()
        events = []
        today = datetime.now()
        from_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        try:
            # EDGAR EFTS full-text search for recent 10-Q/10-K filings
            resp = requests.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": "",
                    "dateRange": "custom",
                    "startdt": from_date,
                    "enddt": to_date,
                    "forms": "10-Q,10-K",
                },
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for filing in data.get("hits", {}).get("hits", []):
                source = filing.get("_source", {})
                entity_name = source.get("entity_name", "")
                file_date = source.get("file_date", "")
                form_type = source.get("form_type", "")

                # CIK에서 ticker 찾기
                cik = str(source.get("entity_id", ""))
                ticker = self._cik_map.get(cik, "")
                if not ticker:
                    continue

                events.append(EarningsEvent(
                    ticker=ticker,
                    company_name=entity_name or self._ticker_to_name.get(ticker, ticker),
                    report_date=file_date,
                    source="edgar",
                    market="US",
                ))

        except requests.exceptions.HTTPError as e:
            # EFTS API가 변경되었을 수 있으므로 대안 시도
            logger.warning("[edgar] EFTS search failed (%s), trying recent filings API", e)
            events = await self._collect_recent_filings(from_date, to_date)
        except Exception as e:
            logger.error("[edgar] Collection failed: %s", e)

        self._log_result(events)
        return events

    async def _collect_recent_filings(self, from_date: str, to_date: str) -> list[EarningsEvent]:
        """EDGAR 최근 제출 RSS 기반 대안."""
        events = []
        try:
            resp = requests.get(
                "https://www.sec.gov/cgi-bin/browse-edgar",
                params={
                    "action": "getcurrent",
                    "type": "10-Q",
                    "dateb": "",
                    "owner": "include",
                    "count": 40,
                    "search_text": "",
                    "output": "atom",
                },
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()

            # Atom XML 파싱
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")

            for entry in soup.find_all("entry"):
                title = entry.find("title")
                updated = entry.find("updated")
                if not title or not updated:
                    continue

                title_text = title.text
                # "10-Q - COMPANY NAME (CIK)" 형태
                parts = title_text.split(" - ", 1)
                if len(parts) < 2:
                    continue
                company_part = parts[1].strip()
                # CIK 추출
                import re
                cik_match = re.search(r"\((\d+)\)", company_part)
                if cik_match:
                    cik = cik_match.group(1)
                    ticker = self._cik_map.get(cik, "")
                    company_name = company_part[:cik_match.start()].strip()
                else:
                    continue

                if not ticker:
                    continue

                file_date = updated.text[:10]  # YYYY-MM-DD
                if from_date <= file_date <= to_date:
                    events.append(EarningsEvent(
                        ticker=ticker,
                        company_name=company_name,
                        report_date=file_date,
                        source="edgar",
                        market="US",
                    ))

        except Exception as e:
            logger.error("[edgar] Recent filings fallback failed: %s", e)

        return events
