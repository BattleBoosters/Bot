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


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> float | None:
    """Average True Range (Wilder). Daily-bar ATR over `window` bars."""
    if len(close) < window + 1:
        return None
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).dropna()
    if len(tr) < window:
        return None
    val = tr.ewm(alpha=1 / window, adjust=False).mean().iloc[-1]
    if pd.isna(val) or val <= 0:
        return None
    return float(val)


def wyckoff_compression(ohlcv: pd.DataFrame, recent: int = 20, baseline: int = 90) -> float | None:
    """Range tightening + volume rising = Wyckoff accumulation signature.

    Returns a 0-1 score:
      - Volatility ratio: ATR(recent) / ATR(baseline) — lower means range
        is tightening (compression).
      - Volume ratio: median volume(recent) / median volume(baseline) —
        higher means capital is entering despite tight range.

    A pure compression with volume holding/rising scores high. Random walk
    or expanding range scores low.
    """
    if len(ohlcv) < baseline + 5:
        return None
    if not {"high", "low", "close", "volume"}.issubset(ohlcv.columns):
        return None
    close = ohlcv["close"].astype(float)
    high = ohlcv["high"].astype(float)
    low = ohlcv["low"].astype(float)
    volume = ohlcv["volume"].astype(float)

    atr_r = atr(high.tail(recent + 1), low.tail(recent + 1), close.tail(recent + 1), recent)
    atr_b = atr(high.tail(baseline + 1), low.tail(baseline + 1), close.tail(baseline + 1), baseline)
    if atr_r is None or atr_b is None or atr_b <= 0:
        return None

    last = float(close.iloc[-1])
    if last <= 0:
        return None
    # Normalise to % of price so tokens at any scale compare.
    atr_r_pct = atr_r / last
    atr_b_pct = atr_b / last
    if atr_b_pct <= 0:
        return None
    vol_ratio = atr_r_pct / atr_b_pct  # < 1 = compression, > 1 = expansion

    med_v_r = median_volume(volume, recent)
    med_v_b = median_volume(volume, baseline)
    if med_v_r is None or med_v_b is None or med_v_b <= 0:
        return None
    vol_growth = med_v_r / med_v_b  # > 1 = volume rising

    # Compression component: 0.5 ratio → 1.0, 1.0 → 0.0, capped.
    compression = clip01((1.0 - vol_ratio) / 0.5)
    # Volume component: 0.8 → 0, 1.5 → 1.
    vol_score = clip01((vol_growth - 0.8) / 0.7)
    return float(0.6 * compression + 0.4 * vol_score)


def holder_growth_rate(snapshots: list[tuple[pd.Timestamp, int]], days: int = 14) -> float | None:
    """% growth in holder count between the oldest snapshot ≤ `days` ago and now.

    `snapshots` is a list of (timestamp, holder_count) sorted ascending.
    Returns None if we don't yet have a snapshot at least `days` days old.
    """
    if not snapshots or len(snapshots) < 2:
        return None
    now_ts = snapshots[-1][0]
    cutoff = now_ts - pd.Timedelta(days=days)
    older = [(t, c) for (t, c) in snapshots if t <= cutoff]
    if not older:
        return None
    base_count = older[-1][1]
    last_count = snapshots[-1][1]
    if base_count <= 0:
        return None
    return float(last_count / base_count - 1.0)


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
