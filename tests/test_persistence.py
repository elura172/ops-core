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


def test_migrations_discovered_include_multi_tenant():
    from app.db.migrate import discover

    migrations = discover()
    assert len(migrations) >= 5
    assert migrations[4].version == 5
    assert migrations[4].name == "multi_tenant_partitioning"
    assert "CREATE TABLE IF NOT EXISTS tenants" in migrations[4].sql()


def test_open_user_store_sqlite(tmp_path):
    from app.db.factory import open_user_store

    store = open_user_store(tmp_path / "users.db")
    tid = store.create_tenant("Acme Books")
    assert tid > 0
    uid = store.create_user(tid, "user@acme.com", "hash123")
    assert uid > 0
    u = store.get_user_by_email("user@acme.com")
    assert u is not None
    assert u.tenant_id == tid
    store.close()


def test_postgres_dsn_check_accepts_path_values():
    from pathlib import Path

    from app.db.factory import is_postgres_dsn

    assert not is_postgres_dsn(Path("local.db"))
