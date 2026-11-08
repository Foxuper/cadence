from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from support import KEY, FakeClock, make_store

from cadence.core.enums import Kind, OccurrenceState, Reason, Status


def test_create_claim_complete_one_shot(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="hello",
        kind=Kind.AT,
        next_at=clock.now(),
        interval_s=None,
        max_fires=None,
        expires_at=None,
    )
    assert store.due(clock.now())[0].id == beat.id
    assert store.claim(beat, "k1", "worker", clock.now()) is True
    assert store.claim(beat, "k1", "worker", clock.now()) is False
    done = store.complete(beat, "k1", clock.now(), "2026-01-01")
    assert done.status is Status.DONE and done.fires == 1
    messages, fires = store.ledger("2026-01-01", beat.session_key)
    assert (messages, fires) == (0, 1)
    store.record_message(beat.id, "2026-01-01")
    messages, fires = store.ledger("2026-01-01", beat.session_key)
    assert (messages, fires) == (1, 1)


def test_unanswered_counts_only_delivered_messages(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="hello",
        kind=Kind.AT,
        next_at=clock.now(),
        interval_s=None,
        max_fires=None,
        expires_at=None,
    )
    store.complete(beat, "k1", clock.now(), "2026-01-01")
    session = store.get_session(KEY)
    assert session is not None and session.unanswered_count == 0
    store.record_message(beat.id, "2026-01-01")
    session = store.get_session(KEY)
    assert session is not None and session.unanswered_count == 1
    store.note_human_message(
        KEY,
        current_session_id="s1",
        platform="test",
        chat_id="1",
        human_at=clock.now(),
        human_session_id="s1",
    )
    session = store.get_session(KEY)
    assert session is not None and session.unanswered_count == 0


def test_recurring_complete_schedules_next(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="hourly",
        kind=Kind.EVERY,
        next_at=clock.now(),
        interval_s=3600,
        max_fires=None,
        expires_at=None,
    )
    updated = store.complete(beat, "k2", clock.now(), "2026-01-01")
    assert updated.status is Status.ACTIVE
    assert updated.next_at == clock.now() + 3600


def test_recurring_complete_keeps_the_interval_anchored(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    due = clock.now()
    beat = store.create_beat(
        session_key=KEY,
        note="every 10m",
        kind=Kind.EVERY,
        next_at=due,
        interval_s=600,
        max_fires=None,
        expires_at=None,
    )
    clock.advance(90)
    updated = store.complete(beat, "k4", clock.now(), "2026-01-01")
    assert updated.next_at == due + 600


def test_failed_claim_can_retry_and_delete(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="retry",
        kind=Kind.AT,
        next_at=clock.now(),
        interval_s=None,
        max_fires=None,
        expires_at=None,
    )
    assert store.claim(beat, "k3", "worker", clock.now())
    failed = store.fail(beat, "k3", clock.now(), Reason.INJECT_REFUSED, 1)
    assert failed.status is Status.ACTIVE and failed.last_error == Reason.INJECT_REFUSED
    assert store.claim(failed, "k3", "worker", clock.now()) is True
    assert store.delete_beat(beat.id) is True
    assert store.get_beat(beat.id) is None


def test_start_session_clears_human_proof_even_with_same_id(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    assert store.has_human(KEY)
    store.start_session(KEY, "s1")
    assert not store.has_human(KEY)
    session = store.get_session(KEY)
    assert session is not None
    assert session.last_human_at is None and session.human_session_id == ""


def test_has_human_requires_matching_session(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    store.touch_session(KEY, current_session_id="s2")
    assert not store.has_human(KEY)
    store.touch_session(KEY, current_session_id="s1", human_session_id="")
    assert not store.has_human(KEY)


def test_note_human_message_resumes_muted(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="muted",
        kind=Kind.EVERY,
        next_at=clock.now(),
        interval_s=600,
        max_fires=None,
        expires_at=None,
    )
    store.set_status(beat.id, Status.PAUSED, error=Reason.UNANSWERED_MUTE, next_at=None)
    resumed = store.note_human_message(
        KEY,
        current_session_id="s1",
        platform="test",
        chat_id="1",
        human_at=clock.now(),
        human_session_id="s1",
    )
    assert resumed == 1
    assert store.require_beat(beat.id).status is Status.ACTIVE
    assert store.has_human(KEY)


def test_recover_stale_claims(tmp_path: Path, clock: FakeClock) -> None:
    store = make_store(tmp_path, clock)
    beat = store.create_beat(
        session_key=KEY,
        note="stale",
        kind=Kind.AT,
        next_at=clock.now() - 400,
        interval_s=None,
        max_fires=None,
        expires_at=None,
    )
    store.conn.execute(
        "INSERT INTO occurrences(beat_id,due_key,state,claimed_by,claimed_at) VALUES(?,?,?,?,?)",
        (beat.id, "stale", OccurrenceState.CLAIMED, "worker", clock.now() - 301),
    )
    store.conn.execute(
        "INSERT INTO occurrences(beat_id,due_key,state,claimed_by,claimed_at) VALUES(?,?,?,?,?)",
        (beat.id, "recent", OccurrenceState.CLAIMED, "worker", clock.now() - 10),
    )
    store.conn.commit()
    assert store.recover_stale(clock.now()) == 1
    stale = cast(
        "sqlite3.Row | None",
        store.conn.execute(
            "SELECT state, error FROM occurrences WHERE beat_id=? AND due_key=?",
            (beat.id, "stale"),
        ).fetchone(),
    )
    assert stale is not None
    assert cast(str, stale["state"]) == OccurrenceState.FAILED
    assert cast(str, stale["error"]) == Reason.STALE_CLAIM
    recent = cast(
        "sqlite3.Row | None",
        store.conn.execute(
            "SELECT state FROM occurrences WHERE beat_id=? AND due_key=?",
            (beat.id, "recent"),
        ).fetchone(),
    )
    assert recent is not None and cast(str, recent["state"]) == OccurrenceState.CLAIMED
    assert store.recover_stale(clock.now()) == 0
