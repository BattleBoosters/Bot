from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.config import Settings
from scanner.sources.base import Token
from scanner.sources.geckoterminal import _wash_signal
from scanner.universe import is_gem_candidate


def test_wash_signal_clean():
    out = _wash_signal(buys=120, sells=110, buyers=80, sellers=70)
    assert out.get("wash_trade_warning") is not True
    assert 0.0 < out["unique_trader_ratio"] <= 1.0


def test_wash_signal_flags_low_unique_ratio():
    # 1000 trades, only 50 unique traders → ratio 0.05 → warning
    out = _wash_signal(buys=600, sells=400, buyers=30, sellers=20)
    assert out["wash_trade_warning"] is True
    assert out["unique_trader_ratio"] < 0.20


def test_wash_signal_flags_one_sided():
    out = _wash_signal(buys=900, sells=20, buyers=200, sellers=10)
    assert out.get("one_sided_warning") is True


def test_wash_signal_ignores_low_volume():
    out = _wash_signal(buys=5, sells=4, buyers=3, sellers=2)
    assert "wash_trade_warning" not in out
    assert "one_sided_warning" not in out


def test_universe_rejects_wash_when_enabled():
    s = Settings(TELEGRAM_BOT_TOKEN="x", TELEGRAM_CHAT_ID="y", SCANNER_REJECT_WASH_TRADE=True)
    tok = Token(
        symbol="WASH",
        chain="solana",
        address="0xwash",
        mcap_usd=10_000_000,
        vol_24h_usd=1_000_000,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=20),
        source="geckoterminal",
        extra={"wash_trade_warning": True},
    )
    ok, reason = is_gem_candidate(tok, s)
    assert not ok and reason == "wash"


def test_universe_keeps_wash_when_disabled():
    s = Settings(TELEGRAM_BOT_TOKEN="x", TELEGRAM_CHAT_ID="y", SCANNER_REJECT_WASH_TRADE=False)
    tok = Token(
        symbol="WASH",
        chain="solana",
        address="0xwash",
        mcap_usd=10_000_000,
        vol_24h_usd=1_000_000,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=20),
        source="geckoterminal",
        extra={"wash_trade_warning": True},
    )
    ok, _ = is_gem_candidate(tok, s)
    assert ok
