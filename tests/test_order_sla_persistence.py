from datetime import UTC, datetime, timedelta

import pytest

from app.db.sqlite import SCHEMA, SQLiteOrderRepository
from app.domain.order_store import OrderEvent, OrderStore
from app.domain.orders import OrderStatus
from app.workers.sla_watchdog import SLAWatchdog


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def repository(request, tmp_path):
    if request.param == "memory":
        yield OrderStore()
    elif request.param == "sqlite":
        repo = SQLiteOrderRepository(tmp_path / "orders.db")
        yield repo
        repo.close()
    else:
        yield request.getfixturevalue("postgres_order_repository")


def event(event_id, status, order_id="order"):
    return OrderEvent(event_id, order_id, status, datetime.now(UTC) - timedelta(days=2))


def test_late_first_event_is_excluded_from_sla(repository):
    repository.apply(event("late", OrderStatus.FULFILLMENT_PENDING))
    repository.apply(event("earlier", OrderStatus.PAID))
    orders = repository.active()
    assert len(orders) == 1
    assert orders[0].created_at_estimated is True
    alerts = []
    assert SLAWatchdog().audit(orders, alerts.append) == []
    assert alerts == []


def test_known_first_event_stays_eligible_after_advancing(repository):
    repository.apply(event("first", OrderStatus.RECEIVED))
    repository.apply(event("next", OrderStatus.FULFILLMENT_PENDING))
    orders = repository.active()
    assert orders[0].created_at_estimated is False
    alerts = []
    SLAWatchdog().audit(orders, alerts.append)
    assert len(alerts) == 1
    assert alerts[0].severity == "CRITICAL"


def test_terminal_orders_are_excluded_from_active(repository):
    repository.apply(event("delivered", OrderStatus.DELIVERED, "done"))
    repository.apply(event("cancelled", OrderStatus.CANCELLED, "cancelled"))
    repository.apply(event("paid", OrderStatus.PAID, "active"))
    assert [order.order_id for order in repository.active()] == ["active"]


def test_sqlite_preserves_provenance_after_restart(tmp_path):
    path = tmp_path / "orders.db"
    repo = SQLiteOrderRepository(path)
    repo.apply(event("late", OrderStatus.LABEL_CREATED, "estimated"))
    repo.apply(event("first", OrderStatus.RECEIVED, "known"))
    repo.close()
    reopened = SQLiteOrderRepository(path)
    try:
        assert {o.order_id: o.created_at_estimated for o in reopened.active()} == {
            "estimated": True,
            "known": False,
        }
    finally:
        reopened.close()


def test_sqlite_legacy_upgrade_is_conservative_and_idempotent(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    timestamp = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO orders(order_id, created_at, status, updated_at) VALUES (?, ?, ?, ?)",
        ("legacy", timestamp, "FULFILLMENT_PENDING", timestamp),
    )
    connection.commit()
    connection.close()
    for _ in range(2):
        repo = SQLiteOrderRepository(path)
        try:
            assert repo.active()[0].created_at_estimated is True
        finally:
            repo.close()
