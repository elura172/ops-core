import pytest

from app.api.sandbox import SandboxTransport
from app.api.signing import sign_request
from app.domain.audit import AuditLog
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewStatus


def test_signing_is_deterministic():
    assert sign_request("secret", "/product", {"b": "2", "a": "1"}) == sign_request("secret", "/product", {"a": "1", "b": "2"})


def test_sandbox_captures_request_without_network():
    transport = SandboxTransport()
    result = transport.post("/product", {"shop_id": "shop"}, {"status": "DRAFT"}, "sig")
    assert result["sandbox"]
    assert transport.requests[0]["path"] == "/product"


def test_executor_requires_approval_and_audits():
    class Client:
        def create_product_draft(self, payload): return {"dry_run": True, "payload": payload}
    audit = AuditLog()
    executor = ApprovedActionExecutor(Client(), audit)
    pending = ReviewItem("r-1", "create_product_draft", {"title": "Box"})
    with pytest.raises(PermissionError):
        executor.execute(pending)
    pending.status = ReviewStatus.APPROVED
    pending.reviewer = "owner"
    assert executor.execute(pending)["dry_run"]
    assert audit.events[0].resource_id == "r-1"
