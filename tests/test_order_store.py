from datetime import UTC, datetime

from app.api.webhooks import reduce_order_event, verify_signature
from app.domain.order_events import Outcome
from app.domain.order_store import OrderEvent, OrderStore, reconcile
from app.domain.orders import OrderStatus


def event(event_id="e-1", status=OrderStatus.PAID):
    return OrderEvent(event_id, "o-1", status, datetime.now(UTC))


def test_events_are_idempotent():
    store = OrderStore()
    assert store.apply(event()).outcome is Outcome.APPLIED
    assert store.apply(event()).outcome is Outcome.DUPLICATE
    assert store.orders["o-1"].status == OrderStatus.PAID


def test_forward_jump_fast_forwards_and_reports_skipped_states():
    store = OrderStore()
    store.apply(event())
    outcome = store.apply(event("e-2", OrderStatus.DELIVERED))
    assert outcome.outcome is Outcome.APPLIED
    assert store.orders["o-1"].status == OrderStatus.DELIVERED
    assert outcome.skipped == (
        OrderStatus.FULFILLMENT_PENDING,
        OrderStatus.LABEL_CREATED,
        OrderStatus.SHIPPED,
    )


def test_late_event_does_not_regress_the_order():
    store = OrderStore()
    store.apply(event("e-1", OrderStatus.DELIVERED))
    outcome = store.apply(event("e-2", OrderStatus.PAID))
    assert outcome.outcome is Outcome.STALE
    assert store.orders["o-1"].status == OrderStatus.DELIVERED


def test_unreachable_status_is_quarantined_rather_than_dropped():
    store = OrderStore()
    store.apply(event("e-1", OrderStatus.DELIVERED))
    outcome = store.apply(event("e-2", OrderStatus.CANCELLED))
    assert outcome.outcome is Outcome.QUARANTINED
    assert [e["event_id"] for e in store.quarantined()] == ["e-2"]
    assert store.orders["o-1"].status == OrderStatus.DELIVERED
    assert store.resolve_quarantined("e-2") is True
    assert store.quarantined() == []
    assert store.resolve_quarantined("e-2") is False


def test_repeated_status_is_a_no_op():
    store = OrderStore()
    store.apply(event())
    assert store.apply(event("e-2", OrderStatus.PAID)).outcome is Outcome.UNCHANGED


def test_signature_and_payload_reduction():
    body = b'{"order_id":"o-1"}'
    import hashlib
    import hmac

    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "secret")
    assert not verify_signature(body, "bad", "secret")
    assert reduce_order_event(
        {"event_id": "e", "order_id": "o", "status": "PAID", "recipient_address": "secret"}
    ) == ("e", "o", "PAID")


def test_reconcile_returns_status_drift():
    store = OrderStore()
    store.apply(event())
    assert reconcile(store.orders.values(), [("o-1", OrderStatus.FULFILLMENT_PENDING)]) == ["o-1"]
