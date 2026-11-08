from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from support import DEFAULT_TEST_POLICY, KEY, FakeClock, make_store

from cadence.core.engine import Engine
from cadence.core.enums import Kind, Reason, Status, UrgentGate
from cadence.core.model import Beat
from cadence.core.policy import Policy
from cadence.core.store import Store


def setup(
    tmp_path: Path,
    clock: FakeClock,
    *,
    human: bool = True,
    policy: Policy | None = None,
) -> tuple[Store, Engine, list[tuple[str, str]]]:
    store = make_store(tmp_path, clock, human=human)
    calls: list[tuple[str, str]] = []

    def inject(text: str, *, session_key: str) -> bool:
        calls.append((text, session_key))
        return True

    pol = policy or DEFAULT_TEST_POLICY
    engine = Engine(store, clock, inject, lambda: pol)
    return store, engine, calls


def beat(
    store: Store,
    clock: FakeClock,
    kind: Kind = Kind.AT,
    interval: float | None = None,
    *,
    urgent: bool = False,
) -> Beat:
    return store.create_beat(
        session_key=KEY,
        note="remember",
        kind=kind,
        next_at=clock.now(),
        interval_s=interval,
        max_fires=None,
        expires_at=None,
        urgent=urgent,
    )


def test_one_shot_injects_and_terminates(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(tmp_path, clock)
    created = beat(store, clock)
    assert engine.tick() == 1
    assert len(calls) == 1 and calls[0][1] == KEY
    stored = store.require_beat(created.id)
    assert stored.status is Status.DONE and stored.fires == 1


def test_recurring_reschedules(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(tmp_path, clock)
    created = beat(store, clock, Kind.EVERY, 3600)
    engine.tick()
    stored = store.require_beat(created.id)
    assert stored.status is Status.ACTIVE
    assert stored.next_at == clock.now() + 3600
    assert len(calls) == 1


def test_no_human_defers(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(tmp_path, clock, human=False)
    created = beat(store, clock)
    engine.tick()
    assert calls == []
    stored = store.require_beat(created.id)
    assert stored.status is Status.ACTIVE and stored.fires == 0


def test_quiet_hours_defers(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(DEFAULT_TEST_POLICY, quiet_hours="23:00-07:00"),
    )
    clock.wall = datetime(2026, 1, 1, 23, 30, tzinfo=UTC).timestamp()
    created = beat(store, clock)
    engine.tick()
    assert calls == []
    stored = store.require_beat(created.id)
    assert stored.status is Status.ACTIVE and stored.last_error == Reason.QUIET_HOURS
    assert stored.next_at == datetime(2026, 1, 2, 7, 0, tzinfo=UTC).timestamp()


def test_urgent_bypasses_quiet_hours_with_policy(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="23:00-07:00",
            urgent_bypass=(UrgentGate.QUIET_HOURS,),
        ),
    )
    clock.wall = datetime(2026, 1, 1, 23, 30, tzinfo=UTC).timestamp()
    beat(store, clock, urgent=True)
    engine.tick()
    assert len(calls) == 1


def test_urgent_still_defers_with_policy_off(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="23:00-07:00",
            urgent_bypass=(),
        ),
    )
    clock.wall = datetime(2026, 1, 1, 23, 30, tzinfo=UTC).timestamp()
    created = beat(store, clock, urgent=True)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error == Reason.QUIET_HOURS


def test_urgent_bypasses_spacing_with_policy(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            spacing_minutes=15,
            urgent_bypass=(UrgentGate.QUIET_HOURS, UrgentGate.SPACING),
        ),
    )
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (clock.now() - 60, KEY))
    store.conn.commit()
    beat(store, clock, urgent=True)
    engine.tick()
    assert len(calls) == 1


def test_urgent_does_not_bypass_spacing_with_quiet_hours_policy(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            spacing_minutes=15,
            urgent_bypass=(UrgentGate.QUIET_HOURS,),
        ),
    )
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (clock.now() - 60, KEY))
    store.conn.commit()
    created = beat(store, clock, urgent=True)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error == Reason.SPACING


def test_urgent_spacing_only_bypasses_spacing(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            spacing_minutes=15,
            urgent_bypass=(UrgentGate.SPACING,),
        ),
    )
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (clock.now() - 60, KEY))
    store.conn.commit()
    beat(store, clock, urgent=True)
    engine.tick()
    assert len(calls) == 1


def test_urgent_spacing_only_still_defers_quiet_hours(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="23:00-07:00",
            urgent_bypass=(UrgentGate.SPACING,),
        ),
    )
    clock.wall = datetime(2026, 1, 1, 23, 30, tzinfo=UTC).timestamp()
    created = beat(store, clock, urgent=True)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error == Reason.QUIET_HOURS


def test_urgent_does_not_bypass_daily_message_cap(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            max_messages_per_day=1,
            urgent_bypass=(UrgentGate.QUIET_HOURS, UrgentGate.SPACING),
        ),
    )
    clock.wall = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).timestamp()
    store.conn.execute(
        "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,1,0)",
        ("2026-01-01", KEY),
    )
    store.conn.commit()
    created = beat(store, clock, urgent=True)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error == Reason.DAILY_MESSAGE_CAP


def test_urgent_does_not_bypass_catch_up(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(
            DEFAULT_TEST_POLICY,
            catch_up_minutes=60,
            urgent_bypass=(UrgentGate.QUIET_HOURS, UrgentGate.SPACING),
        ),
    )
    created = store.create_beat(
        session_key=KEY,
        note="remember",
        kind=Kind.AT,
        next_at=clock.now() - 7200,
        interval_s=None,
        max_fires=None,
        expires_at=None,
        urgent=True,
    )
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).status is Status.MISSED


def test_spacing_defers(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(tmp_path, clock, policy=replace(DEFAULT_TEST_POLICY, spacing_minutes=1))
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (clock.now() - 30, KEY))
    store.conn.commit()
    created = beat(store, clock)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error == Reason.SPACING


def test_daily_cap_defers(tmp_path: Path, clock: FakeClock) -> None:
    store, engine, calls = setup(
        tmp_path,
        clock,
        policy=replace(DEFAULT_TEST_POLICY, max_messages_per_day=1),
    )
    store.conn.execute(
        "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,1,1)", ("2026-01-01", KEY)
    )
    store.conn.commit()
    clock.wall = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).timestamp()
    created = beat(store, clock)
    engine.tick()
    assert calls == []
    assert store.require_beat(created.id).last_error in {Reason.DAILY_MESSAGE_CAP, Reason.DAILY_FIRE_CAP}


def test_refused_injection_retries(tmp_path: Path, clock: FakeClock) -> None:
    store = Store(tmp_path / "cadence.db", clock)
    store.touch_session(
        KEY,
        current_session_id="s1",
        platform="test",
        chat_id="1",
        human_at=clock.now(),
        human_session_id="s1",
    )
    pol = DEFAULT_TEST_POLICY
    calls: list[str] = []

    def inject(text: str, *, session_key: str) -> bool:
        calls.append(text)
        return False

    engine = Engine(store, clock, inject, lambda: pol)
    created = beat(store, clock)
    engine.tick()
    stored = store.require_beat(created.id)
    assert len(calls) == 1
    assert stored.status is Status.ACTIVE and stored.last_error == Reason.INJECT_REFUSED
    assert stored.next_at == clock.now() + 1


def test_pause_during_injection_wins(tmp_path: Path, clock: FakeClock) -> None:
    store, _engine, _calls = setup(tmp_path, clock)
    created = beat(store, clock, Kind.EVERY, 600)
    pol = DEFAULT_TEST_POLICY

    def inject(text: str, *, session_key: str) -> bool:
        store.set_status(created.id, Status.PAUSED, next_at=None)
        return True

    engine = Engine(store, clock, inject, lambda: pol)
    assert engine.tick() == 1
    stored = store.require_beat(created.id)
    assert stored.status is Status.PAUSED
    assert stored.fires == 1
    assert stored.next_at is None


def test_delete_during_injection_removes_beat(tmp_path: Path, clock: FakeClock) -> None:
    store, _engine, _calls = setup(tmp_path, clock)
    created = beat(store, clock, Kind.EVERY, 600)
    pol = DEFAULT_TEST_POLICY

    def inject(text: str, *, session_key: str) -> bool:
        store.delete_beat(created.id)
        return True

    engine = Engine(store, clock, inject, lambda: pol)
    assert engine.tick() == 1
    assert store.get_beat(created.id) is None
