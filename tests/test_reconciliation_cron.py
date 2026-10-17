from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from starlette.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.reconciliation_service import ReconciliationService
from app.auth import create_access_token
from app.config import Settings
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.review_store import SQLiteReviewStore
from app.db.users import SQLiteUserStore
from app.db.want_list import Collector, CollectorHold, SQLiteWantListStore
from app.review.executor import ApprovedActionExecutor


@pytest.fixture
def recon_env(tmp_path, settings):
    review_db = str(tmp_path / "reviews.db")
    inv_db = str(tmp_path / "inventory.db")
    want_db = str(tmp_path / "wants.db")
    user_db = str(tmp_path / "users.db")

    rev_store = SQLiteReviewStore(review_db)
    inv_store = SQLiteInventoryStore(inv_db)
    want_store = SQLiteWantListStore(want_db)
    user_store = SQLiteUserStore(user_db)

    class MockClient:
        pass

    executor = ApprovedActionExecutor(
        client=MockClient(),
        inventory_store=inv_store,
        want_list_store=want_store,
    )
    operator = OperatorService(rev_store, executor)

    test_settings = Settings(
        tts_app_key=settings.tts_app_key,
        tts_app_secret=settings.tts_app_secret,
        tts_access_token=settings.tts_access_token,
        tts_shop_id=settings.tts_shop_id,
        webhook_secret=settings.webhook_secret,
        tokens=settings.tokens,
        review_db=review_db,
        inventory_db=inv_db,
        want_list_db=want_db,
        user_db=user_db,
        dry_run=True,
        cron_secret="cron-secret-super-key-123",
        biblio_api_key="mock_key",
        biblio_dealer_id="DEALER-42",
        abebooks_api_key="mock_key",
    )

    recon_service = ReconciliationService(
        operator=operator,
        user_store=user_store,
        settings=test_settings,
        want_list_store=want_store,
        inventory_store=inv_store,
    )

    app = create_app(
        operator=operator,
        settings=test_settings,
        inventory_store=inv_store,
        user_store=user_store,
    )
    client = TestClient(app)

    return {
        "client": client,
        "operator": operator,
        "inv_store": inv_store,
        "want_store": want_store,
        "rev_store": rev_store,
        "user_store": user_store,
        "recon_service": recon_service,
        "settings": test_settings,
    }


def test_reconciliation_service_dry_run_cycle(recon_env):
    service = recon_env["recon_service"]
    result = service.run_reconciliation_cycle(demo_sample=False)

    assert result["status"] == "success"
    assert result["tenants_checked"] >= 1
    assert result["orders_polled"]["biblio"] == 0
    assert result["orders_polled"]["abebooks"] == 0
    assert result["orders_processed"] == 0
    assert result["collisions_detected"] == 0
    assert "corvus_actions" in result


def test_reconciliation_service_demo_pulse_and_corvus_delist(recon_env):
    inv_store = recon_env["inv_store"]
    service = recon_env["recon_service"]

    # Seed book in store
    book = InventoryItem(
        sku="BK-DUNE-1965",
        title="Dune (First Edition)",
        authors=["Frank Herbert"],
        format="Hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("180.00"),
        online_price_cad=Decimal("180.00"),
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    inv_store.add(book, tenant_id=1)

    # Run cycle with demo_sample=True
    result = service.run_reconciliation_cycle(demo_sample=True, force_tenant_id=1)

    assert result["status"] == "success"
    assert result["orders_processed"] == 1
    assert result["collisions_detected"] == 0
    assert result["corvus_actions"]["approved"] >= 1

    # Book should be delisted in inventory
    updated = inv_store.get("BK-DUNE-1965", tenant_id=1)
    assert updated is not None
    assert updated.status == "delisted"


def test_reconciliation_service_collision_with_collector_hold(recon_env):
    inv_store = recon_env["inv_store"]
    want_store = recon_env["want_store"]
    rev_store = recon_env["rev_store"]
    service = recon_env["recon_service"]

    book = InventoryItem(
        sku="BK-DUNE-1965",
        title="Dune (First Edition)",
        authors=["Frank Herbert"],
        format="Hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("180.00"),
        online_price_cad=Decimal("180.00"),
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    inv_store.add(book, tenant_id=1)

    # Place an active 48-hour courtesy hold for a patron
    now = datetime.now(UTC)
    want_store.create_collector(
        Collector(
            id="col-77",
            tenant_id=1,
            name="Baron Harkonnen",
            email="baron@arrakis.com",
            phone="555-0199",
        )
    )
    want_store.create_hold(
        CollectorHold(
            id="hold-99",
            tenant_id=1,
            sku="BK-DUNE-1965",
            want_item_id="want-1",
            collector_id="col-77",
            collector_name="Baron Harkonnen",
            book_title="Dune (First Edition)",
            price_cad=Decimal("180.00"),
            held_at=now,
            expires_at=now + timedelta(hours=48),
            status="active",
        )
    )

    # Run cycle with demo order
    result = service.run_reconciliation_cycle(demo_sample=True, force_tenant_id=1)

    assert result["status"] == "success"
    assert result["orders_processed"] == 1
    assert result["collisions_detected"] == 1

    # Verify a contention review was created on the Review Desk
    pending = rev_store.list_items(pending_only=True, tenant_id=1)
    contention_reviews = [r for r in pending if r.operation == "contention_hold_vs_marketplace"]
    assert len(contention_reviews) == 1
    review = contention_reviews[0]
    assert review.payload["sku"] == "BK-DUNE-1965"
    assert review.payload["source_channel"] == "biblio"
    assert review.payload["collector_name"] == "Baron Harkonnen"

    # Book should NOT be delisted automatically by Corvus because hold takes operator precedence
    book_after = inv_store.get("BK-DUNE-1965", tenant_id=1)
    assert book_after.status == "active"


def test_reconciliation_multi_tenant_billing_skip(recon_env):
    user_store = recon_env["user_store"]
    service = recon_env["recon_service"]

    # Create tenant 2 with inactive subscription
    tenant_2_id = user_store.create_tenant("Inactive Antiquarian")
    # Verify default subscription status is inactive
    billing = user_store.get_tenant_billing_status(tenant_2_id)
    assert billing.get("subscription_status") == "inactive"

    # Run cycle across all tenants
    result = service.run_reconciliation_cycle()
    checked_tenant_ids = [t["tenant_id"] for t in result["tenant_summaries"]]
    assert 1 in checked_tenant_ids
    assert tenant_2_id not in checked_tenant_ids


def test_cron_endpoint_auth_enforcement(recon_env):
    client = recon_env["client"]
    cron_secret = recon_env["settings"].cron_secret

    # 1. Reject without auth when cron_secret is configured
    res_no_auth = client.get("/api/cron/sync")
    assert res_no_auth.status_code == 401
    assert "unauthorized cron invocation" in res_no_auth.json()["detail"]

    # 2. Reject with invalid bearer token
    res_bad_auth = client.get("/api/cron/sync", headers={"Authorization": "Bearer wrong-token"})
    assert res_bad_auth.status_code == 401

    # 3. Accept with valid CRON_SECRET bearer token
    res_valid_get = client.get(
        "/api/cron/sync",
        headers={"Authorization": f"Bearer {cron_secret}"},
    )
    assert res_valid_get.status_code == 200
    body = res_valid_get.json()
    assert body["status"] == "success"
    assert "tenants_checked" in body
    assert "corvus_actions" in body

    # 4. Accept via POST as well
    res_valid_post = client.post(
        "/api/cron/sync",
        headers={"Authorization": f"Bearer {cron_secret}"},
    )
    assert res_valid_post.status_code == 200

    # 5. Reject an operator JWT; the cron endpoint accepts only CRON_SECRET.
    jwt_token = create_access_token({"sub": "operator@opscore.io", "tenant_id": 1, "role": "admin"})
    res_jwt = client.get(
        "/api/cron/sync",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert res_jwt.status_code == 401


def test_cron_endpoint_with_demo_query(recon_env):
    client = recon_env["client"]
    inv_store = recon_env["inv_store"]
    cron_secret = recon_env["settings"].cron_secret

    book = InventoryItem(
        sku="BK-DUNE-1965",
        title="Dune (First Edition)",
        authors=["Frank Herbert"],
        format="Hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("180.00"),
        online_price_cad=Decimal("180.00"),
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    inv_store.add(book, tenant_id=1)

    # Trigger demo pulse via HTTP
    res = client.get(
        "/api/cron/sync?demo=true",
        headers={"Authorization": f"Bearer {cron_secret}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["orders_processed"] == 1
    assert data["corvus_actions"]["approved"] >= 1

    # Verify inventory was delisted
    updated = inv_store.get("BK-DUNE-1965", tenant_id=1)
    assert updated.status == "delisted"
