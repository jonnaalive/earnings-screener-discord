"""SQLite 데이터베이스 (실적 이벤트 + 스크리닝 결과 저장)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS earnings_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    report_date TEXT NOT NULL,
    source TEXT NOT NULL,
    market TEXT,
    eps_actual REAL,
    eps_estimate REAL,
    revenue_actual REAL,
    revenue_estimate REAL,
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(ticker, report_date, source)
);

CREATE TABLE IF NOT EXISTS screen_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    yf_ticker TEXT,
    company_name TEXT NOT NULL,
    market TEXT,
    report_date TEXT NOT NULL,
    current_price REAL,
    currency TEXT DEFAULT 'USD',
    industry TEXT DEFAULT '',
    description TEXT DEFAULT '',
    eps_surprise TEXT DEFAULT '',
    context TEXT DEFAULT '',
    near_52w_low INTEGER DEFAULT 0,
    near_52w_low_detail TEXT,
    revenue_growth INTEGER DEFAULT 0,
    revenue_growth_detail TEXT,
    op_income_growth INTEGER DEFAULT 0,
    op_income_growth_detail TEXT,
    net_margin_improvement INTEGER DEFAULT 0,
    net_margin_improvement_detail TEXT,
    all_pass INTEGER DEFAULT 0,
    pass_count INTEGER DEFAULT 0,
    screened_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(ticker, report_date)
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    total_collected INTEGER DEFAULT 0,
    total_screened INTEGER DEFAULT 0,
    total_sent INTEGER DEFAULT 0,
    duration_sec REAL,
    status TEXT DEFAULT 'success',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_date ON earnings_events(report_date);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON earnings_events(ticker);
CREATE INDEX IF NOT EXISTS idx_screen_date ON screen_results(report_date);
CREATE INDEX IF NOT EXISTS idx_screen_allpass ON screen_results(all_pass);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info("Database connected: %s", self.db_path)

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def is_event_exists(self, ticker: str, report_date: str, source: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM earnings_events WHERE ticker = ? AND report_date = ? AND source = ?",
            (ticker, report_date, source),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def save_event(self, event) -> bool:
        """이벤트 저장. 중복이면 False 반환."""
        try:
            cursor = await self._db.execute(
                """INSERT OR IGNORE INTO earnings_events
                (ticker, company_name, report_date, source, market,
                 eps_actual, eps_estimate, revenue_actual, revenue_estimate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.ticker, event.company_name, event.report_date,
                    event.source, event.market,
                    event.eps_actual, event.eps_estimate,
                    event.revenue_actual, event.revenue_estimate,
                ),
            )
            await self._db.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error("Failed to save event %s: %s", event.ticker, e)
            return False

    async def save_screen_result(self, result) -> bool:
        try:
            await self._db.execute(
                """INSERT OR REPLACE INTO screen_results
                (ticker, yf_ticker, company_name, market, report_date,
                 current_price, currency,
                 industry, description, eps_surprise, context,
                 near_52w_low, near_52w_low_detail,
                 revenue_growth, revenue_growth_detail,
                 op_income_growth, op_income_growth_detail,
                 net_margin_improvement, net_margin_improvement_detail,
                 all_pass, pass_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.ticker, result.yf_ticker, result.company_name,
                    result.market, result.report_date,
                    result.current_price, result.currency,
                    result.industry, result.description,
                    result.eps_surprise, result.context,
                    int(result.near_52w_low.passed), result.near_52w_low.detail,
                    int(result.revenue_growth.passed), result.revenue_growth.detail,
                    int(result.op_income_growth.passed), result.op_income_growth.detail,
                    int(result.net_margin_improvement.passed), result.net_margin_improvement.detail,
                    int(result.all_pass), result.pass_count,
                ),
            )
            await self._db.commit()
            return True
        except Exception as e:
            logger.error("Failed to save screen result %s: %s", result.ticker, e)
            return False

    async def save_run_log(self, run_date: str, collected: int, screened: int,
                           sent: int, duration: float, status: str = "success",
                           error: str = None):
        await self._db.execute(
            """INSERT INTO run_log
            (run_date, total_collected, total_screened, total_sent,
             duration_sec, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_date, collected, screened, sent, duration, status, error),
        )
        await self._db.commit()

    async def get_events_by_date(self, report_date: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM earnings_events WHERE report_date = ?",
            (report_date,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_events_recent(self, days: int = 7) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM earnings_events WHERE report_date >= date('now', ? || ' days') ORDER BY report_date DESC",
            (f"-{days}",),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
