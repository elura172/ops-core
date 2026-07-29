import pytest

from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.domain.audit import AuditLog
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewQueue


def service():
    queue = ReviewQueue()
    queue.submit(ReviewItem("r-1", "update_inventory", {"changes": {"sku-1": 3}}))
    client = TikTokClient("token", "shop", dry_run=True)
    return OperatorService(queue, ApprovedActionExecutor(client, AuditLog()))


def test_operator_health_and_pending_reviews():
    operator = service()
    assert operator.health() == {"status": "ok", "mode": "dry-run"}
    assert operator.list_reviews(pending_only=True)[0]["review_id"] == "r-1"


def test_operator_decide_then_execute():
    operator = service()
    operator.decide("r-1", True, "owner", "inventory verified")
    result = operator.execute("r-1", actor="grace")
    assert result["result"]["dry_run"] is True
    assert result["status"] == "EXECUTED"
    assert result["executed_by"] == "grace"


def test_operator_rejects_unknown_review():
    with pytest.raises(KeyError):
        service().execute("missing", actor="grace")
