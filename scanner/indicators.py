"""Pure-pandas technical indicators used by the scoring layer.

Every function expects an OHLCV DataFrame indexed by daily UTC timestamp
with at least a 'close' column. Returns plain Python floats (or None for
insufficient data) so the scoring layer can be a thin arithmetic shell.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    val = close.iloc[-window:].mean()
    if pd.isna(val):
        return None
    return float(val)


def perf(close: pd.Series, window: int) -> float | None:
    """Return relative price change over `window` bars: close[-1]/close[-window-1] - 1."""
    if len(close) <= window:
        return None
    start = close.iloc[-window - 1]
    end = close.iloc[-1]
    if start is None or pd.isna(start) or start <= 0:
        return None
    return float(end / start - 1.0)


def up_days(close: pd.Series, window: int) -> int | None:
    if len(close) < window + 1:
        return None
    diffs = close.iloc[-window - 1:].diff().iloc[1:]
    return int((diffs > 0).sum())


def closes_below_ma(close: pd.Series, window_ma: int, lookback: int) -> int | None:
    if len(close) < max(window_ma, lookback):
        return None
    ma_series = close.rolling(window_ma).mean()
    tail = close.iloc[-lookback:]
    ma_tail = ma_series.iloc[-lookback:]
    if ma_tail.isna().any():
        return None
    return int((tail < ma_tail).sum())


def rsi(close: pd.Series, window: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(close) < window + 1:
        return None
    delta = close.diff().dropna()
    if len(delta) < window:
        return None
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    rs = last_gain / last_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def median_volume(volume: pd.Series, window: int) -> float | None:
    if len(volume) < window:
        return None
    val = volume.iloc[-window:].median()
    if pd.isna(val) or val <= 0:
        return None
    return float(val)


def log_slope_per_day(close: pd.Series, min_points: int = 14) -> float | None:
    """Linear regression slope of log(close) vs time index, in 'log-units per day'.

    A slope of ln(2)/30 ≈ 0.0231 means the series doubles every 30 days. The
    annualised growth rate equals exp(slope * 365) - 1, used by the scoring
    layer as a horizon-agnostic 'how steep is the all-time uptrend' signal.
    """
    s = close.dropna()
    if len(s) < min_points:
        return None
    s = s[s > 0]
    if len(s) < min_points:
        return None
    y = np.log(s.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    x_mean = x.mean()
    y_mean = y.mean()
    num = float(((x - x_mean) * (y - y_mean)).sum())
    den = float(((x - x_mean) ** 2).sum())
    if den <= 0:
        return None
    return num / den


def annualised_from_log_slope(slope_per_day: float | None) -> float | None:
    if slope_per_day is None:
        return None
    return float(np.exp(slope_per_day * 365.0) - 1.0)


def drawdown_from_ath(close: pd.Series) -> float | None:
    """Current drawdown from rolling all-time high in the provided series.

    Returns a negative number (or 0.0 at the high). E.g. -0.18 = 18% below
    the highest close seen in the window.
    """
    s = close.dropna()
    if s.empty:
        return None
    high = float(s.max())
    last = float(s.iloc[-1])
    if high <= 0:
        return None
    return last / high - 1.0


def weeks_up_ratio(close: pd.Series, n_weeks: int = 12) -> float | None:
    """Fraction of the last `n_weeks` calendar weeks that closed positive.

    Resamples the daily series to weekly closes (W-SUN), takes the last
    `n_weeks + 1` weekly closes, returns up_weeks / n_weeks. None if
    insufficient history.
    """
    s = close.dropna()
    if s.empty:
        return None
    if not isinstance(s.index, pd.DatetimeIndex):
        try:
            s = s.copy()
            s.index = pd.to_datetime(s.index)
        except Exception:
            return None
    weekly = s.resample("W-SUN").last().dropna()
    if len(weekly) < n_weeks + 1:
        return None
    diffs = weekly.iloc[-n_weeks - 1:].diff().iloc[1:]
    return float((diffs > 0).sum() / n_weeks)


def clip01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return float(max(0.0, min(1.0, x)))


def linmap(x: float, lo: float, hi: float) -> float:
    """Linear ramp from 0 at x<=lo to 1 at x>=hi."""
    if hi <= lo:
        return 0.0
    return clip01((x - lo) / (hi - lo))


def linmap_decay(x: float, peak_lo: float, peak_hi: float, zero: float) -> float:
    """1 between [peak_lo, peak_hi]; linear decay to 0 at `zero` above peak_hi."""
    if x < peak_lo:
        return 0.0
    if x <= peak_hi:
        return 1.0
    if zero <= peak_hi:
        return 0.0
    return clip01(1.0 - (x - peak_hi) / (zero - peak_hi))
