from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LaunchChecklist:
    secrets_configured: bool
    signing_verified: bool
    sandbox_passed: bool
    pii_controls_verified: bool
    approval_required: bool = True

    def failures(self) -> tuple[str, ...]:
        checks = {
            "secrets_configured": self.secrets_configured,
            "signing_verified": self.signing_verified,
            "sandbox_passed": self.sandbox_passed,
            "pii_controls_verified": self.pii_controls_verified,
            "approval_required": self.approval_required,
        }
        return tuple(name for name, passed in checks.items() if not passed)

    @property
    def ready(self) -> bool:
        return not self.failures()


class LaunchGate:
    """Prevents live mutation until every operational control is attested."""

    def __init__(self, checklist: LaunchChecklist, explicitly_enabled: bool = False) -> None:
        self.checklist = checklist
        self.explicitly_enabled = explicitly_enabled

    def assert_live_allowed(self) -> None:
        failures = self.checklist.failures()
        if failures:
            raise PermissionError("launch checklist incomplete: " + ", ".join(failures))
        if not self.explicitly_enabled:
            raise PermissionError("live mode requires explicit enablement")
