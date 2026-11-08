from __future__ import annotations

from pathlib import Path

from support import FakeCtx, as_context

from cadence.core.enums import UrgentGate
from cadence.core.policy import Policy


def policy_with(tmp_path: Path, config: dict[str, object]) -> Policy:
    return Policy.from_ctx(as_context(FakeCtx(tmp_path, config)))


def test_urgent_bypass_defaults_to_empty(tmp_path: Path) -> None:
    assert policy_with(tmp_path, {}).urgent_bypass == ()


def test_urgent_bypass_accepts_list(tmp_path: Path) -> None:
    policy = policy_with(tmp_path, {"urgent_bypass": ["spacing"]})
    assert policy.urgent_bypass == (UrgentGate.SPACING,)


def test_urgent_bypass_accepts_both_and_dedupes(tmp_path: Path) -> None:
    policy = policy_with(
        tmp_path,
        {"urgent_bypass": ["quiet_hours", "spacing", "quiet_hours"]},
    )
    assert policy.urgent_bypass == (UrgentGate.QUIET_HOURS, UrgentGate.SPACING)


def test_urgent_bypass_accepts_comma_string(tmp_path: Path) -> None:
    policy = policy_with(tmp_path, {"urgent_bypass": "spacing, quiet_hours"})
    assert policy.urgent_bypass == (UrgentGate.SPACING, UrgentGate.QUIET_HOURS)


def test_urgent_bypass_ignores_unknown_values(tmp_path: Path) -> None:
    policy = policy_with(tmp_path, {"urgent_bypass": ["spacing", "daily_caps"]})
    assert policy.urgent_bypass == (UrgentGate.SPACING,)


def test_old_quiet_hours_urgent_is_ignored(tmp_path: Path) -> None:
    policy = policy_with(tmp_path, {"quiet_hours_urgent": True})
    assert policy.urgent_bypass == ()
