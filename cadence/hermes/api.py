"""Typed boundary for the private Hermes modules cadence touches.

Everything that imports a Hermes internal, and every ``pyright: ignore`` for
that import, lives here. The rest of the plugin only sees typed functions.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from ..protocols import PluginContext, PluginManager


def session_env(name: str) -> str:
    try:
        from gateway.session_context import get_session_env  # pyright: ignore[reportUnknownVariableType]
    except Exception:
        return ""
    reader = cast("Callable[[str, str], str]", get_session_env)
    return reader(name, "") or ""


def session_key() -> str:
    return session_env("HERMES_SESSION_KEY")


def manager_of(ctx: PluginContext) -> PluginManager | None:
    """``ctx._manager`` is private in Hermes; keep the cast in one place."""
    return cast("PluginManager | None", getattr(ctx, "_manager", None))


def get_home() -> Path:
    from hermes_constants import get_hermes_home  # pyright: ignore[reportUnknownVariableType]

    return Path(cast("Callable[[], Path]", get_hermes_home)())


@contextmanager
def home_scope(home: Path) -> Generator[None]:
    token: object | None = None
    reset: Callable[[object], object] | None = None
    try:
        from hermes_constants import (  # pyright: ignore[reportUnknownVariableType]
            reset_hermes_home_override,  # pyright: ignore[reportUnknownVariableType]
            set_hermes_home_override,  # pyright: ignore[reportUnknownVariableType]
        )

        setter = cast("Callable[[Path], object]", set_hermes_home_override)
        reset = cast("Callable[[object], object]", reset_hermes_home_override)
        token = setter(home)
    except Exception:
        token = None
    try:
        yield
    finally:
        if token is not None and reset is not None:
            with suppress(Exception):
                reset(token)
