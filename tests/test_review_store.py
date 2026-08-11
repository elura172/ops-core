import threading
from datetime import timedelta

import pytest

from app.db.review_store import SQLiteReviewStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import AlreadyExecuted, ReviewItem, ReviewQueue, ReviewStatus


class RecordingClient:
    dry_run = True

    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()

    def update_inventory(self, changes):
        with self.lock:
            self.calls += 1
        return {"dry_run": True, "changes": changes}


def seeded(store, review_id="r-1"):
    store.submit(ReviewItem(review_id, "update_inventory", {"changes": {"sku-1": 3}}))
    store.decide(review_id, approve=True, reviewer="ada")
    return store


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "memory":
        yield ReviewQueue()
    elif request.param == "sqlite":
        sqlite_store = SQLiteReviewStore(tmp_path / "reviews.db")
        yield sqlite_store
        sqlite_store.close()
    else:
        # Resolved lazily so the memory and sqlite runs never need a server.
        yield request.getfixturevalue("postgres_store")


def test_both_stores_share_the_decision_contract(store):
    seeded(store)
    assert store.get("r-1").status is ReviewStatus.APPROVED
    assert store.get("r-1").reviewer == "ada"
    with pytest.raises(ValueError):
        store.decide("r-1", approve=True, reviewer="ada")
    with pytest.raises(ValueError):
        store.decide("r-1", approve=True, reviewer="   ")
    with pytest.raises(KeyError):
        store.decide("missing", approve=True, reviewer="ada")


def test_both_stores_reject_a_second_claim(store):
    seeded(store)
    store.claim_for_execution("r-1", "grace")
    with pytest.raises(ValueError):
        store.claim_for_execution("r-1", "grace")
    store.complete_execution("r-1", {"ok": True})
    with pytest.raises(AlreadyExecuted):
        store.claim_for_execution("r-1", "grace")


def test_both_stores_make_failure_terminal(store):
    seeded(store)
    store.claim_for_execution("r-1", "grace")
    store.fail_execution("r-1", "upstream down")
    assert store.get("r-1").status is ReviewStatus.FAILED
    with pytest.raises(PermissionError):
        store.claim_for_execution("r-1", "grace")


def test_pending_only_listing(store):
    store.submit(ReviewItem("r-1", "update_inventory", {"changes": {}}))
    store.submit(ReviewItem("r-2", "update_inventory", {"changes": {}}))
    store.decide("r-2", approve=False, reviewer="ada")
    assert [i.review_id for i in store.list_items(pending_only=True)] == ["r-1"]
    assert len(store.list_items()) == 2


def test_duplicate_review_id_is_refused(store):
    store.submit(ReviewItem("r-1", "update_inventory", {"changes": {}}))
    with pytest.raises(ValueError):
        store.submit(ReviewItem("r-1", "update_inventory", {"changes": {}}))


def test_approval_survives_a_restart(tmp_path):
    path = tmp_path / "reviews.db"
    seeded(SQLiteReviewStore(path)).close()

    reopened = SQLiteReviewStore(path)
    client = RecordingClient()
    executor = ApprovedActionExecutor(client)
    assert executor.execute(reopened, "r-1", actor="grace")["replayed"] is False
    reopened.close()

    # A restart must not hand the same approved mutation to the client again.
    after_restart = SQLiteReviewStore(path)
    assert after_restart.get("r-1").status is ReviewStatus.EXECUTED
    assert executor.execute(after_restart, "r-1", actor="grace")["replayed"] is True
    assert client.calls == 1
    after_restart.close()


def test_racing_workers_execute_the_mutation_once(tmp_path):
    store = SQLiteReviewStore(tmp_path / "reviews.db")
    seeded(store)
    client = RecordingClient()
    executor = ApprovedActionExecutor(client)
    results: list[dict] = []
    errors: list[Exception] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        try:
            outcome = executor.execute(store, "r-1", actor="grace")
        except Exception as exc:  # noqa: BLE001 - recorded and asserted below
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert client.calls == 1
    assert len([r for r in results if not r["replayed"]]) == 1
    # Losers either replay the stored result or are told it is mid-flight; never a
    # second call to the client.
    assert all(isinstance(exc, ValueError) for exc in errors)
    assert store.get("r-1").status is ReviewStatus.EXECUTED
    store.close()


def test_audit_trail_is_written_in_the_same_transaction(tmp_path):
    store = SQLiteReviewStore(tmp_path / "reviews.db")
    seeded(store)
    ApprovedActionExecutor(RecordingClient()).execute(store, "r-1", actor="grace")
    store.close()

    trail = SQLiteReviewStore(tmp_path / "reviews.db").audit_trail("r-1")
    assert [row["action"] for row in trail] == ["review.decide", "approved_action.execute"]
    assert trail[0]["actor"] == "ada"
    assert trail[1]["actor"] == "grace"
    assert '"approved_by": "ada"' in trail[1]["detail"]


def test_abandoned_execution_is_recovered_as_failed(store):
    seeded(store)
    store.claim_for_execution("r-1", "grace")
    assert store.recover_stale_executions(timedelta(seconds=-1))[0].review_id == "r-1"
    assert store.get("r-1").status is ReviewStatus.FAILED
    # Recovery means a human re-approves; it never retries on its own.
    with pytest.raises(PermissionError):
        store.claim_for_execution("r-1", "grace")
