import hashlib
import hmac
import threading
from datetime import UTC, datetime

from app.api.webhook_service import handle_order_webhook
from app.db.sqlite import SQLiteOrderRepository
from app.domain.order_events import Outcome
from app.domain.order_store import OrderEvent
from app.domain.orders import OrderStatus


def event(event_id="e-1", status=OrderStatus.PAID, order_id="o-1"):
    return OrderEvent(event_id, order_id, status, datetime.now(UTC))


def test_sqlite_repository_is_durable(tmp_path):
    path = tmp_path / "orders.db"
    repo = SQLiteOrderRepository(path)
    assert repo.apply(event()).outcome is Outcome.APPLIED
    repo.close()
    reopened = SQLiteOrderRepository(path)
    assert reopened.apply(event()).outcome is Outcome.DUPLICATE
    reopened.close()


def test_sqlite_matches_in_memory_semantics(tmp_path):
    repo = SQLiteOrderRepository(tmp_path / "orders.db")
    assert repo.apply(event("e-1", OrderStatus.SHIPPED)).skipped == (
        OrderStatus.PAID,
        OrderStatus.FULFILLMENT_PENDING,
        OrderStatus.LABEL_CREATED,
    )
    assert repo.apply(event("e-2", OrderStatus.PAID)).outcome is Outcome.STALE
    assert repo.apply(event("e-3", OrderStatus.DELIVERED)).outcome is Outcome.APPLIED
    assert repo.apply(event("e-4", OrderStatus.CANCELLED)).outcome is Outcome.QUARANTINED
    quarantined = repo.quarantined()
    assert [row["event_id"] for row in quarantined] == ["e-4"]
    assert quarantined[0]["local_status"] == "DELIVERED"
    repo.close()


def test_concurrent_writers_apply_each_event_once(tmp_path):
    """The duplicate check and the write must not interleave across threads."""
    repo = SQLiteOrderRepository(tmp_path / "orders.db")
    outcomes: list[Outcome] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        outcome = repo.apply(event("same-event", OrderStatus.PAID))
        with lock:
            outcomes.append(outcome.outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(Outcome.APPLIED) == 1
    assert outcomes.count(Outcome.DUPLICATE) == 7
    repo.close()


def test_webhook_handler_verifies_and_deduplicates():
    body = b'{"event_id":"e-1","order_id":"o-1","status":"PAID"}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    repo = SQLiteOrderRepository(":memory:")

    first = handle_order_webhook(body, signature, "secret", repo)
    assert first.status == 200
    assert first.body["outcome"] == "applied"

    second = handle_order_webhook(body, signature, "secret", repo)
    assert second.status == 200
    assert second.body["outcome"] == "duplicate"

    forged = handle_order_webhook(body, "bad", "secret", repo)
    assert forged.status == 401
    assert forged.body["accepted"] is False
    repo.close()
