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

    @classmethod
    def success(cls, *, beat: Beat | None = None, beats: Iterable[Beat] | None = None) -> Result:
        return cls(
            ok=True,
            beat=BeatView.of(beat) if beat is not None else None,
            beats=tuple(BeatView.of(item) for item in beats) if beats is not None else None,
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
        return data
