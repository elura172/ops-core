import pytest

from app.api.sandbox import SandboxTransport
from app.api.signing import sign_request
from app.domain.audit import AuditLog
from app.review.executor import ApprovedActionExecutor, ExecutionFailed
from app.review.queue import ReviewItem, ReviewQueue, ReviewStatus


def test_signing_is_deterministic():
    assert sign_request("secret", "/product", {"b": "2", "a": "1"}) == sign_request(
        "secret", "/product", {"a": "1", "b": "2"}
    )


def test_sandbox_captures_request_without_network():
    transport = SandboxTransport()
    result = transport.post("/product", {"shop_id": "shop"}, {"status": "DRAFT"}, "sig")
    assert result["sandbox"]
    assert transport.requests[0]["path"] == "/product"


class RecordingClient:
    def __init__(self):
        self.calls = 0

    def create_product_draft(self, payload):
        self.calls += 1
        return {"dry_run": True, "payload": payload}


def approved_store(review_id="r-1", operation="create_product_draft"):
    store = ReviewQueue()
    store.submit(ReviewItem(review_id, operation, {"title": "Box"}))
    store.decide(review_id, approve=True, reviewer="owner")
    return store


def test_executor_requires_approval_and_audits():
    audit = AuditLog()
    executor = ApprovedActionExecutor(RecordingClient(), audit)
    pending = ReviewQueue()
    pending.submit(ReviewItem("r-1", "create_product_draft", {"title": "Box"}))
    with pytest.raises(PermissionError):
        executor.execute(pending, "r-1", actor="grace")

    store = approved_store()
    assert executor.execute(store, "r-1", actor="grace")["result"]["dry_run"]
    assert audit.events[0].resource_id == "r-1"


def test_executor_never_runs_the_mutation_twice():
    client = RecordingClient()
    executor = ApprovedActionExecutor(client, AuditLog())
    store = approved_store()
    first = executor.execute(store, "r-1", actor="grace")
    second = executor.execute(store, "r-1", actor="grace")
    assert client.calls == 1
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["result"] == first["result"]
    assert store.get("r-1").status is ReviewStatus.EXECUTED


def test_failed_execution_is_terminal_and_requires_reapproval():
    class Failing:
        def create_product_draft(self, payload):
            raise RuntimeError("upstream rejected the draft")

    executor = ApprovedActionExecutor(Failing(), AuditLog())
    store = approved_store()
    with pytest.raises(ExecutionFailed):
        executor.execute(store, "r-1", actor="grace")
    item = store.get("r-1")
    assert item.status is ReviewStatus.FAILED
    assert item.failure == "upstream rejected the draft"
    with pytest.raises(PermissionError):
        executor.execute(store, "r-1", actor="grace")


def test_unallowlisted_operation_does_not_consume_the_approval():
    store = approved_store("r-2", "delete_shop")
    with pytest.raises(ValueError):
        ApprovedActionExecutor(RecordingClient(), AuditLog()).execute(store, "r-2", actor="grace")
    assert store.get("r-2").status is ReviewStatus.APPROVED


def test_unknown_review_raises_key_error():
    with pytest.raises(KeyError):
        ApprovedActionExecutor(RecordingClient()).execute(ReviewQueue(), "missing", actor="grace")
