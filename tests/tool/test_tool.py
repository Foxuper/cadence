from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from support import DEFAULT_TEST_POLICY, KEY, FakeClock, make_store

from cadence.core.enums import Action, ErrorCode, Kind, Reason, Status, UrgentGate
from cadence.core.policy import Policy
from cadence.core.store import Store
from cadence.tool import Tool


def make_tool(
    tmp_path: Path,
    clock: FakeClock,
    policy: Policy | None = None,
    *,
    human: bool = True,
) -> tuple[Store, Tool]:
    store = make_store(tmp_path, clock, human=human)
    env = {
        "HERMES_SESSION_KEY": KEY,
        "HERMES_SESSION_PLATFORM": "test",
        "HERMES_SESSION_CHAT_ID": "1",
        "HERMES_SESSION_ID": "s1",
    }
    tool = Tool(store, clock, lambda: policy or DEFAULT_TEST_POLICY, lambda name: env.get(name, ""))
    return store, tool


def test_create_list_and_update(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "hello"})
    assert result.ok and result.beat is not None and result.beat.kind is Kind.AT
    beat_id = result.beat.id
    listed = tool.handle({"action": Action.LIST})
    assert listed.beats is not None and listed.beats[0].id == beat_id
    updated = tool.handle({"action": Action.UPDATE, "id": beat_id, "note": "goodbye"})
    assert updated.ok and updated.beat is not None and updated.beat.note == "goodbye"
    listed = tool.handle({"action": Action.LIST})
    assert listed.beats is not None and listed.beats[0].note == "goodbye"
    cancelled = tool.handle({"action": Action.CANCEL, "id": beat_id})
    assert cancelled.beat is not None and cancelled.beat.status is Status.CANCELLED
    assert tool.handle({"action": Action.DELETE, "id": beat_id}).ok is True
    assert store.get_beat(beat_id) is None


def test_terminal_update_rejected(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    store.set_status(created.id, Status.DONE, next_at=None)
    result = tool.handle({"action": Action.UPDATE, "id": created.id, "note": "y"})
    assert result.ok is False and result.error is ErrorCode.ITEM_IS_TERMINAL


def test_command_add_and_list(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    text = tool.command("add in 10m remember")
    assert "c_" in text
    assert "No beats" not in tool.command("list")
    assert "remember" in tool.command("list")


def test_command_add_accepts_until_and_max(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    text = tool.command("add every 10m max 3 until in 2h review pending")
    assert "c_" in text
    beat = store.list_beats(KEY)[0]
    assert beat.max_fires == 3
    assert beat.expires_at is not None
    assert beat.note == "review pending"
    assert beat.kind is Kind.EVERY and beat.interval_s == 600


def test_command_update_accepts_until_and_max(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "every": "10m", "note": "x"}).beat
    assert created is not None
    assert "Error" not in tool.command(f"update {created.id} max 5")
    assert "Error" not in tool.command(f"update {created.id} until in 1h")
    stored = store.require_beat(created.id)
    assert stored.max_fires == 5
    assert stored.expires_at is not None


def test_create_rejects_short_interval(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock, replace(DEFAULT_TEST_POLICY, min_interval_minutes=10))
    result = tool.handle({"action": Action.CREATE, "every": "1m", "note": "x"})
    assert result.ok is False and result.error is ErrorCode.INTERVAL_TOO_SHORT
    assert result.minimum_seconds == 600.0


def test_update_rejects_short_interval(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock, replace(DEFAULT_TEST_POLICY, min_interval_minutes=10))
    created = tool.handle({"action": Action.CREATE, "every": "10m", "note": "x"}).beat
    assert created is not None
    result = tool.handle({"action": Action.UPDATE, "id": created.id, "every": "1m"})
    assert result.ok is False and result.error is ErrorCode.INTERVAL_TOO_SHORT


def test_create_rejects_invalid_until(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "until": "nope"})
    assert result.ok is False and result.error is ErrorCode.INVALID_UNTIL


def test_create_rejects_invalid_max_fires(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "max_fires": "nope"})
    assert result.ok is False and result.error is ErrorCode.INVALID_MAX_FIRES


def test_create_accepts_urgent(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "urgent": True})
    assert result.ok is True and result.beat is not None and result.beat.urgent is True


def test_update_can_toggle_urgent(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    updated = tool.handle({"action": Action.UPDATE, "id": created.id, "urgent": True})
    assert updated.ok is True and updated.beat is not None and updated.beat.urgent is True
    cleared = tool.handle({"action": Action.UPDATE, "id": created.id, "urgent": False})
    assert cleared.ok is True and cleared.beat is not None and cleared.beat.urgent is False


def test_command_update_urgent_requires_boolean(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    text = tool.command(f"update {created.id} urgent true")
    assert "urgent" in text
    assert store.require_beat(created.id).urgent is True
    text = tool.command(f"update {created.id} urgent false")
    assert "urgent" not in text
    assert store.require_beat(created.id).urgent is False
    assert tool.command(f"update {created.id} urgent").startswith("Usage:")
    assert tool.command(f"update {created.id} urgent yes").startswith("Usage:")


def test_resume_clears_pause_reason(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    store.set_status(created.id, Status.PAUSED, error=Reason.UNANSWERED_MUTE, next_at=None)
    store.conn.execute("UPDATE beats SET noop_streak=3 WHERE id=?", (created.id,))
    store.conn.commit()
    result = tool.handle({"action": Action.RESUME, "id": created.id})
    assert result.ok is True and result.beat is not None
    assert result.beat.status is Status.ACTIVE
    assert result.beat.last_error == ""
    assert store.require_beat(created.id).noop_streak == 0
    assert "paused:" not in tool.command(f"show {created.id}")


def test_command_add_urgent(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    text = tool.command("add urgent in 10m remember")
    assert "urgent" in text
    assert store.list_beats(KEY)[0].urgent is True


def test_create_receipt_warns_about_quiet_hours(tmp_path: Path, clock: FakeClock) -> None:
    tool = make_tool(
        tmp_path,
        clock,
        replace(DEFAULT_TEST_POLICY, quiet_hours="22:00-08:00", timezone="UTC"),
    )[1]
    clock.wall = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"})
    hold = datetime(2026, 1, 1, 8, 0, tzinfo=UTC).timestamp()
    assert result.ok is True
    assert result.quiet_hours_until == hold
    assert result.effective_next_at == hold
    assert result.warning is not None and "quiet hours" in result.warning
    assert result.to_dict()["effective_next_at"] == hold


def test_urgent_receipt_warns_when_bypass_is_disabled(tmp_path: Path, clock: FakeClock) -> None:
    tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="22:00-08:00",
            urgent_bypass=(),
            timezone="UTC",
        ),
    )[1]
    clock.wall = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "urgent": True})
    assert result.ok is True
    assert result.effective_next_at == datetime(2026, 1, 1, 8, 0, tzinfo=UTC).timestamp()
    assert result.warning is not None and "urgent_bypass" in result.warning


def test_urgent_receipt_skips_quiet_hours_when_enabled(tmp_path: Path, clock: FakeClock) -> None:
    tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="22:00-08:00",
            urgent_bypass=(UrgentGate.QUIET_HOURS,),
            timezone="UTC",
        ),
    )[1]
    clock.wall = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "urgent": True})
    assert result.ok is True
    assert result.quiet_hours_until is None
    assert result.effective_next_at is None
    assert result.warning == ""


def test_urgent_receipt_spacing_only_still_warns_about_quiet_hours(tmp_path: Path, clock: FakeClock) -> None:
    tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="22:00-08:00",
            urgent_bypass=(UrgentGate.SPACING,),
            timezone="UTC",
        ),
    )[1]
    clock.wall = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x", "urgent": True})
    assert result.quiet_hours_until == datetime(2026, 1, 1, 8, 0, tzinfo=UTC).timestamp()
    assert result.warning is not None
    assert "quiet_hours" in result.warning and "urgent_bypass" in result.warning


def test_command_warns_about_quiet_hours(tmp_path: Path, clock: FakeClock) -> None:
    tool = make_tool(
        tmp_path,
        clock,
        replace(DEFAULT_TEST_POLICY, quiet_hours="22:00-08:00", timezone="UTC"),
    )[1]
    clock.wall = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    text = tool.command("add in 10m remember")
    assert "Warning:" in text and "quiet hours" in text


def test_create_receipt_warns_about_spacing(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(DEFAULT_TEST_POLICY, spacing_minutes=15, timezone="UTC"),
    )
    now = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    clock.wall = now
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (now - 60, KEY))
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 5m", "note": "x"})
    hold = now - 60 + 900
    assert result.ok is True
    assert result.spacing_until == hold
    assert result.effective_next_at == hold
    assert result.warning is not None and "spacing" in result.warning
    assert result.to_dict()["spacing_until"] == hold


def test_receipt_combines_quiet_hours_and_spacing(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            quiet_hours="22:00-08:00",
            spacing_minutes=15,
            timezone="UTC",
        ),
    )
    now = datetime(2026, 1, 1, 7, 50, tzinfo=UTC).timestamp()
    clock.wall = now
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (now, KEY))
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 5m", "note": "x"})
    quiet = datetime(2026, 1, 1, 8, 0, tzinfo=UTC).timestamp()
    spacing = now + 900
    assert result.quiet_hours_until == quiet
    assert result.spacing_until == spacing
    assert result.effective_next_at == spacing
    assert result.warning is not None
    assert "quiet hours" in result.warning and "spacing" in result.warning


def test_command_labels_deferred_paused_and_errors(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    store.conn.execute("UPDATE beats SET last_error=? WHERE id=?", (Reason.SPACING.value, created.id))
    store.conn.commit()
    text = tool.command(f"show {created.id}")
    assert "deferred: spacing" in text and "error: spacing" not in text
    store.conn.execute(
        "UPDATE beats SET last_error=?, status=? WHERE id=?",
        (Reason.UNANSWERED_MUTE.value, Status.PAUSED.value, created.id),
    )
    store.conn.commit()
    assert "paused: unanswered_mute" in tool.command(f"show {created.id}")
    store.conn.execute(
        "UPDATE beats SET last_error=?, status=? WHERE id=?",
        (Reason.INJECT_REFUSED.value, Status.ACTIVE.value, created.id),
    )
    store.conn.commit()
    assert "error: inject_refused" in tool.command(f"show {created.id}")


def test_urgent_receipt_skips_spacing_when_policy_allows(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            spacing_minutes=15,
            urgent_bypass=(UrgentGate.QUIET_HOURS, UrgentGate.SPACING),
            timezone="UTC",
        ),
    )
    now = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    clock.wall = now
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (now - 60, KEY))
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 5m", "note": "x", "urgent": True})
    assert result.ok is True
    assert result.spacing_until is None
    assert result.effective_next_at is None
    assert result.warning == ""


def test_urgent_receipt_warns_about_spacing_with_quiet_hours_policy(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(
            DEFAULT_TEST_POLICY,
            spacing_minutes=15,
            urgent_bypass=(UrgentGate.QUIET_HOURS,),
            timezone="UTC",
        ),
    )
    now = datetime(2026, 1, 1, 0, 30, tzinfo=UTC).timestamp()
    clock.wall = now
    store.conn.execute("UPDATE sessions SET last_beat_at=? WHERE session_key=?", (now - 60, KEY))
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 5m", "note": "x", "urgent": True})
    assert result.spacing_until == now - 60 + 900
    assert result.warning is not None and "spacing" in result.warning


def test_receipt_warns_about_daily_message_cap(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(DEFAULT_TEST_POLICY, max_messages_per_day=1, timezone="UTC"),
    )
    clock.wall = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).timestamp()
    store.conn.execute(
        "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,1,0)",
        ("2026-01-01", KEY),
    )
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 1h", "note": "x"})
    midnight = datetime(2026, 1, 2, 0, 0, tzinfo=UTC).timestamp()
    assert result.cap_until == midnight
    assert result.effective_next_at == midnight
    assert result.warning is not None and "daily message cap" in result.warning


def test_receipt_warns_about_daily_fire_cap(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(
        tmp_path,
        clock,
        replace(DEFAULT_TEST_POLICY, max_fires_per_day=1, timezone="UTC"),
    )
    clock.wall = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).timestamp()
    store.conn.execute(
        "INSERT INTO ledger(day,session_key,messages,fires) VALUES(?,?,0,1)",
        ("2026-01-01", KEY),
    )
    store.conn.commit()
    result = tool.handle({"action": Action.CREATE, "at": "in 1h", "note": "x"})
    midnight = datetime(2026, 1, 2, 0, 0, tzinfo=UTC).timestamp()
    assert result.cap_until == midnight
    assert result.warning is not None and "daily fire cap" in result.warning


def test_receipt_warns_about_catch_up(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    clock.wall = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).timestamp()
    result = tool.handle({"action": Action.CREATE, "at": "2026-01-01T08:00:00+00:00", "note": "x"})
    assert result.warning is not None
    assert "catch-up" in result.warning and "missed" in result.warning


def test_receipt_warns_about_expiry(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    soon = tool.handle({"action": Action.CREATE, "at": "in 10m", "until": "in 5m", "note": "x"})
    assert soon.warning is not None and "expires before it fires" in soon.warning
    past = tool.handle(
        {
            "action": Action.CREATE,
            "at": "in 10m",
            "until": "2020-01-01T00:00:00+00:00",
            "note": "x",
        }
    )
    assert past.warning is not None and "already expired" in past.warning


def test_receipt_warns_about_missing_human_proof(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock, human=False)
    result = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"})
    assert result.warning is not None and "no human message" in result.warning


def test_create_daily_schedule(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "daily": "13:00, 9:00,13:00", "note": "pulse"})
    assert result.ok is True and result.beat is not None
    assert result.beat.kind is Kind.DAILY
    assert result.beat.daily_times == ("09:00", "13:00")
    stored = store.require_beat(result.beat.id)
    assert stored.daily_times == ("09:00", "13:00")
    assert stored.interval_s is None


def test_create_rejects_mixed_schedules(tmp_path: Path, clock: FakeClock) -> None:
    _, tool = make_tool(tmp_path, clock)
    result = tool.handle({"action": Action.CREATE, "daily": "09:00", "at": "in 10m", "note": "x"})
    assert result.ok is False and result.error is ErrorCode.ONE_OF_AT_OR_EVERY


def test_command_add_daily(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    text = tool.command("add daily at 09:00,13:00 recordar")
    assert "daily" in text and "09:00,13:00" in text
    beat = store.list_beats(KEY)[0]
    assert beat.kind is Kind.DAILY and beat.daily_times == ("09:00", "13:00")


def test_update_to_daily(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    result = tool.handle({"action": Action.UPDATE, "id": created.id, "daily": "10:00,14:00"})
    assert result.ok is True and result.beat is not None
    assert result.beat.kind is Kind.DAILY
    assert result.beat.daily_times == ("10:00", "14:00")
    assert store.require_beat(created.id).interval_s is None


def test_command_update_daily(tmp_path: Path, clock: FakeClock) -> None:
    store, tool = make_tool(tmp_path, clock)
    created = tool.handle({"action": Action.CREATE, "at": "in 10m", "note": "x"}).beat
    assert created is not None
    text = tool.command(f"update {created.id} daily at 10:00")
    assert "daily" in text and "10:00" in text
    assert store.require_beat(created.id).kind is Kind.DAILY
    assert store.require_beat(created.id).daily_times == ("10:00",)
