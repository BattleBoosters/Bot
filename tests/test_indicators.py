from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scanner.indicators import (
    annualised_from_log_slope,
    clip01,
    closes_below_ma,
    drawdown_from_ath,
    linmap,
    linmap_decay,
    log_slope_per_day,
    ma,
    median_volume,
    perf,
    rsi,
    up_days,
    weeks_up_ratio,
)


def test_ma(clean_uptrend):
    val = ma(clean_uptrend["close"], 10)
    assert val is not None
    # Last 10 days of a $1→$2.50 climb sit comfortably above $2.
    assert 2.0 < val < 2.7


def test_ma_insufficient(clean_uptrend):
    assert ma(clean_uptrend["close"].iloc[:5], 10) is None


def test_perf_positive(clean_uptrend):
    p = perf(clean_uptrend["close"], 30)
    assert p is not None and p > 0


def test_perf_flat(flat_series):
    p = perf(flat_series["close"], 7)
    assert p == 0.0


def test_up_days_clean(clean_uptrend):
    # Noise can flip 1-2 daily bars; we expect a strong majority green.
    val = up_days(clean_uptrend["close"], 14)
    assert val is not None and val >= 9


def test_up_days_flat(flat_series):
    assert up_days(flat_series["close"], 7) == 0


def test_closes_below_ma_clean(clean_uptrend):
    val = closes_below_ma(clean_uptrend["close"], 10, 7)
    assert val is not None and val <= 2


def test_rsi_uptrend_high(clean_uptrend):
    val = rsi(clean_uptrend["close"], 14)
    assert val is not None and val > 60


def test_rsi_flat_neutral(flat_series):
    val = rsi(flat_series["close"], 14)
    assert val is not None and 40 <= val <= 60


def test_median_volume(clean_uptrend):
    med14 = median_volume(clean_uptrend["volume"], 14)
    med60 = median_volume(clean_uptrend["volume"], 60)
    assert med14 is not None and med60 is not None
    # Volume ramp is concentrated in the last 30 days → med14 above med60.
    assert med14 > med60


def test_log_slope_uptrend_positive(clean_uptrend):
    slope = log_slope_per_day(clean_uptrend["close"])
    assert slope is not None and slope > 0


def test_log_slope_flat_zero(flat_series):
    slope = log_slope_per_day(flat_series["close"])
    assert slope is not None
    assert abs(slope) < 1e-9


def test_log_slope_insufficient():
    s = pd.Series([1.0, 1.1])
    assert log_slope_per_day(s, min_points=14) is None


def test_annualised_from_log_slope():
    # ln(2)/365 → annualised = exp(ln(2)) - 1 = 1.0 (i.e. +100%/yr)
    s = np.log(2) / 365
    out = annualised_from_log_slope(s)
    assert out == pytest.approx(1.0, abs=1e-6)


def test_drawdown_from_ath_uptrend(clean_uptrend):
    dd = drawdown_from_ath(clean_uptrend["close"])
    assert dd is not None
    # We end at-or-near the ATH, so drawdown is tiny.
    assert -0.10 < dd <= 0.0


def test_drawdown_from_ath_deep(deep_drawdown):
    dd = drawdown_from_ath(deep_drawdown["close"])
    assert dd is not None and dd < -0.45


def test_drawdown_from_ath_empty():
    assert drawdown_from_ath(pd.Series([], dtype=float)) is None


def test_weeks_up_ratio_uptrend(clean_uptrend):
    val = weeks_up_ratio(clean_uptrend["close"], n_weeks=12)
    assert val is not None
    assert val >= 0.7


def test_weeks_up_ratio_flat(flat_series):
    val = weeks_up_ratio(flat_series["close"], n_weeks=12)
    assert val == 0.0


def test_weeks_up_ratio_insufficient():
    short = pd.Series(
        np.linspace(1.0, 1.5, 14),
        index=pd.date_range(end="2026-04-25", periods=14, freq="D", tz="UTC"),
    )
    assert weeks_up_ratio(short, n_weeks=12) is None


def test_clip01_bounds():
    assert clip01(-1) == 0.0
    assert clip01(2) == 1.0
    assert clip01(0.5) == 0.5
    assert clip01(float("nan")) == 0.0


def test_linmap():
    assert linmap(0.10, 0.10, 0.50) == 0.0
    assert linmap(0.30, 0.10, 0.50) == pytest.approx(0.5)
    assert linmap(0.50, 0.10, 0.50) == 1.0
    assert linmap(0.60, 0.10, 0.50) == 1.0


def test_linmap_decay():
    assert linmap_decay(60.0, 50.0, 75.0, 88.0) == 1.0
    assert linmap_decay(40.0, 50.0, 75.0, 88.0) == 0.0
    val = linmap_decay(80.0, 50.0, 75.0, 88.0)
    assert 0 < val < 1
    assert linmap_decay(88.0, 50.0, 75.0, 88.0) == 0.0
