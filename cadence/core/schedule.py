"""Time parsing and local-time policy helpers."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from datetime import tzinfo as TzInfo
from functools import lru_cache
from zoneinfo import ZoneInfo

from .enums import Kind

LOG = logging.getLogger("cadence")

DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(w|d|h|m|s)", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
UTC_ALIASES = frozenset({"UTC", "GMT", "ETC/UTC", "ETC/GMT", "Z"})


@lru_cache(maxsize=32)
def tzinfo(name: str = "") -> TzInfo:
    """Resolve an IANA zone; UTC works even on Windows without ``tzdata``."""
    raw = name.strip()
    if raw:
        if raw.upper() in UTC_ALIASES:
            return UTC
        try:
            return ZoneInfo(raw)
        except Exception:
            LOG.warning("cadence: unknown time zone %r; using the local zone", raw)
    return datetime.now().astimezone().tzinfo or UTC


def parse_duration(text: object) -> float:
    """Parse ``30m``, ``2h``, ``1d`` or a bare number of minutes."""
    raw = str(text or "").strip().lower()
    if not raw:
        raise ValueError("duration is required")
    parts = [(match.group(1), match.group(2)) for match in DURATION_RE.finditer(raw)]
    if parts and re.sub(DURATION_RE, "", raw).strip() in {"", "+", "and", ","}:
        seconds = sum(float(value) * UNIT_SECONDS[unit.lower()] for value, unit in parts)
    elif raw.replace(".", "", 1).isdigit():
        seconds = float(raw) * 60
    else:
        raise ValueError(f"invalid duration: {text!r}")
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds


def parse_when(text: str, now: float, tz_name: str = "") -> tuple[Kind, float, float | None]:
    """Return ``(kind, next_at, interval_s)`` for an ``at``/``every`` expression."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("time is required")
    lower = raw.lower()
    tz = tzinfo(tz_name)

    if lower.startswith("every "):
        interval = parse_duration(lower[6:])
        return Kind.EVERY, now + interval, interval
    if lower.startswith("in "):
        return Kind.AT, now + parse_duration(lower[3:]), None
    if lower.startswith("at "):
        raw = raw[3:].strip()
        lower = raw.lower()

    clock = CLOCK_RE.match(raw)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("clock time must be HH:MM")
        local = datetime.fromtimestamp(now, tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local.timestamp() <= now:
            local += timedelta(days=1)
        return Kind.AT, local.timestamp(), None

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("use 'in 10m', 'every 1h', '15:30' or an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return Kind.AT, parsed.timestamp(), None


def parse_until(text: object | None, now: float, tz_name: str = "") -> float | None:
    if not text:
        return None
    raw = str(text).strip()
    lower = raw.lower()
    if lower.startswith("in "):
        return now + parse_duration(lower[3:])
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return now + parse_duration(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tzinfo(tz_name))
    return parsed.timestamp()


def parse_daily_times(text: object) -> tuple[str, ...]:
    """Parse ``09:00,13:00`` into sorted, deduplicated local clock times."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("daily requires at least one time")
    parts = [part for part in re.split(r"[,\s]+", raw) if part]
    times: list[str] = []
    for part in parts:
        match = CLOCK_RE.match(part)
        if not match:
            raise ValueError(f"invalid daily time: {part!r} (use HH:MM)")
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("daily time must be HH:MM")
        value = f"{hour:02d}:{minute:02d}"
        if value not in times:
            times.append(value)
    if not times:
        raise ValueError("daily requires at least one time")
    return tuple(sorted(times))


def next_daily(now: float, times: tuple[str, ...], tz_name: str = "") -> float:
    """Next local wall-clock occurrence from ``times``, strictly after ``now``."""
    if not times:
        return now + 86400
    tz = tzinfo(tz_name)
    local = datetime.fromtimestamp(now, tz)
    best: float | None = None
    for value in times:
        hour, minute = (int(part) for part in value.split(":", 1))
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        timestamp = candidate.timestamp()
        if timestamp <= now:
            timestamp = (candidate + timedelta(days=1)).timestamp()
        if best is None or timestamp < best:
            best = timestamp
    return best if best is not None else now + 86400


def local_day(now: float, tz_name: str = "") -> str:
    return datetime.fromtimestamp(now, tzinfo(tz_name)).date().isoformat()


def next_day_start(now: float, tz_name: str = "") -> float:
    tz = tzinfo(tz_name)
    today = datetime.fromtimestamp(now, tz)
    tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def quiet_until(now: float, quiet_hours: str, tz_name: str = "") -> float | None:
    """End of the current quiet window, or ``None`` when not quiet."""
    raw = str(quiet_hours or "").strip()
    if not raw:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$", raw)
    if not match:
        return None
    sh, sm, eh, em = (int(value) for value in match.groups())
    if sh > 23 or eh > 23 or sm > 59 or em > 59:
        return None
    if (sh, sm) == (eh, em):
        return None
    tz = tzinfo(tz_name)
    local = datetime.fromtimestamp(now, tz)
    start = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start < end:
        return end.timestamp() if start <= local < end else None
    if local >= start:
        return (end + timedelta(days=1)).timestamp()
    if local < end:
        return end.timestamp()
    return None


def format_ts(value: float | None, tz_name: str = "") -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(float(value), tzinfo(tz_name)).strftime("%Y-%m-%d %H:%M")
