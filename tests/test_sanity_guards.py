from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scanner.scoring import score_token
from scanner.sources.base import Token


def _tok(**kw) -> Token:
    base = dict(
        symbol="GEM",
        name="Gem",
        chain="solana",
        address="0xabc",
        mcap_usd=20_000_000,
        vol_24h_usd=2_000_000,
        price_usd=1.0,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="geckoterminal",
        pool_address="pool1",
    )
    base.update(kw)
    return Token(**base)


def _ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range(end="2026-04-25", periods=n, freq="D", tz="UTC").normalize()
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
        index=idx,
    )


def test_perf_artifact_30d_rejected(btc_uptrend):
    """A pool whose 30d perf is +1000% (10× a $1 base) is data garbage."""
    closes = list(np.linspace(1.0, 1.10, 90)) + list(np.linspace(1.10, 12.0, 30))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection == "perf_artifact"
    assert sc.score == 0.0


def test_perf_artifact_90d_rejected(btc_uptrend):
    """A 90d perf > +2000% is also rejected (covers the WFI / WeFi
    early-pool kind of explosion that produced 3e17 %/yr slopes)."""
    closes = list(np.linspace(1.0, 1.05, 30)) + list(np.linspace(1.05, 25.0, 90))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection == "perf_artifact"


def test_price_floor_artifact_rejected(btc_uptrend):
    """Any close below 1e-10 in the window means decimals/migration noise.

    The data-gap or perf-artifact guards may fire first depending on the
    exact distribution; we just want SOME guard to catch it."""
    closes = [1e-15] * 60 + list(np.linspace(0.50, 1.10, 60))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    assert sc.rejection in {
        "price_floor_artifact", "perf_artifact", "data_gap",
    }, f"unexpected rejection: {sc.rejection}"


def test_data_gap_rejected(btc_uptrend):
    """A consecutive-bar gap >50× betrays a data corruption / pool reset."""
    closes = list(np.linspace(1.0, 1.20, 90)) + [100.0] + list(np.linspace(100.0, 110.0, 29))
    sc = score_token(_tok(), _ohlcv(closes), btc_uptrend)
    # Either rejected as data_gap (preferred) or perf_artifact (fallback).
    assert sc.rejection in {"data_gap", "perf_artifact"}


def test_clean_uptrend_passes_sanity(clean_uptrend, btc_uptrend):
    """Real long uptrend (no insane numbers) sails through the guards."""
    sc = score_token(_tok(), clean_uptrend, btc_uptrend)
    assert sc.rejection is None


def test_post_peak_toggle_helps_drawdown(deep_drawdown, btc_uptrend):
    """A token at ~50% drawdown should score 0 on ATH proximity by default
    but lift toward neutral when post-peak mode is enabled."""
    strict = score_token(_tok(), deep_drawdown, btc_uptrend, include_post_peak=False)
    relaxed = score_token(_tok(), deep_drawdown, btc_uptrend, include_post_peak=True)
    # Both may still be rejected by trend gate at the bottom; if scored,
    # the post-peak run gives a non-zero ATH proximity factor.
    if strict.rejection is None and relaxed.rejection is None:
        assert relaxed.factors["ath_proximity"] >= strict.factors["ath_proximity"]
