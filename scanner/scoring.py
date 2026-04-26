"""Composite momentum score for the gem scanner.

The score is a weighted sum of bounded factors after a hard trend gate.
Designed for pumps in the 3–10 day window on $1M–$100M small caps:
short MAs, perf measured at 3 and 7 days, acceleration check, volume
confirmation on a 3-vs-14-day baseline, RSI tolerance up to 75, relative
strength vs BTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

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
from scanner.sources.base import Token


WEIGHTS = {
    "perf_7d": 0.25,
    "perf_3d": 0.15,
    "acceleration": 0.10,
    "regularity": 0.10,
    "volume": 0.20,
    "rsi": 0.05,
    "rs_btc": 0.10,
    "liquidity": 0.05,
}


@dataclass
class ScoredCandidate:
    token: Token
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float | int | None] = field(default_factory=dict)
    rejection: str | None = None


def _trend_gate(close: pd.Series) -> bool:
    if len(close) < 30:
        return False
    ma10 = ma(close, 10)
    ma30 = ma(close, 30)
    last = float(close.iloc[-1])
    if ma10 is None or ma30 is None:
        return False
    return last > ma10 > ma30


def _factor_perf_7d(p: float | None) -> float:
    return linmap(p, 0.10, 0.50) if p is not None else 0.0


def _factor_perf_3d(p: float | None) -> float:
    return linmap(p, 0.05, 0.30) if p is not None else 0.0


def _factor_acceleration(p3: float | None, p7: float | None) -> float:
    if p3 is None or p7 is None or p7 <= 0:
        return 0.0
    ratio = p3 / p7
    return linmap(ratio, 0.20, 0.55)


def _factor_regularity(close: pd.Series) -> float:
    ud = up_days(close, 7)
    if ud is None:
        return 0.0
    base = ud / 7.0
    cb = closes_below_ma(close, 10, 7)
    if cb is not None and cb > 1:
        base -= 0.15 * (cb - 1)
    return clip01(base)


def _factor_volume(volume: pd.Series) -> float:
    med3 = median_volume(volume, 3)
    med14 = median_volume(volume, 14)
    if med3 is None or med14 is None or med14 <= 0:
        return 0.0
    ratio = med3 / med14 - 1.0
    return clip01(ratio)


def _factor_rsi(value: float | None) -> float:
    if value is None:
        return 0.0
    return linmap_decay(value, 50.0, 75.0, 88.0)


def _factor_rs_btc(token_perf: float | None, btc_perf: float | None) -> float:
    if token_perf is None or btc_perf is None:
        return 0.0
    return linmap(token_perf - btc_perf, 0.0, 0.30)


def _factor_liquidity(vol_24h_usd: float | None) -> float:
    if vol_24h_usd is None or vol_24h_usd <= 0:
        return 0.0
    return clip01(np.log10(vol_24h_usd / 1e5))


def score_token(
    token: Token, ohlcv: pd.DataFrame, btc_ohlcv: pd.DataFrame | None
) -> ScoredCandidate:
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return ScoredCandidate(token=token, score=0.0, rejection="no_ohlcv")

    close = ohlcv["close"].astype(float)
    volume = ohlcv["volume"].astype(float) if "volume" in ohlcv.columns else pd.Series(dtype=float)

    if not _trend_gate(close):
        return ScoredCandidate(token=token, score=0.0, rejection="trend_gate")

    p3 = perf(close, 3)
    p7 = perf(close, 7)
    p14 = perf(close, 14)
    rsi_val = rsi(close, 14)

    btc_p7: float | None = None
    if btc_ohlcv is not None and not btc_ohlcv.empty and "close" in btc_ohlcv.columns:
        btc_p7 = perf(btc_ohlcv["close"].astype(float), 7)

    factors = {
        "perf_7d": _factor_perf_7d(p7),
        "perf_3d": _factor_perf_3d(p3),
        "acceleration": _factor_acceleration(p3, p7),
        "regularity": _factor_regularity(close),
        "volume": _factor_volume(volume) if not volume.empty else 0.0,
        "rsi": _factor_rsi(rsi_val),
        "rs_btc": _factor_rs_btc(p7, btc_p7),
        "liquidity": _factor_liquidity(token.vol_24h_usd),
    }
    score = sum(WEIGHTS[k] * factors[k] for k in WEIGHTS)

    metrics = {
        "perf_3d": p3,
        "perf_7d": p7,
        "perf_14d": p14,
        "rsi_14": rsi_val,
        "btc_perf_7d": btc_p7,
        "up_days_7": up_days(close, 7),
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
    top_n: int,
) -> list[ScoredCandidate]:
    qualified = [s for s in scored if s.rejection is None and s.score >= threshold]
    qualified.sort(key=lambda s: s.score, reverse=True)
    return qualified[:top_n]
