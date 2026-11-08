from datetime import UTC, datetime

import pytest

from cadence.core import schedule
from cadence.core.enums import Kind


def ts(*parts: int) -> float:
    return datetime(*parts, tzinfo=UTC).timestamp()


def test_duration_units_and_bare_minutes() -> None:
    assert schedule.parse_duration("30m") == 1800
    assert schedule.parse_duration("2h") == 7200
    assert schedule.parse_duration("1h30m") == 5400
    assert schedule.parse_duration("10") == 600
    with pytest.raises(ValueError):
        schedule.parse_duration("nope")


def test_parse_when_in_every_and_clock() -> None:
    now = ts(2026, 1, 1, 12, 0)
    assert schedule.parse_when("in 10m", now, "UTC") == (Kind.AT, now + 600, None)
    kind, when, interval = schedule.parse_when("every 1h", now, "UTC")
    assert kind is Kind.EVERY and interval == 3600 and when == now + 3600
    kind, when, _ = schedule.parse_when("15:30", now, "UTC")
    assert kind is Kind.AT and when == ts(2026, 1, 1, 15, 30)
    kind, when, _ = schedule.parse_when("11:30", now, "UTC")
    assert kind is Kind.AT and when == ts(2026, 1, 2, 11, 30)


def test_quiet_window_crosses_midnight() -> None:
    assert schedule.quiet_until(ts(2026, 1, 1, 12, 0), "23:00-07:00", "UTC") is None
    assert schedule.quiet_until(ts(2026, 1, 1, 23, 30), "23:00-07:00", "UTC") == ts(2026, 1, 2, 7, 0)
    assert schedule.quiet_until(ts(2026, 1, 1, 6, 0), "23:00-07:00", "UTC") == ts(2026, 1, 1, 7, 0)
    assert schedule.quiet_until(ts(2026, 1, 1, 12, 0), "", "UTC") is None


def test_next_day_start() -> None:
    assert schedule.next_day_start(ts(2026, 1, 1, 23, 59), "UTC") == ts(2026, 1, 2, 0, 0)


def test_utc_resolves_without_tzdata() -> None:
    assert schedule.tzinfo("UTC") is UTC
    assert schedule.tzinfo("Etc/UTC") is UTC


def test_parse_daily_times_normalizes_sorts_and_dedupes() -> None:
    assert schedule.parse_daily_times("13:00, 9:05,13:00") == ("09:05", "13:00")
    with pytest.raises(ValueError):
        schedule.parse_daily_times("")
    with pytest.raises(ValueError):
        schedule.parse_daily_times("24:00")
    with pytest.raises(ValueError):
        schedule.parse_daily_times("9:5")


def test_next_daily_picks_the_next_clock_time() -> None:
    now = ts(2026, 1, 1, 10, 0)
    assert schedule.next_daily(now, ("09:00", "13:00"), "UTC") == ts(2026, 1, 1, 13, 0)
    later = ts(2026, 1, 1, 14, 0)
    assert schedule.next_daily(later, ("09:00", "13:00"), "UTC") == ts(2026, 1, 2, 9, 0)
    exactly = ts(2026, 1, 1, 9, 0)
    assert schedule.next_daily(exactly, ("09:00",), "UTC") == ts(2026, 1, 2, 9, 0)
