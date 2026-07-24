from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SandboxTransport:
    """Captures signed requests without contacting TikTok."""

    requests: list[dict[str, Any]] = field(default_factory=list)

    def post(self, path: str, params: dict[str, str], body: dict[str, Any], signature: str) -> dict[str, Any]:
        request = {"method": "POST", "path": path, "params": params, "body": body, "signature": signature}
        self.requests.append(request)
        return {"sandbox": True, "accepted": True, "request_index": len(self.requests) - 1}
