"""텔레그램 채널 모니터링 수집기."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from models.schemas import EarningsEvent
from services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# 6자리 한국 종목코드 패턴
TICKER_PATTERN = re.compile(r"\b(\d{6})\b")
# $AAPL 패턴
US_TICKER_DOLLAR_PATTERN = re.compile(r"\$([A-Z]{1,5})\b")
# 이름(코드) 패턴
NAME_CODE_PATTERN = re.compile(r"([가-힣A-Za-z0-9]+)\s*[\(\[]\s*(\d{6})\s*[\)\]]")


class TelegramCollector(BaseCollector):
    name = "telegram"

    def __init__(self, api_id: int, api_hash: str, phone: str,
                 session_name: str, folder_id: int = 0):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name
        self.folder_id = folder_id
        self._keywords: list[str] = []
        self._ticker_map: dict = {}
        self._load_config()

    def _load_config(self):
        # 채널 설정 로드
        channels_path = BASE_DIR / "config" / "channels.yaml"
        if channels_path.exists():
            with open(channels_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            kw = cfg.get("earnings_keywords", {})
            self._keywords = kw.get("ko", []) + kw.get("en", [])
        else:
            self._keywords = ["실적", "어닝", "매출", "영업이익", "EPS",
                              "earnings", "beat", "miss", "revenue"]

        # 티커 맵 로드
        ticker_map_path = DATA_DIR / "ticker_map.json"
        if ticker_map_path.exists():
            with open(ticker_map_path, "r", encoding="utf-8") as f:
                self._ticker_map = json.load(f)

    def _has_earnings_keywords(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self._keywords)

    def _extract_tickers(self, text: str) -> list[dict]:
        """메시지에서 종목 정보 추출."""
        results = []
        seen = set()

        # 1) 이름(코드) 패턴
        for match in NAME_CODE_PATTERN.finditer(text):
            name, ticker = match.group(1).strip(), match.group(2)
            if ticker not in seen:
                seen.add(ticker)
                results.append({"ticker": ticker, "name": name, "market": "KR"})

        # 2) $AAPL 패턴
        for match in US_TICKER_DOLLAR_PATTERN.finditer(text):
            ticker = match.group(1)
            if ticker not in seen:
                seen.add(ticker)
                results.append({"ticker": ticker, "name": ticker, "market": "US"})

        # 3) ticker_map에서 이름 매칭
        for name, info in self._ticker_map.items():
            if info["ticker"] in seen:
                continue
            if name in text:
                seen.add(info["ticker"])
                market = "KR" if info.get("market") in ("KOSPI", "KOSDAQ") else "US"
                results.append({"ticker": info["ticker"], "name": name, "market": market})

        # 4) 단독 6자리 코드 (날짜 오인식 방지)
        for match in TICKER_PATTERN.finditer(text):
            ticker = match.group(1)
            if ticker in seen:
                continue
            context = text[max(0, match.start() - 5):match.end() + 5]
            if re.search(r"(20\d{2}|년|월|일|시|분)", context):
                continue
            seen.add(ticker)
            results.append({"ticker": ticker, "name": "unknown", "market": "KR"})

        return results

    async def collect(self, lookback_days: int = 3) -> list[EarningsEvent]:
        events = []

        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetDialogFiltersRequest
        except ImportError:
            logger.warning("[telegram] telethon not installed, skipping")
            return events

        session_path = BASE_DIR / "sessions" / self.session_name
        client = TelegramClient(str(session_path), self.api_id, self.api_hash)

        try:
            await client.start(phone=self.phone)
            cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

            # 폴더 내 채널 가져오기
            channels = []
            if self.folder_id:
                try:
                    filters = await client(GetDialogFiltersRequest())
                    for f in filters.filters:
                        if hasattr(f, "id") and f.id == self.folder_id:
                            for peer in getattr(f, "include_peers", []):
                                try:
                                    entity = await client.get_entity(peer)
                                    channels.append(entity)
                                except Exception:
                                    pass
                            break
                except Exception as e:
                    logger.warning("[telegram] Failed to get folder channels: %s", e)

            if not channels:
                # 폴더가 없으면 최근 대화에서 채널만
                async for dialog in client.iter_dialogs():
                    if dialog.is_channel:
                        channels.append(dialog.entity)
                    if len(channels) >= 20:
                        break

            # 채널별 메시지 읽기
            for channel in channels:
                try:
                    async for msg in client.iter_messages(channel, limit=100):
                        if msg.date.replace(tzinfo=timezone.utc) < cutoff:
                            break
                        if not msg.text:
                            continue
                        if not self._has_earnings_keywords(msg.text):
                            continue

                        tickers = self._extract_tickers(msg.text)
                        for t in tickers:
                            events.append(EarningsEvent(
                                ticker=t["ticker"],
                                company_name=t["name"],
                                report_date=msg.date.strftime("%Y-%m-%d"),
                                source="telegram",
                                market=t["market"],
                            ))
                except Exception as e:
                    channel_title = getattr(channel, "title", "unknown")
                    logger.warning("[telegram] Error reading %s: %s", channel_title, e)

        except Exception as e:
            logger.error("[telegram] Collection failed: %s", e)
        finally:
            await client.disconnect()

        self._log_result(events)
        return events
