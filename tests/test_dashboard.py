import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.db.users import SQLiteUserStore
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
    user_store = SQLiteUserStore(":memory:")
    client = TestClient(create_app(operator, OrderStore(), settings, user_store=user_store))
    client.user_store = user_store
    try:
        yield client
    finally:
        user_store.close()


def _active_test_tenant(user_store, name):
    tenant_id = user_store.create_tenant(name)
    customer_id = f"cus_test_{tenant_id}"
    user_store.update_tenant_stripe_customer(tenant_id, customer_id)
    user_store.update_subscription_status(customer_id, "active")
    return tenant_id


def test_dashboard_shell_and_packaged_assets(dashboard_client):
    response = dashboard_client.get("/dashboard")
    assert response.status_code == 200
    assert "Your bookshop." in response.text
    assert "bad()" not in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "https://fonts.googleapis.com" in response.headers["Content-Security-Policy"]
    assert "https://fonts.gstatic.com" in response.headers["Content-Security-Policy"]
    assert "img-src 'self' blob: data:" in response.headers["Content-Security-Policy"]
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
    assert dashboard_client.post("/reviews/draft/decision", json={"approve": True}, headers=operator).status_code == 403
    assert dashboard_client.post("/reviews/draft/decision", json={"approve": True}, headers=reviewer).status_code == 200
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

    # Relist restores active status
    relist_resp = dashboard_client.post("/inventory/BK-DUNE-1965/relist", headers=reviewer)
    assert relist_resp.status_code == 200
    assert relist_resp.json() == {"status": "ok", "sku": "BK-DUNE-1965"}
    inv = dashboard_client.get("/inventory", headers=reviewer).json()
    dune = next(i for i in inv if i["sku"] == "BK-DUNE-1965")
    assert dune["status"] == "active"

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
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.auth import create_access_token
    from app.db.inventory import InventoryItem, SQLiteInventoryStore

    tenant_one_id = _active_test_tenant(dashboard_client.user_store, "Isolation tenant one")
    tenant_two_id = _active_test_tenant(dashboard_client.user_store, "Isolation tenant two")
    t1_token = create_access_token({"sub": "user1@example.com", "tenant_id": tenant_one_id, "role": "admin"})
    t2_token = create_access_token({"sub": "user2@example.com", "tenant_id": tenant_two_id, "role": "admin"})
    t1_headers = {"Authorization": f"Bearer {t1_token}"}
    t2_headers = {"Authorization": f"Bearer {t2_token}"}

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
    sqlite_inv = SQLiteInventoryStore(tmp_path / "inv.db")
    sqlite_inv.add(
        InventoryItem(
            "SKU-101",
            "Book 101",
            ["Author"],
            "hardcover",
            "Fine",
            Decimal("20"),
            Decimal("25"),
            "active",
            now,
            now,
        ),
        tenant_id=tenant_one_id,
    )
    # Tenant two cannot delete tenant one's item.
    assert sqlite_inv.delete("SKU-101", tenant_id=tenant_two_id) is False
    # Item is still present for tenant one.
    assert any(i.sku == "SKU-101" for i in sqlite_inv.list_all(tenant_id=tenant_one_id))
    # Tenant one can delete its own item.
    assert sqlite_inv.delete("SKU-101", tenant_id=tenant_one_id) is True
    # Item is now gone
    assert not any(i.sku == "SKU-101" for i in sqlite_inv.list_all(tenant_id=tenant_one_id))
    # Subsequent delete is False
    assert sqlite_inv.delete("SKU-101", tenant_id=tenant_one_id) is False


def test_batch_delete_proposals_endpoint(dashboard_client):
    from app.auth import create_access_token

    admin_headers = {
        "Authorization": f"Bearer {create_access_token({'sub': 'admin@example.com', 'tenant_id': 1, 'role': 'admin'})}"
    }
    operator_headers = {
        "Authorization": f"Bearer {create_access_token({'sub': 'op@example.com', 'tenant_id': 1, 'role': 'operator'})}"
    }

    # Create 3 proposals
    ids = []
    for i in range(3):
        res = dashboard_client.post(
            "/reviews",
            json={
                "operation": "update_inventory",
                "payload": {"sku": f"BATCH-{i}", "stock": i + 1},
            },
            headers=admin_headers,
        )
        assert res.status_code == 200
        ids.append(res.json()["review_id"])

    # 1. Unauthenticated -> 401
    assert dashboard_client.post("/reviews/batch-delete", json={"review_ids": ids}).status_code == 401

    # 2. Operator lacking review:decide -> 403
    assert (
        dashboard_client.post("/reviews/batch-delete", json={"review_ids": ids}, headers=operator_headers).status_code
        == 403
    )

    # 3. Batch delete 2 items via POST /reviews/batch-delete
    del_res = dashboard_client.post(
        "/reviews/batch-delete",
        json={"review_ids": ids[:2]},
        headers=admin_headers,
    )
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"
    assert del_res.json()["count"] == 2
    assert set(del_res.json()["review_ids"]) == set(ids[:2])

    # Verify first 2 are gone, 3rd remains
    assert dashboard_client.get(f"/reviews/{ids[0]}", headers=admin_headers).status_code == 404
    assert dashboard_client.get(f"/reviews/{ids[1]}", headers=admin_headers).status_code == 404
    assert dashboard_client.get(f"/reviews/{ids[2]}", headers=admin_headers).status_code == 200

    # 4. Batch delete the last item via DELETE /reviews/batch
    del_res2 = dashboard_client.request(
        "DELETE",
        "/reviews/batch",
        json={"review_ids": [ids[2]]},
        headers=admin_headers,
    )
    assert del_res2.status_code == 200
    assert del_res2.json()["count"] == 1
    assert dashboard_client.get(f"/reviews/{ids[2]}", headers=admin_headers).status_code == 404

    # 5. Empty review_ids -> 200 with count 0
    empty_res = dashboard_client.post(
        "/reviews/batch-delete",
        json={"review_ids": []},
        headers=admin_headers,
    )
    assert empty_res.status_code == 200
    assert empty_res.json()["count"] == 0


def test_batch_delete_multitenant_isolation(dashboard_client):
    from app.auth import create_access_token

    tenant_one_id = _active_test_tenant(dashboard_client.user_store, "Batch tenant one")
    tenant_two_id = _active_test_tenant(dashboard_client.user_store, "Batch tenant two")
    t1_token = create_access_token({"sub": "user1@example.com", "tenant_id": tenant_one_id, "role": "admin"})
    t2_token = create_access_token({"sub": "user2@example.com", "tenant_id": tenant_two_id, "role": "admin"})
    t1_headers = {"Authorization": f"Bearer {t1_token}"}
    t2_headers = {"Authorization": f"Bearer {t2_token}"}

    # Tenant 1 creates 2 proposals
    t1_ids = []
    for i in range(2):
        res = dashboard_client.post(
            "/reviews",
            json={"operation": "update_inventory", "payload": {"sku": f"T1-BATCH-{i}", "stock": 1}},
            headers=t1_headers,
        )
        t1_ids.append(res.json()["review_id"])

    # Tenant 2 creates 1 proposal
    t2_id = dashboard_client.post(
        "/reviews",
        json={"operation": "update_inventory", "payload": {"sku": "T2-BATCH-0", "stock": 1}},
        headers=t2_headers,
    ).json()["review_id"]

    # Tenant 2 attempts to batch delete Tenant 1's IDs
    cross_res = dashboard_client.post(
        "/reviews/batch-delete",
        json={"review_ids": t1_ids},
        headers=t2_headers,
    )
    assert cross_res.status_code == 200
    # Deleted count is 0 because tenant 2 does not own them!
    assert cross_res.json()["count"] == 0

    # Tenant 1 items still exist
    assert dashboard_client.get(f"/reviews/{t1_ids[0]}", headers=t1_headers).status_code == 200
    assert dashboard_client.get(f"/reviews/{t1_ids[1]}", headers=t1_headers).status_code == 200

    # Tenant 1 batch deletes own items
    own_res = dashboard_client.post(
        "/reviews/batch-delete",
        json={"review_ids": t1_ids},
        headers=t1_headers,
    )
    assert own_res.status_code == 200
    assert own_res.json()["count"] == 2

    # Tenant 1 items are now gone
    assert dashboard_client.get(f"/reviews/{t1_ids[0]}", headers=t1_headers).status_code == 404
    assert dashboard_client.get(f"/reviews/{t1_ids[1]}", headers=t1_headers).status_code == 404

    # Tenant 2's item is completely unaffected
    assert dashboard_client.get(f"/reviews/{t2_id}", headers=t2_headers).status_code == 200


def test_intake_execution_populates_bookshelf_for_physical_only(dashboard_client):
    from app.auth import create_access_token

    admin_headers = {
        "Authorization": f"Bearer {create_access_token({'sub': 'admin@example.com', 'tenant_id': 1, 'role': 'admin'})}"
    }

    # Propose an intake book with physical-only bargain bin strategy
    prop = dashboard_client.post(
        "/reviews",
        json={
            "operation": "intake_book",
            "payload": {
                "section": "Bargain Corner",
                "shelf": "Cart-1",
                "extraction": {
                    "title": "The Road Past Altamont",
                    "authors": ["Gabrielle Roy"],
                    "format": "paperback",
                },
                "disambiguation": {
                    "best_candidate": {
                        "title": "The Road Past Altamont",
                        "authors": ["Gabrielle Roy"],
                    }
                },
                "valuation": {
                    "condition_grade": "Fair",
                    "channel_strategy": "bargain_bin_cart",
                    "in_store_price_cad": "5.00",
                    "online_price_cad": "0.00",
                },
            },
        },
        headers=admin_headers,
    ).json()

    review_id = prop["review_id"]

    # Approve
    decide_resp = dashboard_client.post(
        f"/reviews/{review_id}/decision",
        json={"approve": True, "reason": "Approved for physical cart"},
        headers=admin_headers,
    )
    assert decide_resp.status_code == 200

    # Execute
    exec_resp = dashboard_client.post(
        f"/reviews/{review_id}/execute",
        headers=admin_headers,
    )
    assert exec_resp.status_code == 200

    # Verify book is on the Bookshelf!
    inv = dashboard_client.get("/inventory", headers=admin_headers).json()
    roy_book = next((b for b in inv if b["title"] == "The Road Past Altamont"), None)
    assert roy_book is not None
    assert roy_book["section"] == "Bargain Corner"
    assert roy_book["shelf"] == "Cart-1"
    assert roy_book["in_store_price_cad"] == 5.0
    assert roy_book["status"] == "active"


def test_batch_intake_execution_populates_bookshelf(dashboard_client):
    from app.auth import create_access_token

    tenant_id = _active_test_tenant(dashboard_client.user_store, "Batch intake tenant")
    token = create_access_token({"sub": "batch_user@example.com", "tenant_id": tenant_id, "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    review_ids = []
    for idx in range(5):
        prop = dashboard_client.post(
            "/reviews",
            json={
                "operation": "intake_book",
                "payload": {
                    "section": "Science Fiction",
                    "shelf": f"SF-0{idx + 1}",
                    "extraction": {
                        "title": f"Future Chronicles Vol {idx + 1}",
                        "authors": ["Arthur C. Clarke"],
                        "format": "hardcover",
                    },
                    "disambiguation": {"best_candidate": {"title": f"Future Chronicles Vol {idx + 1}"}},
                    "valuation": {
                        "condition_grade": "Very Good",
                        "channel_strategy": "multi_channel_antiquarian",
                        "in_store_price_cad": "25.00",
                        "online_price_cad": "30.00",
                    },
                },
            },
            headers=headers,
        ).json()
        review_ids.append(prop["review_id"])

    # Approve and execute all
    for rid in review_ids:
        dashboard_client.post(
            f"/reviews/{rid}/decision",
            json={"approve": True, "reason": "Batch approve"},
            headers=headers,
        )
        dashboard_client.post(f"/reviews/{rid}/execute", headers=headers)

    # Verify all 5 books appear in /inventory for the test tenant
    inv = dashboard_client.get("/inventory", headers=headers).json()
    assert len(inv) == 5
    titles = {b["title"] for b in inv}
    for idx in range(5):
        assert f"Future Chronicles Vol {idx + 1}" in titles
