from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import cast

import yaml

from cadence.core.policy import Policy

MANIFEST = Path(__file__).resolve().parent.parent / "plugin.yaml"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _policy_defaults() -> dict[str, object]:
    policy = Policy()
    return {field.name: cast(object, getattr(policy, field.name)) for field in fields(policy)}


def test_manifest_config_defaults_match_policy() -> None:
    raw = cast(object, yaml.safe_load(MANIFEST.read_text(encoding="utf-8")))
    manifest = _mapping(raw)
    schema = _mapping(manifest["config_schema"])
    defaults = _policy_defaults()
    assert set(schema) == set(defaults)
    for key, raw_spec in schema.items():
        spec = _mapping(raw_spec)
        if "default" not in spec:
            continue
        expected = defaults[key]
        if isinstance(expected, tuple):
            expected = list(cast("tuple[object, ...]", expected))
        assert expected == spec["default"], f"{key} default drifted from Policy"
