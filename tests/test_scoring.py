from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from scanner.scoring import WEIGHTS, rank_candidates, score_token
from scanner.sources.base import Token


def _gem_token(**kw) -> Token:
    base = dict(
        symbol="GEM",
        name="Gem",
        chain="solana",
        address="0xabc",
        mcap_usd=20_000_000,
        vol_24h_usd=2_000_000,
        price_usd=1.0,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="geckoterminal",
        pool_address="pool1",
    )
    base.update(kw)
    return Token(**base)


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_score_clean_uptrend(clean_uptrend, btc_uptrend):
    sc = score_token(_gem_token(), clean_uptrend, btc_uptrend)
    assert sc.rejection is None, sc.rejection
    assert sc.score > 0.55, f"expected solid score, got {sc.score} factors={sc.factors}"
    for v in sc.factors.values():
        assert 0.0 <= v <= 1.0
    # All long-term factors should fire on a clean 120-day climb.
    assert sc.factors["slope"] > 0.5
    assert sc.factors["ath_proximity"] > 0.7
    assert sc.factors["perf_consist"] > 0.5
    assert sc.factors["volume"] > 0.2


def test_score_flat_blocked_by_gate(flat_series, btc_uptrend):
    sc = score_token(_gem_token(), flat_series, btc_uptrend)
    assert sc.rejection == "trend_gate"
    assert sc.score == 0.0


def test_score_no_ohlcv(btc_uptrend):
    sc = score_token(_gem_token(), pd.DataFrame(), btc_uptrend)
    assert sc.rejection == "no_ohlcv"


def test_parabolic_rsi_factor_damped(parabolic_blow_off, btc_uptrend):
    sc = score_token(_gem_token(), parabolic_blow_off, btc_uptrend)
    if sc.rejection is None:
        # On a vertical 4× over 10 days RSI should be saturated → factor near 0.
        assert sc.factors["rsi"] < 0.3


def test_deep_drawdown_punishes_ath_factor(deep_drawdown, btc_uptrend):
    sc = score_token(_gem_token(), deep_drawdown, btc_uptrend)
    # Trend gate may still pass during the up-leg's tail, but ATH proximity
    # must be 0 because we're 50% under the high.
    if sc.rejection is None:
        assert sc.factors["ath_proximity"] == 0.0
    else:
        # Or the trend gate killed it at the bottom — also acceptable.
        assert sc.rejection == "trend_gate"


def test_rank_threshold_no_topn(clean_uptrend, flat_series, btc_uptrend):
    a = score_token(_gem_token(symbol="A"), clean_uptrend, btc_uptrend)
    b = score_token(_gem_token(symbol="B"), flat_series, btc_uptrend)
    ranked = rank_candidates([a, b], threshold=0.50)
    assert len(ranked) == 1
    assert ranked[0].token.symbol == "A"


def test_rank_topn_optional(clean_uptrend, btc_uptrend):
    a = score_token(_gem_token(symbol="A"), clean_uptrend, btc_uptrend)
    b = score_token(_gem_token(symbol="B"), clean_uptrend, btc_uptrend)
    full = rank_candidates([a, b], threshold=0.0)
    capped = rank_candidates([a, b], threshold=0.0, top_n=1)
    assert len(full) == 2
    assert len(capped) == 1


def test_rs_btc_factor_zero_when_btc_outperforms(clean_uptrend):
    """If BTC's 30d perf exceeds the token's, rs_btc factor must be 0."""
    import numpy as np
    btc_super = clean_uptrend.copy()
    closes = list(np.linspace(60_000.0, 200_000.0, len(btc_super)))
    btc_super["close"] = pd.Series(closes, index=btc_super.index)
    sc = score_token(_gem_token(), clean_uptrend, btc_super)
    assert sc.factors["rs_btc"] == 0.0
