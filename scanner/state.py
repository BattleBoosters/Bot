"""SQLite-backed dedupe / re-alert state and watchlist.

A token is alerted at most once every `cooldown_days` unless its score has
risen by ≥0.10 since the last alert. The 'broke a new 14d high' rule lives
upstream in scoring/alerts because it requires the OHLCV series.

The watchlist table tracks tokens that scored ≥ watchlist threshold during
the last full scan so the hourly delta scan can re-fetch their OHLCV (cache
forced fresh) and alert if their score crosses the main threshold between
full runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from scanner.sources.base import Token

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS alerted_tokens (
    token_key       TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    last_alert_ts   TEXT NOT NULL,
    last_score      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerted_ts ON alerted_tokens(last_alert_ts);

CREATE TABLE IF NOT EXISTS watchlist (
    token_key       TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    chain           TEXT,
    address         TEXT,
    coingecko_id    TEXT,
    pool_address    TEXT,
    last_score      REAL NOT NULL,
    last_seen_ts    TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_seen ON watchlist(last_seen_ts);

CREATE TABLE IF NOT EXISTS holder_snapshots (
    token_key       TEXT NOT NULL,
    ts              TEXT NOT NULL,
    holder_count    INTEGER,
    top10_share     REAL,
    top20_share     REAL,
    tvl_usd         REAL,
    buyers_h24      INTEGER,
    sellers_h24     INTEGER,
    PRIMARY KEY (token_key, ts)
);
CREATE INDEX IF NOT EXISTS idx_holder_ts ON holder_snapshots(token_key, ts);
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

    async def upsert_watchlist(self, token: Token, score: float) -> None:
        ts = datetime.now(tz=timezone.utc).isoformat()
        snapshot = _token_to_snapshot(token)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO watchlist(token_key, symbol, chain, address, coingecko_id,
                                      pool_address, last_score, last_seen_ts, snapshot_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_key) DO UPDATE SET
                    symbol=excluded.symbol,
                    chain=excluded.chain,
                    address=excluded.address,
                    coingecko_id=excluded.coingecko_id,
                    pool_address=excluded.pool_address,
                    last_score=excluded.last_score,
                    last_seen_ts=excluded.last_seen_ts,
                    snapshot_json=excluded.snapshot_json
                """,
                (
                    token.key,
                    token.symbol,
                    token.chain,
                    token.address,
                    token.coingecko_id,
                    token.pool_address,
                    float(score),
                    ts,
                    snapshot,
                ),
            )
            await db.commit()

    async def load_watchlist(self, max_age_hours: int = 36) -> list[Token]:
        cutoff = datetime.now(tz=timezone.utc).timestamp() - max_age_hours * 3600
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT snapshot_json, last_seen_ts FROM watchlist")
            rows = await cur.fetchall()
        out: list[Token] = []
        for snap, last_seen in rows:
            try:
                ts = datetime.fromisoformat(last_seen)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts.timestamp() < cutoff:
                    continue
                out.append(_snapshot_to_token(snap))
            except (ValueError, json.JSONDecodeError) as e:
                logger.debug("watchlist row skip: %s", e)
                continue
        return out

    async def prune_watchlist(self, max_age_hours: int = 48) -> int:
        cutoff = (
            datetime.now(tz=timezone.utc).timestamp() - max_age_hours * 3600
        )
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM watchlist WHERE last_seen_ts < ?", (cutoff_iso,)
            )
            await db.commit()
            return cur.rowcount or 0


    async def record_holder_snapshot(
        self,
        token_key: str,
        *,
        holder_count: int | None = None,
        top10_share: float | None = None,
        top20_share: float | None = None,
        tvl_usd: float | None = None,
        buyers_h24: int | None = None,
        sellers_h24: int | None = None,
    ) -> None:
        """Append a snapshot row keyed by (token, ts). Used to compute
        growth rates over time on subsequent runs."""
        ts = datetime.now(tz=timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO holder_snapshots(
                    token_key, ts, holder_count, top10_share, top20_share,
                    tvl_usd, buyers_h24, sellers_h24
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_key, ts, holder_count, top10_share, top20_share,
                    tvl_usd, buyers_h24, sellers_h24,
                ),
            )
            await db.commit()

    async def load_holder_snapshots(
        self, token_key: str, max_age_days: int = 90
    ) -> list[dict]:
        """Return all snapshots for `token_key` not older than `max_age_days`,
        sorted ascending by ts. Each row is a plain dict for easy use in
        the scoring layer."""
        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT ts, holder_count, top10_share, top20_share, tvl_usd,
                       buyers_h24, sellers_h24
                FROM holder_snapshots
                WHERE token_key = ? AND ts >= ?
                ORDER BY ts ASC
                """,
                (token_key, cutoff),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def prune_holder_snapshots(self, max_age_days: int = 180) -> int:
        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM holder_snapshots WHERE ts < ?", (cutoff,)
            )
            await db.commit()
            return cur.rowcount or 0


def _token_to_snapshot(t: Token) -> str:
    data = {
        "symbol": t.symbol,
        "name": t.name,
        "chain": t.chain,
        "address": t.address,
        "coingecko_id": t.coingecko_id,
        "mcap_usd": t.mcap_usd,
        "vol_24h_usd": t.vol_24h_usd,
        "price_usd": t.price_usd,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "source": t.source,
        "pool_address": t.pool_address,
        "chart_url": t.chart_url,
        "suspected_honeypot": t.suspected_honeypot,
        "extra": t.extra,
    }
    return json.dumps(data, default=str)


def _snapshot_to_token(snap: str) -> Token:
    d = json.loads(snap)
    created = d.get("created_at")
    if created:
        try:
            ca = datetime.fromisoformat(created)
            if ca.tzinfo is None:
                ca = ca.replace(tzinfo=timezone.utc)
        except ValueError:
            ca = None
    else:
        ca = None
    return Token(
        symbol=d.get("symbol") or "",
        name=d.get("name") or "",
        chain=d.get("chain"),
        address=d.get("address"),
        coingecko_id=d.get("coingecko_id"),
        mcap_usd=d.get("mcap_usd"),
        vol_24h_usd=d.get("vol_24h_usd"),
        price_usd=d.get("price_usd"),
        created_at=ca,
        source=d.get("source") or "",
        pool_address=d.get("pool_address"),
        chart_url=d.get("chart_url"),
        suspected_honeypot=bool(d.get("suspected_honeypot", False)),
        extra=d.get("extra") or {},
    )
