from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app.api.biblio_service import BiblioService
from app.api.http import create_app
from app.api.operator import OperatorService
from app.auth import create_access_token
from app.config import Settings
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.review_store import SQLiteReviewStore
from app.db.want_list import Collector, CollectorHold, SQLiteWantListStore, WantItem
from app.publish.books import BiblioListingAdapter
from app.review.executor import ApprovedActionExecutor


@pytest.fixture
def biblio_env(tmp_path, settings):
    review_db = str(tmp_path / "reviews.db")
    inv_db = str(tmp_path / "inventory.db")
    want_db = str(tmp_path / "wants.db")
    user_db = str(tmp_path / "users.db")

    rev_store = SQLiteReviewStore(review_db)
    inv_store = SQLiteInventoryStore(inv_db)
    want_store = SQLiteWantListStore(want_db)

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
        biblio_api_key="mock_key",
    )

    app = create_app(operator=operator, settings=test_settings)
    client = TestClient(app)

    return {
        "client": client,
        "operator": operator,
        "inv_store": inv_store,
        "want_store": want_store,
        "rev_store": rev_store,
        "settings": test_settings,
    }


def test_biblio_service_fetch_orders_dry_run_and_demo():
    # 1. Standard dry-run returns empty list
    orders = BiblioService.fetch_orders(api_key="mock_key", dry_run=True, demo_sample=False)
    assert orders == []

    # 2. Demo sample returns a realistic order
    demo_orders = BiblioService.fetch_orders(api_key="mock_key", dry_run=True, demo_sample=True)
    assert len(demo_orders) == 1
    assert demo_orders[0]["order_id"] == "BIB-892410"
    assert demo_orders[0]["items"][0]["sku"] == "BK-DUNE-1965"


def test_biblio_service_process_order_standard_sync(biblio_env):
    operator = biblio_env["operator"]
    want_store = biblio_env["want_store"]

    order = {
        "order_id": "BIB-1001",
        "items": [{"sku": "BK-HOBBIT-1937", "price": "250.00"}],
    }

    result = BiblioService.process_order(
        order,
        operator=operator,
        tenant_id=1,
        want_list_store=want_store,
    )

    assert result["status"] == "processed"
    assert result["order_id"] == "BIB-1001"
    assert "BK-HOBBIT-1937" in result["processed_skus"]
    assert result["collisions"] == []

    # Verify a sync_inventory proposal was submitted
    items = operator.reviews.list_items(tenant_id=1)
    sync_item = next((i for i in items if i.payload.get("sku") == "BK-HOBBIT-1937"), None)
    assert sync_item is not None
    assert sync_item.operation == "sync_inventory"


def test_biblio_service_contention_with_active_collector_hold(biblio_env):
    operator = biblio_env["operator"]
    want_store = biblio_env["want_store"]

    now = datetime.now(UTC)
    # Seed an active 48-hour courtesy hold for a patron
    collector = want_store.create_collector(Collector(
        id="col-77",
        tenant_id=1,
        name="Lord Ravenscroft",
        email="ravenscroft@antiquarian.org",
    ))
    hold = want_store.create_hold(CollectorHold(
        id="hold-99",
        tenant_id=1,
        sku="BK-DUNE-1965",
        want_item_id="want-1",
        collector_id="col-77",
        collector_name="Lord Ravenscroft",
        book_title="Dune (First Edition, 1965)",
        price_cad=Decimal("180.00"),
        held_at=now,
        expires_at=now + timedelta(hours=48),
        status="active",
    ))

    # An online buyer purchases the same volume on Biblio
    incoming_order = {
        "order_id": "BIB-2002",
        "buyer_name": "Online Collector X",
        "items": [{"sku": "BK-DUNE-1965", "price": "180.00"}],
    }

    result = BiblioService.process_order(
        incoming_order,
        operator=operator,
        tenant_id=1,
        want_list_store=want_store,
    )

    assert result["status"] == "processed"
    assert len(result["collisions"]) == 1
    collision = result["collisions"][0]
    assert collision["sku"] == "BK-DUNE-1965"
    assert collision["hold_id"] == "hold-99"

    # Verify proposal was created as contention_hold_vs_marketplace
    rev = operator.reviews.get(collision["review_id"], tenant_id=1)
    assert rev is not None
    assert rev.operation == "contention_hold_vs_marketplace"
    assert rev.payload["collector_name"] == "Lord Ravenscroft"
    assert rev.payload["order_id"] == "BIB-2002"
    assert rev.payload["source_channel"] == "biblio"


def test_resolve_contention_approve_fulfills_and_releases_hold(biblio_env):
    operator = biblio_env["operator"]
    want_store = biblio_env["want_store"]
    inv_store = biblio_env["inv_store"]

    now = datetime.now(UTC)
    # Add book to inventory
    inv_store.add(
        InventoryItem("BK-RARE-1", "Rare Grimoire", ["Hermes"], "hardcover", "Fine", Decimal("500"), Decimal("600"), "active", now, now),
        tenant_id=1,
    )
    # Add active hold
    want_store.create_hold(CollectorHold(
        id="hold-grimoire",
        tenant_id=1,
        sku="BK-RARE-1",
        want_item_id="w-1",
        collector_id="c-1",
        collector_name="Count Cagliostro",
        book_title="Rare Grimoire",
        held_at=now,
        expires_at=now + timedelta(hours=48),
        status="active",
    ))

    # Submit contention proposal
    proposed = operator.propose(
        operation="contention_hold_vs_marketplace",
        payload={
            "sku": "BK-RARE-1",
            "order_id": "BIB-3003",
            "source_channel": "biblio",
            "hold_id": "hold-grimoire",
            "collector_name": "Count Cagliostro",
        },
        tenant_id=1,
    )
    rev_id = proposed["review_id"]

    # 1. Operator decides to approve (sell to online buyer and release hold)
    dec = operator.decide(rev_id, approve=True, reviewer="head_clerk", reason="Buyer paid full listing price", tenant_id=1)
    assert dec["status"] == "APPROVED"

    # 2. Operator executes
    exec_res = operator.execute(rev_id, actor="head_clerk", tenant_id=1)
    assert exec_res["status"] == "EXECUTED"
    assert exec_res["result"]["status"] == "contention_resolved"
    assert exec_res["result"]["action"] == "fulfilled_marketplace_order"

    # Verify hold is now marked released
    hold_after = want_store.get_hold("hold-grimoire", tenant_id=1)
    assert hold_after.status == "released"
    assert "Released for biblio order BIB-3003" in hold_after.notes

    # Verify inventory item is delisted locally
    item_after = inv_store.get("BK-RARE-1", tenant_id=1)
    assert item_after.status == "delisted"


def test_reject_contention_cancels_biblio_order(biblio_env):
    client = biblio_env["client"]
    operator = biblio_env["operator"]
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'admin@example.com', 'tenant_id': 1, 'role': 'admin'})}"}

    # Propose contention
    proposed = operator.propose(
        operation="contention_hold_vs_marketplace",
        payload={
            "sku": "BK-POETRY-1900",
            "order_id": "BIB-4004",
            "source_channel": "biblio",
            "hold_id": "hold-poetry",
            "collector_name": "Lady Augusta",
        },
        tenant_id=1,
    )
    rev_id = proposed["review_id"]

    # When rejecting via HTTP decision endpoint, it should cancel the order with reason
    with patch("app.api.biblio_service.BiblioService.cancel_order") as mock_cancel:
        mock_cancel.return_value = True
        dec_res = client.post(
            f"/reviews/{rev_id}/decision",
            json={"approve": False, "reason": "Reserved for established patron"},
            headers=admin_headers,
        )
        assert dec_res.status_code == 200
        assert dec_res.json()["status"] == "REJECTED"

        # Verify BiblioService.cancel_order was called
        mock_cancel.assert_called_once()
        args, kwargs = mock_cancel.call_args
        assert args[0] == "BIB-4004"
        assert kwargs["reason"] == "Reserved for established patron"


def test_http_sync_biblio_orders_endpoint(biblio_env):
    client = biblio_env["client"]
    admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'admin@example.com', 'tenant_id': 1, 'role': 'admin'})}"}

    # 1. Standard sync with no orders
    res = client.post("/inventory/biblio/sync-orders", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "synced"
    assert data["marketplace"] == "biblio"
    assert data["orders_checked"] == 0

    # 2. Demo sync
    demo_res = client.post("/inventory/biblio/sync-orders?demo=true", headers=admin_headers)
    assert demo_res.status_code == 200
    demo_data = demo_res.json()
    assert demo_data["status"] == "synced"
    assert demo_data["orders_checked"] == 1
    assert demo_data["orders_processed"] == 1


def test_http_webhook_biblio_endpoint(biblio_env):
    client = biblio_env["client"]

    webhook_payload = {
        "order_id": "BIB-WH-888",
        "items": [{"sku": "BK-TEST-WH", "price": "45.00"}],
    }

    res = client.post("/webhooks/biblio", json=webhook_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "processed"
    assert data["order_id"] == "BIB-WH-888"


def test_biblio_listing_adapter_delist_and_cancel():
    adapter = BiblioListingAdapter(api_key="mock_key", dry_run=True)
    assert adapter.delist_book("BK-DUNE-1965") is True
    assert adapter.cancel_order("BIB-111", reason="Out of stock") is True
