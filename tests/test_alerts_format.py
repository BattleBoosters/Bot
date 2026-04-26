from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.alerts import _split_for_telegram, format_digest
from scanner.scoring import ScoredCandidate
from scanner.sources.base import Token


def _candidate() -> ScoredCandidate:
    tok = Token(
        symbol="GEM",
        name="Gem",
        chain="solana",
        address="0xabc",
        mcap_usd=18_000_000,
        vol_24h_usd=3_200_000,
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=47),
        source="geckoterminal",
        chart_url="https://example.com/gem",
    )
    return ScoredCandidate(
        token=tok,
        score=0.84,
        factors={"acceleration": 0.54},
        metrics={
            "perf_3d": 0.28,
            "perf_7d": 0.52,
            "perf_14d": 1.18,
            "rsi_14": 68.0,
            "btc_perf_7d": 0.05,
            "up_days_7": 6,
        },
    )


def test_format_digest_contains_essentials():
    out = format_digest([_candidate()], universe_size=1247, candidates_total=38, top_n=15)
    assert "GEM" in out
    assert "score 0.84" in out
    assert "mcap $18.00M" in out
    assert "vol24h $3.20M" in out
    assert "vol/mcap" in out
    assert "RSI 68" in out
    assert "up 6/7" in out
    assert "https://example.com/gem" in out


def test_format_digest_empty():
    out = format_digest([], universe_size=200, candidates_total=0, top_n=15)
    assert "Aucun candidat" in out


def test_split_for_telegram():
    text = "abc\n" * 2000
    chunks = _split_for_telegram(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
