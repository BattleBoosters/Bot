from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from scanner.accumulation import (
    WEIGHTS,
    rank_accumulation,
    score_accumulation,
)
from scanner.sources.base import Token


def _tok(**kw) -> Token:
    base = dict(
        symbol="GEM",
        name="Gem",
        chain="solana",
        address="So1NaTokenMint000000000000000000000000000000",
        mcap_usd=20_000_000.0,
        vol_24h_usd=2_000_000.0,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=120),
        source="geckoterminal",
        pool_address="pool1",
        chart_url="https://example.com/x",
        extra={"tx_h24": {"buyers": 220, "sellers": 140, "buys": 380, "sells": 250}},
    )
    base.update(kw)
    return Token(**base)


def _snapshots_growing(days: int = 20) -> list[dict]:
    """Synthetic snapshot history: holders climb +25%, top20 falls 4pp,
    TVL grows +30%, buyers dominate sellers."""
    out = []
    now = datetime.now(tz=timezone.utc)
    for i in range(days):
        ts = (now - timedelta(days=days - 1 - i)).isoformat()
        out.append({
            "ts": ts,
            "holder_count": int(1000 * (1 + 0.25 * i / (days - 1))),
            "top10_share": 0.55 - 0.02 * (i / (days - 1)),
            "top20_share": 0.70 - 0.04 * (i / (days - 1)),
            "tvl_usd": 500_000.0 * (1 + 0.30 * i / (days - 1)),
            "buyers_h24": 200 + i,
            "sellers_h24": 150,
        })
    return out


def _snapshots_dying(days: int = 20) -> list[dict]:
    out = []
    now = datetime.now(tz=timezone.utc)
    for i in range(days):
        ts = (now - timedelta(days=days - 1 - i)).isoformat()
        out.append({
            "ts": ts,
            "holder_count": int(1000 * (1 - 0.10 * i / (days - 1))),
            "top10_share": 0.55 + 0.05 * (i / (days - 1)),
            "top20_share": 0.70 + 0.05 * (i / (days - 1)),
            "tvl_usd": 500_000.0 * (1 - 0.20 * i / (days - 1)),
            "buyers_h24": 50,
            "sellers_h24": 200,
        })
    return out


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_score_strong_accumulation(clean_uptrend):
    sc = score_accumulation(_tok(), clean_uptrend, _snapshots_growing(20))
    assert sc.rejection is None
    assert sc.score > 0.5, f"expected strong accumulation, got {sc.score} {sc.factors}"
    assert sc.factors["holder_growth"] > 0.5
    # 14-day delta on a 20-day linear ramp yields ~+20% (not the full +30%
    # over the whole window) → score lands around 0.45.
    assert sc.factors["tvl_growth"] > 0.4
    assert sc.factors["distribution"] > 0.5
    assert sc.factors["buy_pressure"] > 0.0
    assert sc.metrics["holder_growth_14d"] is not None
    assert sc.metrics["snapshot_history_days"] is not None and \
        sc.metrics["snapshot_history_days"] >= 14


def test_score_dying_token_low_factors(clean_uptrend):
    sc = score_accumulation(_tok(), clean_uptrend, _snapshots_dying(20))
    assert sc.factors["holder_growth"] == 0.0
    assert sc.factors["tvl_growth"] == 0.0
    assert sc.factors["distribution"] < 0.5
    assert sc.factors["buy_pressure"] == 0.0


def test_score_no_data(clean_uptrend):
    sc = score_accumulation(_tok(), pd.DataFrame(), [])
    assert sc.rejection == "no_data"


def test_score_partial_data_only_ohlcv(clean_uptrend):
    sc = score_accumulation(_tok(), clean_uptrend, [])
    assert sc.rejection is None
    # Only Wyckoff (and maybe buy_pressure from extra.tx_h24) contribute.
    assert sc.factors["holder_growth"] == 0.0
    assert sc.factors["distribution"] == 0.0
    # buy_pressure can fire from the live tx_h24 in token.extra alone.
    assert sc.factors["buy_pressure"] >= 0.0


def test_rank_accumulation_threshold(clean_uptrend):
    a = score_accumulation(_tok(symbol="A"), clean_uptrend, _snapshots_growing())
    b = score_accumulation(_tok(symbol="B"), clean_uptrend, _snapshots_dying())
    out = rank_accumulation([a, b], threshold=0.4)
    assert len(out) == 1
    assert out[0].token.symbol == "A"


def test_rank_accumulation_topn_optional(clean_uptrend):
    a = score_accumulation(_tok(symbol="A"), clean_uptrend, _snapshots_growing())
    b = score_accumulation(_tok(symbol="B"), clean_uptrend, _snapshots_growing())
    full = rank_accumulation([a, b], threshold=0.0)
    capped = rank_accumulation([a, b], threshold=0.0, top_n=1)
    assert len(full) == 2 and len(capped) == 1
