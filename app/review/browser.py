from __future__ import annotations

from typing import Protocol


class BrowserAdapter(Protocol):
    """Boundary for approved, normal-session UI tasks when no API exists."""

    def capture(self, url: str) -> bytes: ...


class DisabledBrowserAdapter:
    """Default adapter: prevents accidental unattended browser activity."""

    def capture(self, url: str) -> bytes:
        raise RuntimeError("browser automation is disabled; use an approved, human-supervised adapter")
