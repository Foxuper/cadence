"""Closed sets of string values used by cadence."""

from enum import StrEnum


class Kind(StrEnum):
    AT = "at"
    EVERY = "every"
    DAILY = "daily"


class Scope(StrEnum):
    CHAT = "chat"
    SESSION = "session"


class Status(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    MISSED = "missed"


class Action(StrEnum):
    CREATE = "create"
    LIST = "list"
    GET = "get"
    UPDATE = "update"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    DELETE = "delete"
    RESPOND = "respond"


class ErrorCode(StrEnum):
    NOT_GATEWAY = "not_gateway"
    SCHEDULER_BUSY = "scheduler_busy"
    NOTE_REQUIRED = "note_required"
    ONE_OF_AT_OR_EVERY = "one_of_at_or_every"
    INVALID_SCOPE = "invalid_scope"
    SESSION_UNKNOWN = "session_unknown"
    INTERVAL_TOO_SHORT = "interval_too_short"
    USE_EVERY_FIELD = "use_every_field"
    INVALID_TIME = "invalid_time"
    INVALID_MAX_FIRES = "invalid_max_fires"
    INVALID_UNTIL = "invalid_until"
    ITEM_IS_TERMINAL = "item_is_terminal"
    UNKNOWN_BEAT = "unknown_beat"
    NOT_IN_SESSION = "not_in_session"
    NOT_IN_BEAT = "not_in_beat"
    NOT_ACTIVE = "not_active"
    NOT_PAUSED = "not_paused"
    UNKNOWN_ACTION = "unknown_action"


class NoopBackoff(StrEnum):
    OFF = "off"
    GENTLE = "gentle"
    ASSERTIVE = "assertive"


class UrgentGate(StrEnum):
    QUIET_HOURS = "quiet_hours"
    SPACING = "spacing"


class UnansweredPolicy(StrEnum):
    OFF = "off"
    HARD = "hard"
    SMART = "smart"


class OccurrenceState(StrEnum):
    CLAIMED = "claimed"
    INJECTED = "injected"
    FAILED = "failed"


class Reason(StrEnum):
    QUIET_HOURS = "quiet_hours"
    DAILY_MESSAGE_CAP = "daily_message_cap"
    DAILY_FIRE_CAP = "daily_fire_cap"
    SPACING = "spacing"
    INJECT_REFUSED = "inject_refused"
    NOOP_PAUSE = "noop_pause"
    NOOP_BACKOFF = "noop_backoff"
    UNANSWERED_MUTE = "unanswered_mute"
    EXPIRED = "expired"
    MISSED = "missed"
    STALE_CLAIM = "stale_claim"


DEFERRAL_REASONS: frozenset[str] = frozenset(
    {
        Reason.QUIET_HOURS.value,
        Reason.DAILY_MESSAGE_CAP.value,
        Reason.DAILY_FIRE_CAP.value,
        Reason.SPACING.value,
        Reason.NOOP_BACKOFF.value,
    }
)

PAUSE_REASONS: frozenset[str] = frozenset(
    {
        Reason.NOOP_PAUSE.value,
        Reason.UNANSWERED_MUTE.value,
    }
)
