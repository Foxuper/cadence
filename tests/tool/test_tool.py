from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from support import DEFAULT_TEST_POLICY, KEY, FakeClock, make_store

from cadence.core.enums import Action, ErrorCode, Kind, Status
from cadence.core.policy import Policy
from cadence.core.store import Store
from cadence.tool import Tool


def make_tool(tmp_path: Path, clock: FakeClock, policy: Policy | None = None) -> tuple[Store, Tool]:
    store = make_store(tmp_path, clock)
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
