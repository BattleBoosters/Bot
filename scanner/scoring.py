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


def _trend_gate(close: pd.Series) -> bool:
    if len(close) < 50:
        return False
    ma20 = ma(close, 20)
    ma50 = ma(close, 50)
    last = float(close.iloc[-1])
    if ma20 is None or ma50 is None:
        return False
    return last > ma20 > ma50


def _factor_slope(annualised: float | None) -> float:
    """Annualised growth rate from log-regression slope.

    +50%/yr → 0, +350%/yr → 1. A token doubling every 6 months
    (≈ +300%/yr) sits comfortably near the top of the scale.
    """
    if annualised is None:
        return 0.0
    return linmap(annualised, 0.50, 3.50)


def _factor_ath_proximity(dd: float | None) -> float:
    """1.0 at the all-time high, 0.0 at -25% drawdown or worse."""
    if dd is None:
        return 0.0
    return clip01(1.0 + dd / 0.25)


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
    token: Token, ohlcv: pd.DataFrame, btc_ohlcv: pd.DataFrame | None
) -> ScoredCandidate:
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return ScoredCandidate(token=token, score=0.0, rejection="no_ohlcv")

    close = ohlcv["close"].astype(float)
    volume = ohlcv["volume"].astype(float) if "volume" in ohlcv.columns else pd.Series(dtype=float)

    if not _trend_gate(close):
        return ScoredCandidate(token=token, score=0.0, rejection="trend_gate")

    p7 = perf(close, 7)
    p14 = perf(close, 14)
    p30 = perf(close, 30)
    p60 = perf(close, 60)
    p90 = perf(close, 90)
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
        "ath_proximity": _factor_ath_proximity(dd),
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
