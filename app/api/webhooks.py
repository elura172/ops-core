from __future__ import annotations

import hashlib
import hmac


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify an HMAC-SHA256 webhook signature without logging the body."""
    if not body or not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    received = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def reduce_order_event(payload: dict[str, object]) -> tuple[str, str, str]:
    """Extract only event/order/status identifiers from a webhook payload."""
    event_id = str(payload.get("event_id", ""))
    order_id = str(payload.get("order_id", ""))
    status = str(payload.get("status", ""))
    if not all((event_id, order_id, status)):
        raise ValueError("webhook requires event_id, order_id, and status")
    return event_id, order_id, status
