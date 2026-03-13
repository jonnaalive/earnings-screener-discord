"""Earnings Screener — 파이프라인 오케스트레이터."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from config.settings import get_settings
from database.db import Database
from models.schemas import EarningsEvent
from services.collectors.finnhub_collector import FinnhubCollector
from services.collectors.edgar_collector import EdgarCollector
from services.collectors.dart_collector import DartCollector
from services.collectors.telegram_collector import TelegramCollector
from services.financial_data import enrich_event
from services.screener import screen
from services.low52w_scanner import scan_52w_low
from services.telegram_sender import TelegramSender

logger = logging.getLogger("earnings_screener")


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def collect_all(settings) -> list[EarningsEvent]:
    """모든 수집기에서 이벤트 수집."""
    all_events = []
    collectors = []

    # Finnhub
    if settings.finnhub.api_key:
        collectors.append(FinnhubCollector(settings.finnhub.api_key))

    # EDGAR
    if settings.edgar.user_agent:
        collectors.append(EdgarCollector(settings.edgar.user_agent))

    # DART
    if settings.dart.api_key:
        collectors.append(DartCollector(settings.dart.api_key))

    # Telegram
    if settings.telegram_user.api_id:
        collectors.append(TelegramCollector(
            api_id=settings.telegram_user.api_id,
            api_hash=settings.telegram_user.api_hash,
            phone=settings.telegram_user.phone,
            session_name=settings.telegram_user.session_name,
            folder_id=settings.folder_id,
        ))

    for collector in collectors:
        try:
            events = await collector.collect(lookback_days=settings.lookback_days)
            all_events.extend(events)
        except Exception as e:
            logger.error("[%s] Collector failed (non-fatal): %s", collector.name, e)

    logger.info("Total collected: %d events from %d collectors",
                len(all_events), len(collectors))
    return all_events


def deduplicate(events: list[EarningsEvent]) -> list[EarningsEvent]:
    """(ticker, report_date) 기준 중복 제거. 첫 번째 소스 우선."""
    seen = set()
    unique = []
    for e in events:
        key = (e.ticker, e.report_date)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    logger.info("Deduplicated: %d → %d events", len(events), len(unique))
    return unique


async def run_pipeline():
    """전체 파이프라인 실행."""
    settings = get_settings()
    setup_logging(settings.log_level)

    start_time = time.time()
    run_date = datetime.now().strftime("%Y-%m-%d")
    logger.info("=== Earnings Screener Pipeline START (%s) ===", run_date)

    db = Database(settings.db_path)
    sender = TelegramSender(settings.telegram_bot)

    try:
        await db.connect()

        # 1. 수집
        events = await collect_all(settings)

        # 2. 중복 제거
        events = deduplicate(events)

        # 3. DB 저장
        new_count = 0
        for event in events:
            saved = await db.save_event(event)
            if saved:
                new_count += 1
        logger.info("Saved %d new events to DB", new_count)

        # 4. 재무데이터 보강 + 스크리닝
        results = []
        for event in events:
            try:
                financials = enrich_event(
                    event,
                    edgar_user_agent=settings.edgar.user_agent,
                    dart_api_key=settings.dart.api_key,
                )
                if financials is None:
                    logger.debug("Skipping %s: no financial data", event.ticker)
                    continue

                result = screen(financials, settings.low_52w_threshold, event=event)
                results.append(result)

                # DB 저장
                await db.save_screen_result(result)

            except Exception as e:
                logger.warning("Failed to process %s: %s", event.ticker, e)

        logger.info("Screened: %d / %d events", len(results), len(events))

        # 4-1. S&P 500 52주 신저가 독립 스캔
        try:
            low52_results = scan_52w_low(threshold=settings.low_52w_threshold)
            # 이미 실적 스크리닝에 포함된 종목은 제외
            existing_tickers = {r.ticker for r in results}
            new_low52 = [r for r in low52_results if r.ticker not in existing_tickers]
            if new_low52:
                for r in new_low52:
                    await db.save_screen_result(r)
                results.extend(new_low52)
                logger.info("[52w_scanner] Added %d new 52-week low stocks", len(new_low52))
        except Exception as e:
            logger.error("[52w_scanner] Scan failed (non-fatal): %s", e)

        # 4-2. 주간 다이제스트용 3+ pass 종목 누적
        _save_weekly_hits(results, run_date)

        # 5. 결과 발송
        sent = 0
        if results:
            sent = await sender.send_results(results, len(events), run_date)
        else:
            await sender.send_text(
                f"[Earnings Screener] {run_date}\n수집 {len(events)}개, 스크리닝 결과 없음"
            )
            sent = 1

        # 6. 실행 로그
        duration = time.time() - start_time
        await db.save_run_log(run_date, len(events), len(results), sent, duration)

        logger.info("=== Pipeline DONE (%.1fs) — collected=%d, screened=%d, sent=%d ===",
                     duration, len(events), len(results), sent)

    except Exception as e:
        duration = time.time() - start_time
        logger.exception("Pipeline failed: %s", e)
        try:
            await db.save_run_log(run_date, 0, 0, 0, duration, "error", str(e))
            await sender.send_error(str(e))
        except Exception:
            pass
    finally:
        await db.close()


WEEKLY_HITS_PATH = Path(__file__).resolve().parent / "data" / "weekly_hits.json"


def _save_weekly_hits(results, run_date: str):
    """3+ pass 종목을 weekly_hits.json에 누적."""
    hits = [r for r in results if r.pass_count >= 3]
    if not hits:
        return

    existing = []
    if WEEKLY_HITS_PATH.exists():
        try:
            existing = json.loads(WEEKLY_HITS_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    seen = {(h["ticker"], h["date"]) for h in existing}
    for r in hits:
        key = (r.ticker, run_date)
        if key in seen:
            continue
        seen.add(key)
        existing.append({
            "ticker": r.ticker,
            "name": r.company_name,
            "pass_count": r.pass_count,
            "date": run_date,
        })

    WEEKLY_HITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEEKLY_HITS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Weekly hits: saved %d new (total %d)", len(hits), len(existing))


def main():
    """CLI 진입점."""
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
