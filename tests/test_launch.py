import pytest

from app.ops.launch import LaunchChecklist, LaunchGate


def complete():
    return LaunchChecklist(True, True, True, True)


def test_incomplete_checklist_blocks_live_mode():
    gate = LaunchGate(LaunchChecklist(True, False, True, True), explicitly_enabled=True)
    with pytest.raises(PermissionError, match="signing_verified"):
        gate.assert_live_allowed()


def test_explicit_enablement_is_required():
    with pytest.raises(PermissionError, match="explicit enablement"):
        LaunchGate(complete()).assert_live_allowed()


def test_complete_explicit_gate_allows_next_layer():
    LaunchGate(complete(), explicitly_enabled=True).assert_live_allowed()
