import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.security import TokenRegistry
from app.api.tiktok_client import TikTokClient
from app.auth import create_access_token
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.review_store import SQLiteReviewStore
from app.domain.audit import AuditLog
from app.domain.order_store import OrderStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue


@pytest.fixture
def inv_store(tmp_path):
    store = SQLiteInventoryStore(tmp_path / "inventory.db")
    now = datetime.now(UTC)
    for i in range(1, 6):
        store.add(
            InventoryItem(
                sku=f"BK-TEST-{i:03d}",
                title=f"Book Title {i}",
                authors=[f"Author {i}"],
                format="hardcover_with_jacket",
                condition="Fine",
                in_store_price_cad=Decimal(f"{10 * i}.00"),
                online_price_cad=Decimal(f"{15 * i}.00"),
                status="active",
                section="Literature",
                shelf="A1",
                last_audited_at=None,
                created_at=now,
                updated_at=now,
            ),
            tenant_id=1,
        )
    return store


@pytest.fixture
def test_client(inv_store, tmp_path, settings):
    review_queue = ReviewQueue()
    executor = ApprovedActionExecutor(TikTokClient("token", "shop", dry_run=True), AuditLog(), inventory_store=inv_store)
    operator = OperatorService(review_queue, executor)

    app = create_app(
        operator=operator,
        order_repository=OrderStore(),
        inventory_store=inv_store,
        settings=settings,
    )
    return TestClient(app)


def auth_header(tenant_id: int = 1, role: str = "admin") -> dict[str, str]:
    token = create_access_token({"sub": f"user_{tenant_id}@test.com", "tenant_id": tenant_id, "role": role})
    return {"Authorization": f"Bearer {token}"}


def test_sqlite_batch_operations_and_tenant_isolation(inv_store):
    # 1. get_batch
    items = inv_store.get_batch(["BK-TEST-001", "BK-TEST-002"], tenant_id=1)
    assert len(items) == 2
    assert {i.sku for i in items} == {"BK-TEST-001", "BK-TEST-002"}

    # Tenant 2 gets nothing
    assert inv_store.get_batch(["BK-TEST-001", "BK-TEST-002"], tenant_id=2) == []

    # 2. batch_delist
    affected = inv_store.batch_delist(["BK-TEST-001", "BK-TEST-002"], tenant_id=1)
    assert affected == 2
    items = inv_store.get_batch(["BK-TEST-001", "BK-TEST-002"], tenant_id=1)
    assert all(i.status == "delisted" for i in items)

    # 3. batch_relist
    affected = inv_store.batch_relist(["BK-TEST-001"], tenant_id=1)
    assert affected == 1
    item = inv_store.get("BK-TEST-001", tenant_id=1)
    assert item.status == "active"

    # 4. batch_update_location
    affected = inv_store.batch_update_location(["BK-TEST-001", "BK-TEST-002"], "Vault", "V-01", tenant_id=1)
    assert affected == 2
    for sku in ("BK-TEST-001", "BK-TEST-002"):
        item = inv_store.get(sku, tenant_id=1)
        assert item.section == "Vault"
        assert item.shelf == "V-01"

    # 5. batch_update_prices
    affected = inv_store.batch_update_prices({
        "BK-TEST-001": (Decimal("12.50"), Decimal("18.50")),
        "BK-TEST-002": (Decimal("25.00"), Decimal("35.00")),
    }, tenant_id=1)
    assert affected == 2
    item1 = inv_store.get("BK-TEST-001", tenant_id=1)
    assert item1.in_store_price_cad == Decimal("12.50")
    assert item1.online_price_cad == Decimal("18.50")

    # 6. batch_delete
    affected = inv_store.batch_delete(["BK-TEST-001", "BK-TEST-002"], tenant_id=1)
    assert affected == 2
    assert inv_store.get("BK-TEST-001", tenant_id=1) is None
    assert inv_store.get("BK-TEST-002", tenant_id=1) is None


def test_batch_action_delist_and_relist_flow(test_client, inv_store):
    headers = auth_header(tenant_id=1)

    # Delist 2 books
    resp = test_client.post(
        "/inventory/batch-action",
        json={"action": "delist", "skus": ["BK-TEST-001", "BK-TEST-002"]},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["action"] == "delist"
    assert data["affected_count"] == 2
    assert "review_id" in data

    # Verify inventory store reflects delisted
    item1 = inv_store.get("BK-TEST-001", tenant_id=1)
    item2 = inv_store.get("BK-TEST-002", tenant_id=1)
    assert item1.status == "delisted"
    assert item2.status == "delisted"

    # Verify review proposal
    rev = test_client.get(f"/reviews/{data['review_id']}", headers=headers).json()
    assert rev["operation"] == "update_inventory"
    assert rev["payload"]["changes"] == {"BK-TEST-001": 0, "BK-TEST-002": 0}

    # Relist 1 book
    relist_resp = test_client.post(
        "/inventory/batch-action",
        json={"action": "relist", "skus": ["BK-TEST-001"]},
        headers=headers,
    )
    assert relist_resp.status_code == 200
    relist_data = relist_resp.json()
    assert relist_data["affected_count"] == 1
    assert inv_store.get("BK-TEST-001", tenant_id=1).status == "active"


def test_batch_action_price_adjustments(test_client, inv_store):
    headers = auth_header(tenant_id=1)

    # Percentage adjustment: -10% on BK-TEST-003 (in_store: 30.00 -> 27.00, online: 45.00 -> 40.50)
    resp = test_client.post(
        "/inventory/batch-action",
        json={
            "action": "price_adjust",
            "skus": ["BK-TEST-003"],
            "params": {"price_mode": "percent", "price_value": -10.0, "price_target": "both"},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected_count"] == 1
    assert "review_id" in data

    item = inv_store.get("BK-TEST-003", tenant_id=1)
    assert item.in_store_price_cad == Decimal("27.00")
    assert item.online_price_cad == Decimal("40.50")

    # Fixed delta: +$5.00 online only on BK-TEST-004 (online was 60.00 -> 65.00)
    resp2 = test_client.post(
        "/inventory/batch-action",
        json={
            "action": "price_adjust",
            "skus": ["BK-TEST-004"],
            "params": {"price_mode": "fixed_delta", "price_value": 5.0, "price_target": "online"},
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    item4 = inv_store.get("BK-TEST-004", tenant_id=1)
    assert item4.in_store_price_cad == Decimal("40.00")
    assert item4.online_price_cad == Decimal("65.00")

    # Flat override: set BK-TEST-005 to $19.99 for both
    resp3 = test_client.post(
        "/inventory/batch-action",
        json={
            "action": "price_adjust",
            "skus": ["BK-TEST-005"],
            "params": {"price_mode": "flat_override", "price_value": 19.99, "price_target": "both"},
        },
        headers=headers,
    )
    assert resp3.status_code == 200
    item5 = inv_store.get("BK-TEST-005", tenant_id=1)
    assert item5.in_store_price_cad == Decimal("19.99")
    assert item5.online_price_cad == Decimal("19.99")


def test_batch_action_relocate_and_delete(test_client, inv_store):
    headers = auth_header(tenant_id=1)

    # Relocate
    resp = test_client.post(
        "/inventory/batch-action",
        json={
            "action": "relocate",
            "skus": ["BK-TEST-001", "BK-TEST-002"],
            "params": {"section": "Antiquarian", "shelf": "Vault-B"},
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["affected_count"] == 2
    for sku in ("BK-TEST-001", "BK-TEST-002"):
        i = inv_store.get(sku, tenant_id=1)
        assert i.section == "Antiquarian"
        assert i.shelf == "Vault-B"

    # Delete
    del_resp = test_client.post(
        "/inventory/batch-action",
        json={"action": "delete", "skus": ["BK-TEST-001", "BK-TEST-002"]},
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["affected_count"] == 2
    assert inv_store.get("BK-TEST-001", tenant_id=1) is None
    assert inv_store.get("BK-TEST-002", tenant_id=1) is None


def test_inventory_export_csv_and_uiee(test_client):
    headers = auth_header(tenant_id=1)

    # 1. Export CSV
    csv_resp = test_client.post(
        "/inventory/export",
        json={"skus": ["BK-TEST-001", "BK-TEST-002"], "format": "csv"},
        headers=headers,
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["Content-Type"]
    reader = list(csv.reader(io.StringIO(csv_resp.text)))
    assert reader[0] == [
        "sku", "title", "authors", "format", "condition",
        "in_store_price_cad", "online_price_cad", "status",
        "section", "shelf", "last_audited_at", "created_at"
    ]
    assert len(reader) == 3  # Header + 2 items

    # 2. Export UIEE
    uiee_resp = test_client.post(
        "/inventory/export",
        json={"skus": ["BK-TEST-001"], "format": "uiee"},
        headers=headers,
    )
    assert uiee_resp.status_code == 200
    assert "UR|BK-TEST-001" in uiee_resp.text
    assert "TI|Book Title 1" in uiee_resp.text
    assert "PR|15.00" in uiee_resp.text
