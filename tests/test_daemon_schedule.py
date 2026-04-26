from __future__ import annotations

from datetime import datetime, timezone

from scanner.main import _next_full_scan, _next_watchlist_scan


def test_next_full_scan_picks_today_if_future():
    # 03:00 UTC, hours [0, 6, 12, 18] @ :30 → next is today 06:30
    now = datetime(2026, 4, 26, 3, 0, tzinfo=timezone.utc)
    nxt = _next_full_scan(now, [0, 6, 12, 18], 30)
    assert nxt == datetime(2026, 4, 26, 6, 30, tzinfo=timezone.utc)


def test_next_full_scan_rolls_to_next_day():
    # 23:00 UTC → next is tomorrow 00:30
    now = datetime(2026, 4, 26, 23, 0, tzinfo=timezone.utc)
    nxt = _next_full_scan(now, [0, 6, 12, 18], 30)
    assert nxt == datetime(2026, 4, 27, 0, 30, tzinfo=timezone.utc)


def test_next_full_scan_skips_just_passed():
    # Exactly at scheduled minute → the next slot in the future.
    now = datetime(2026, 4, 26, 6, 30, tzinfo=timezone.utc)
    nxt = _next_full_scan(now, [0, 6, 12, 18], 30)
    assert nxt == datetime(2026, 4, 26, 12, 30, tzinfo=timezone.utc)


def test_next_watchlist_scan_simple():
    now = datetime(2026, 4, 26, 6, 17, 42, tzinfo=timezone.utc)
    nxt = _next_watchlist_scan(now, 60)
    assert nxt == datetime(2026, 4, 26, 7, 17, tzinfo=timezone.utc)
