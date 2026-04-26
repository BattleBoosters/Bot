from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# All fixtures are 120 daily bars so they exercise MA50, weeks_up_ratio
# (12 weeks ≈ 84 days) and log-slope regression with a meaningful sample.

LEN = 120


def make_ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range(end="2026-04-25", periods=n, freq="D", tz="UTC").normalize()
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": volumes,
        },
        index=idx,
    )


@pytest.fixture
def clean_uptrend() -> pd.DataFrame:
    """120 days: steady climb $1 → $2.50 with light noise + rising volume."""
    rng = np.random.default_rng(7)
    base = np.linspace(1.0, 2.5, LEN)
    noise = rng.normal(1.0, 0.010, LEN)
    closes = (base * noise).tolist()
    volumes = [600_000.0] * 60 + [900_000.0] * 30 + [2_000_000.0] * 30
    return make_ohlcv(closes, volumes)


@pytest.fixture
def flat_series() -> pd.DataFrame:
    closes = [1.0] * LEN
    return make_ohlcv(closes)


@pytest.fixture
def parabolic_blow_off() -> pd.DataFrame:
    """Slow grind for 110 days, then a vertical 4× spike over the last 10."""
    base = list(np.linspace(1.0, 1.20, 110))
    spike = list(np.linspace(1.20, 4.50, 11))[1:]
    closes = base + spike
    return make_ohlcv(closes)


@pytest.fixture
def choppy_uptrend() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    drift = np.linspace(0, 0.40, LEN)
    noise = rng.normal(0, 0.05, LEN).cumsum()
    closes = [float(1.0 + d + n) for d, n in zip(drift, noise)]
    return make_ohlcv(closes)


@pytest.fixture
def btc_uptrend() -> pd.DataFrame:
    closes = list(np.linspace(60000.0, 66000.0, LEN))
    return make_ohlcv(closes)


@pytest.fixture
def deep_drawdown() -> pd.DataFrame:
    """Hits ATH at day 60 ($2.5), then drops 50% by day 120 ($1.25)."""
    up = list(np.linspace(1.0, 2.5, 60))
    down = list(np.linspace(2.5, 1.25, 61))[1:]
    closes = up + down
    return make_ohlcv(closes)
