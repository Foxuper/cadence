from __future__ import annotations

from pathlib import Path

from support import FakeCtx, as_context

from cadence.core.enums import ErrorCode
from cadence.hermes.registration import Controller, register
from cadence.hermes.runtime import FileLock


def test_gateway_registration_and_unload(tmp_path: Path) -> None:
    ctx = FakeCtx(tmp_path)
    register(as_context(ctx))
    assert "cadence" in ctx.tools and "cadence" in ctx.commands
    handler = ctx.tools["cadence"]["handler"]
    assert callable(handler)
    result = handler({"action": "list"})
    assert isinstance(result, str) and result.startswith("{")
    assert set(ctx.hooks) == {
        "pre_gateway_dispatch",
        "on_session_start",
        "pre_llm_call",
        "transform_llm_output",
        "on_session_end",
    }
    for callback in reversed(ctx.unloads):
        callback()


def test_busy_profile_reports_scheduler_busy(tmp_path: Path) -> None:
    holder = FileLock(tmp_path / "cadence.lock")
    assert holder.acquire()
    ctx = FakeCtx(tmp_path)
    controller = Controller(as_context(ctx), tmp_path, tmp_path, gateway_expected=True)
    try:
        result = controller.handle({"action": "list"})
        assert result.ok is False and result.error is ErrorCode.SCHEDULER_BUSY
        assert "another" in controller.command("list").lower()
    finally:
        holder.release()
        controller.close()
