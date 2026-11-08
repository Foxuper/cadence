from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest
from support import KEY, FakeCtx, FakeEvent, FakeGateway, as_context

from cadence.core.enums import Action, ErrorCode, Kind
from cadence.core.model import MARKER, SILENT, Beat
from cadence.hermes.registration import Controller
from cadence.protocols import GatewayEvent, SessionStore


@pytest.fixture
def controller(tmp_path: Path) -> Iterator[Controller]:
    ctx = FakeCtx(tmp_path)
    instance = Controller(as_context(ctx), tmp_path, tmp_path, gateway_expected=True)
    assert instance.command("help").startswith("Cadence:")
    yield instance
    instance.close()


def make_beat(controller: Controller) -> Beat:
    store = controller.store
    assert store is not None
    return store.create_beat(
        session_key=KEY,
        note="test",
        kind=Kind.EVERY,
        next_at=controller.clock.now() + 3600,
        interval_s=600,
        max_fires=None,
        expires_at=None,
    )


def dispatch(controller: Controller, event: FakeEvent) -> None:
    gateway = FakeGateway()
    controller.pre_gateway_dispatch(
        event=cast("GatewayEvent", event),
        gateway=gateway,
        session_store=cast("SessionStore", gateway.session_store),
    )


def session_env_for(session_id: str) -> Callable[[str], str]:
    def fake(name: str) -> str:
        return session_id if name == "HERMES_SESSION_ID" else ""

    return fake


def test_human_message_marks_session(controller: Controller) -> None:
    dispatch(controller, FakeEvent("hello"))
    store = controller.store
    assert store is not None
    session = store.get_session(KEY)
    assert session is not None
    assert session.current_session_id == "s1"
    assert session.human_session_id == "s1"
    assert store.has_human(KEY)


def test_slash_command_marks_session(controller: Controller) -> None:
    dispatch(controller, FakeEvent("/cadence list"))
    store = controller.store
    assert store is not None
    assert store.has_human(KEY)


def test_new_clears_human_proof(controller: Controller) -> None:
    dispatch(controller, FakeEvent("hello"))
    store = controller.store
    assert store is not None and store.has_human(KEY)
    dispatch(controller, FakeEvent("/new"))
    assert not store.has_human(KEY)


def test_internal_event_is_ignored(controller: Controller) -> None:
    dispatch(controller, FakeEvent("hello", internal=True))
    store = controller.store
    assert store is not None
    assert store.get_session(KEY) is None


def test_beat_marker_is_not_a_human_message(controller: Controller) -> None:
    dispatch(controller, FakeEvent(f"{MARKER}c_dead\u27e7\nnote"))
    store = controller.store
    assert store is not None
    assert store.get_session(KEY) is None


def test_silent_text_is_a_defensive_fallback(controller: Controller) -> None:
    store, runtime = controller.store, controller.runtime
    assert store is not None and runtime is not None
    beat = make_beat(controller)
    runtime.note_active("s1", "t1", KEY, beat.id)
    assert controller.transform_llm_output(SILENT, session_id="s1", turn_id="t1") == SILENT
    assert store.require_beat(beat.id).noop_streak == 1


def test_respond_silent_suppresses(controller: Controller, monkeypatch: pytest.MonkeyPatch) -> None:
    store, runtime = controller.store, controller.runtime
    assert store is not None and runtime is not None
    beat = make_beat(controller)
    runtime.note_active("s1", "t1", KEY, beat.id)
    monkeypatch.setattr("cadence.hermes.registration.session_env", session_env_for("s1"))
    result = controller.handle({"action": Action.RESPOND, "silent": True})
    assert result.ok is True
    assert controller.transform_llm_output("not delivered", session_id="s1", turn_id="t1") == SILENT
    assert store.require_beat(beat.id).noop_streak == 1


def test_respond_not_silent_delivers(controller: Controller, monkeypatch: pytest.MonkeyPatch) -> None:
    store, runtime = controller.store, controller.runtime
    assert store is not None and runtime is not None
    beat = make_beat(controller)
    runtime.note_active("s1", "t1", KEY, beat.id)
    monkeypatch.setattr("cadence.hermes.registration.session_env", session_env_for("s1"))
    result = controller.handle({"action": Action.RESPOND, "silent": False})
    assert result.ok is True
    assert controller.transform_llm_output("hello", session_id="s1", turn_id="t1") is None
    day = controller.policy().day(controller.clock.now())
    messages, _fires = store.ledger(day, KEY)
    assert messages == 1
    assert store.require_beat(beat.id).noop_streak == 0


def test_respond_outside_a_beat_is_rejected(controller: Controller, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cadence.hermes.registration.session_env", session_env_for("s1"))
    result = controller.handle({"action": Action.RESPOND, "silent": True})
    assert result.ok is False and result.error is ErrorCode.NOT_IN_BEAT


def test_normal_reply_counts_a_message(controller: Controller) -> None:
    store, runtime = controller.store, controller.runtime
    assert store is not None and runtime is not None
    beat = make_beat(controller)
    runtime.note_active("s1", "t2", KEY, beat.id)
    assert controller.transform_llm_output("hello", session_id="s1", turn_id="t2") is None
    day = controller.policy().day(controller.clock.now())
    messages, _fires = store.ledger(day, KEY)
    assert messages == 1
    assert store.require_beat(beat.id).noop_streak == 0


def test_pre_llm_call_correlates_the_marker(controller: Controller, monkeypatch: pytest.MonkeyPatch) -> None:
    store, runtime = controller.store, controller.runtime
    assert store is not None and runtime is not None
    beat = make_beat(controller)

    def fake_session_env(name: str) -> str:
        return KEY if name == "HERMES_SESSION_KEY" else ""

    monkeypatch.setattr("cadence.hermes.api.session_env", fake_session_env)
    controller.pre_llm_call(
        session_id="s1",
        turn_id="t3",
        user_message=f"{MARKER}{beat.id}\u27e7\nnote",
        platform="test",
    )
    assert controller.transform_llm_output(SILENT, session_id="s1", turn_id="t3") == SILENT
    assert store.require_beat(beat.id).noop_streak == 1
