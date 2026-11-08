"""Shared test helpers and Hermes fakes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cadence.core.policy import Policy
from cadence.core.store import Store
from cadence.protocols import PluginContext

KEY = "agent:test:dm:1"

DEFAULT_TEST_POLICY = Policy(
    quiet_hours="",
    timezone="UTC",
    spacing_minutes=0,
    min_interval_minutes=0,
    retry_seconds=1,
    catch_up_minutes=60,
)


def make_store(tmp_path: Path, clock: FakeClock, *, human: bool = True) -> Store:
    store = Store(tmp_path / "cadence.db", clock)
    if human:
        store.touch_session(
            KEY,
            current_session_id="s1",
            platform="test",
            chat_id="1",
            human_at=clock.now(),
            human_session_id="s1",
        )
    else:
        store.touch_session(KEY, current_session_id="s1", platform="test", chat_id="1")
    return store


class FakeClock:
    def __init__(self, wall: float = 1760000000.0, mono: float = 100.0) -> None:
        self.wall = wall
        self.mono = mono

    def now(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


class FakeManager:
    def __init__(self, home: Path) -> None:
        self.home_path = home
        self.scope_key = str(home)
        self._cli_ref: object | None = None
        self.has_gateway_message_injector = True
        self._gateway_message_injector: tuple[object, None] = (object(), None)


class FakeCtx:
    def __init__(self, home: Path) -> None:
        self._manager = FakeManager(home)
        self.tools: dict[str, dict[str, object]] = {}
        self.commands: dict[str, dict[str, object]] = {}
        self.hooks: dict[str, list[Callable[..., object]]] = {}
        self.unloads: list[Callable[[], None]] = []
        self.injected: list[str] = []

    def get_config(self, key: str, default: object = None) -> object:
        return default

    def register_tool(self, name: str, **kwargs: object) -> None:
        self.tools[name] = kwargs

    def register_command(self, name: str, **kwargs: object) -> None:
        self.commands[name] = kwargs

    def register_hook(self, name: str, callback: Callable[..., object]) -> None:
        self.hooks.setdefault(name, []).append(callback)

    def on_unload(self, callback: Callable[[], None]) -> None:
        self.unloads.append(callback)

    def inject_message(self, content: str, **kwargs: object) -> bool:
        self.injected.append(content)
        return True


def as_context(ctx: FakeCtx) -> PluginContext:
    """FakeCtx satisfies the protocol structurally; this keeps the intent explicit."""
    return ctx


class FakePlatform:
    def __init__(self, value: str) -> None:
        self.value = value


class FakeSource:
    def __init__(self, chat_id: str = "1", platform: str = "test") -> None:
        self.chat_id = chat_id
        self.platform = FakePlatform(platform)


class FakeEvent:
    def __init__(
        self,
        text: str,
        *,
        internal: bool = False,
        source: FakeSource | None = None,
    ) -> None:
        self.text = text
        self.internal = internal
        self.source = source or FakeSource()


class FakeSessionEntry:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class FakeSessionStore:
    def __init__(self, session_id: str = "s1") -> None:
        self.session_id = session_id

    def lookup_by_session_key(self, key: str) -> FakeSessionEntry | None:
        return FakeSessionEntry(self.session_id)


class FakeGateway:
    def __init__(self, session_id: str = "s1") -> None:
        self.session_store = FakeSessionStore(session_id)

    def _session_key_for_source(self, source: FakeSource | None) -> str:
        return KEY
