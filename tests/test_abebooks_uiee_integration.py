from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.abebooks_service import AbeBooksService
from app.api.http import create_app
from app.api.operator import OperatorService
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.domain.order_store import OrderStore
from app.domain.uiee import UIEEExporter, UIEERecord
from app.publish.books import AbeBooksListingAdapter
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from tests.conftest import ADMIN_TOKEN, auth


def test_uiee_record_formatting():
    record = UIEERecord(
        sku="BK-BB-ATWOOD-1",
        title="The Handmaid's Tale",
        author="Margaret Atwood",
        price_cad=Decimal("150.00"),
        binding="Hardcover",
        condition="Fine",
        jacket_condition="Near Fine",
        section="Canadiana / Literature",
        shelf="A-01",
        isbn="0771008139",
        publisher="McClelland and Stewart",
        year="1985",
        edition="First Edition",
        description="True first Canadian edition. Jacket has slight rubbing at spine tips.",
        status="Y",
    )
    uiee = record.to_uiee()

    assert "UR|BK-BB-ATWOOD-1" in uiee
    assert "TI|The Handmaid's Tale" in uiee
    assert "AA|Margaret Atwood" in uiee
    assert "BD|Hardcover" in uiee
    assert "CO|Fine" in uiee
    assert "JK|Near Fine" in uiee
    assert "PU|McClelland and Stewart" in uiee
    assert "DP|1985" in uiee
    assert "ED|First Edition" in uiee
    assert "IS|0771008139" in uiee
    assert "PR|150.00" in uiee
    assert "KW|Canadiana / Literature" in uiee
    assert "LO|A-01" in uiee
    assert "SY|Y" in uiee


def test_uiee_catalog_export_and_parse():
    now = datetime.now(UTC)
    items = [
        InventoryItem(
            sku="BK-1",
            title="Dune",
            authors=["Frank Herbert"],
            format="hardcover",
            condition="Fine",
            in_store_price_cad=Decimal("900.00"),
            online_price_cad=Decimal("950.00"),
            status="active",
            created_at=now,
            updated_at=now,
            section="Sci-Fi",
            shelf="B-01",
        ),
        InventoryItem(
            sku="BK-2",
            title="Neuromancer",
            authors=["William Gibson"],
            format="paperback",
            condition="Very Good",
            in_store_price_cad=Decimal("25.00"),
            online_price_cad=Decimal("30.00"),
            status="delisted",
            created_at=now,
            updated_at=now,
            section="Sci-Fi",
            shelf="B-02",
        ),
    ]

    # Full export
    full_uiee = UIEEExporter.export_catalog(items, status_filter="all")
    assert "UR|BK-1" in full_uiee
    assert "UR|BK-2" in full_uiee
    assert "SY|Y" in full_uiee
    assert "SY|N" in full_uiee

    # Active only export
    active_uiee = UIEEExporter.export_catalog(items, status_filter="active")
    assert "UR|BK-1" in active_uiee
    assert "UR|BK-2" not in active_uiee

    # Parse round-trip
    parsed = UIEEExporter.parse_uiee(full_uiee)
    assert len(parsed) == 2
    assert parsed[0]["UR"] == "BK-1"
    assert parsed[0]["TI"] == "Dune"
    assert parsed[0]["PR"] == "950.00"
    assert parsed[1]["UR"] == "BK-2"
    assert parsed[1]["TI"] == "Neuromancer"


def test_abebooks_adapter_delist():
    adapter = AbeBooksListingAdapter(dry_run=True)
    assert adapter.channel_name == "abebooks"
    assert adapter.delist_book("BK-TEST-1") is True


def test_abebooks_order_sync_triggers_corvus_and_delists_store():
    # Model client: The Book Bazaar (Ottawa)
    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)
    book = InventoryItem(
        sku="BK-BB-DAVIES-1",
        title="Fifth Business",
        authors=["Robertson Davies"],
        format="hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("85.00"),
        online_price_cad=Decimal("95.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Canadiana",
        shelf="D-03",
    )
    store.add(book, tenant_id=1)
    assert store.get("BK-BB-DAVIES-1", tenant_id=1).status == "active"

    queue = ReviewQueue()
    executor = ApprovedActionExecutor(client=None, inventory_store=store)
    operator = OperatorService(queue, executor)

    # Inbound AbeBooks order
    order_event = {
        "orderId": "AB-2024-998877",
        "orderDate": "2026-10-07T12:00:00Z",
        "items": [
            {
                "bookId": "BK-BB-DAVIES-1",
                "title": "Fifth Business",
                "price": "95.00",
            }
        ],
    }

    result = AbeBooksService.process_order(order_event, operator, tenant_id=1)
    assert result["status"] == "processed"
    assert result["processed_skus"] == ["BK-BB-DAVIES-1"]

    # Internal store marked as delisted
    updated = store.get("BK-BB-DAVIES-1", tenant_id=1)
    assert updated is not None
    assert updated.status == "delisted"

    # Corvus auto-executed review item
    reviews = queue.list_items(pending_only=False, tenant_id=1)
    assert len(reviews) == 1
    review = reviews[0]
    assert review.status.value == "EXECUTED"
    assert review.executed_by == "agent:corvus"
    assert review.result["inventory_store"] == "delisted"

    # Multi-channel sync: Square POS, eBay, Amazon, Shopify are all delisted
    sync_res = review.result["sync_results"]
    assert sync_res["square"] == "delisted"
    assert sync_res["ebay"] == "delisted"
    assert sync_res["amazon_spapi"] == "delisted"
    assert sync_res["shopify"] == "delisted"
    assert "abebooks" not in sync_res  # AbeBooks is source channel, skipped


def test_abebooks_and_uiee_http_endpoints(settings):
    app = create_app(order_repository=OrderStore(), settings=settings)
    client = TestClient(app)

    # 1. Download UIEE export
    resp_uiee = client.get("/inventory/export/uiee?status=active", headers=auth(ADMIN_TOKEN))
    assert resp_uiee.status_code == 200
    assert "text/plain" in resp_uiee.headers["content-type"]
    assert "attachment; filename=" in resp_uiee.headers["content-disposition"]
    uiee_body = resp_uiee.text
    assert "UIEE Export" in uiee_body
    assert "UR|BK-DUNE-1965" in uiee_body

    # 2. Webhook order POST
    resp_wh = client.post(
        "/webhooks/abebooks",
        headers=auth(ADMIN_TOKEN),
        json={"orderId": "AB-TEST-4422", "items": [{"bookId": "BK-DUNE-1965"}]},
    )
    assert resp_wh.status_code == 200
    assert resp_wh.json()["order_id"] == "AB-TEST-4422"

    # 3. Manual sync-orders endpoint
    resp_sync = client.post("/inventory/abebooks/sync-orders", headers=auth(ADMIN_TOKEN))
    assert resp_sync.status_code == 200
    assert resp_sync.json()["marketplace"] == "abebooks"
