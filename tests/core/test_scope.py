from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from support import DEFAULT_TEST_POLICY, KEY, FakeClock, make_store

from cadence.core.engine import Engine
from cadence.core.enums import Kind, Reason, Scope, Status, UnansweredPolicy
from cadence.core.model import Beat
from cadence.core.policy import Policy
from cadence.core.store import Store


def make(tmp_path: Path, clock: FakeClock, policy: Policy | None = None) -> tuple[Store, Engine, list[str]]:
    store = make_store(tmp_path, clock)
    calls: list[str] = []

    def inject(text: str, *, session_key: str) -> bool:
        calls.append(text)
        return True

    engine = Engine(store, clock, inject, lambda: policy or DEFAULT_TEST_POLICY)
    return store, engine, calls


def create(store: Store, clock: FakeClock, *, scope: Scope = Scope.CHAT, anchor: str | None = None) -> Beat:
    return store.create_beat(
        session_key=KEY,
        note="test",
        kind=Kind.AT,
        next_at=clock.now(),
        interval_s=None,
        max_fires=None,
        expires_at=None,
        scope=scope,
        anchor_session_id=anchor,
    )


def test_chat_scope_follows_session(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = make(tmp_path, clock)
    create(store, clock, scope=Scope.CHAT, anchor="s1")
    store.touch_session(
        KEY,
        current_session_id="s2",
        human_at=clock.now(),
        human_session_id="s2",
    )
    engine.tick()
    assert len(calls) == 1


def test_session_scope_is_held_until_that_session_returns(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = make(tmp_path, clock)
    original = create(store, clock, scope=Scope.SESSION, anchor="s1")
    store.touch_session(KEY, current_session_id="s2")
    engine.tick()
    assert calls == []
    assert store.require_beat(original.id).status is Status.ACTIVE
    store.touch_session(KEY, current_session_id="s1")
    engine.tick()
    assert len(calls) == 1


def test_hard_unanswered_mute_pauses_beat(tmp_path: Path, clock: FakeClock) -> None:
    policy = replace(
        DEFAULT_TEST_POLICY,
        unanswered_policy=UnansweredPolicy.HARD,
        unanswered_after=1,
    )
    store, engine, calls = make(tmp_path, clock, policy)
    store.conn.execute("UPDATE sessions SET unanswered_count=1 WHERE session_key=?", (KEY,))
    store.conn.commit()
    beat = create(store, clock)
    engine.tick()
    assert calls == []
    stored = store.require_beat(beat.id)
    assert stored.status is Status.PAUSED and stored.last_error == Reason.UNANSWERED_MUTE


def test_start_session_clears_human(tmp_path: Path, clock: FakeClock) -> None:
    store, _engine, _calls = make(tmp_path, clock)
    store.touch_session(KEY, human_at=clock.now(), human_session_id="s1")
    assert store.has_human(KEY)
    store.start_session(KEY, "s9")
    assert not store.has_human(KEY)
    session = store.get_session(KEY)
    assert session is not None and session.current_session_id == "s9"
