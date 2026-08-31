"""환경변수 기반 설정 로드."""
import os
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from dotenv import load_dotenv
load_dotenv()
BASE_DIR=Path(__file__).resolve().parent.parent

@dataclass(frozen=True)
class TelegramUserConfig: api_id:int; api_hash:str; phone:str; session_name:str
@dataclass(frozen=True)
class TelegramBotConfig: bot_token:str; chat_id:str
@dataclass(frozen=True)
class FinnhubConfig: api_key:str
@dataclass(frozen=True)
class DartConfig: api_key:str
@dataclass(frozen=True)
class EdgarConfig: user_agent:str

@dataclass
class Settings:
    telegram_user:TelegramUserConfig; telegram_bot:TelegramBotConfig; finnhub:FinnhubConfig
    dart:DartConfig; edgar:EdgarConfig; folder_id:int; schedule_hours:list[int]
    lookback_days:int; low_52w_threshold:float; log_level:str; db_path:Path
    discord_webhook_url:str=""; turnaround_discord_webhook_url:str=""
    @classmethod
    def load(cls):
        hours=[int(h.strip()) for h in os.environ.get("SCHEDULE_HOURS","7,19").split(",")]
        return cls(
            TelegramUserConfig(int(os.environ.get("TELEGRAM_API_ID") or "0"),os.environ.get("TELEGRAM_API_HASH",""),os.environ.get("TELEGRAM_PHONE",""),os.environ.get("SESSION_NAME","earnings_screener")),
            TelegramBotConfig(os.environ.get("TELEGRAM_BOT_TOKEN",""),os.environ.get("TELEGRAM_CHAT_ID","")),
            FinnhubConfig(os.environ.get("FINNHUB_API_KEY","")), DartConfig(os.environ.get("OPENDART_API_KEY","")),
            EdgarConfig(os.environ.get("EDGAR_USER_AGENT","EarningsScreener admin@example.com")),
            int(os.environ.get("TELEGRAM_FOLDER_ID") or "0"),hours,int(os.environ.get("LOOKBACK_DAYS","3")),
            float(os.environ.get("LOW_52W_THRESHOLD","0.05")),os.environ.get("LOG_LEVEL","INFO"),BASE_DIR/"data"/"earnings_screener.db",
            os.environ.get("DISCORD_WEBHOOK_URL",""),os.environ.get("TURNAROUND_DISCORD_WEBHOOK_URL","")
        )
@lru_cache(maxsize=1)
def get_settings(): return Settings.load()
