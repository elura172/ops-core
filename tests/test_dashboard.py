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


def test_camera_upload_endpoint(dashboard_client):
    from unittest.mock import patch
    from app.domain.books import BookFormat, BookStackExtraction, SpineExtractionItem

    reviewer = auth(REVIEWER_TOKEN)

    mock_extraction = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(10, 20, 100, 200),
                title="Dune",
                subtitle="Chronicles",
                authors=("Frank Herbert",),
                format=BookFormat.HARDCOVER_WITH_JACKET,
                visible_condition_cues=("clean_as_new",),
                raw_spine_text="DUNE FRANK HERBERT",
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_stack",
            return_value=mock_extraction,
        ),
    ):
        files = {"file": ("books.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")}
        resp = dashboard_client.post("/intake/camera", files=files, headers=reviewer)
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
        assert data["orientation"] == "horizontal_stack"
        assert len(data["proposals"]) == 1


def test_sample_stack_endpoint(dashboard_client):
    reviewer = auth(REVIEWER_TOKEN)
    resp = dashboard_client.post("/intake/sample", headers=reviewer)
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 3
    assert len(data["proposals"]) == 3


def test_delete_and_clear_reviews_endpoints(dashboard_client):
    reviewer = auth(REVIEWER_TOKEN)
    operator = auth(OPERATOR_TOKEN)

    # Propose an item to test deletion
    resp = dashboard_client.post(
        "/reviews",
        json={"operation": "update_inventory", "payload": {"sku": "DEL-1", "stock": 5}},
        headers=reviewer,
    )
    assert resp.status_code == 200
    rev_id = resp.json()["review_id"]

    # Verify unauthorized cannot delete
    assert dashboard_client.delete(f"/reviews/{rev_id}").status_code == 401

    # Delete the review
    del_resp = dashboard_client.delete(f"/reviews/{rev_id}", headers=reviewer)
    assert del_resp.status_code == 200
    assert del_resp.json() == {"status": "deleted", "review_id": rev_id}

    # Verify it is gone
    assert dashboard_client.get(f"/reviews/{rev_id}", headers=reviewer).status_code == 404
    # Second delete returns 404
    assert dashboard_client.delete(f"/reviews/{rev_id}", headers=reviewer).status_code == 404

    # Test clear endpoint
    resp1 = dashboard_client.post(
        "/reviews",
        json={"operation": "update_inventory", "payload": {"sku": "DEL-2", "stock": 2}},
        headers=reviewer,
    )
    rev2 = resp1.json()["review_id"]
    dashboard_client.post(f"/reviews/{rev2}/decision", json={"approve": False, "reason": "reject"}, headers=reviewer)

    clear_resp = dashboard_client.delete("/reviews?status=INACTIVE", headers=reviewer)
    assert clear_resp.status_code == 200
    assert clear_resp.json()["status"] == "cleared"
    assert clear_resp.json()["count"] >= 1


def test_inventory_soft_delist_and_hard_delete_endpoints(dashboard_client):
    reviewer = auth(REVIEWER_TOKEN)

    # Initial inventory contains seeded items
    inv = dashboard_client.get("/inventory", headers=reviewer).json()
    skus = [i["sku"] for i in inv]
    assert "BK-DUNE-1965" in skus

    # Soft delist (default hard=False)
    delist_resp = dashboard_client.delete("/inventory/BK-DUNE-1965", headers=reviewer)
    assert delist_resp.status_code == 200
    assert delist_resp.json() == {"status": "ok"}

    # Item is still in inventory but marked delisted
    inv = dashboard_client.get("/inventory", headers=reviewer).json()
    dune = next(i for i in inv if i["sku"] == "BK-DUNE-1965")
    assert dune["status"] == "delisted"

    # Hard delete (hard=True)
    hard_resp = dashboard_client.delete("/inventory/BK-DUNE-1965?hard=true", headers=reviewer)
    assert hard_resp.status_code == 200
    assert hard_resp.json() == {"status": "deleted", "sku": "BK-DUNE-1965"}

    # Item is permanently gone
    inv = dashboard_client.get("/inventory", headers=reviewer).json()
    assert not any(i["sku"] == "BK-DUNE-1965" for i in inv)

    # Subsequent hard delete returns 404
    assert dashboard_client.delete("/inventory/BK-DUNE-1965?hard=true", headers=reviewer).status_code == 404


def test_multitenant_deletion_isolation(dashboard_client, tmp_path):
    from app.auth import create_access_token
    from app.db.inventory import InventoryItem
    from decimal import Decimal
    from datetime import UTC, datetime

    t1_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'user1@example.com', 'tenant_id': 101, 'role': 'admin'})}"}
    t2_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'user2@example.com', 'tenant_id': 102, 'role': 'admin'})}"}

    # 1. Multi-tenant review proposal isolation
    r1 = dashboard_client.post(
        "/reviews",
        json={"operation": "update_inventory", "payload": {"sku": "SKU-T1", "stock": 1}},
        headers=t1_headers,
    ).json()["review_id"]

    # Tenant 2 tries to delete Tenant 1's proposal -> 404
    assert dashboard_client.delete(f"/reviews/{r1}", headers=t2_headers).status_code == 404
    # Tenant 1 can delete its own proposal -> 200
    assert dashboard_client.delete(f"/reviews/{r1}", headers=t1_headers).status_code == 200

    # 2. Multi-tenant inventory deletion isolation
    now = datetime.now(UTC)
    from app.db.inventory import SQLiteInventoryStore, InventoryItem
    sqlite_inv = SQLiteInventoryStore(tmp_path / "inv.db")
    sqlite_inv.add(
        InventoryItem("SKU-101", "Book 101", ["Author"], "hardcover", "Fine", Decimal("20"), Decimal("25"), "active", now, now),
        tenant_id=101,
    )
    # Tenant 102 cannot delete Tenant 101's item
    assert sqlite_inv.delete("SKU-101", tenant_id=102) is False
    # Item is still present for Tenant 101
    assert any(i.sku == "SKU-101" for i in sqlite_inv.list_all(tenant_id=101))
    # Tenant 101 can delete its own item
    assert sqlite_inv.delete("SKU-101", tenant_id=101) is True
    # Item is now gone
    assert not any(i.sku == "SKU-101" for i in sqlite_inv.list_all(tenant_id=101))
    # Subsequent delete is False
    assert sqlite_inv.delete("SKU-101", tenant_id=101) is False

