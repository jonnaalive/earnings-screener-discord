"""Finnhub 어닝 캘린더 수집기."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from models.schemas import EarningsEvent
from services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class FinnhubCollector(BaseCollector):
    name = "finnhub"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://finnhub.io/api/v1"

    async def collect(self, lookback_days: int = 3) -> list[EarningsEvent]:
        events = []
        today = datetime.now()
        from_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        try:
            resp = requests.get(
                f"{self.base_url}/calendar/earnings",
                params={"from": from_date, "to": to_date, "token": self.api_key},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("earningsCalendar", []):
                # epsActual이 있는 것만 (이미 발표된 건)
                if item.get("epsActual") is None:
                    continue

                events.append(EarningsEvent(
                    ticker=item["symbol"],
                    company_name=item["symbol"],  # Finnhub은 회사명 미제공
                    report_date=item["date"],
                    source="finnhub",
                    market="US",
                    eps_actual=item.get("epsActual"),
                    eps_estimate=item.get("epsEstimate"),
                    revenue_actual=item.get("revenueActual"),
                    revenue_estimate=item.get("revenueEstimate"),
                ))

        except Exception as e:
            logger.error("[finnhub] Collection failed: %s", e)

        self._log_result(events)
        return events
