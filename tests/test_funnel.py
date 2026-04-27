from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.alerts import _render_funnel, format_digest
from scanner.scoring import score_token
from scanner.sources.base import Token
from datetime import datetime, timezone


def _ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range(end="2026-04-25", periods=n, freq="D", tz="UTC").normalize()
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
        index=idx,
    )


def _tok() -> Token:
    return Token(
        symbol="GEM", name="Gem", chain="solana", address="0xabc",
        mcap_usd=20_000_000, vol_24h_usd=2_000_000,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="geckoterminal", pool_address="pool1",
    )


def test_insufficient_history_is_distinct_from_trend_gate(btc_uptrend):
    """A pool with only 25 bars should be rejected as insufficient_history,
    not as trend_gate failure."""
    closes = list(np.linspace(1.0, 1.5, 25))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection == "insufficient_history"


def test_short_history_30_to_49_uses_ma20_fallback(btc_uptrend):
    """30-49 daily bars: gate accepts close > MA20 alone, no MA50 required."""
    closes = list(np.linspace(1.0, 1.6, 40))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection is None  # passes the relaxed short-history gate
    assert sc.score > 0


def test_trend_gate_still_strict_with_full_history(btc_uptrend):
    """120 bars sideways then dip → MA20 < MA50 or close < MA20 → reject."""
    closes = list(np.linspace(1.0, 1.5, 80)) + list(np.linspace(1.5, 0.95, 40))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection == "trend_gate"


def test_render_funnel_compact_layout():
    out = _render_funnel({
        "raw": 600, "deduped": 526, "passed_filters": 80,
        "scored": 50, "qualified": 0,
        "rejections": {
            "trend_gate": 30, "insufficient_history": 10,
            "no_ohlcv": 5, "score_below_threshold": 5,
        },
    })
    assert "raw 600" in out and "deduped 526" in out
    assert "filtres 80" in out and "scored 50" in out and "qualified 0" in out
    # Sorted by frequency, prettified labels rendered.
    assert "trend_gate" in out
    assert "history<30d 10" in out
    assert "score<seuil 5" in out


def test_render_funnel_no_rejections_falls_back_to_one_line():
    out = _render_funnel({
        "raw": 1, "deduped": 1, "passed_filters": 1,
        "scored": 1, "qualified": 1, "rejections": {},
    })
    assert "\n" not in out


def test_format_digest_renders_funnel_when_zero_candidates():
    out = format_digest(
        [],
        universe_size=526,
        candidates_total=0,
        highlight_top_n=10,
        mcap_window_str="($1M–$300M)",
        funnel={
            "raw": 600, "deduped": 526, "passed_filters": 80,
            "scored": 50, "qualified": 0,
            "rejections": {"trend_gate": 30, "perf_artifact": 5},
        },
    )
    assert "Aucun candidat" in out
    assert "Funnel" in out and "trend_gate" in out
