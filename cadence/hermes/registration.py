"""Plugin registration, controller and lifecycle hooks."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from ..core.engine import Engine
from ..core.enums import Action, ErrorCode
from ..core.model import MARKER, SILENT
from ..core.policy import Policy
from ..core.results import Result
from ..core.store import Store
from ..protocols import GatewayEvent, PluginContext, SessionStore
from ..tool import SCHEMA, Tool
from .api import get_home, manager_of, session_env, session_key
from .runtime import Runtime, SystemClock

LOG = logging.getLogger("cadence")
BOOTSTRAP_TIMEOUT_S = 300.0


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


class Controller:
    """Registers the surfaces immediately; opens the profile DB only inside a live gateway."""

    def __init__(self, ctx: PluginContext, home: Path, data_dir: Path, gateway_expected: bool) -> None:
        self.ctx = ctx
        self.home = home
        self.data_dir = data_dir
        self.gateway_expected = gateway_expected
        self.clock = SystemClock()
        self.store: Store | None = None
        self.engine: Engine | None = None
        self.tool: Tool | None = None
        self.runtime: Runtime | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self.busy = False

    def policy(self) -> Policy:
        return Policy.from_ctx(self.ctx)

    def start(self) -> None:
        if not self.gateway_expected:
            return
        self.thread = threading.Thread(target=self._bootstrap, name="cadence-bootstrap", daemon=True)
        self.thread.start()

    def _bootstrap(self) -> None:
        deadline = time.monotonic() + BOOTSTRAP_TIMEOUT_S
        while not self.stop.is_set():
            manager = manager_of(self.ctx)
            if manager is not None and manager.has_gateway_message_injector:
                self._ensure()
                return
            if time.monotonic() >= deadline:
                LOG.warning("cadence: gateway message injector did not appear; scheduler disabled")
                return
            self.stop.wait(2.0)

    def _ensure(self) -> bool:
        if self._closed:
            return False
        if self.tool is not None:
            return True
        self.busy = False
        if not self.gateway_expected:
            return False
        manager = manager_of(self.ctx)
        if manager is None or not manager.has_gateway_message_injector:
            return False
        with self._lock:
            if self.tool is not None:
                return True
            try:
                store = Store(self.data_dir / "cadence.db", self.clock)
                engine = Engine(store, self.clock, self._inject, self.policy)
                tool = Tool(store, self.clock, self.policy, session_env)
                runtime = Runtime(
                    home=self.home,
                    data_dir=self.data_dir,
                    store=store,
                    engine=engine,
                    policy_provider=self.policy,
                    clock=self.clock,
                )
                if not runtime.start():
                    self.busy = True
                    store.close()
                    return False
                self.store, self.engine, self.tool, self.runtime = store, engine, tool, runtime
                return True
            except Exception:
                LOG.warning("cadence: runtime setup failed", exc_info=True)
                return False

    def _inject(self, text: str, *, session_key: str) -> bool:
        if not session_key:
            return False
        return bool(self.ctx.inject_message(text, session_key=session_key))

    def _ready(self) -> tuple[Store, Tool] | None:
        if not self._ensure():
            return None
        store, tool = self.store, self.tool
        if store is None or tool is None:
            return None
        return store, tool

    def _session_target(self) -> tuple[Store, str] | None:
        store = self.store
        if not self._ensure() or store is None:
            return None
        key = session_key()
        if not key or key == "default":
            return None
        return store, key

    def handle(self, args: Mapping[str, object], **kwargs: object) -> Result:
        ready = self._ready()
        if ready is None:
            if self.busy:
                return Result.failure(
                    ErrorCode.SCHEDULER_BUSY,
                    "Another Cadence scheduler owns this profile.",
                )
            return Result.failure(ErrorCode.NOT_GATEWAY, "Cadence only runs inside the messaging gateway.")
        store, tool = ready
        if str(args.get("action") or "").strip().lower() == Action.RESPOND.value:
            runtime = self.runtime
            session_id = str(kwargs.get("session_id") or "") or session_env("HERMES_SESSION_ID")
            if (
                runtime is None
                or not session_id
                or not runtime.mark_response(session_id, _flag(args.get("silent")))
            ):
                return Result.failure(ErrorCode.NOT_IN_BEAT, "respond is only valid on a beat turn.")
            return Result(ok=True)
        with store.lock:
            return tool.handle(args, **kwargs)

    def command(self, raw: str = "") -> str:
        ready = self._ready()
        if ready is None:
            if self.busy:
                return "Another Cadence scheduler owns this profile."
            return "Cadence only runs inside the messaging gateway."
        store, tool = ready
        with store.lock:
            return tool.command(raw)

    def pre_gateway_dispatch(
        self,
        event: GatewayEvent | None = None,
        gateway: object | None = None,
        session_store: SessionStore | None = None,
        **kwargs: object,
    ) -> None:
        store = self.store
        if not self._ensure() or event is None or store is None:
            return None
        if event.internal:
            return None
        text = event.text or ""
        if text.startswith(MARKER):
            return None
        source = event.source
        platform = ""
        chat_id = ""
        if source is not None:
            platform = source.platform.value if source.platform is not None else ""
            chat_id = source.chat_id or ""
        resolver = getattr(gateway, "_session_key_for_source", None) if gateway is not None else None
        key = ""
        if callable(resolver):
            try:
                key = str(resolver(source) or "")
            except Exception:
                key = ""
        if not key:
            return None
        event_session_id = str(getattr(event, "session_id", "") or "")
        command = ""
        if text.startswith("/"):
            command = text.split(maxsplit=1)[0].lstrip("/").lower()
        with store.lock:
            if command in {"new", "reset"}:
                store.start_session(key, session_env("HERMES_SESSION_ID"))
                return None
            human_session = event_session_id
            if not human_session and session_store is not None:
                try:
                    entry = session_store.lookup_by_session_key(key)
                    human_session = (entry.session_id or "") if entry is not None else ""
                except Exception:
                    human_session = ""
            if not human_session:
                human_session = session_env("HERMES_SESSION_ID")
            store.note_human_message(
                key,
                current_session_id=event_session_id or human_session or None,
                platform=platform,
                chat_id=chat_id,
                human_at=self.clock.now(),
                human_session_id=human_session,
            )
        return None

    def pre_llm_call(
        self,
        session_id: str = "",
        turn_id: str = "",
        user_message: str = "",
        platform: str = "",
        **kwargs: object,
    ) -> None:
        target = self._session_target()
        if target is None:
            return None
        store, key = target
        with store.lock:
            store.touch_session(
                key,
                current_session_id=session_id,
                platform=platform,
                chat_id=session_env("HERMES_SESSION_CHAT_ID"),
            )
        if str(user_message or "").startswith(MARKER) and self.runtime is not None:
            match = re.match(r"\u27e6cadence:([A-Za-z0-9_]+)\u27e7", str(user_message))
            if match:
                self.runtime.note_active(session_id, turn_id, key, match.group(1))
        return None

    def on_session_start(self, session_id: str = "", platform: str = "", **kwargs: object) -> None:
        target = self._session_target()
        if target is None:
            return None
        store, key = target
        with store.lock:
            store.touch_session(key, current_session_id=session_id)
        return None

    def transform_llm_output(
        self,
        response_text: str = "",
        session_id: str = "",
        turn_id: str = "",
        **kwargs: object,
    ) -> str | None:
        runtime, store = self.runtime, self.store
        if runtime is None or store is None:
            return None
        entry = runtime.pop_active(session_id, turn_id)
        if entry is None:
            return None
        beat_id = entry.beat_id
        response = str(response_text or "")
        silent = entry.silent
        if silent is None:
            # Defensive fallback only: respond is the primary mechanism.
            silent = response.strip().upper() == SILENT.upper()
        with store.lock:
            if silent:
                policy = self.policy()
                if policy.noop_active:
                    store.record_noop(
                        beat_id,
                        after=policy.effective_noop_after,
                        factor=2.0,
                        max_hours=float(policy.noop_max_hours),
                        pause_after=policy.noop_pause_after,
                        now=self.clock.now(),
                    )
                return SILENT
            policy = self.policy()
            store.reset_noop(beat_id)
            if response.strip():
                store.record_message(beat_id, policy.day(self.clock.now()))
        return None

    def on_session_end(self, session_id: str = "", **kwargs: object) -> None:
        if self.runtime is not None:
            self.runtime.clear_active(session_id)
        return None

    def close(self) -> None:
        self._closed = True
        self.stop.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        stopped = True
        if self.runtime is not None:
            stopped = self.runtime.close()
        if self.store is not None:
            if stopped:
                self.store.close()
            else:
                LOG.warning("cadence: scheduler thread still alive; leaving the store open")
        if stopped:
            self.runtime = None
            self.store = None
            self.tool = None
            self.engine = None


def register(ctx: PluginContext) -> Controller:
    manager = manager_of(ctx)
    home = Path(manager.home_path) if manager is not None else get_home()

    # Advisory only: manager._cli_ref is None in gateway mode.
    gateway_expected = bool(manager is not None and getattr(manager, "_cli_ref", None) is None)
    controller = Controller(ctx, home, home / "plugin-data" / "cadence", gateway_expected)

    def tool_handler(args: Mapping[str, object], **kwargs: object) -> str:
        return json.dumps(controller.handle(args, **kwargs).to_dict(), ensure_ascii=False)

    ctx.register_tool(
        name="cadence",
        toolset="cadence",
        schema=SCHEMA,
        handler=tool_handler,
        description="Persistent heartbeat beats in this chat.",
    )
    ctx.register_command(
        name="cadence",
        handler=controller.command,
        description="Create or manage persistent heartbeat beats in this chat.",
        args_hint=(
            "[list | add [session] (in 10m|every 15m|at 16:30) [until 2h] [max 12] <note> "
            "| show|pause|resume|cancel|delete <id> | update <id> ...]"
        ),
    )
    ctx.register_hook("pre_gateway_dispatch", controller.pre_gateway_dispatch)
    ctx.register_hook("on_session_start", controller.on_session_start)
    ctx.register_hook("pre_llm_call", controller.pre_llm_call)
    ctx.register_hook("transform_llm_output", controller.transform_llm_output)
    ctx.register_hook("on_session_end", controller.on_session_end)
    ctx.on_unload(controller.close)
    controller.start()
    return controller
