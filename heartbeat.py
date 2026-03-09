"""Heartbeat 모니터링 — 스케줄러가 살아있는지 확인."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram import Bot

from config.settings import get_settings

logger = logging.getLogger("earnings_screener.heartbeat")


async def send_heartbeat():
    settings = get_settings()
    bot = Bot(token=settings.telegram_bot.bot_token)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[Earnings Screener] Heartbeat OK - {now}"

    try:
        await bot.send_message(chat_id=settings.telegram_bot.chat_id, text=text)
        logger.info("Heartbeat sent")
    except Exception as e:
        logger.error("Heartbeat failed: %s", e)


if __name__ == "__main__":
    asyncio.run(send_heartbeat())
