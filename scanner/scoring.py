"""Composite long-term momentum score.

The signal we care about: a small-cap whose all-time chart shows a real
uptrend with active buying pressure right now. Not a 3-day spike — a
sustained climb where the price is near its rolling high, the regression
slope on log-price is positive across the full series, and the trend is
confirmed at multiple horizons (7d/14d/30d positive, MA20 > MA50, RSI
out of overbought territory).

Pipeline: hard trend gate → bounded factors → weighted sum.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from scanner.indicators import (
    annualised_from_log_slope,
    clip01,
    drawdown_from_ath,
    linmap,
    linmap_decay,
    log_slope_per_day,
    ma,
    median_volume,
    perf,
    rsi,
    weeks_up_ratio,
)
from scanner.sources.base import Token


WEIGHTS = {
    "slope":         0.30,
    "ath_proximity": 0.20,
    "perf_consist":  0.15,
    "volume":        0.15,
    "rs_btc":        0.10,
    "rsi":           0.10,
}


@dataclass
class ScoredCandidate:
    token: Token
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    rejection: str | None = None


def _trend_gate_status(close: pd.Series) -> str:
    """Returns one of:
      - "ok"                 : MA50 + MA20 + last all aligned bullish
      - "ok_short"           : 30 ≤ history < 50; only MA20 confirmed
      - "insufficient_history": < 30 daily bars (MA20 not even reliable)
      - "trend_gate"         : enough history but MAs not aligned
    """
    n = len(close)
    if n < 30:
        return "insufficient_history"
    last = float(close.iloc[-1])
    ma20 = ma(close, 20)
    if ma20 is None:
        return "insufficient_history"
    if n < 50:
        # Short-history pools: accept MA20 alone when the last close
        # is comfortably above it. Better than rejecting fresh DEX
        # listings that haven't accumulated 50 bars yet.
        return "ok_short" if last > ma20 else "trend_gate"
    ma50 = ma(close, 50)
    if ma50 is None:
        return "trend_gate"
    return "ok" if last > ma20 > ma50 else "trend_gate"


# Sanity-guard thresholds: anything beyond these is almost always bad data
# (pool migration, decimal mismatch, near-zero genesis price), not a real
# opportunity.
MAX_PERF_30D = 5.0       # > +500% in 30 days
MAX_PERF_90D = 20.0      # > +2000% in 90 days
MIN_PRICE_FLOOR = 1e-10  # any candle close below this is dust-data
MAX_BAR_GAP_RATIO = 50.0 # consecutive close ratio > 50× is a data gap


def _data_artifact_reason(
    close: pd.Series, p30: float | None, p90: float | None
) -> str | None:
    """Detect garbage OHLCV that would generate astronomical scores.

    Returns a rejection-reason string when the data looks like an
    artifact, otherwise None. We hard-reject because no legitimate gem
    moves >500%/30d on a clean chart — when we see it, the early bars
    are essentially zero and the perf math explodes.
    """
    if p30 is not None and p30 > MAX_PERF_30D:
        return "perf_artifact"
    if p90 is not None and p90 > MAX_PERF_90D:
        return "perf_artifact"
    s = close.dropna()
    if not s.empty and float(s.min()) < MIN_PRICE_FLOOR:
        return "price_floor_artifact"
    if len(s) >= 2:
        ratio = (s / s.shift(1)).dropna()
        if not ratio.empty:
            r_max = float(ratio.max())
            r_min = float(ratio.min())
            if r_max > MAX_BAR_GAP_RATIO or (r_min > 0 and 1.0 / r_min > MAX_BAR_GAP_RATIO):
                return "data_gap"
    return None


def _factor_slope(annualised: float | None) -> float:
    """Annualised growth rate from log-regression slope.

    +50%/yr → 0, +350%/yr → 1. A token doubling every 6 months
    (≈ +300%/yr) sits comfortably near the top of the scale. Any value
    above 3.5 saturates at 1, so even if a residual artifact slips
    through (e.g. a 10× slope) the score doesn't blow up.
    """
    if annualised is None:
        return 0.0
    return linmap(annualised, 0.50, 3.50)


def _factor_ath_proximity(dd: float | None, include_post_peak: bool = False) -> float:
    """1.0 at the all-time high, 0.0 at the cutoff drawdown.

    Default cutoff = -25% (strict: only "near ATH" qualifies). When
    `include_post_peak` is True the cutoff softens to -50% so tokens
    that already had a major move and are now consolidating still
    surface — useful for second-leg plays. The trade-off: more noise.
    """
    if dd is None:
        return 0.0
    cutoff = 0.50 if include_post_peak else 0.25
    return clip01(1.0 + dd / cutoff)


def _factor_perf_consist(p7: float | None, p14: float | None, p30: float | None) -> float:
    """All three horizons positive = strong; one missing/negative drops the
    score; magnitude blended in."""
    horizons = [p7, p14, p30]
    present = [p for p in horizons if p is not None]
    if not present:
        return 0.0
    positive_share = sum(1 for p in present if p > 0) / len(present)
    avg_mag = float(np.mean([max(0.0, p) for p in present]))
    magnitude = linmap(avg_mag, 0.05, 0.50)
    return clip01(0.6 * positive_share + 0.4 * magnitude)


def _factor_volume(volume: pd.Series) -> float:
    """Median volume on the last 14 days vs the last 60 days. Capital
    sticking around (or growing) keeps the trend alive."""
    med14 = median_volume(volume, 14)
    med60 = median_volume(volume, 60)
    if med14 is None or med60 is None or med60 <= 0:
        return 0.0
    ratio = med14 / med60
    # 0.8x = 0, 1.0x = 0.5, 1.5x or more = 1
    return clip01((ratio - 0.8) / 0.7)


def _factor_rs_btc(p30: float | None, btc_p30: float | None) -> float:
    if p30 is None or btc_p30 is None:
        return 0.0
    return linmap(p30 - btc_p30, 0.0, 0.50)


def _factor_rsi(value: float | None) -> float:
    """Penalise terminal overheat. Comfortable up to 75, fading to 0 at 88."""
    if value is None:
        return 0.0
    return linmap_decay(value, 40.0, 75.0, 88.0)


def score_token(
    token: Token,
    ohlcv: pd.DataFrame,
    btc_ohlcv: pd.DataFrame | None,
    include_post_peak: bool = False,
) -> ScoredCandidate:
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return ScoredCandidate(token=token, score=0.0, rejection="no_ohlcv")

    close = ohlcv["close"].astype(float)
    volume = ohlcv["volume"].astype(float) if "volume" in ohlcv.columns else pd.Series(dtype=float)

    gate_status = _trend_gate_status(close)
    if gate_status in {"trend_gate", "insufficient_history"}:
        return ScoredCandidate(token=token, score=0.0, rejection=gate_status)

    p7 = perf(close, 7)
    p14 = perf(close, 14)
    p30 = perf(close, 30)
    p60 = perf(close, 60)
    p90 = perf(close, 90)

    artifact = _data_artifact_reason(close, p30, p90)
    if artifact is not None:
        return ScoredCandidate(token=token, score=0.0, rejection=artifact)

    rsi_val = rsi(close, 14)
    slope_d = log_slope_per_day(close, min_points=20)
    annualised = annualised_from_log_slope(slope_d)
    dd = drawdown_from_ath(close)
    weeks_up = weeks_up_ratio(close, n_weeks=12)

    btc_p30: float | None = None
    if btc_ohlcv is not None and not btc_ohlcv.empty and "close" in btc_ohlcv.columns:
        btc_p30 = perf(btc_ohlcv["close"].astype(float), 30)

    factors = {
        "slope":         _factor_slope(annualised),
        "ath_proximity": _factor_ath_proximity(dd, include_post_peak=include_post_peak),
        "perf_consist":  _factor_perf_consist(p7, p14, p30),
        "volume":        _factor_volume(volume) if not volume.empty else 0.0,
        "rs_btc":        _factor_rs_btc(p30, btc_p30),
        "rsi":           _factor_rsi(rsi_val),
    }
    score = sum(WEIGHTS[k] * factors[k] for k in WEIGHTS)

    metrics = {
        "perf_7d":           p7,
        "perf_14d":          p14,
        "perf_30d":          p30,
        "perf_60d":          p60,
        "perf_90d":          p90,
        "rsi_14":            rsi_val,
        "btc_perf_30d":      btc_p30,
        "log_slope_per_day": slope_d,
        "annualised_growth": annualised,
        "drawdown_from_ath": dd,
        "weeks_up_12":       weeks_up,
        "history_days":      int(len(close)),
    }
    return ScoredCandidate(
        token=token,
        score=float(score),
        factors=factors,
        metrics=metrics,
        rejection=None,
    )


def rank_candidates(
    scored: list[ScoredCandidate],
    threshold: float,
    top_n: int | None = None,
) -> list[ScoredCandidate]:
    """Filter by threshold and sort by score desc.

    `top_n` is now optional. If None, all qualified candidates are returned —
    the user wants the full list, not an arbitrary slice. Charts still get
    capped downstream by SCANNER_CHART_TOP_N for Telegram volume reasons.
    """
    qualified = [s for s in scored if s.rejection is None and s.score >= threshold]
    qualified.sort(key=lambda s: s.score, reverse=True)
    if top_n is None:
        return qualified
    return qualified[:top_n]
