"""Hermes loader entry point for cadence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cadence.protocols import PluginContext


def register(ctx: PluginContext) -> object:
    from .cadence.hermes.registration import register as register_plugin

    return register_plugin(ctx)
