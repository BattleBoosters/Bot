"""Composite accumulation score.

The trend scorer answers "is this token in an established uptrend right
now?". This scorer answers the leading question: "is this token being
quietly accumulated before the price moves?".

Signals (all bounded 0-1, weighted sum, no hard gate):

    wyckoff           Range tightening + volume rising on the OHLCV
                      (purely price-based, no extra API needed).
    holder_growth     % growth in holder count over the last 14 days
                      from SQLite snapshots — *the* leading indicator.
                      Comes online once the snapshot history is at least
                      14 days deep.
    distribution      Top-20 holder concentration falling over time
                      (the founder/whale is letting go), or already low
                      to begin with. Same snapshot-driven signal as
                      holder_growth.
    tvl_growth        Pool TVL rising relative to baseline — capital
                      sticking around (snapshot-driven).
    buy_pressure      Buyers count > sellers count sustained on the h24
                      transaction stats (uses GeckoTerminal data we
                      already capture).

The score is parallel to the trend score, not a replacement. A token
that scores high on BOTH = max-conviction setup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from scanner.indicators import clip01, linmap, wyckoff_compression
from scanner.sources.base import Token


WEIGHTS = {
    "wyckoff":       0.25,
    "holder_growth": 0.30,
    "distribution":  0.15,
    "tvl_growth":    0.15,
    "buy_pressure":  0.15,
}


@dataclass
class AccumulationCandidate:
    token: Token
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    rejection: str | None = None


def _factor_holder_growth(
    snapshots: list[dict], days: int = 14
) -> tuple[float, float | None]:
    """Holder-count delta over `days`. Linear ramp: -5% → 0, +30% → 1."""
    if not snapshots:
        return 0.0, None
    latest = snapshots[-1]
    if latest.get("holder_count") is None:
        return 0.0, None
    latest_ts = pd.to_datetime(latest["ts"])
    cutoff = latest_ts - pd.Timedelta(days=days)
    older = [
        s for s in snapshots
        if pd.to_datetime(s["ts"]) <= cutoff and s.get("holder_count") is not None
    ]
    if not older:
        return 0.0, None
    base = older[-1]["holder_count"]
    last = latest["holder_count"]
    if base <= 0:
        return 0.0, None
    growth = float(last) / float(base) - 1.0
    return linmap(growth, -0.05, 0.30), growth


def _factor_distribution(snapshots: list[dict], days: int = 14) -> tuple[float, dict]:
    """Top-20 concentration: low absolute level + decreasing trend = good.

    - Absolute component: `top20 < 0.5` saturates at 1, `> 0.95` floors to 0.
    - Trend component: top20 ↓ by 2 points over `days` = 1, flat = 0.5,
      ↑ by 2 points = 0.
    Combined: 0.6 * absolute + 0.4 * trend.
    """
    metrics: dict[str, Any] = {}
    if not snapshots:
        return 0.0, metrics
    latest = snapshots[-1]
    top20 = latest.get("top20_share")
    if top20 is None:
        return 0.0, metrics
    metrics["top20_share"] = top20
    absolute = clip01((0.95 - float(top20)) / 0.45)

    latest_ts = pd.to_datetime(latest["ts"])
    cutoff = latest_ts - pd.Timedelta(days=days)
    older = [
        s for s in snapshots
        if pd.to_datetime(s["ts"]) <= cutoff and s.get("top20_share") is not None
    ]
    if older:
        base = float(older[-1]["top20_share"])
        delta = float(top20) - base
        metrics["top20_delta"] = delta
        # +0.02 (worse) → 0, -0.02 (better) → 1, flat → 0.5.
        trend = clip01((0.02 - delta) / 0.04)
    else:
        trend = 0.5
    return float(0.6 * absolute + 0.4 * trend), metrics


def _factor_tvl_growth(snapshots: list[dict], days: int = 14) -> tuple[float, float | None]:
    """Pool TVL growth over `days`. -5% → 0, +50% → 1."""
    if not snapshots:
        return 0.0, None
    latest = snapshots[-1]
    if latest.get("tvl_usd") is None:
        return 0.0, None
    latest_ts = pd.to_datetime(latest["ts"])
    cutoff = latest_ts - pd.Timedelta(days=days)
    older = [
        s for s in snapshots
        if pd.to_datetime(s["ts"]) <= cutoff and s.get("tvl_usd") is not None
    ]
    if not older:
        return 0.0, None
    base = float(older[-1]["tvl_usd"])
    last = float(latest["tvl_usd"])
    if base <= 0:
        return 0.0, None
    growth = last / base - 1.0
    return linmap(growth, -0.05, 0.50), growth


def _factor_buy_pressure(snapshots: list[dict], current_token: Token) -> float:
    """Excess of unique buyers over sellers, averaged over snapshots.

    Uses GeckoTerminal `transactions.h24` (buyers, sellers). When the
    current token already has fresh values in `extra.tx_h24` we add
    that as a "now" sample for tighter responsiveness.
    """
    samples: list[float] = []
    for s in snapshots[-7:]:
        b = s.get("buyers_h24")
        ss = s.get("sellers_h24")
        if b is not None and ss is not None and (b + ss) > 30:
            ratio = (b - ss) / (b + ss)
            samples.append(float(ratio))
    tx_now = (current_token.extra or {}).get("tx_h24") or {}
    nb, ns = tx_now.get("buyers"), tx_now.get("sellers")
    if nb is not None and ns is not None and (nb + ns) > 30:
        samples.append(float(nb - ns) / float(nb + ns))
    if not samples:
        return 0.0
    avg = float(np.mean(samples))
    return linmap(avg, 0.0, 0.30)


def score_accumulation(
    token: Token,
    ohlcv: pd.DataFrame | None,
    snapshots: list[dict] | None,
) -> AccumulationCandidate:
    """Score the token on accumulation evidence.

    Designed to *complement* the trend scorer, not replace it. It will
    return rejection="no_data" if neither OHLCV nor snapshots are
    available (we have nothing to look at). Otherwise it scores whatever
    data is present — partial signal still beats no signal during
    the first weeks of running.
    """
    snaps = snapshots or []
    if (ohlcv is None or ohlcv.empty) and not snaps:
        return AccumulationCandidate(token=token, score=0.0, rejection="no_data")

    wyck = 0.0
    if ohlcv is not None and not ohlcv.empty:
        try:
            v = wyckoff_compression(ohlcv, recent=20, baseline=90)
            wyck = float(v) if v is not None else 0.0
        except Exception:
            wyck = 0.0

    holder_factor, holder_growth = _factor_holder_growth(snaps, days=14)
    dist_factor, dist_metrics = _factor_distribution(snaps, days=14)
    tvl_factor, tvl_growth = _factor_tvl_growth(snaps, days=14)
    buy_factor = _factor_buy_pressure(snaps, token)

    factors = {
        "wyckoff":       wyck,
        "holder_growth": holder_factor,
        "distribution":  dist_factor,
        "tvl_growth":    tvl_factor,
        "buy_pressure":  buy_factor,
    }
    score = sum(WEIGHTS[k] * factors[k] for k in WEIGHTS)

    metrics: dict[str, Any] = {
        "holder_growth_14d": holder_growth,
        "tvl_growth_14d":    tvl_growth,
        "snapshot_history_days": _snapshot_history_days(snaps),
        **dist_metrics,
    }
    return AccumulationCandidate(
        token=token,
        score=float(score),
        factors=factors,
        metrics=metrics,
        rejection=None,
    )


def _snapshot_history_days(snapshots: list[dict]) -> int | None:
    if not snapshots or len(snapshots) < 2:
        return None
    first = pd.to_datetime(snapshots[0]["ts"])
    last = pd.to_datetime(snapshots[-1]["ts"])
    return int(max(0, (last - first).total_seconds() / 86400))


def rank_accumulation(
    scored: list[AccumulationCandidate],
    threshold: float,
    top_n: int | None = None,
) -> list[AccumulationCandidate]:
    qualified = [s for s in scored if s.rejection is None and s.score >= threshold]
    qualified.sort(key=lambda s: s.score, reverse=True)
    if top_n is None:
        return qualified
    return qualified[:top_n]
