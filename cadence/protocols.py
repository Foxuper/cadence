"""Structural types for the collaborators cadence receives at runtime.

These are local Protocols on purpose: they describe only the attributes and
methods cadence uses, so the plugin never imports Hermes internals and strict
type checking stays free of their ``Any``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from os import PathLike
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...


class Injector(Protocol):
    def __call__(self, text: str, *, session_key: str) -> bool: ...


class PluginContext(Protocol):
    def get_config(self, key: str, default: object = None) -> object: ...
    def inject_message(self, content: str, *, session_key: str | None = None) -> bool: ...
    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: Mapping[str, object],
        handler: Callable[..., str],
        description: str = "",
    ) -> object: ...
    def register_command(
        self,
        *,
        name: str,
        handler: Callable[..., str],
        description: str = "",
        args_hint: str = "",
    ) -> object: ...
    def register_hook(self, name: str, callback: Callable[..., object]) -> object: ...
    def on_unload(self, callback: Callable[[], None]) -> object: ...


class PluginManager(Protocol):
    home_path: str | PathLike[str]
    has_gateway_message_injector: bool


class PlatformValue(Protocol):
    value: str


class EventSource(Protocol):
    platform: PlatformValue | None
    chat_id: str | None


class GatewayEvent(Protocol):
    text: str
    internal: bool
    source: EventSource | None


class SessionEntry(Protocol):
    session_id: str | None


class SessionStore(Protocol):
    def lookup_by_session_key(self, key: str) -> SessionEntry | None: ...
