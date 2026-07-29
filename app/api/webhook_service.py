from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.api.webhooks import reduce_order_event, verify_signature
from app.domain.order_events import ApplyOutcome, Outcome
from app.domain.order_store import OrderEvent
from app.domain.orders import OrderStatus
from app.observability.logging import get_logger

logger = get_logger("webhook")

#: An event we durably captured but could not fold into local state still counts as
#: delivered; anything else that fails must surface a retryable status to the platform.
_OUTCOME_STATUS = {
    Outcome.APPLIED: 200,
    Outcome.UNCHANGED: 200,
    Outcome.DUPLICATE: 200,
    Outcome.STALE: 200,
    Outcome.QUARANTINED: 202,
}


class OrderRepository(Protocol):
    def apply(self, event: OrderEvent) -> ApplyOutcome: ...


@dataclass(frozen=True)
class WebhookResult:
    """HTTP status is carried beside the body, never inside it."""

    status: int
    body: dict[str, Any] = field(default_factory=dict)


def handle_order_webhook(
    body: bytes, signature: str, secret: str, repository: OrderRepository
) -> WebhookResult:
    """Framework-neutral webhook handler; never logs or returns raw payloads."""
    if not verify_signature(body, signature, secret):
        logger.warning("webhook.signature_rejected", byte_length=len(body))
        return WebhookResult(401, {"accepted": False, "error": "invalid signature"})
    try:
        payload = json.loads(body)
        event_id, order_id, status = reduce_order_event(payload)
        event = OrderEvent(event_id, order_id, OrderStatus(status), datetime.now(UTC))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("webhook.payload_rejected", reason=str(exc))
        return WebhookResult(400, {"accepted": False, "error": str(exc)})

    try:
        outcome = repository.apply(event)
    except Exception:
        # A storage failure must stay retryable; swallowing it loses the event.
        logger.error("webhook.persist_failed", exc_info=True, order_id=order_id, event_id=event_id)
        return WebhookResult(500, {"accepted": False, "error": "event could not be persisted"})

    logger.info(
        "webhook.processed",
        order_id=order_id,
        event_id=event_id,
        outcome=outcome.outcome.value,
        order_status=outcome.status.value if outcome.status else None,
    )
    return WebhookResult(
        _OUTCOME_STATUS[outcome.outcome],
        {
            "accepted": True,
            "outcome": outcome.outcome.value,
            "applied": outcome.applied,
            "order_status": outcome.status.value if outcome.status else None,
            "skipped": [status.value for status in outcome.skipped],
        },
    )
