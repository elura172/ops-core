"""The Postgres order repository: contract parity, contention, and the proofs.

Two guards replace SQLite's single writer lock, and each has a mutation test
that removes it and requires the resulting regression to be observed:

- the ``processed_events`` primary key (with its rowcount check) is what makes
  a raced duplicate report DUPLICATE; remove the check and the loser reports a
  fold it never performed, and the ledger no longer matches what happened.
- ``SELECT ... FOR UPDATE`` on the order row is what makes two different
  events for one order classify sequentially; remove it and a stale event can
  overwrite a newer status.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

import pytest

from app.db.sqlite import SQLiteOrderRepository
from app.domain.order_events import ApplyOutcome, Outcome, Resolution, classify
from app.domain.order_store import OrderEvent
from app.domain.orders import OrderStatus

WORKERS = 8


def event(event_id: str, status: OrderStatus, order_id: str = "o-1") -> OrderEvent:
    return OrderEvent(event_id, order_id, status, datetime.now(UTC))


@pytest.fixture(params=["sqlite", "postgres"])
def repository(request, tmp_path):
    if request.param == "sqlite":
        repo = SQLiteOrderRepository(tmp_path / "orders.db")
        yield repo
        repo.close()
    else:
        yield request.getfixturevalue("postgres_order_repository")


def test_backends_share_the_event_folding_contract(repository):
    assert repository.apply(event("e-1", OrderStatus.SHIPPED)).skipped == (
        OrderStatus.PAID,
        OrderStatus.FULFILLMENT_PENDING,
        OrderStatus.LABEL_CREATED,
    )
    assert repository.apply(event("e-1", OrderStatus.SHIPPED)).outcome is Outcome.DUPLICATE
    assert repository.apply(event("e-2", OrderStatus.PAID)).outcome is Outcome.STALE
    assert repository.apply(event("e-3", OrderStatus.SHIPPED)).outcome is Outcome.UNCHANGED
    assert repository.apply(event("e-4", OrderStatus.DELIVERED)).outcome is Outcome.APPLIED
    assert repository.apply(event("e-5", OrderStatus.CANCELLED)).outcome is Outcome.QUARANTINED

    quarantined = repository.quarantined()
    assert [row["event_id"] for row in quarantined] == ["e-5"]
    assert quarantined[0]["local_status"] == "DELIVERED"
    assert quarantined[0]["resolved"] == 0
    assert repository.resolve_quarantined("e-5") is True
    assert repository.resolve_quarantined("e-5") is False
    assert repository.quarantined() == []
    with pytest.raises(ValueError):
        repository.apply(OrderEvent("", "o-1", OrderStatus.PAID, datetime.now(UTC)))


def test_postgres_repository_is_durable(postgres_dsn, postgres_order_repository):
    from app.db.postgres_orders import PostgresOrderRepository

    assert postgres_order_repository.apply(event("e-1", OrderStatus.PAID)).applied
    reopened = PostgresOrderRepository(postgres_dsn)
    assert reopened.apply(event("e-1", OrderStatus.PAID)).outcome is Outcome.DUPLICATE
    reopened.close()


def _race(apply_calls: list, workers: int = WORKERS) -> list[ApplyOutcome]:
    """Run one apply-callable per worker, barrier-synchronized."""
    outcomes: list[ApplyOutcome] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(apply_calls))

    def run(call) -> None:
        barrier.wait()
        try:
            outcome = call()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            with lock:
                errors.append(exc)
        else:
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=(call,)) for call in apply_calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]
    return outcomes


def test_racing_duplicate_events_apply_once(postgres_dsn, postgres_order_repository):
    """Every worker holds its own connection pool, as separate processes would."""
    from app.db.postgres_orders import PostgresOrderRepository

    repos = [PostgresOrderRepository(postgres_dsn, pool_size=2) for _ in range(WORKERS)]
    try:
        outcomes = _race([lambda repo=repo: repo.apply(event("same-event", OrderStatus.PAID)) for repo in repos])
    finally:
        for repo in repos:
            repo.close()

    counted = [outcome.outcome for outcome in outcomes]
    assert counted.count(Outcome.APPLIED) == 1
    assert counted.count(Outcome.DUPLICATE) == WORKERS - 1


def test_racing_distinct_events_never_lose_the_newer_status(postgres_dsn, postgres_order_repository):
    """PAID and SHIPPED race on one order; SHIPPED must win every time.

    Whichever interleaving occurs, classification is serialized by the order-row
    lock: if PAID folds first SHIPPED fast-forwards over it, and if SHIPPED
    folds first PAID is stale. An order can never move backwards.
    """
    from app.db.postgres_orders import PostgresOrderRepository

    repos = [PostgresOrderRepository(postgres_dsn, pool_size=2) for _ in range(2)]
    try:
        for round_number in range(20):
            order_id = f"o-{round_number}"
            # Seed the row so the race is serialized by the row lock, not by
            # the orders primary-key insert.
            repos[0].apply(event(f"seed-{round_number}", OrderStatus.RECEIVED, order_id))
            _race(
                [
                    lambda r=repos[0], o=order_id, n=round_number: r.apply(event(f"paid-{n}", OrderStatus.PAID, o)),
                    lambda r=repos[1], o=order_id, n=round_number: r.apply(
                        event(f"shipped-{n}", OrderStatus.SHIPPED, o)
                    ),
                ]
            )
            with repos[0].pool.connection() as connection:
                row = connection.execute("SELECT status FROM orders WHERE order_id = %s", (order_id,)).fetchone()
            assert row["status"] == "SHIPPED", f"round {round_number}: a stale PAID overwrote SHIPPED"
    finally:
        for repo in repos:
            repo.close()


# --- the proofs that the guards are load-bearing ---------------------------


class NoDuplicateGuard:
    """apply() with the rowcount check on the processed_events INSERT removed."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def apply(self, event: OrderEvent) -> ApplyOutcome:
        with self._inner.pool.connection() as connection, connection.transaction():
            seen = connection.execute(
                "SELECT 1 FROM processed_events WHERE event_id = %s", (event.event_id,)
            ).fetchone()
            if seen:
                return ApplyOutcome(Outcome.DUPLICATE, event.order_id)
            connection.execute(
                "INSERT INTO orders (order_id, created_at, status, updated_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING",
                (event.order_id, event.occurred_at, "RECEIVED", event.occurred_at),
            )
            row = connection.execute(
                "SELECT status FROM orders WHERE order_id = %s FOR UPDATE", (event.order_id,)
            ).fetchone()
            decision = classify(OrderStatus(row["status"]), event.status)
            connection.execute(
                "INSERT INTO processed_events (event_id, order_id, status, occurred_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (event.event_id, event.order_id, event.status.value, event.occurred_at),
            )
            # No rowcount check: the fast path above is now the only duplicate
            # defense, and it read a snapshot from before the race resolved.
            if decision.resolution is Resolution.APPLY:
                connection.execute(
                    "UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s",
                    (event.status.value, event.occurred_at, event.order_id),
                )
                return ApplyOutcome(Outcome.APPLIED, event.order_id, event.status)
            return ApplyOutcome(Outcome.UNCHANGED, event.order_id)


def test_removing_the_duplicate_guard_misreports_the_race(postgres_dsn, postgres_order_repository):
    """Losers must say DUPLICATE. Without the rowcount check they classify
    against the winner's committed state and report UNCHANGED -- an outcome for
    a fold they never performed -- which is exactly the corruption that makes
    ``test_racing_duplicate_events_apply_once`` fail."""
    from app.db.postgres_orders import PostgresOrderRepository

    repos = [PostgresOrderRepository(postgres_dsn, pool_size=2) for _ in range(WORKERS)]
    try:
        misreported = False
        for round_number in range(10):
            order_id = f"dup-order-{round_number}"
            repos[0].apply(event(f"seed-dup-{round_number}", OrderStatus.RECEIVED, order_id))
            outcomes = _race(
                [
                    lambda repo=repo, n=round_number, o=order_id: NoDuplicateGuard(repo).apply(
                        event(f"dup-{n}", OrderStatus.PAID, o)
                    )
                    for repo in repos
                ]
            )
            counted = [o.outcome for o in outcomes]
            if counted.count(Outcome.DUPLICATE) != WORKERS - 1:
                misreported = True
                break
    finally:
        for repo in repos:
            repo.close()

    assert misreported, (
        "the unguarded apply still reported every raced duplicate correctly; "
        "the race harness can no longer detect a missing duplicate guard"
    )


class NoRowLock:
    """apply() with FOR UPDATE removed: classification reads an unlocked row."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def apply(self, event: OrderEvent) -> ApplyOutcome:
        with self._inner.pool.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO orders (order_id, created_at, status, updated_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING",
                (event.order_id, event.occurred_at, "RECEIVED", event.occurred_at),
            )
            row = connection.execute("SELECT status FROM orders WHERE order_id = %s", (event.order_id,)).fetchone()
            decision = classify(OrderStatus(row["status"]), event.status)
            if decision.resolution is Resolution.APPLY:
                connection.execute(
                    "UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s",
                    (event.status.value, event.occurred_at, event.order_id),
                )
                return ApplyOutcome(Outcome.APPLIED, event.order_id, event.status)
            return ApplyOutcome(Outcome.STALE, event.order_id)


def test_removing_the_row_lock_loses_updates(postgres_dsn, postgres_order_repository):
    from app.db.postgres_orders import PostgresOrderRepository

    repos = [PostgresOrderRepository(postgres_dsn, pool_size=2) for _ in range(2)]
    try:
        regressed = False
        for round_number in range(50):
            order_id = f"unlocked-{round_number}"
            repos[0].apply(event(f"seed-{order_id}", OrderStatus.RECEIVED, order_id))
            _race(
                [
                    lambda r=repos[0], o=order_id: NoRowLock(r).apply(event(f"paid-{o}", OrderStatus.PAID, o)),
                    lambda r=repos[1], o=order_id: NoRowLock(r).apply(event(f"shipped-{o}", OrderStatus.SHIPPED, o)),
                ]
            )
            with repos[0].pool.connection() as connection:
                row = connection.execute("SELECT status FROM orders WHERE order_id = %s", (order_id,)).fetchone()
            if row["status"] == "PAID":
                regressed = True
                break
    finally:
        for repo in repos:
            repo.close()

    assert regressed, (
        "the unlocked apply never lost an update; the race harness can no longer detect a missing row lock"
    )
