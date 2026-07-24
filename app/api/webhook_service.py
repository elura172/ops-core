from __future__ import annotations

import json
from datetime import datetime, timezone

from app.api.webhooks import reduce_order_event, verify_signature
from app.domain.order_store import OrderEvent


def handle_order_webhook(body: bytes, signature: str, secret: str, repository) -> dict[str, object]:
    """HTTP-framework-neutral webhook handler; never logs or returns raw payloads."""
    if not verify_signature(body, signature, secret):
        return {"accepted": False, "status": 401, "error": "invalid signature"}
    try:
        payload = json.loads(body)
        event_id, order_id, status = reduce_order_event(payload)
        from app.domain.orders import OrderStatus
        event = OrderEvent(event_id, order_id, OrderStatus(status), datetime.now(timezone.utc))
        applied = repository.apply(event)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"accepted": False, "status": 400, "error": str(exc)}
    return {"accepted": True, "status": 200, "duplicate": not applied}
