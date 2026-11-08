"""Immutable records shared by the cadence store, scheduler and tool."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import Kind, Scope, Status

MARKER = "\u27e6cadence:"
SILENT = "[SILENT]"
TERMINAL = frozenset({Status.DONE, Status.CANCELLED, Status.EXPIRED, Status.FAILED, Status.MISSED})
ACTIVE = frozenset({Status.ACTIVE, Status.PAUSED})


@dataclass(frozen=True)
class Beat:
    id: str
    session_key: str
    note: str
    kind: Kind
    next_at: float | None
    interval_s: float | None
    status: Status
    created_at: float
    updated_at: float
    last_fired_at: float | None = None
    fires: int = 0
    max_fires: int | None = None
    expires_at: float | None = None
    last_error: str = ""
    error_count: int = 0
    version: int = 1
    scope: Scope = Scope.CHAT
    urgent: bool = False
    anchor_session_id: str | None = None
    noop_streak: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True, slots=True)
class BeatView:
    """The subset of a beat the model sees in tool output."""

    id: str
    note: str
    kind: Kind
    scope: Scope
    urgent: bool
    status: Status
    next_at: float | None
    interval_s: float | None
    fires: int
    max_fires: int | None
    expires_at: float | None
    last_error: str

    @classmethod
    def of(cls, beat: Beat) -> BeatView:
        return cls(
            id=beat.id,
            note=beat.note,
            kind=beat.kind,
            scope=beat.scope,
            urgent=beat.urgent,
            status=beat.status,
            next_at=beat.next_at,
            interval_s=beat.interval_s,
            fires=beat.fires,
            max_fires=beat.max_fires,
            expires_at=beat.expires_at,
            last_error=beat.last_error,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "note": self.note,
            "kind": self.kind.value,
            "scope": self.scope.value,
            "urgent": self.urgent,
            "status": self.status.value,
            "next_at": self.next_at,
            "interval_s": self.interval_s,
            "fires": self.fires,
            "max_fires": self.max_fires,
            "expires_at": self.expires_at,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class Session:
    session_key: str
    current_session_id: str = ""
    platform: str = ""
    chat_id: str = ""
    last_human_at: float | None = None
    human_session_id: str = ""
    last_beat_at: float | None = None
    unanswered_count: int = 0
