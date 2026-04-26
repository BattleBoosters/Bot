"""SQLite-backed dedupe / re-alert state.

A token is alerted at most once every `cooldown_days` unless its score has
risen by ≥0.10 since the last alert. The 'broke a new 14d high' rule lives
upstream in scoring/alerts because it requires the OHLCV series.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS alerted_tokens (
    token_key       TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    last_alert_ts   TEXT NOT NULL,
    last_score      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerted_ts ON alerted_tokens(last_alert_ts);
"""


class AlertState:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def should_alert(
        self,
        token_key: str,
        score: float,
        cooldown_days: int,
        score_jump: float = 0.10,
    ) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT last_alert_ts, last_score FROM alerted_tokens WHERE token_key=?",
                (token_key,),
            )
            row = await cur.fetchone()
        if not row:
            return True
        last_ts_str, last_score = row
        try:
            last_ts = datetime.fromisoformat(last_ts_str)
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        delta_days = (datetime.now(tz=timezone.utc) - last_ts).total_seconds() / 86_400.0
        if delta_days >= cooldown_days:
            return True
        if score - last_score >= score_jump:
            return True
        return False

    async def mark_alerted(
        self, token_key: str, symbol: str, score: float
    ) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO alerted_tokens(token_key, symbol, last_alert_ts, last_score)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(token_key) DO UPDATE SET
                    symbol=excluded.symbol,
                    last_alert_ts=excluded.last_alert_ts,
                    last_score=excluded.last_score
                """,
                (token_key, symbol, ts, score),
            )
            await db.commit()
