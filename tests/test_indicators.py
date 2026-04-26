from __future__ import annotations

from scanner.indicators import (
    clip01,
    closes_below_ma,
    linmap,
    linmap_decay,
    ma,
    median_volume,
    perf,
    rsi,
    up_days,
)


def test_ma(clean_uptrend):
    val = ma(clean_uptrend["close"], 10)
    assert val is not None
    assert 1.20 < val < 1.50


def test_ma_insufficient(clean_uptrend):
    assert ma(clean_uptrend["close"].iloc[:5], 10) is None


def test_perf_positive(clean_uptrend):
    p = perf(clean_uptrend["close"], 7)
    assert p is not None and p > 0


def test_perf_flat(flat_series):
    p = perf(flat_series["close"], 7)
    assert p == 0.0


def test_up_days_clean(clean_uptrend):
    assert up_days(clean_uptrend["close"], 7) == 7


def test_up_days_flat(flat_series):
    assert up_days(flat_series["close"], 7) == 0


def test_closes_below_ma_clean(clean_uptrend):
    assert closes_below_ma(clean_uptrend["close"], 10, 7) == 0


def test_rsi_uptrend_high(clean_uptrend):
    val = rsi(clean_uptrend["close"], 14)
    assert val is not None and val > 80


def test_rsi_flat_neutral(flat_series):
    val = rsi(flat_series["close"], 14)
    assert val is not None and 40 <= val <= 60


def test_median_volume(clean_uptrend):
    med3 = median_volume(clean_uptrend["volume"], 3)
    med14 = median_volume(clean_uptrend["volume"], 14)
    assert med3 is not None and med14 is not None
    assert med3 > med14  # last 3 days higher volumes per fixture


def test_clip01_bounds():
    assert clip01(-1) == 0.0
    assert clip01(2) == 1.0
    assert clip01(0.5) == 0.5
    assert clip01(float("nan")) == 0.0


def test_linmap():
    import pytest
    assert linmap(0.10, 0.10, 0.50) == 0.0
    assert linmap(0.30, 0.10, 0.50) == pytest.approx(0.5)
    assert linmap(0.50, 0.10, 0.50) == 1.0
    assert linmap(0.60, 0.10, 0.50) == 1.0


def test_linmap_decay():
    # plateau region
    assert linmap_decay(60.0, 50.0, 75.0, 88.0) == 1.0
    # below the floor
    assert linmap_decay(40.0, 50.0, 75.0, 88.0) == 0.0
    # decaying
    val = linmap_decay(80.0, 50.0, 75.0, 88.0)
    assert 0 < val < 1
    # at zero point
    assert linmap_decay(88.0, 50.0, 75.0, 88.0) == 0.0
