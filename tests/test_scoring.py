from __future__ import annotations

from datetime import datetime, timezone

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
    tok = _gem_token()
    sc = score_token(tok, clean_uptrend, btc_uptrend)
    assert sc.rejection is None, sc.rejection
    assert sc.score > 0.50, f"expected solid score, got {sc.score} factors={sc.factors}"
    # Sanity: every factor in [0, 1]
    for v in sc.factors.values():
        assert 0.0 <= v <= 1.0
    # Strong factors that should fire on a clean +40% 7-day pump with rising volume
    assert sc.factors["perf_7d"] > 0.5
    assert sc.factors["regularity"] > 0.5
    assert sc.factors["volume"] > 0.5


def test_score_flat_blocked_by_gate(flat_series, btc_uptrend):
    tok = _gem_token()
    sc = score_token(tok, flat_series, btc_uptrend)
    assert sc.rejection == "trend_gate"
    assert sc.score == 0.0


def test_score_no_ohlcv(btc_uptrend):
    tok = _gem_token()
    import pandas as pd
    sc = score_token(tok, pd.DataFrame(), btc_uptrend)
    assert sc.rejection == "no_ohlcv"


def test_parabolic_penalised_by_rsi(parabolic_blow_off, btc_uptrend):
    tok = _gem_token()
    sc = score_token(tok, parabolic_blow_off, btc_uptrend)
    if sc.rejection is None:
        # RSI factor should be heavily damped on a vertical move.
        assert sc.factors["rsi"] < 0.5


def test_rank_filters_threshold_and_topn(clean_uptrend, btc_uptrend, flat_series):
    a = score_token(_gem_token(symbol="A"), clean_uptrend, btc_uptrend)
    b = score_token(_gem_token(symbol="B"), flat_series, btc_uptrend)
    ranked = rank_candidates([a, b], threshold=0.50, top_n=10)
    assert len(ranked) == 1
    assert ranked[0].token.symbol == "A"


def test_rs_btc_factor_zero_when_btc_outperforms(clean_uptrend):
    """If BTC's 7d perf exceeds the token's, rs_btc factor must be 0."""
    tok = _gem_token()
    import numpy as np
    import pandas as pd

    # Build a BTC series where the last 7 days deliver +100% — far above the
    # ~+40% the clean_uptrend gem fixture produces.
    closes = list(np.linspace(60_000.0, 66_000.0, 23)) + list(
        np.linspace(66_000.0, 132_000.0, 8)
    )[1:]
    btc_super = clean_uptrend.copy()
    btc_super["close"] = pd.Series(closes, index=btc_super.index)
    sc = score_token(tok, clean_uptrend, btc_super)
    assert sc.factors["rs_btc"] == 0.0
