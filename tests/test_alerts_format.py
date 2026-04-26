from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.alerts import (
    CSV_COLUMNS,
    _split_for_telegram,
    build_csv,
    format_card,
    format_digest,
)
from scanner.scoring import ScoredCandidate
from scanner.sources.base import Token


def _candidate(symbol: str = "GEM", score: float = 0.84) -> ScoredCandidate:
    tok = Token(
        symbol=symbol,
        name=symbol.title(),
        chain="solana",
        address="0xabc",
        mcap_usd=18_000_000,
        vol_24h_usd=3_200_000,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=120),
        source="geckoterminal",
        chart_url=f"https://example.com/{symbol.lower()}",
        extra={"unique_trader_ratio": 0.45},
    )
    return ScoredCandidate(
        token=tok,
        score=score,
        factors={
            "slope": 0.85,
            "ath_proximity": 0.95,
            "perf_consist": 0.80,
            "volume": 0.70,
            "rs_btc": 0.55,
            "rsi": 0.90,
        },
        metrics={
            "perf_7d": 0.12,
            "perf_14d": 0.28,
            "perf_30d": 0.55,
            "perf_60d": 0.95,
            "perf_90d": 1.40,
            "rsi_14": 68.0,
            "btc_perf_30d": 0.04,
            "log_slope_per_day": 0.011,
            "annualised_growth": 4.20,
            "drawdown_from_ath": -0.03,
            "weeks_up_12": 0.83,
            "history_days": 120,
        },
    )


def test_format_digest_contains_essentials():
    out = format_digest(
        [_candidate()],
        universe_size=1247,
        candidates_total=38,
        highlight_top_n=10,
    )
    assert "GEM" in out
    assert "score 0.84" in out
    assert "mcap $18.00M" in out
    assert "vol24h $3.20M" in out
    assert "30j" in out and "60j" in out and "90j" in out
    assert "slope" in out
    assert "ATH" in out
    assert "RSI 68" in out
    assert "https://example.com/gem" in out
    assert "CSV joint" in out


def test_format_digest_empty():
    out = format_digest(
        [],
        universe_size=200,
        candidates_total=0,
        highlight_top_n=10,
    )
    assert "Aucun candidat" in out


def test_format_digest_caps_to_highlight_top_n():
    cands = [_candidate(symbol=f"G{i}", score=0.9 - i * 0.01) for i in range(20)]
    out = format_digest(cands, universe_size=1000, candidates_total=20, highlight_top_n=5)
    # Only top 5 numeric prefixes appear.
    assert "#5" in out
    assert "#6" not in out


def test_format_card_includes_long_term_metrics():
    card = format_card(_candidate())
    assert "GEM" in card
    assert "score 0.84" in card
    assert "slope" in card
    assert "ATH" in card
    assert "weeks↑" in card


def test_build_csv_roundtrip():
    cands = [_candidate(symbol="A", score=0.81), _candidate(symbol="B", score=0.62)]
    blob = build_csv(cands)
    text = blob.decode("utf-8")
    lines = text.strip().splitlines()
    assert len(lines) == 3  # header + 2 rows
    header = lines[0].split(",")
    assert header == CSV_COLUMNS
    # Score column populated and ordered by caller (we pass in sorted order)
    assert "0.81" in lines[1]
    assert "0.62" in lines[2]
    # Sanity: includes some required telemetry columns
    for col in (
        "annualised_growth", "drawdown_from_ath", "weeks_up_12",
        "factor_slope", "wash_warning", "honeypot",
    ):
        assert col in header


def test_split_for_telegram():
    text = "abc\n" * 2000
    chunks = _split_for_telegram(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
