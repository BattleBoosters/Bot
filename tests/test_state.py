from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scanner.sources.base import Token
from scanner.state import AlertState, _snapshot_to_token, _token_to_snapshot


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "scanner.db"


def _tok(**kw) -> Token:
    base = dict(
        symbol="GEM",
        name="Gem",
        chain="solana",
        address="0xabc",
        coingecko_id=None,
        mcap_usd=20_000_000.0,
        vol_24h_usd=2_000_000.0,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=20),
        source="geckoterminal",
        pool_address="pool1",
        chart_url="https://example.com/x",
        extra={"unique_trader_ratio": 0.4},
    )
    base.update(kw)
    return Token(**base)


async def test_should_alert_first_time(state_path):
    s = AlertState(state_path)
    await s.init()
    ok = await s.should_alert("solana:0xabc", 0.7, cooldown_days=5)
    assert ok is True


async def test_should_alert_cooldown(state_path):
    s = AlertState(state_path)
    await s.init()
    await s.mark_alerted("solana:0xabc", "GEM", 0.70)
    ok = await s.should_alert("solana:0xabc", 0.72, cooldown_days=5)
    assert ok is False


async def test_should_alert_score_jump(state_path):
    s = AlertState(state_path)
    await s.init()
    await s.mark_alerted("solana:0xabc", "GEM", 0.62)
    ok = await s.should_alert("solana:0xabc", 0.75, cooldown_days=5, score_jump=0.10)
    assert ok is True


async def test_watchlist_roundtrip(state_path):
    s = AlertState(state_path)
    await s.init()
    tok = _tok()
    await s.upsert_watchlist(tok, 0.55)
    loaded = await s.load_watchlist()
    assert len(loaded) == 1
    assert loaded[0].symbol == "GEM"
    assert loaded[0].pool_address == "pool1"
    assert loaded[0].extra.get("unique_trader_ratio") == 0.4
    # Upserting again updates score, no duplicate row.
    await s.upsert_watchlist(tok, 0.65)
    again = await s.load_watchlist()
    assert len(again) == 1


async def test_watchlist_prune(state_path):
    s = AlertState(state_path)
    await s.init()
    tok = _tok()
    await s.upsert_watchlist(tok, 0.50)
    # Force the row to look stale by direct UPDATE.
    import aiosqlite
    stale_iso = (datetime.now(tz=timezone.utc) - timedelta(days=5)).isoformat()
    async with aiosqlite.connect(state_path) as db:
        await db.execute("UPDATE watchlist SET last_seen_ts=?", (stale_iso,))
        await db.commit()
    removed = await s.prune_watchlist(max_age_hours=48)
    assert removed == 1
    assert await s.load_watchlist() == []


def test_snapshot_serialization():
    tok = _tok()
    snap = _token_to_snapshot(tok)
    back = _snapshot_to_token(snap)
    assert back.symbol == tok.symbol
    assert back.chain == tok.chain
    assert back.pool_address == tok.pool_address
    assert back.created_at is not None
    assert back.extra == tok.extra
