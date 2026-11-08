"""Settings and deterministic policy for cadence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar

from ..protocols import PluginContext
from . import schedule
from .enums import NoopBackoff, UnansweredPolicy

E = TypeVar("E", bound=StrEnum)


def _int(value: object, default: int) -> int:
    if isinstance(value, (bool, int, float, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _choice(value: object, choices: type[E], default: E) -> E:
    try:
        return choices(str(value or "").strip().lower())
    except ValueError:
        return default


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


@dataclass(frozen=True)
class Policy:
    enabled: bool = True
    timezone: str = ""
    quiet_hours: str = "22:00-08:00"
    max_messages_per_day: int = 12
    max_fires_per_day: int = 24
    spacing_minutes: int = 15
    min_interval_minutes: int = 15
    catch_up_minutes: int = 180
    retry_seconds: int = 300
    poll_seconds: int = 15
    noop_backoff: NoopBackoff = NoopBackoff.GENTLE
    noop_after: int = 3
    noop_max_hours: int = 24
    noop_pause_after: int = 6
    unanswered_policy: UnansweredPolicy = UnansweredPolicy.SMART
    unanswered_after: int = 2

    @classmethod
    def from_ctx(cls, ctx: PluginContext) -> Policy:
        defaults = cls()
        get = ctx.get_config
        try:
            return cls(
                enabled=_bool(get("enabled", defaults.enabled), defaults.enabled),
                timezone=str(get("timezone", defaults.timezone) or ""),
                quiet_hours=str(get("quiet_hours", defaults.quiet_hours) or ""),
                max_messages_per_day=max(
                    0,
                    _int(
                        get("max_messages_per_day", defaults.max_messages_per_day),
                        defaults.max_messages_per_day,
                    ),
                ),
                max_fires_per_day=max(
                    0, _int(get("max_fires_per_day", defaults.max_fires_per_day), defaults.max_fires_per_day)
                ),
                spacing_minutes=max(
                    0, _int(get("spacing_minutes", defaults.spacing_minutes), defaults.spacing_minutes)
                ),
                min_interval_minutes=max(
                    0,
                    _int(
                        get("min_interval_minutes", defaults.min_interval_minutes),
                        defaults.min_interval_minutes,
                    ),
                ),
                catch_up_minutes=max(
                    0, _int(get("catch_up_minutes", defaults.catch_up_minutes), defaults.catch_up_minutes)
                ),
                retry_seconds=max(
                    1, _int(get("retry_seconds", defaults.retry_seconds), defaults.retry_seconds)
                ),
                poll_seconds=max(1, _int(get("poll_seconds", defaults.poll_seconds), defaults.poll_seconds)),
                noop_backoff=_choice(
                    get("noop_backoff", defaults.noop_backoff), NoopBackoff, defaults.noop_backoff
                ),
                noop_after=max(1, _int(get("noop_after", defaults.noop_after), defaults.noop_after)),
                noop_max_hours=max(
                    1, _int(get("noop_max_hours", defaults.noop_max_hours), defaults.noop_max_hours)
                ),
                noop_pause_after=max(
                    0, _int(get("noop_pause_after", defaults.noop_pause_after), defaults.noop_pause_after)
                ),
                unanswered_policy=_choice(
                    get("unanswered_policy", defaults.unanswered_policy),
                    UnansweredPolicy,
                    defaults.unanswered_policy,
                ),
                unanswered_after=max(
                    1, _int(get("unanswered_after", defaults.unanswered_after), defaults.unanswered_after)
                ),
            )
        except Exception:
            return defaults

    @property
    def spacing_s(self) -> float:
        return float(self.spacing_minutes) * 60

    @property
    def min_interval_s(self) -> float:
        return float(self.min_interval_minutes) * 60

    @property
    def catch_up_s(self) -> float:
        return float(self.catch_up_minutes) * 60

    @property
    def retry_s(self) -> float:
        return float(self.retry_seconds)

    @property
    def poll_s(self) -> float:
        return float(self.poll_seconds)

    @property
    def noop_active(self) -> bool:
        return self.noop_backoff in {NoopBackoff.GENTLE, NoopBackoff.ASSERTIVE}

    @property
    def effective_noop_after(self) -> int:
        return 1 if self.noop_backoff is NoopBackoff.ASSERTIVE else self.noop_after

    def quiet_until(self, now: float) -> float | None:
        return schedule.quiet_until(now, self.quiet_hours, self.timezone)

    def day(self, now: float) -> str:
        return schedule.local_day(now, self.timezone)

    def next_day(self, now: float) -> float:
        return schedule.next_day_start(now, self.timezone)


class PolicyProvider(Protocol):
    def __call__(self) -> Policy: ...
