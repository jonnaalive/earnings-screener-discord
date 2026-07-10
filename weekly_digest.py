"""주간 다이제스트 — 이번주 3+/4 pass 종목 취합 발송."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from telegram import Bot
from telegram.constants import ParseMode

from config.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("weekly_digest")

WEEKLY_HITS_PATH = Path(__file__).resolve().parent / "data" / "weekly_hits.json"


def load_hits() -> list[dict]:
    if not WEEKLY_HITS_PATH.exists():
        return []
    try:
        return json.loads(WEEKLY_HITS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def build_message(hits: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    four_pass = [h for h in hits if h["pass_count"] == 4]
    three_pass = [h for h in hits if h["pass_count"] == 3]

    # 동일 티커 중복 제거 (가장 최근 날짜 유지)
    def dedup(items):
        seen = {}
        for h in items:
            t = h["ticker"]
            if t not in seen or h["date"] > seen[t]["date"]:
                seen[t] = h
        return sorted(seen.values(), key=lambda x: x["date"], reverse=True)

    four_pass = dedup(four_pass)
    three_pass = dedup(three_pass)

    lines = []
    lines.append(f"📋 <b>Weekly Digest</b>  ({today})")
    lines.append("")

    lines.append(f"🏆 <b>ALL 4 PASS</b> ({len(four_pass)})")
    if four_pass:
        for h in four_pass:
            lines.append(f"  ▸ <b>{h['ticker']}</b>  {h['name']}  <i>({h['date']})</i>")
    else:
        lines.append("  없음")
    lines.append("")

    lines.append(f"🔸 <b>3 PASS</b> ({len(three_pass)})")
    if three_pass:
        for h in three_pass:
            lines.append(f"  ▸ <b>{h['ticker']}</b>  {h['name']}  <i>({h['date']})</i>")
    else:
        lines.append("  없음")

    return "\n".join(lines)


async def send_digest():
    hits = load_hits()
    if not hits:
        logger.info("No weekly hits to send.")
        return

    settings = get_settings()
    msg = build_message(hits)

    if settings.discord_webhook_url:
        from services.discord_sender import send_html, USERNAME

        send_html(settings.discord_webhook_url, msg, username=USERNAME)
        logger.info("Weekly digest sent to Discord (%d hits)", len(hits))
    else:
        bot = Bot(token=settings.telegram_bot.bot_token)
        await bot.send_message(
            chat_id=settings.telegram_bot.chat_id,
            text=msg,
            parse_mode=ParseMode.HTML,
        )
        logger.info("Weekly digest sent (%d hits)", len(hits))

    # 리셋
    WEEKLY_HITS_PATH.write_text("[]", encoding="utf-8")
    logger.info("weekly_hits.json reset.")


def main():
    asyncio.run(send_digest())


if __name__ == "__main__":
    main()
