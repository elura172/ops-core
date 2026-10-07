import hashlib
import hmac
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.security import TokenRegistry
from app.api.tiktok_client import TikTokClient
from app.domain.audit import AuditLog
from app.domain.order_store import OrderStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewQueue
from tests.conftest import ADMIN_TOKEN, OPERATOR_TOKEN, REVIEWER_TOKEN, WEBHOOK_SECRET, auth


@pytest.fixture
def client(settings):
    queue = ReviewQueue()
    queue.submit(ReviewItem("r-1", "update_inventory", {"changes": {"sku-1": 3}}))
    operator = OperatorService(
        queue,
        ApprovedActionExecutor(TikTokClient("token", "shop", dry_run=True), AuditLog()),
    )
    return TestClient(
        create_app(operator=operator, order_repository=OrderStore(), settings=settings)
    )


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> dict[str, str]:
    return {"X-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def test_health_needs_no_credentials(client):
    assert client.get("/health").json()["status"] == "ok"


def test_review_endpoints_reject_anonymous_callers(client):
    assert client.get("/reviews").status_code == 401
    assert client.post("/reviews/r-1/decision", json={"approve": True}).status_code == 401
    assert client.post("/reviews/r-1/execute").status_code == 401


def test_review_endpoints_reject_unknown_token(client):
    assert client.get("/reviews", headers=auth("not-a-real-token")).status_code == 401


def test_permissions_are_enforced_per_role(client):
    # A reviewer may decide but not execute; an operator is the mirror image.
    assert (
        client.post(
            "/reviews/r-1/decision", json={"approve": True}, headers=auth(REVIEWER_TOKEN)
        ).status_code
        == 200
    )
    assert client.post("/reviews/r-1/execute", headers=auth(REVIEWER_TOKEN)).status_code == 403
    assert client.post("/reviews/r-1/execute", headers=auth(OPERATOR_TOKEN)).status_code == 200


def test_reviewer_identity_comes_from_the_token_not_the_body(client):
    response = client.post(
        "/reviews/r-1/decision",
        json={"approve": True, "reviewer": "attacker"},
        headers=auth(REVIEWER_TOKEN),
    )
    assert response.json()["reviewer"] == "ada"


def test_execute_is_idempotent_and_does_not_replay_the_mutation(client):
    client.post("/reviews/r-1/decision", json={"approve": True}, headers=auth(REVIEWER_TOKEN))
    first = client.post("/reviews/r-1/execute", headers=auth(ADMIN_TOKEN)).json()
    second = client.post("/reviews/r-1/execute", headers=auth(ADMIN_TOKEN)).json()
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["result"] == first["result"]
    assert second["status"] == "EXECUTED"


def test_endpoints_fail_closed_when_no_tokens_are_configured(settings):
    unconfigured = TestClient(
        create_app(
            order_repository=OrderStore(),
            settings=replace(settings, tokens=TokenRegistry()),
        )
    )
    assert unconfigured.get("/reviews", headers=auth("anything")).status_code == 503


def test_forged_webhook_returns_a_real_401_status(client):
    response = client.post("/webhooks/orders", content=b"{}", headers={"X-Signature": "bad"})
    assert response.status_code == 401


def test_malformed_webhook_returns_400(client):
    body = b'{"event_id":"e-1"}'
    assert client.post("/webhooks/orders", content=body, headers=sign(body)).status_code == 400


def test_valid_webhook_returns_200_and_applies(client):
    body = b'{"event_id":"e-1","order_id":"o-1","status":"PAID"}'
    response = client.post("/webhooks/orders", content=body, headers=sign(body))
    assert response.status_code == 200
    assert response.json()["outcome"] == "applied"


def test_webhook_creates_fulfill_order_proposal(client):
    # Send a PAID event, which triggers the fulfill_order proposal
    body = b'{"event_id":"e-full","order_id":"o-full","status":"PAID"}'
    response = client.post("/webhooks/orders", content=body, headers=sign(body))
    assert response.status_code == 200

    # Ensure the proposal was created in the review queue
    response_reviews = client.get("/reviews", headers=auth(ADMIN_TOKEN))
    assert response_reviews.status_code == 200
    reviews = response_reviews.json()
    
    fulfill_reviews = [r for r in reviews if r["operation"] == "fulfill_order"]
    assert len(fulfill_reviews) == 1
    
    # Detail should contain the payload
    review_id = fulfill_reviews[0]["review_id"]
    response_detail = client.get(f"/reviews/{review_id}", headers=auth(ADMIN_TOKEN))
    assert response_detail.status_code == 200
    detail = response_detail.json()
    assert detail["payload"]["order_id"] == "o-full"
    assert detail["payload"]["status"] == "PAID"


def test_out_of_order_webhook_is_not_silently_dropped(client):
    body = b'{"event_id":"e-9","order_id":"o-9","status":"SHIPPED"}'
    response = client.post("/webhooks/orders", content=body, headers=sign(body))
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "applied"
    assert payload["order_status"] == "SHIPPED"
    assert payload["skipped"] == ["PAID", "FULFILLMENT_PENDING", "LABEL_CREATED"]


def test_conflicting_webhook_is_captured_for_review(client):
    delivered = b'{"event_id":"e-10","order_id":"o-10","status":"DELIVERED"}'
    client.post("/webhooks/orders", content=delivered, headers=sign(delivered))
    cancelled = b'{"event_id":"e-11","order_id":"o-10","status":"CANCELLED"}'
    response = client.post("/webhooks/orders", content=cancelled, headers=sign(cancelled))
    assert response.status_code == 202
    assert response.json()["outcome"] == "quarantined"


def test_storage_failure_is_retryable(settings):
    class BrokenRepository:
        def apply(self, event):
            raise RuntimeError("disk is full")

    broken = TestClient(create_app(order_repository=BrokenRepository(), settings=settings))
    body = b'{"event_id":"e-2","order_id":"o-2","status":"PAID"}'
    assert broken.post("/webhooks/orders", content=body, headers=sign(body)).status_code == 500


def test_responses_carry_a_correlation_id(client):
    response = client.get("/health", headers={"X-Correlation-Id": "trace-1"})
    assert response.headers["X-Correlation-Id"] == "trace-1"
    assert client.get("/health").headers["X-Correlation-Id"]


def test_quarantined_events_are_reachable_and_resolvable(client):
    delivered = b'{"event_id":"e-20","order_id":"o-20","status":"DELIVERED"}'
    client.post("/webhooks/orders", content=delivered, headers=sign(delivered))
    cancelled = b'{"event_id":"e-21","order_id":"o-20","status":"CANCELLED"}'
    client.post("/webhooks/orders", content=cancelled, headers=sign(cancelled))

    assert client.get("/orders/quarantined").status_code == 401
    open_events = client.get("/orders/quarantined", headers=auth(REVIEWER_TOKEN)).json()
    assert [row["event_id"] for row in open_events] == ["e-21"]

    # Resolution is a decision, so an execute-only operator cannot make it.
    assert (
        client.post("/orders/quarantined/e-21/resolve", headers=auth(OPERATOR_TOKEN)).status_code
        == 403
    )
    resolved = client.post("/orders/quarantined/e-21/resolve", headers=auth(REVIEWER_TOKEN))
    assert resolved.status_code == 200
    assert resolved.json()["resolved_by"] == "ada"
    assert client.get("/orders/quarantined", headers=auth(REVIEWER_TOKEN)).json() == []
    assert (
        client.post("/orders/quarantined/e-21/resolve", headers=auth(REVIEWER_TOKEN)).status_code
        == 404
    )


def test_approvals_survive_a_process_restart(tmp_path, settings):
    from dataclasses import replace

    from app.db.review_store import SQLiteReviewStore
    from app.review.queue import ReviewItem

    durable = replace(settings, review_db=str(tmp_path / "reviews.db"))
    SQLiteReviewStore(durable.review_db).submit(
        ReviewItem("r-restart", "update_inventory", {"changes": {"sku-1": 3}})
    )

    first_boot = TestClient(create_app(order_repository=OrderStore(), settings=durable))
    first_boot.post(
        "/reviews/r-restart/decision", json={"approve": True}, headers=auth(REVIEWER_TOKEN)
    )
    executed = first_boot.post("/reviews/r-restart/execute", headers=auth(OPERATOR_TOKEN)).json()
    assert executed["replayed"] is False

    # A fresh process must not hand the same approved mutation to the client again.
    second_boot = TestClient(create_app(order_repository=OrderStore(), settings=durable))
    replayed = second_boot.post("/reviews/r-restart/execute", headers=auth(OPERATOR_TOKEN)).json()
    assert replayed["replayed"] is True
    assert replayed["result"] == executed["result"]
