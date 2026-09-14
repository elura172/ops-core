import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.domain.order_store import OrderStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewQueue
from tests.conftest import OPERATOR_TOKEN, REVIEWER_TOKEN, auth


@pytest.fixture
def dashboard_client(settings):
    queue = ReviewQueue()
    queue.submit(ReviewItem("draft", "create_product_draft", {"title": "<script>bad()</script>"}))
    queue.submit(ReviewItem("publish", "publish", {"resource_id": "book"}))
    operator = OperatorService(queue, ApprovedActionExecutor(TikTokClient("test", "test")))
    return TestClient(create_app(operator, OrderStore(), settings))


def test_dashboard_shell_and_packaged_assets(dashboard_client):
    response = dashboard_client.get("/dashboard")
    assert response.status_code == 200
    assert "Good work." in response.text
    assert "bad()" not in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    for asset in ("dashboard.css", "dashboard.js"):
        assert dashboard_client.get(f"/dashboard/assets/{asset}").status_code == 200
    assert dashboard_client.get("/dashboard/assets/http.py").status_code == 404


def test_action_previews_require_auth_and_are_not_cacheable(dashboard_client):
    assert dashboard_client.get("/reviews/draft").status_code == 401
    response = dashboard_client.get("/reviews/draft", headers=auth(REVIEWER_TOKEN))
    assert response.status_code == 200
    assert response.json()["payload"] == {"title": "<script>bad()</script>"}
    assert response.headers["Cache-Control"] == "no-store"
    assert dashboard_client.get("/reviews/missing", headers=auth(REVIEWER_TOKEN)).status_code == 404


def test_session_permissions_and_blocked_execution(dashboard_client):
    assert dashboard_client.get("/operator/session").status_code == 401
    response = dashboard_client.get("/operator/session", headers=auth(REVIEWER_TOKEN)).json()
    assert response == {
        "name": "ada",
        "permissions": ["review:decide", "review:read"],
        "mode": "dry-run",
    }
    item = dashboard_client.get("/reviews/publish", headers=auth(REVIEWER_TOKEN)).json()
    assert item["execution_blocked"] == "publishing is disabled"
    assert item["status"] == "PENDING"


def test_preview_decide_execute_result_flow(dashboard_client):
    reviewer = auth(REVIEWER_TOKEN)
    operator = auth(OPERATOR_TOKEN)
    assert dashboard_client.get("/reviews/draft", headers=reviewer).json()["status"] == "PENDING"
    assert (
        dashboard_client.post(
            "/reviews/draft/decision", json={"approve": True}, headers=operator
        ).status_code
        == 403
    )
    assert (
        dashboard_client.post(
            "/reviews/draft/decision", json={"approve": True}, headers=reviewer
        ).status_code
        == 200
    )
    assert dashboard_client.post("/reviews/draft/execute", headers=reviewer).status_code == 403
    assert dashboard_client.post("/reviews/draft/execute", headers=operator).status_code == 200
    item = dashboard_client.get("/reviews/draft", headers=reviewer).json()
    assert item["status"] == "EXECUTED"
    assert item["reviewer"] == "ada"
    assert item["executed_by"] == "grace"
    assert item["result"]["dry_run"] is True
