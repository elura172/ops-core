from datetime import datetime, timezone

import pytest

from app.api.webhooks import reduce_order_event, verify_signature
from app.domain.order_store import OrderEvent, OrderStore, reconcile
from app.domain.orders import OrderStatus


def event(event_id="e-1", status=OrderStatus.PAID):
    return OrderEvent(event_id, "o-1", status, datetime.now(timezone.utc))


def test_events_are_idempotent():
    store = OrderStore()
    assert store.apply(event()) is True
    assert store.apply(event()) is False
    assert store.orders["o-1"].status == OrderStatus.PAID


def test_invalid_transition_is_rejected_without_marking_processed():
    store = OrderStore()
    store.apply(event())
    with pytest.raises(ValueError):
        store.apply(event("e-2", OrderStatus.DELIVERED))
    assert "e-2" not in store.processed_event_ids


def test_signature_and_payload_reduction():
    body = b'{"order_id":"o-1"}'
    import hashlib, hmac
    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "secret")
    assert not verify_signature(body, "bad", "secret")
    assert reduce_order_event({"event_id": "e", "order_id": "o", "status": "PAID", "recipient_address": "secret"}) == ("e", "o", "PAID")


def test_reconcile_returns_status_drift():
    store = OrderStore()
    store.apply(event())
    assert reconcile(store.orders.values(), [("o-1", OrderStatus.FULFILLMENT_PENDING)]) == ["o-1"]
