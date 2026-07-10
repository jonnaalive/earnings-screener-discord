"""환경변수 기반 설정 로드."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class TelegramUserConfig:
    api_id: int
    api_hash: str
    phone: str
    session_name: str


@dataclass(frozen=True)
class TelegramBotConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class FinnhubConfig:
    api_key: str


@dataclass(frozen=True)
class DartConfig:
    api_key: str


@dataclass(frozen=True)
class EdgarConfig:
    user_agent: str


@dataclass
class Settings:
    telegram_user: TelegramUserConfig
    telegram_bot: TelegramBotConfig
    finnhub: FinnhubConfig
    dart: DartConfig
    edgar: EdgarConfig
    folder_id: int
    schedule_hours: list[int]
    lookback_days: int
    low_52w_threshold: float
    log_level: str
    db_path: Path
    discord_webhook_url: str = ""

    @classmethod
    def load(cls) -> "Settings":
        hours_str = os.environ.get("SCHEDULE_HOURS", "7,19")
        hours = [int(h.strip()) for h in hours_str.split(",")]

        return cls(
            telegram_user=TelegramUserConfig(
                api_id=int(os.environ.get("TELEGRAM_API_ID", "0")),
                api_hash=os.environ.get("TELEGRAM_API_HASH", ""),
                phone=os.environ.get("TELEGRAM_PHONE", ""),
                session_name=os.environ.get("SESSION_NAME", "earnings_screener"),
            ),
            telegram_bot=TelegramBotConfig(
                bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            ),
            finnhub=FinnhubConfig(
                api_key=os.environ.get("FINNHUB_API_KEY", ""),
            ),
            dart=DartConfig(
                api_key=os.environ.get("OPENDART_API_KEY", ""),
            ),
            edgar=EdgarConfig(
                user_agent=os.environ.get("EDGAR_USER_AGENT", "EarningsScreener admin@example.com"),
            ),
            folder_id=int(os.environ.get("TELEGRAM_FOLDER_ID", "0")),
            schedule_hours=hours,
            lookback_days=int(os.environ.get("LOOKBACK_DAYS", "3")),
            low_52w_threshold=float(os.environ.get("LOW_52W_THRESHOLD", "0.05")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            db_path=BASE_DIR / "data" / "earnings_screener.db",
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
