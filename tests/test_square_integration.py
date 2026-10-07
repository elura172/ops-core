from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.square_service import SquareService
from app.db.inventory import InventoryItem
from app.domain.order_store import OrderStore
from app.publish.books import SquareCatalogAdapter
from tests.conftest import ADMIN_TOKEN, auth


def test_square_service_dry_run_catalog_and_checkout():
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-TEST-SQ-1",
        title="Neuromancer",
        authors=["William Gibson"],
        format="paperback",
        condition="Very Good",
        in_store_price_cad=Decimal("25.00"),
        online_price_cad=Decimal("30.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi / Fantasy",
        shelf="B-03",
    )

    # 1. Catalog push
    cat_res = SquareService.push_catalog_item(item, dry_run=True)
    assert cat_res["status"] == "synced"
    assert cat_res["catalog_object_id"] == "sq-cat-BK-TEST-SQ-1"
    assert cat_res["price_cad"] == 25.0

    # 2. Checkout link creation
    pay_res = SquareService.create_checkout_link(item, dry_run=True)
    assert pay_res["status"] == "ready"
    assert "square.link" in pay_res["payment_url"]
    assert pay_res["amount_cad"] == 25.0
    assert pay_res["sku"] == "BK-TEST-SQ-1"


def test_square_catalog_adapter():
    adapter = SquareCatalogAdapter(dry_run=True)
    assert adapter.channel_name == "square"

    ext_id = adapter.list_book(
        sku="BK-DUNE-1",
        title="Dune",
        condition="Fine",
        price_cad=Decimal("950.00"),
        details={
            "authors": ["Frank Herbert"],
            "format": "hardcover",
            "section": "Sci-Fi",
            "shelf": "B-01",
            "in_store_price_cad": Decimal("900.00"),
        },
    )
    assert ext_id == "sq-cat-BK-DUNE-1"
    assert adapter.delist_book("BK-DUNE-1") is True


def test_square_api_endpoints(settings):
    app = create_app(order_repository=OrderStore(), settings=settings)
    client = TestClient(app)

    # 1. Push single book to Square catalog
    resp = client.post(
        "/inventory/BK-DUNE-1965/square/catalog",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "synced"
    assert data["sku"] == "BK-DUNE-1965"

    # 2. Sync all active books to Square catalog
    resp_all = client.post(
        "/inventory/square/catalog/sync-all",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp_all.status_code == 200
    all_data = resp_all.json()
    assert all_data["status"] == "completed"
    assert all_data["synced_count"] >= 1

    # 3. Create Square checkout link
    resp_co = client.post(
        "/inventory/BK-DUNE-1965/square/checkout",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp_co.status_code == 200
    co_data = resp_co.json()
    assert co_data["status"] == "ready"
    assert "payment_url" in co_data
    assert co_data["amount_cad"] == 950.0
