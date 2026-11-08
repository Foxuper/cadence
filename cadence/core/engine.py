"""Deterministic scheduler loop for cadence beats."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from ..protocols import Clock, Injector
from .enums import Kind, Reason, Scope, Status, UnansweredPolicy
from .model import MARKER, Beat
from .policy import Policy, PolicyProvider
from .store import Store

LOG = logging.getLogger("cadence")


@dataclass(frozen=True)
class Plan:
    """A claimed beat that is ready to inject, with everything needed to persist."""

    beat: Beat
    text: str
    due_key: str
    day: str


class Engine:
    def __init__(self, store: Store, clock: Clock, inject: Injector, policy_provider: PolicyProvider) -> None:
        self.store = store
        self.clock = clock
        self.inject = inject
        self.policy_provider = policy_provider
        self.owner = secrets.token_hex(8)

    def _text(self, beat: Beat, unanswered: int, policy: Policy) -> str:
        lines = [
            f"{MARKER}{beat.id}\u27e7",
            f"[Cadence heartbeat \u00b7 {beat.id} \u00b7 {beat.kind.value} \u00b7 scope={beat.scope.value}]",
            beat.note,
        ]
        if policy.unanswered_policy is UnansweredPolicy.SMART and unanswered:
            lines.append(
                f"[Unanswered beats: {unanswered}. Decide whether to insist, wait, "
                "or finish silently with cadence respond silent=true.]"
            )
        return "\n".join(lines)

    def _plan(self, beat: Beat, now: float, policy: Policy) -> Plan | None:
        """Decide what a due beat should do and claim it. Caller holds ``store.lock``."""
        current = self.store.get_beat(beat.id)
        if current is None or current.status is not Status.ACTIVE:
            return None
        beat = current
        if beat.expires_at is not None and beat.expires_at <= now:
            self.store.expire(beat)
            return None
        if beat.kind is Kind.AT and beat.next_at is not None and now - beat.next_at > policy.catch_up_s:
            self.store.miss(beat)
            return None
        session = self.store.get_session(beat.session_key)
        if beat.scope is Scope.SESSION:
            current_session = session.current_session_id if session else ""
            if not current_session or current_session != (beat.anchor_session_id or ""):
                return None
        quiet = policy.quiet_until(now)
        if quiet is not None and not (beat.urgent and policy.quiet_hours_urgent):
            self.store.defer(beat, quiet, Reason.QUIET_HOURS)
            return None
        if not self.store.has_human(beat.session_key):
            return None
        if session is None:
            session = self.store.get_session(beat.session_key)
        if (
            policy.unanswered_policy is UnansweredPolicy.HARD
            and session is not None
            and session.unanswered_count >= policy.unanswered_after
        ):
            self.store.mute(beat)
            return None
        day = policy.day(now)
        messages, fires = self.store.ledger(day, beat.session_key)
        if policy.max_messages_per_day and messages >= policy.max_messages_per_day:
            self.store.defer(beat, policy.next_day(now), Reason.DAILY_MESSAGE_CAP)
            return None
        if policy.max_fires_per_day and fires >= policy.max_fires_per_day:
            self.store.defer(beat, policy.next_day(now), Reason.DAILY_FIRE_CAP)
            return None
        last_beat = session.last_beat_at if session else None
        if last_beat is not None and last_beat + policy.spacing_s > now:
            self.store.defer(beat, last_beat + policy.spacing_s, Reason.SPACING)
            return None
        if beat.next_at is None:
            return None
        due_key = f"{beat.id}:{float(beat.next_at):.6f}"
        if not self.store.claim(beat, due_key, self.owner, now):
            return None
        unanswered = session.unanswered_count if session else 0
        return Plan(beat, self._text(beat, unanswered, policy), due_key, day)

    def _deliver(self, plan: Plan, now: float, policy: Policy) -> bool:
        """Inject outside ``store.lock`` so ingress and tool calls are never blocked."""
        try:
            accepted = bool(self.inject(plan.text, session_key=plan.beat.session_key))
        except Exception as exc:
            LOG.warning("cadence injection failed", exc_info=True)
            with self.store.lock:
                if self.store.get_beat(plan.beat.id) is not None:
                    self.store.fail(plan.beat, plan.due_key, now, type(exc).__name__, policy.retry_s)
            return False
        with self.store.lock:
            if self.store.get_beat(plan.beat.id) is None:
                return accepted
            if accepted:
                self.store.complete(plan.beat, plan.due_key, now, plan.day)
            else:
                self.store.fail(plan.beat, plan.due_key, now, Reason.INJECT_REFUSED, policy.retry_s)
        return accepted

    def tick(self) -> int:
        policy = self.policy_provider()
        if not policy.enabled:
            return 0
        now = self.clock.now()
        fired = 0
        with self.store.lock:
            due = self.store.due(now)
        for beat in due:
            with self.store.lock:
                plan = self._plan(beat, now, policy)
            if plan is not None and self._deliver(plan, now, policy):
                fired += 1
        return fired
