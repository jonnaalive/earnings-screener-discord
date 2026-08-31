"""Run turnaround radar after the normal earnings screener."""
import asyncio
import logging
from datetime import datetime
from config.settings import get_settings
from main import collect_all, deduplicate, setup_logging
from services.financial_data import enrich_event
from services.turnaround import detect_turnaround, build_turnaround_message
from services.discord_sender import send_html

logger = logging.getLogger("turnaround_radar")

async def run_turnaround():
    s=get_settings(); setup_logging(s.log_level)
    events=deduplicate(await collect_all(s))
    signals=[]
    for event in events:
        try:
            fin=enrich_event(event,edgar_user_agent=s.edgar.user_agent,dart_api_key=s.dart.api_key)
            if not fin: continue
            signal=detect_turnaround(fin,event)
            if signal: signals.append(signal)
        except Exception as exc:
            logger.warning("Failed to process %s: %s", event.ticker, exc)
            continue
    if not signals:
        logger.info("No turnaround signals detected from %d events", len(events))
        return 0
    # Prefer a dedicated channel. Fallback keeps the feature usable before a second webhook is configured.
    webhook=s.turnaround_discord_webhook_url or s.discord_webhook_url
    if not webhook:
        raise RuntimeError("Discord webhook is not configured")
    text=build_turnaround_message(signals,datetime.now().strftime("%Y-%m-%d"))
    send_html(webhook,text,username="턴어라운드 레이더")
    logger.info("Sent %d turnaround signals", len(signals))
    return len(signals)

if __name__=="__main__": asyncio.run(run_turnaround())
