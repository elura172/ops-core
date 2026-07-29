from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CustomerResponse:
    response_id: str
    recipient: str
    subject: str
    body: str
    order_id: str | None = None
    approved: bool = False

    def validate(self) -> None:
        if not self.response_id or not self.recipient or "@" not in self.recipient:
            raise ValueError("response ID and valid recipient are required")
        if not self.subject.strip() or not self.body.strip():
            raise ValueError("subject and body are required")
        if any(
            term in self.body.lower()
            for term in ("send password", "credit card number", "full card")
        ):
            raise ValueError("response requests prohibited sensitive credentials")


class Mailer(Protocol):
    def send(self, response: CustomerResponse) -> None: ...

    #: Adapters that can be switched off expose this; the executor preflights on it.
    enabled: bool


class DryRunMailer:
    def __init__(self) -> None:
        self.sent: list[CustomerResponse] = []

    def send(self, response: CustomerResponse) -> None:
        response.validate()
        if not response.approved:
            raise PermissionError("customer response requires approval")
        self.sent.append(response)


class HimalayaMailer:
    """Thin subprocess boundary; disabled unless explicitly enabled by the caller.

    The exact Himalaya command may vary by installed version, so callers can
    provide a compatible binary/argument wrapper and test it independently.
    """

    def __init__(self, *, enabled: bool = False, binary: str = "himalaya") -> None:
        self.enabled = enabled
        self.binary = binary

    def send(self, response: CustomerResponse) -> None:
        response.validate()
        if not response.approved:
            raise PermissionError("customer response requires approval")
        if not self.enabled:
            raise PermissionError("Himalaya sending is disabled")
        # The body goes over stdin, never argv: process arguments are world-readable
        # via `ps`, and this one carries buyer correspondence.
        subprocess.run(
            [self.binary, "message", "send", "--to", response.recipient],
            input=f"Subject: {response.subject}\n\n{response.body}",
            text=True,
            check=True,
            timeout=30,
        )
