"""Tool result values and their agent-facing JSON projection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .enums import ErrorCode
from .model import Beat, BeatView


@dataclass(frozen=True, slots=True)
class Result:
    ok: bool
    error: ErrorCode | None = None
    detail: str = ""
    beat: BeatView | None = None
    beats: tuple[BeatView, ...] | None = None
    minimum_seconds: float | None = None
    quiet_hours_until: float | None = None
    spacing_until: float | None = None
    cap_until: float | None = None
    effective_next_at: float | None = None
    warning: str = ""

    @classmethod
    def success(
        cls,
        *,
        beat: Beat | None = None,
        beats: Iterable[Beat] | None = None,
        quiet_hours_until: float | None = None,
        spacing_until: float | None = None,
        cap_until: float | None = None,
        effective_next_at: float | None = None,
        warning: str = "",
    ) -> Result:
        return cls(
            ok=True,
            beat=BeatView.of(beat) if beat is not None else None,
            beats=tuple(BeatView.of(item) for item in beats) if beats is not None else None,
            quiet_hours_until=quiet_hours_until,
            spacing_until=spacing_until,
            cap_until=cap_until,
            effective_next_at=effective_next_at,
            warning=warning,
        )

    @classmethod
    def failure(cls, error: ErrorCode, detail: str = "", minimum_seconds: float | None = None) -> Result:
        return cls(ok=False, error=error, detail=detail, minimum_seconds=minimum_seconds)

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"ok": self.ok}
        if self.error is not None:
            data["error"] = self.error.value
        if self.detail:
            data["detail"] = self.detail
        if self.minimum_seconds is not None:
            data["minimum_seconds"] = self.minimum_seconds
        if self.beat is not None:
            data["beat"] = self.beat.to_dict()
        if self.beats is not None:
            data["beats"] = [beat.to_dict() for beat in self.beats]
        if self.quiet_hours_until is not None:
            data["quiet_hours_until"] = self.quiet_hours_until
        if self.spacing_until is not None:
            data["spacing_until"] = self.spacing_until
        if self.cap_until is not None:
            data["cap_until"] = self.cap_until
        if self.effective_next_at is not None:
            data["effective_next_at"] = self.effective_next_at
        if self.warning:
            data["warning"] = self.warning
        return data
