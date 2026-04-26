"""Lightweight per-run scan metrics so the daemon log shows what happened."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Self


@dataclass
class ScanStats:
    started_at: float = field(default_factory=perf_counter)
    finished_at: float | None = None

    universe_raw: int = 0
    universe_deduped: int = 0
    universe_passed: int = 0
    candidates_scored: int = 0
    candidates_qualified: int = 0
    alerts_sent: int = 0

    rejected_mcap: int = 0
    rejected_age: int = 0
    rejected_liquidity: int = 0
    rejected_honeypot: int = 0
    rejected_wash: int = 0
    rejected_missing: int = 0

    source_errors: dict[str, int] = field(default_factory=dict)
    ohlcv_misses: int = 0
    watchlist_size: int = 0

    accumulation_scored: int = 0
    accumulation_qualified: int = 0
    accumulation_alerts: int = 0

    def finish(self) -> Self:
        self.finished_at = perf_counter()
        return self

    @property
    def duration_s(self) -> float:
        return (self.finished_at or perf_counter()) - self.started_at

    def summary_line(self) -> str:
        return (
            f"scan done in {self.duration_s:.1f}s | "
            f"raw={self.universe_raw} deduped={self.universe_deduped} "
            f"passed={self.universe_passed} scored={self.candidates_scored} "
            f"qualified={self.candidates_qualified} alerts={self.alerts_sent} | "
            f"acc scored={self.accumulation_scored} qual={self.accumulation_qualified} "
            f"alerts={self.accumulation_alerts} | "
            f"rej mcap={self.rejected_mcap} age={self.rejected_age} "
            f"liq={self.rejected_liquidity} honey={self.rejected_honeypot} "
            f"wash={self.rejected_wash} missing={self.rejected_missing} | "
            f"ohlcv_miss={self.ohlcv_misses} watchlist={self.watchlist_size} | "
            f"errors={dict(self.source_errors)}"
        )

    def bump_error(self, source: str) -> None:
        self.source_errors[source] = self.source_errors.get(source, 0) + 1
