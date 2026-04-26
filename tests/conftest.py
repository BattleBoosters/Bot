from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    """Build a daily OHLCV DataFrame from a close list (open=high=low=close)."""
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
    """30 days: slow grind for 23 days, then a clean +40% pump over the last 7."""
    base = list(np.linspace(1.00, 1.10, 23))
    pump = list(np.linspace(1.10, 1.55, 8))[1:]  # 7 fresh values, no duplicate
    closes = base + pump
    assert len(closes) == 30
    volumes = [600_000.0] * 23 + [2_800_000.0] * 7
    return make_ohlcv(closes, volumes)


@pytest.fixture
def flat_series() -> pd.DataFrame:
    closes = [1.0] * 30
    return make_ohlcv(closes)


@pytest.fixture
def parabolic_blow_off() -> pd.DataFrame:
    base = list(np.linspace(1.0, 1.10, 22))
    spike = list(np.linspace(1.10, 3.50, 8))
    closes = base + spike
    return make_ohlcv(closes)


@pytest.fixture
def choppy_uptrend() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    drift = np.linspace(0, 0.30, 30)
    noise = rng.normal(0, 0.05, 30).cumsum()
    closes = [float(1.0 + d + n) for d, n in zip(drift, noise)]
    return make_ohlcv(closes)


@pytest.fixture
def btc_uptrend() -> pd.DataFrame:
    closes = list(np.linspace(60000.0, 64000.0, 30))
    return make_ohlcv(closes)
