"""Agent tool and chat command for cadence."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from .core import schedule
from .core.enums import (
    DEFERRAL_REASONS,
    PAUSE_REASONS,
    Action,
    ErrorCode,
    Kind,
    Scope,
    Status,
    UrgentGate,
)
from .core.model import Beat, BeatView, Session
from .core.patch import BeatPatch
from .core.policy import Policy, PolicyProvider
from .core.results import Result
from .core.store import Store
from .protocols import Clock

SessionEnv = Callable[[str], str]

SCHEMA = {
    "name": "cadence",
    "description": (
        "Create, list, update, pause, resume, cancel or delete a persistent cadence heartbeat. "
        "A beat fires an agent turn in this same chat at a time or on a recurring interval. "
        "On a beat turn, finish with action=respond; set silent=true to suppress the message. "
        "Use it for reminders, follow-ups and periodic checks that need this conversation's context. "
        "For independent scheduled jobs use Hermes cron instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [action.value for action in Action],
            },
            "id": {"type": "string", "description": "Beat id from list or get."},
            "note": {
                "type": "string",
                "minLength": 1,
                "description": "What the agent should do when the beat fires.",
            },
            "at": {
                "type": "string",
                "description": "One-shot: 'in 10m', '15:30' or an ISO timestamp.",
            },
            "every": {
                "type": "string",
                "description": "Recurring interval: '1h', '30m', '1d'.",
            },
            "daily": {
                "type": "string",
                "description": "Recurring local clock times, comma-separated: '09:00,13:00,17:00'. One beat fires at each time daily.",
            },
            "until": {
                "type": "string",
                "description": "Optional expiry: 'in 2h', '2h' or an ISO timestamp.",
            },
            "max_fires": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional maximum fires.",
            },
            "scope": {
                "type": "string",
                "enum": [scope.value for scope in Scope],
                "description": "chat (default) survives /new and follows the chat; session stays anchored to this session.",
            },
            "silent": {
                "type": "boolean",
                "description": "For action=respond: true suppresses the final message.",
            },
            "urgent": {
                "type": "boolean",
                "description": "Bypass the gates listed in urgent_bypass.",
            },
        },
        "required": ["action"],
    },
}

HELP = """Cadence:
/cadence list
/cadence add in 10m <note>
/cadence add urgent in 10m <note>
/cadence add session in 10m <note>     (anchored to this session)
/cadence add every 15m <note>
/cadence add every 15m until 2h <note>
/cadence add every 15m max 12 <note>
/cadence add at 16:30 <note>
/cadence add daily at 09:00,13:00 <note>
/cadence show <id>
/cadence pause|resume|cancel|delete <id>
/cadence update <id> note <note>
/cadence update <id> in 20m <note>
/cadence update <id> until 2h
/cadence update <id> max 12
/cadence update <id> daily at 09:00,13:00
/cadence update <id> urgent true|false

A beat turn finishes with the cadence tool's respond action; silent=true suppresses the message."""


def _str_arg(args: Mapping[str, object], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _take_limits(tokens: list[str], args: dict[str, object]) -> list[str]:
    """Consume leading ``until <when>`` / ``max[ _fires] <n>`` flags."""
    rest = list(tokens)
    while rest:
        token = rest[0].lower()
        if token == "until" and len(rest) >= 2:
            if rest[1].lower() == "in" and len(rest) >= 3:
                args["until"] = f"in {rest[2]}"
                rest = rest[3:]
            else:
                args["until"] = rest[1]
                rest = rest[2:]
            continue
        if token in {"max", "max_fires"} and len(rest) >= 2:
            args["max_fires"] = rest[1]
            rest = rest[2:]
            continue
        break
    return rest


@dataclass(frozen=True)
class _Schedule:
    kind: Kind
    next_at: float
    interval_s: float | None
    daily_times: tuple[str, ...] = ()


class _ToolError(Exception):
    def __init__(self, code: ErrorCode, detail: str = "", minimum_seconds: float | None = None) -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail
        self.minimum_seconds = minimum_seconds

    def result(self) -> Result:
        return Result.failure(self.code, self.detail, self.minimum_seconds)


def _parse_schedule(
    args: Mapping[str, object], now: float, policy: Policy, *, required: bool = True
) -> _Schedule | None:
    at, every, daily = _str_arg(args, "at"), _str_arg(args, "every"), _str_arg(args, "daily")
    if not at and not every and not daily:
        if required:
            raise _ToolError(ErrorCode.ONE_OF_AT_OR_EVERY, "use exactly one of at, every or daily")
        return None
    if sum(bool(value) for value in (at, every, daily)) != 1:
        raise _ToolError(ErrorCode.ONE_OF_AT_OR_EVERY, "use exactly one of at, every or daily")
    try:
        if every:
            interval = schedule.parse_duration(every)
            if policy.min_interval_s and interval < policy.min_interval_s:
                raise _ToolError(ErrorCode.INTERVAL_TOO_SHORT, minimum_seconds=policy.min_interval_s)
            return _Schedule(Kind.EVERY, now + interval, interval)
        if daily:
            times = schedule.parse_daily_times(daily)
            return _Schedule(Kind.DAILY, schedule.next_daily(now, times, policy.timezone), None, times)
        kind, next_at, interval_s = schedule.parse_when(at or "", now, policy.timezone)
        if kind is not Kind.AT:
            raise _ToolError(ErrorCode.USE_EVERY_FIELD)
        return _Schedule(kind, next_at, interval_s)
    except ValueError as exc:
        raise _ToolError(ErrorCode.INVALID_TIME, str(exc)) from exc


def _parse_max_fires(args: Mapping[str, object]) -> int | None:
    raw = args.get("max_fires")
    if raw is None:
        return None
    value = _as_int(raw)
    if value is None or value <= 0:
        raise _ToolError(ErrorCode.INVALID_MAX_FIRES)
    return value


def _parse_until(args: Mapping[str, object], now: float, policy: Policy) -> float | None:
    try:
        return schedule.parse_until(args.get("until"), now, policy.timezone)
    except ValueError as exc:
        raise _ToolError(ErrorCode.INVALID_UNTIL, str(exc)) from exc


def _join_reasons(reasons: list[str]) -> str:
    if len(reasons) == 1:
        return reasons[0]
    if len(reasons) == 2:
        return f"{reasons[0]} and {reasons[1]}"
    return ", ".join(reasons[:-1]) + f" and {reasons[-1]}"


def _schedule_hint(
    next_at: float | None,
    kind: Kind,
    urgent: bool,
    expires_at: float | None,
    policy: Policy,
    session: Session | None,
    *,
    now: float,
    messages: int,
    fires: int,
    has_human: bool,
) -> tuple[float | None, float | None, float | None, float | None, str]:
    """Return ``(quiet_until, spacing_until, cap_until, effective_next_at, warning)``."""
    if next_at is None:
        return None, None, None, None, ""

    urgent_quiet = urgent and UrgentGate.QUIET_HOURS in policy.urgent_bypass
    urgent_spacing = urgent and UrgentGate.SPACING in policy.urgent_bypass

    holds: list[tuple[float, str]] = []

    quiet_until: float | None = None
    if policy.quiet_hours and not urgent_quiet:
        quiet_hold = schedule.quiet_until(next_at, policy.quiet_hours, policy.timezone)
        if quiet_hold is not None:
            quiet_until = quiet_hold
            holds.append((quiet_hold, "quiet hours"))

    cap_until: float | None = None
    midnight = policy.next_day(now)
    if next_at < midnight:
        if policy.max_messages_per_day and messages >= policy.max_messages_per_day:
            cap_until = midnight
            holds.append((midnight, "daily message cap"))
        if policy.max_fires_per_day and fires >= policy.max_fires_per_day:
            cap_until = midnight
            holds.append((midnight, "daily fire cap"))

    spacing_until: float | None = None
    if (
        not urgent_spacing
        and policy.spacing_s > 0
        and session is not None
        and session.last_beat_at is not None
    ):
        base = max([next_at, *(hold for hold, _ in holds)])
        spacing_hold = session.last_beat_at + policy.spacing_s
        if spacing_hold > base:
            spacing_until = spacing_hold
            holds.append((spacing_hold, "spacing"))

    effective_next_at = max((hold for hold, _ in holds), default=None)
    already_expired = expires_at is not None and expires_at <= now
    expires_before_fire = expires_at is not None and not already_expired and expires_at <= next_at
    catch_up_missed = kind is Kind.AT and not already_expired and now - next_at > policy.catch_up_s

    warning_parts: list[str] = []
    if urgent and quiet_until is not None and UrgentGate.QUIET_HOURS not in policy.urgent_bypass:
        warning_parts.append("urgent=true but urgent_bypass does not include quiet_hours")
    if holds and effective_next_at is not None:
        when = schedule.format_ts(effective_next_at, policy.timezone)
        reasons = _join_reasons([label for _, label in holds])
        warning_parts.append(f"{reasons}: the beat will wait until {when}")
    if already_expired:
        warning_parts.append("it has already expired")
    elif expires_before_fire:
        warning_parts.append("it expires before it fires")
    elif effective_next_at is not None and expires_at is not None and expires_at <= effective_next_at:
        warning_parts.append("it expires before that")
    if catch_up_missed:
        warning_parts.append("it is outside the catch-up window and will be marked missed")
    if not has_human and not (already_expired or expires_before_fire or catch_up_missed):
        warning_parts.append("no human message in this session; it will wait")

    return quiet_until, spacing_until, cap_until, effective_next_at, "; ".join(warning_parts)


class Tool:
    def __init__(
        self, store: Store, clock: Clock, policy_provider: PolicyProvider, session_env: SessionEnv
    ) -> None:
        self.store = store
        self.clock = clock
        self.policy_provider = policy_provider
        self.session_env = session_env

    def _session_key(self) -> str:
        key = self.session_env("HERMES_SESSION_KEY") or ""
        if not key or key == "default":
            return ""
        self.store.touch_session(
            key,
            current_session_id=self.session_env("HERMES_SESSION_ID") or "",
            platform=self.session_env("HERMES_SESSION_PLATFORM") or "",
            chat_id=self.session_env("HERMES_SESSION_CHAT_ID") or "",
        )
        return key

    def _own(self, key: str, beat_id: str) -> Beat | None:
        beat = self.store.get_beat(beat_id)
        if beat is None or beat.session_key != key:
            return None
        return beat

    def _create(self, key: str, args: Mapping[str, object]) -> Result:
        note = (_str_arg(args, "note") or "").strip()
        if not note:
            return Result.failure(ErrorCode.NOTE_REQUIRED)
        scope_text = (_str_arg(args, "scope") or Scope.CHAT.value).strip().lower()
        try:
            scope = Scope(scope_text)
        except ValueError:
            return Result.failure(ErrorCode.INVALID_SCOPE)
        anchor: str | None = None
        if scope is Scope.SESSION:
            anchor = (self.session_env("HERMES_SESSION_ID") or "") or None
            if anchor is None:
                return Result.failure(ErrorCode.SESSION_UNKNOWN, "Session-scoped beats need a live session.")
        policy = self.policy_provider()
        now = self.clock.now()
        try:
            parsed = _parse_schedule(args, now, policy)
            if parsed is None:
                return Result.failure(ErrorCode.ONE_OF_AT_OR_EVERY)
            max_fires = _parse_max_fires(args)
            expires_at = _parse_until(args, now, policy)
        except _ToolError as exc:
            return exc.result()
        beat = self.store.create_beat(
            session_key=key,
            note=note,
            kind=parsed.kind,
            next_at=parsed.next_at,
            interval_s=parsed.interval_s,
            max_fires=max_fires,
            expires_at=expires_at,
            scope=scope,
            urgent=_as_bool(args.get("urgent")),
            daily_times=parsed.daily_times,
            anchor_session_id=anchor,
        )
        return self._result_for(beat, policy, now)

    def _update(self, beat: Beat, args: Mapping[str, object]) -> Result:
        if beat.terminal:
            return Result.failure(ErrorCode.ITEM_IS_TERMINAL)
        policy = self.policy_provider()
        now = self.clock.now()
        patch = BeatPatch()
        raw_note = _str_arg(args, "note")
        if raw_note is not None:
            note = raw_note.strip()
            if not note:
                return Result.failure(ErrorCode.NOTE_REQUIRED)
            patch = replace(patch, note=note)
        try:
            parsed = _parse_schedule(args, now, policy, required=False)
            max_fires = _parse_max_fires(args)
        except _ToolError as exc:
            return exc.result()
        if parsed is not None:
            patch = replace(
                patch,
                kind=parsed.kind,
                interval_s=parsed.interval_s,
                daily_times=parsed.daily_times,
                next_at=parsed.next_at,
            )
        if max_fires is not None:
            patch = replace(patch, max_fires=max_fires)
        if args.get("until") is not None:
            try:
                patch = replace(patch, expires_at=_parse_until(args, now, policy))
            except _ToolError as exc:
                return exc.result()
        if args.get("urgent") is not None:
            patch = replace(patch, urgent=_as_bool(args.get("urgent")))
        if not patch.items():
            return self._result_for(beat, policy, now)
        return self._result_for(self.store.update_beat(beat.id, patch), policy, now)

    def _result_for(self, beat: Beat, policy: Policy, now: float) -> Result:
        session = self.store.get_session(beat.session_key)
        messages, fires = self.store.ledger(policy.day(now), beat.session_key)
        has_human = self.store.has_human(beat.session_key)
        quiet, spacing, cap, effective, warning = _schedule_hint(
            beat.next_at,
            beat.kind,
            beat.urgent,
            beat.expires_at,
            policy,
            session,
            now=now,
            messages=messages,
            fires=fires,
            has_human=has_human,
        )
        return Result.success(
            beat=beat,
            quiet_hours_until=quiet,
            spacing_until=spacing,
            cap_until=cap,
            effective_next_at=effective,
            warning=warning,
        )

    def _format(self, beat: BeatView) -> str:
        policy = self.policy_provider()
        when = schedule.format_ts(beat.next_at, policy.timezone)
        text = f"{beat.id} [{beat.status.value}] {beat.kind.value}/{beat.scope.value}"
        if beat.urgent:
            text += " \u00b7 urgent"
        if beat.kind is Kind.DAILY and beat.daily_times:
            text += f" \u00b7 {','.join(beat.daily_times)}"
        if when:
            text += f" \u00b7 {when}"
        text += f" \u00b7 {beat.note}"
        if beat.last_error:
            if beat.last_error in DEFERRAL_REASONS:
                label = "deferred"
            elif beat.last_error in PAUSE_REASONS:
                label = "paused"
            else:
                label = "error"
            text += f" \u00b7 {label}: {beat.last_error}"
        return text

    def handle(self, args: Mapping[str, object], **kwargs: object) -> Result:
        key = self._session_key()
        if not key:
            return Result.failure(ErrorCode.NOT_IN_SESSION)
        raw_action = args.get("action")
        if not isinstance(raw_action, str):
            return Result.failure(ErrorCode.UNKNOWN_ACTION)
        try:
            action = Action(raw_action.strip().lower())
        except ValueError:
            return Result.failure(ErrorCode.UNKNOWN_ACTION)
        if action is Action.CREATE:
            return self._create(key, args)
        if action is Action.LIST:
            return Result.success(beats=self.store.list_beats(key))
        if action is Action.GET:
            beat = self._own(key, str(args.get("id") or ""))
            if beat is None:
                return Result.failure(ErrorCode.UNKNOWN_BEAT)
            return Result.success(beat=beat)
        if action in {Action.UPDATE, Action.PAUSE, Action.RESUME, Action.CANCEL, Action.DELETE}:
            beat = self._own(key, str(args.get("id") or ""))
            if beat is None:
                return Result.failure(ErrorCode.UNKNOWN_BEAT)
            if action is Action.DELETE:
                if not self.store.delete_beat(beat.id):
                    return Result.failure(ErrorCode.UNKNOWN_BEAT)
                return Result(ok=True)
            if action is Action.UPDATE:
                return self._update(beat, args)
            if action is Action.PAUSE:
                if beat.status is not Status.ACTIVE:
                    return Result.failure(ErrorCode.NOT_ACTIVE)
                return Result.success(beat=self.store.set_status(beat.id, Status.PAUSED))
            if action is Action.RESUME:
                if beat.status is not Status.PAUSED:
                    return Result.failure(ErrorCode.NOT_PAUSED)
                now = self.clock.now()
                next_at = beat.next_at if beat.next_at and beat.next_at > now else now
                return Result.success(
                    beat=self.store.update_beat(
                        beat.id,
                        BeatPatch(
                            status=Status.ACTIVE,
                            next_at=next_at,
                            last_error="",
                            noop_streak=0,
                        ),
                    )
                )
            if beat.terminal:
                return Result.failure(ErrorCode.ITEM_IS_TERMINAL)
            return Result.success(beat=self.store.set_status(beat.id, Status.CANCELLED, next_at=None))
        return Result.failure(ErrorCode.UNKNOWN_ACTION)

    def command(self, raw: str) -> str:
        try:
            parts = shlex.split(raw or "")
        except ValueError as exc:
            return f"Error: {exc}"
        if not parts or parts[0].lower() in {"help", "-h", "--help"}:
            return HELP
        aliases = {"add": Action.CREATE, "show": Action.GET}
        raw_action = parts[0].lower()
        action = aliases.get(raw_action)
        if action is None:
            try:
                action = Action(raw_action)
            except ValueError:
                return HELP
        args: dict[str, object] = {"action": action}
        try:
            if action is Action.LIST:
                pass
            elif action is Action.CREATE:
                rest = parts[1:]
                scope = Scope.CHAT
                if rest and rest[0].lower() in {item.value for item in Scope}:
                    scope = Scope(rest[0].lower())
                    rest = rest[1:]
                args["scope"] = scope
                if rest and rest[0].lower() == "urgent":
                    args["urgent"] = True
                    rest = rest[1:]
                if not rest:
                    return (
                        "Usage: /cadence add [session|chat] [urgent] "
                        "(in 10m|every 15m|at 16:30|daily at 09:00,13:00) "
                        "[until 2h] [max 12] <note>"
                    )
                if rest[0].lower() == "daily":
                    if len(rest) < 2:
                        return "Missing the daily times."
                    if rest[1].lower() == "at":
                        if len(rest) < 3:
                            return "Missing the daily times."
                        args["daily"] = rest[2]
                        rest = rest[3:]
                    else:
                        args["daily"] = rest[1]
                        rest = rest[2:]
                elif rest[0].lower() in {"in", "every", "at"}:
                    if len(rest) < 2:
                        return "Missing the beat time."
                    if rest[0].lower() == Kind.EVERY.value:
                        args["every"] = rest[1]
                    else:
                        args["at"] = f"{rest[0]} {rest[1]}"
                    rest = rest[2:]
                else:
                    args["at"] = rest[0]
                    rest = rest[1:]
                rest = _take_limits(rest, args)
                if not rest:
                    return "Missing the beat note."
                args["note"] = " ".join(rest)
            elif action is Action.GET or action in {
                Action.PAUSE,
                Action.RESUME,
                Action.CANCEL,
                Action.DELETE,
            }:
                args["id"] = parts[1] if len(parts) > 1 else ""
            elif action is Action.UPDATE:
                if len(parts) < 3:
                    return (
                        "Usage: /cadence update <id> note <note> | in 20m <note> | "
                        "daily at 09:00,13:00 <note> | until 2h | max 12 | urgent true|false"
                    )
                args["id"] = parts[1]
                rest = parts[2:]
                kind = rest[0].lower()
                if kind == "note":
                    note = " ".join(rest[1:]).strip()
                    if not note:
                        return "Missing the beat note."
                    args["note"] = note
                    rest = []
                elif kind in {"in", Kind.EVERY.value, Kind.AT.value}:
                    if len(rest) < 2:
                        return "Missing the beat time."
                    if kind == Kind.EVERY.value:
                        args["every"] = rest[1]
                    else:
                        args["at"] = f"{kind} {rest[1]}"
                    rest = rest[2:]
                elif kind == "daily":
                    if len(rest) < 2:
                        return "Missing the daily times."
                    if rest[1].lower() == "at":
                        if len(rest) < 3:
                            return "Missing the daily times."
                        args["daily"] = rest[2]
                        rest = rest[3:]
                    else:
                        args["daily"] = rest[1]
                        rest = rest[2:]
                elif kind == "urgent":
                    if len(rest) < 2 or rest[1].lower() not in {"true", "false"}:
                        return "Usage: /cadence update <id> urgent true|false"
                    args["urgent"] = rest[1].lower() == "true"
                    rest = rest[2:]
                elif kind not in {"until", "max", "max_fires"}:
                    args["at"] = rest[0]
                    rest = rest[1:]
                rest = _take_limits(rest, args)
                if rest:
                    args["note"] = " ".join(rest)
            else:
                return HELP
        except Exception as exc:
            return f"Error: {exc}"
        result = self.handle(args)
        if not result.ok:
            code = result.error.value if result.error else "unknown"
            detail = f" ({result.detail})" if result.detail else ""
            return f"Error: {code}{detail}"
        if action is Action.LIST:
            beats = result.beats or ()
            return "\n".join(self._format(beat) for beat in beats) or "No beats in this chat."
        if action is Action.DELETE:
            return "Beat deleted."
        if result.beat is None:
            return "Done."
        text = self._format(result.beat)
        if result.warning:
            text += f"\nWarning: {result.warning}"
        return text
