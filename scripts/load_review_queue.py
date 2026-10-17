"""Load and contention harness for the review queue and the order pipeline.

Four phases, each answering one question:

1. throughput -- N approved items executed by W workers, every worker holding
   its own store (own connection/pool, as separate processes would). Reports
   wall-clock throughput and client-side execute latency percentiles.
2. contention -- C workers barrier-race every item. The run aborts unless each
   item executed its side effect exactly once; the numbers show what losing a
   race costs.
3. alert -- a claim is deliberately abandoned, stale recovery runs, and the
   StaleExecutionRecovered alert must fire. The firing is printed as evidence.
4. order_events -- the webhook ingest path: a shuffled stream of lifecycle
   events (with replays) folded by W workers. The run aborts unless every
   order lands on its terminal status and every replay reports duplicate.

Usage:
    python -m scripts.load_review_queue sqlite [path]
    python -m scripts.load_review_queue postgres postgresql://ops@127.0.0.1:5432/ops_core
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.observability import metrics
from app.observability.alerts import AlertManager
from app.observability.logging import configure_logging
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewStatus

ITEMS = 1000
WORKERS = 8
CONTENTION_ITEMS = 50
CONTENDERS = 12
ORDERS = 250  # x4 events each (PAID, SHIPPED, DELIVERED, one replay) = 1000 ops


class CountingClient:
    dry_run = True

    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def update_inventory(self, changes: dict[str, int]) -> dict[str, Any]:
        with self.lock:
            self.calls += 1
        return {"dry_run": True, "changes": changes}


def percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def at(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))] * 1000

    return {
        "p50_ms": at(0.50),
        "p95_ms": at(0.95),
        "p99_ms": at(0.99),
        "max_ms": ordered[-1] * 1000,
        "mean_ms": statistics.fmean(ordered) * 1000,
    }


def open_backend(backend: str, target: str) -> Callable[[], Any]:
    """Return a factory producing one independent store per worker."""
    if backend == "postgres":
        from app.db.migrate import migrate
        from app.db.postgres import PostgresReviewStore

        migrate(target)
        seed_store = PostgresReviewStore(target)
        with seed_store.pool.connection() as connection:
            connection.execute("TRUNCATE reviews, audit_events")
        seed_store.close()
        return lambda: PostgresReviewStore(target, pool_size=4)
    if backend == "sqlite":
        from app.db.review_store import SQLiteReviewStore

        Path(target).unlink(missing_ok=True)
        return lambda: SQLiteReviewStore(target)
    raise SystemExit(f"unknown backend: {backend}")


def phase_throughput(store_factory: Callable[[], Any]) -> dict[str, Any]:
    seeder = store_factory()
    for index in range(ITEMS):
        seeder.submit(ReviewItem(f"load-{index}", "update_inventory", {"changes": {"sku": 1}}))
        seeder.decide(f"load-{index}", approve=True, reviewer="ada")

    client = CountingClient()
    work: list[str] = [f"load-{index}" for index in range(ITEMS)]
    work_lock = threading.Lock()
    latencies: list[float] = []
    latency_lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker() -> None:
        store = store_factory()
        executor = ApprovedActionExecutor(client)
        barrier.wait()
        while True:
            with work_lock:
                if not work:
                    break
                review_id = work.pop()
            started = time.monotonic()
            executor.execute(store, review_id, actor="grace")
            elapsed = time.monotonic() - started
            with latency_lock:
                latencies.append(elapsed)
        store.close()

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.monotonic() - started

    executed = sum(1 for index in range(ITEMS) if seeder.get(f"load-{index}").status is ReviewStatus.EXECUTED)
    assert executed == ITEMS, f"only {executed}/{ITEMS} executed"
    assert client.calls == ITEMS, f"side effect ran {client.calls} times for {ITEMS} items"
    seeder.close()
    return {
        "items": ITEMS,
        "workers": WORKERS,
        "wall_seconds": round(wall, 3),
        "executions_per_second": round(ITEMS / wall, 1),
        **{key: round(value, 2) for key, value in percentiles(latencies).items()},
    }


def phase_contention(store_factory: Callable[[], Any]) -> dict[str, Any]:
    seeder = store_factory()
    stores = [store_factory() for _ in range(CONTENDERS)]
    client = CountingClient()
    executors = [ApprovedActionExecutor(client) for _ in range(CONTENDERS)]
    losses = {"replayed": 0, "rejected": 0}
    losses_lock = threading.Lock()

    for index in range(CONTENTION_ITEMS):
        review_id = f"race-{index}"
        seeder.submit(ReviewItem(review_id, "update_inventory", {"changes": {"sku": 1}}))
        seeder.decide(review_id, approve=True, reviewer="ada")
        barrier = threading.Barrier(CONTENDERS)

        def contend(slot: int, review_id: str = review_id, barrier: threading.Barrier = barrier):
            barrier.wait()
            try:
                outcome = executors[slot].execute(stores[slot], review_id, actor=f"worker-{slot}")
            except (ValueError, PermissionError):
                with losses_lock:
                    losses["rejected"] += 1
            else:
                if outcome["replayed"]:
                    with losses_lock:
                        losses["replayed"] += 1

        threads = [threading.Thread(target=contend, args=(slot,)) for slot in range(CONTENDERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    expected_losses = CONTENTION_ITEMS * (CONTENDERS - 1)
    assert client.calls == CONTENTION_ITEMS, (
        f"at-most-once violated: {client.calls} side effects for {CONTENTION_ITEMS} items"
    )
    assert losses["replayed"] + losses["rejected"] == expected_losses
    for store in stores:
        store.close()
    seeder.close()
    return {
        "items": CONTENTION_ITEMS,
        "contenders_per_item": CONTENDERS,
        "side_effects": client.calls,
        "losers_replayed_result": losses["replayed"],
        "losers_refused_mid_flight": losses["rejected"],
    }


def open_order_backend(backend: str, target: str) -> Callable[[], Any]:
    if backend == "postgres":
        from app.db.migrate import migrate
        from app.db.postgres_orders import PostgresOrderRepository

        migrate(target)
        seed = PostgresOrderRepository(target)
        with seed.pool.connection() as connection:
            connection.execute("TRUNCATE orders, processed_events, quarantined_events")
        seed.close()
        return lambda: PostgresOrderRepository(target, pool_size=4)
    from app.db.sqlite import SQLiteOrderRepository

    orders_path = str(Path(target).with_suffix(".orders.db"))
    Path(orders_path).unlink(missing_ok=True)
    return lambda: SQLiteOrderRepository(orders_path)


def phase_order_events(backend: str, target: str) -> dict[str, Any]:
    from app.domain.order_events import Outcome
    from app.domain.order_store import OrderEvent
    from app.domain.orders import OrderStatus

    repository_factory = open_order_backend(backend, target)

    def make_event(order: int, name: str, status: OrderStatus) -> OrderEvent:
        return OrderEvent(f"{name}-{order}", f"order-{order}", status, datetime.now(UTC))

    stream: list[OrderEvent] = []
    for order in range(ORDERS):
        stream.append(make_event(order, "paid", OrderStatus.PAID))
        stream.append(make_event(order, "shipped", OrderStatus.SHIPPED))
        stream.append(make_event(order, "delivered", OrderStatus.DELIVERED))
        stream.append(make_event(order, "paid", OrderStatus.PAID))  # replayed delivery
    random.Random(42).shuffle(stream)

    outcomes: dict[str, int] = {}
    latencies: list[float] = []
    lock = threading.Lock()
    barrier = threading.Barrier(WORKERS)

    def worker() -> None:
        repository = repository_factory()
        barrier.wait()
        while True:
            with lock:
                if not stream:
                    break
                order_event = stream.pop()
            started = time.monotonic()
            outcome = repository.apply(order_event)
            elapsed = time.monotonic() - started
            with lock:
                outcomes[outcome.outcome.value] = outcomes.get(outcome.outcome.value, 0) + 1
                latencies.append(elapsed)
        repository.close()

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.monotonic() - started

    checker = repository_factory()
    if backend == "postgres":
        with checker.pool.connection() as connection:
            rows = connection.execute("SELECT status FROM orders").fetchall()
            statuses = [row["status"] for row in rows]
    else:
        with checker._lock:  # noqa: SLF001 - harness-only introspection
            rows = checker.connection.execute("SELECT status FROM orders").fetchall()
            statuses = [row["status"] for row in rows]
    checker.close()

    total = ORDERS * 4
    assert len(statuses) == ORDERS, f"{len(statuses)} orders for {ORDERS} expected"
    assert all(status == "DELIVERED" for status in statuses), "an order lost its terminal status"
    assert outcomes.get(Outcome.DUPLICATE.value, 0) == ORDERS, f"replays misreported: {outcomes}"
    return {
        "events": total,
        "orders": ORDERS,
        "workers": WORKERS,
        "wall_seconds": round(wall, 3),
        "events_per_second": round(total / wall, 1),
        "outcomes": outcomes,
        **{key: round(value, 2) for key, value in percentiles(latencies).items()},
    }


def phase_alert(store_factory: Callable[[], Any]) -> dict[str, Any]:
    store = store_factory()
    manager = AlertManager()
    manager.evaluate()  # baseline: nothing may fire before the incident

    store.submit(ReviewItem("abandoned-1", "update_inventory", {"changes": {"sku": 1}}))
    store.decide("abandoned-1", approve=True, reviewer="ada")
    store.claim_for_execution("abandoned-1", "worker-that-dies")
    # The worker is now dead. The watchdog finds the abandoned claim:
    recovered = store.recover_stale_executions(timedelta(seconds=0))
    metrics.stale_recoveries_total().inc(len(recovered))
    fired = manager.evaluate()
    assert [firing.rule for firing in fired] == ["StaleExecutionRecovered"], "the stale-recovery alert did not fire"
    store.close()
    firing = fired[0]
    return {
        "rule": firing.rule,
        "severity": firing.severity,
        "fired_at": firing.fired_at.isoformat(),
        "recovered_review_ids": [item.review_id for item in recovered],
        "message": firing.message,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    backend = sys.argv[1]
    if backend == "postgres":
        if len(sys.argv) != 3:
            print("postgres needs a DSN", file=sys.stderr)
            return 2
        target = sys.argv[2]
    else:
        target = sys.argv[2] if len(sys.argv) > 2 else tempfile.mktemp(suffix=".db")

    configure_logging(stream=sys.stderr)
    store_factory = open_backend(backend, target)
    report = {
        "backend": backend,
        "throughput": phase_throughput(store_factory),
        "contention": phase_contention(store_factory),
        "alert_firing": phase_alert(store_factory),
        "order_events": phase_order_events(backend, target),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
