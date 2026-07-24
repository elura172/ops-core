from datetime import UTC, datetime

from app.retention.engine import RetentionEngine
from app.retention.models import ConsentStatus, CustomerProfile, LifecycleEvent


def event(name="order_delivered", event_id="e-1"):
    return LifecycleEvent(event_id, "cust-1", name, datetime.now(UTC), "order-1")


def test_marketing_requires_explicit_consent():
    profile = CustomerProfile(
        "cust-1", email="a@example.test", marketing_consent=ConsentStatus.UNKNOWN
    )
    assert RetentionEngine().plan(event(), profile) is None


def test_transactional_message_requires_transactional_consent():
    profile = CustomerProfile(
        "cust-1", email="a@example.test", transactional_consent=ConsentStatus.OPTED_IN
    )
    plan = RetentionEngine().plan(event("order_paid"), profile)
    assert plan and plan.marketing is False


def test_suppressed_customer_is_never_messaged():
    profile = CustomerProfile(
        "cust-1", email="a@example.test", marketing_consent=ConsentStatus.OPTED_IN, suppressed=True
    )
    assert RetentionEngine().plan(event(), profile) is None


def test_events_are_idempotent():
    profile = CustomerProfile(
        "cust-1", email="a@example.test", marketing_consent=ConsentStatus.OPTED_IN
    )
    engine = RetentionEngine()
    assert engine.plan(event(), profile) is not None
    assert engine.plan(event(), profile) is None
