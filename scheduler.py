"""APScheduler 기반 스케줄러 — 매일 지정 시간에 파이프라인 실행."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from main import run_pipeline, setup_logging

logger = logging.getLogger("earnings_screener.scheduler")


def start_scheduler():
    settings = get_settings()
    setup_logging(settings.log_level)

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 설정된 시간에 실행 (기본: 07:00, 19:00 KST)
    for hour in settings.schedule_hours:
        scheduler.add_job(
            run_pipeline,
            trigger=CronTrigger(hour=hour, minute=0, timezone="Asia/Seoul"),
            id=f"earnings_screener_{hour:02d}",
            name=f"Earnings Screener {hour:02d}:00 KST",
            replace_existing=True,
        )
        logger.info("Scheduled: %02d:00 KST", hour)

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    # Graceful shutdown
    loop = asyncio.get_event_loop()

    def shutdown(sig):
        logger.info("Received %s, shutting down...", sig.name)
        scheduler.shutdown(wait=False)
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown, sig)
        except NotImplementedError:
            # Windows에서는 signal handler 제한
            pass

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt, shutting down...")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    start_scheduler()
