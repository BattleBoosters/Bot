from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from scanner.state import AlertState


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "scanner.db"


async def test_snapshot_roundtrip(state_path):
    s = AlertState(state_path)
    await s.init()
    await s.record_holder_snapshot(
        "solana:abc",
        holder_count=1234,
        top10_share=0.42,
        top20_share=0.61,
        tvl_usd=850_000.0,
        buyers_h24=120,
        sellers_h24=80,
    )
    rows = await s.load_holder_snapshots("solana:abc", max_age_days=30)
    assert len(rows) == 1
    r = rows[0]
    assert r["holder_count"] == 1234
    assert r["top20_share"] == 0.61
    assert r["buyers_h24"] == 120


async def test_snapshot_orders_ascending_and_age_filter(state_path):
    s = AlertState(state_path)
    await s.init()
    # Insert two rows with explicit timestamps via raw SQL.
    now = datetime.now(tz=timezone.utc)
    fresh = (now - timedelta(days=1)).isoformat()
    stale = (now - timedelta(days=120)).isoformat()
    async with aiosqlite.connect(state_path) as db:
        await db.execute(
            "INSERT INTO holder_snapshots(token_key, ts, holder_count) VALUES(?, ?, ?)",
            ("solana:abc", stale, 800),
        )
        await db.execute(
            "INSERT INTO holder_snapshots(token_key, ts, holder_count) VALUES(?, ?, ?)",
            ("solana:abc", fresh, 1200),
        )
        await db.commit()

    rows = await s.load_holder_snapshots("solana:abc", max_age_days=90)
    assert len(rows) == 1
    assert rows[0]["holder_count"] == 1200

    rows_all = await s.load_holder_snapshots("solana:abc", max_age_days=180)
    assert len(rows_all) == 2
    # ASC by timestamp.
    assert rows_all[0]["holder_count"] == 800
    assert rows_all[1]["holder_count"] == 1200


async def test_prune_holder_snapshots(state_path):
    s = AlertState(state_path)
    await s.init()
    now = datetime.now(tz=timezone.utc)
    stale = (now - timedelta(days=200)).isoformat()
    fresh = (now - timedelta(days=10)).isoformat()
    async with aiosqlite.connect(state_path) as db:
        await db.execute(
            "INSERT INTO holder_snapshots(token_key, ts, holder_count) VALUES(?, ?, ?)",
            ("solana:abc", stale, 800),
        )
        await db.execute(
            "INSERT INTO holder_snapshots(token_key, ts, holder_count) VALUES(?, ?, ?)",
            ("solana:abc", fresh, 1200),
        )
        await db.commit()
    removed = await s.prune_holder_snapshots(max_age_days=180)
    assert removed == 1
    rows = await s.load_holder_snapshots("solana:abc", max_age_days=365)
    assert len(rows) == 1
