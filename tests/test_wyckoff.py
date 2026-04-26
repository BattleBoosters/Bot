from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.indicators import atr, wyckoff_compression
from tests.conftest import make_ohlcv


def _ohlcv_with_range(
    closes: list[float], hi_lo_pct: list[float], volumes: list[float]
) -> pd.DataFrame:
    """Build OHLCV where each bar has high = close*(1+hi_lo_pct/2), low = close*(1-hi_lo_pct/2)."""
    n = len(closes)
    idx = pd.date_range(end="2026-04-25", periods=n, freq="D", tz="UTC").normalize()
    rows = []
    for c, h_pct in zip(closes, hi_lo_pct):
        h = c * (1.0 + h_pct / 2)
        lo = c * (1.0 - h_pct / 2)
        rows.append((c, h, lo, c))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = volumes
    return df[["open", "high", "low", "close", "volume"]]


def test_atr_basic():
    closes = list(np.linspace(1.0, 1.5, 30))
    hi_lo = [0.04] * 30
    df = _ohlcv_with_range(closes, hi_lo, [1_000_000.0] * 30)
    val = atr(df["high"], df["low"], df["close"], 14)
    assert val is not None
    # Average true range with 4% bar range on $1.25 average ≈ $0.05
    assert 0.02 < val < 0.10


def test_wyckoff_compression_classic_pattern():
    """Wide range early (volatile) → tightening late + volume rising = high score."""
    n = 100
    # Choppy first 70 days (5% bar range), tight last 30 days (1.5% bar range)
    hi_lo = [0.05] * 70 + [0.015] * 30
    # Price stays in a $1 range to avoid swamping ATR with drift
    rng = np.random.default_rng(11)
    base = 1.0 + rng.normal(0, 0.04, n).cumsum() * 0.0  # near-flat
    closes = (1.0 + rng.normal(0, 0.02, n)).tolist()
    # Volume is low first 70 days, ramping up last 30 (capital entering)
    volumes = [400_000.0] * 70 + list(np.linspace(700_000.0, 1_500_000.0, 30))
    df = _ohlcv_with_range(closes, hi_lo, volumes)
    score = wyckoff_compression(df, recent=20, baseline=90)
    assert score is not None
    assert score > 0.5, f"expected compression signal, got {score}"


def test_wyckoff_compression_expanding_range_low_score():
    """Range expanding + volume flat = no accumulation signal."""
    n = 100
    hi_lo = [0.015] * 70 + [0.05] * 30  # tight then expanding
    closes = list(np.linspace(1.0, 1.05, n))
    volumes = [1_000_000.0] * n
    df = _ohlcv_with_range(closes, hi_lo, volumes)
    score = wyckoff_compression(df, recent=20, baseline=90)
    assert score is not None
    assert score < 0.5


def test_wyckoff_compression_insufficient_history():
    df = make_ohlcv(list(np.linspace(1.0, 1.1, 30)))
    assert wyckoff_compression(df, recent=20, baseline=90) is None
