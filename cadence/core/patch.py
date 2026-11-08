"""Field-by-field mutation record for a beat.

``UNSET`` distinguishes "not provided" from "set to None", which the store
needs because clearing ``next_at``/``expires_at``/``max_fires`` is meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import cast

from .enums import Kind, Status


class _Unset(Enum):
    TOKEN = "unset"


Unset = _Unset
UNSET = _Unset.TOKEN


@dataclass(frozen=True, slots=True)
class BeatPatch:
    note: str | Unset = UNSET
    kind: Kind | Unset = UNSET
    next_at: float | Unset | None = UNSET
    interval_s: float | Unset | None = UNSET
    daily_times: tuple[str, ...] | Unset = UNSET
    status: Status | Unset = UNSET
    urgent: bool | Unset = UNSET
    max_fires: int | Unset | None = UNSET
    expires_at: float | Unset | None = UNSET
    last_error: str | Unset = UNSET
    noop_streak: int | Unset = UNSET

    def items(self) -> dict[str, object]:
        """Return only the fields that were explicitly provided."""
        result: dict[str, object] = {}
        for field in fields(self):
            value = cast(object, getattr(self, field.name))
            if value is not UNSET:
                result[field.name] = value
        return result
