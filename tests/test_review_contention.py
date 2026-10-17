"""Prove at-most-once execution under contention, and prove the proof.

The first half hammers one approved item with barrier-synchronized workers and
asserts the side effect happened exactly once. The second half re-runs the same
harness against a store whose conditional ``UPDATE ... WHERE status = 'APPROVED'``
has been stripped -- the exact mutation a careless refactor would make -- and
asserts the harness catches the resulting double execution. If the harness ever
stops detecting that, these tests fail; the at-most-once test is known to be
load-bearing, not merely green.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from app.db.review_store import SQLiteReviewStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewStatus

WORKERS = 12


class CountingClient:
    dry_run = True

    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def update_inventory(self, changes: dict[str, int]) -> dict[str, Any]:
        with self.lock:
            self.calls += 1
        return {"dry_run": True, "changes": changes}


@dataclass
class RaceResult:
    client_calls: int
    executions: int
    replays: int
    errors: list[Exception] = field(default_factory=list)


def race(store: Any, review_id: str, workers: int = WORKERS) -> RaceResult:
    """Barrier-start ``workers`` executors against one approved item."""
    client = CountingClient()
    executor = ApprovedActionExecutor(client)
    outcomes: list[dict[str, Any]] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(workers)

    def worker() -> None:
        barrier.wait()
        try:
            outcome = executor.execute(store, review_id, actor="grace")
        except Exception as exc:  # noqa: BLE001 - recorded and asserted by callers
            with lock:
                errors.append(exc)
        else:
            with lock:
                outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return RaceResult(
        client_calls=client.calls,
        executions=len([o for o in outcomes if not o["replayed"]]),
        replays=len([o for o in outcomes if o["replayed"]]),
        errors=errors,
    )


def seed(store: Any, review_id: str) -> None:
    store.submit(ReviewItem(review_id, "update_inventory", {"changes": {"sku-1": 3}}))
    store.decide(review_id, approve=True, reviewer="ada")


# --- the guarantee ---------------------------------------------------------


@pytest.mark.parametrize("round_number", range(5))
def test_postgres_executes_exactly_once_under_contention(postgres_store, round_number):
    review_id = f"race-{round_number}"
    seed(postgres_store, review_id)
    result = race(postgres_store, review_id)

    assert result.client_calls == 1
    assert result.executions == 1
    # Losers either replay the stored result or are refused mid-flight; both are
    # safe. What is never allowed is a second client call.
    assert result.replays + len(result.errors) == WORKERS - 1
    assert all(isinstance(exc, ValueError) for exc in result.errors)
    assert postgres_store.get(review_id).status is ReviewStatus.EXECUTED


def test_sqlite_executes_exactly_once_under_contention(tmp_path):
    store = SQLiteReviewStore(tmp_path / "reviews.db")
    seed(store, "race-1")
    result = race(store, "race-1")

    assert result.client_calls == 1
    assert result.executions == 1
    assert all(isinstance(exc, ValueError) for exc in result.errors)
    assert store.get("race-1").status is ReviewStatus.EXECUTED
    store.close()


# --- the proof that the guarantee is load-bearing --------------------------


class UnguardedPostgresClaim:
    """Wrap a PostgresReviewStore, replaying its claim without the predicate.

    This is ``claim_for_execution`` with ``AND status = 'APPROVED'`` deleted --
    the read still validates, but the write no longer proves anything. Every
    racer that read APPROVED before the winner committed will claim.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._read_barrier = threading.Barrier(WORKERS)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem:
        from app.review.queue import _validate_claim

        with self._inner._transaction() as connection:
            item = self._inner._fetch(connection, review_id, tenant_id=tenant_id)
            _validate_claim(item)
            self._read_barrier.wait(timeout=10)
            connection.execute(
                "UPDATE reviews SET status = %s, executed_by = %s, executed_at = %s "
                "WHERE review_id = %s AND tenant_id = %s",
                (ReviewStatus.EXECUTING.value, actor, datetime.now(UTC), review_id, tenant_id),
            )
            return self._inner._fetch(connection, review_id, tenant_id=tenant_id)


def test_removing_the_conditional_update_breaks_at_most_once(postgres_store):
    """The mutation the docstring warns about, kept alive so the race test
    provably detects it. If this test starts failing, the harness has lost the
    ability to catch double execution and the green run above means nothing."""
    double_fired = False
    for round_number in range(10):
        review_id = f"unguarded-{round_number}"
        seed(postgres_store, review_id)
        result = race(UnguardedPostgresClaim(postgres_store), review_id)
        if result.client_calls > 1:
            double_fired = True
            break

    assert double_fired, (
        "the unguarded claim never double-executed; the contention harness "
        "can no longer detect a missing conditional update"
    )


class UnguardedSQLiteClaim(SQLiteReviewStore):
    """SQLite claim without the in-transaction validate or the predicate.

    SQLite has two layers (BEGIN IMMEDIATE serializes writers, then the
    conditional UPDATE); the naive rewrite loses both, and the harness must
    catch that too.
    """

    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem:
        # Read outside the write transaction: the classic check-then-act race.
        item = self.get(review_id, tenant_id=tenant_id)
        if item is None:
            raise KeyError(review_id)
        if item.status is not ReviewStatus.EXECUTING:
            from app.review.queue import _validate_claim

            _validate_claim(item)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE reviews SET status = ?, executed_by = ?, executed_at = ? WHERE review_id = ? AND tenant_id = ?",
                (
                    ReviewStatus.EXECUTING.value,
                    actor,
                    datetime.now(UTC).isoformat(),
                    review_id,
                    tenant_id,
                ),
            )
            return self._fetch(connection, review_id, tenant_id=tenant_id)


def test_removing_sqlite_guards_breaks_at_most_once(tmp_path):
    double_fired = False
    for round_number in range(10):
        store = UnguardedSQLiteClaim(tmp_path / f"unguarded-{round_number}.db")
        review_id = "unguarded"
        seed(store, review_id)
        result = race(store, review_id)
        store.close()
        if result.client_calls > 1:
            double_fired = True
            break

    assert double_fired, (
        "the unguarded SQLite claim never double-executed; the contention harness can no longer detect a missing guard"
    )
