from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scanner.config import Settings
from scanner.sources.base import Token
from scanner.universe import apply_filters, dedupe, is_gem_candidate


@pytest.fixture
def settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="x",
        TELEGRAM_CHAT_ID="y",
    )


def _t(**kw) -> Token:
    base = dict(
        symbol="ABC",
        name="ABC",
        chain="solana",
        address="0xabc",
        mcap_usd=20_000_000,
        vol_24h_usd=2_000_000,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=20),
        source="geckoterminal",
    )
    base.update(kw)
    return Token(**base)


def test_pass_all_filters(settings):
    ok, reason = is_gem_candidate(_t(), settings)
    assert ok, reason


def test_reject_too_small_mcap(settings):
    ok, reason = is_gem_candidate(_t(mcap_usd=500_000), settings)
    assert not ok and reason == "mcap"


def test_reject_too_big_mcap(settings):
    ok, reason = is_gem_candidate(_t(mcap_usd=500_000_000), settings)
    assert not ok and reason == "mcap"


def test_reject_too_young(settings):
    ok, reason = is_gem_candidate(
        _t(created_at=datetime.now(tz=timezone.utc) - timedelta(days=2)), settings
    )
    assert not ok and reason == "age"


def test_reject_thin_liquidity(settings):
    ok, reason = is_gem_candidate(
        _t(mcap_usd=50_000_000, vol_24h_usd=500_000), settings
    )
    # vol/mcap = 1% < 5% threshold
    assert not ok and reason == "liquidity"


def test_reject_low_vol_floor(settings):
    ok, reason = is_gem_candidate(
        _t(mcap_usd=2_000_000, vol_24h_usd=50_000), settings
    )
    assert not ok and reason == "liquidity"


def test_reject_honeypot(settings):
    ok, reason = is_gem_candidate(_t(suspected_honeypot=True), settings)
    assert not ok and reason == "honeypot"


def test_dedupe_by_chain_address():
    a = _t(symbol="GEM", mcap_usd=20_000_000, vol_24h_usd=None)
    b = _t(symbol="GEM", mcap_usd=None, vol_24h_usd=2_000_000)
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].mcap_usd == 20_000_000
    assert out[0].vol_24h_usd == 2_000_000


def test_dedupe_by_symbol_when_no_address():
    a = Token(symbol="LSTD", coingecko_id="lstd", mcap_usd=10_000_000, vol_24h_usd=1_000_000)
    b = Token(symbol="LSTD", coingecko_id="lstd", mcap_usd=None, vol_24h_usd=None,
              created_at=datetime.now(tz=timezone.utc) - timedelta(days=200))
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].mcap_usd == 10_000_000
    assert out[0].created_at is not None


def test_apply_filters_stats(settings):
    young = _t(created_at=datetime.now(tz=timezone.utc) - timedelta(days=1))
    too_big = _t(symbol="WHALE", address="0xwhale", mcap_usd=500_000_000)
    good = _t(symbol="GOOD", address="0xgood")
    passed, stats = apply_filters([young, too_big, good], settings)
    assert stats.passed == 1
    assert passed[0].symbol == "GOOD"
    assert stats.rejected_age == 1
    assert stats.rejected_mcap == 1
