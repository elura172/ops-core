from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.ebay_service import EbayService
from app.api.ebay_webhooks import handle_ebay_challenge, handle_ebay_order_webhook
from app.api.http import create_app
from app.api.operator import OperatorService
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.domain.order_store import OrderStore
from app.publish.books import EbayListingAdapter
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from tests.conftest import ADMIN_TOKEN, auth


def test_ebay_condition_mapping():
    assert EbayService.map_condition_to_ebay("Fine") == ("LIKE_NEW", "2750")
    assert EbayService.map_condition_to_ebay("As New") == ("LIKE_NEW", "2750")
    assert EbayService.map_condition_to_ebay("Near Fine") == ("VERY_GOOD", "4000")
    assert EbayService.map_condition_to_ebay("Very Good") == ("VERY_GOOD", "4000")
    assert EbayService.map_condition_to_ebay("Good") == ("GOOD", "5000")
    assert EbayService.map_condition_to_ebay("Fair") == ("ACCEPTABLE", "6000")
    assert EbayService.map_condition_to_ebay("Unknown Grade") == ("GOOD", "5000")


def test_ebay_description_formatting():
    html = EbayService.format_description(
        title="Roughing It in the Bush",
        authors=["Susanna Moodie"],
        format_str="hardcover",
        condition="Fine",
        section="Canadiana / Rare",
        shelf="C-04",
        provenance="Book Bazaar Ottawa Collection (1974)",
    )
    assert "Roughing It in the Bush" in html
    assert "Susanna Moodie" in html
    assert "Canadiana / Rare" in html
    assert "C-04" in html
    assert "Book Bazaar Ottawa Collection" in html


def test_ebay_service_dry_run_push_and_delist():
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-BB-MOODIE-1",
        title="Roughing It in the Bush",
        authors=["Susanna Moodie"],
        format="hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("175.00"),
        online_price_cad=Decimal("195.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Canadiana",
        shelf="C-02",
    )

    # 1. Push inventory item draft
    res = EbayService.push_inventory_item(item, dry_run=True)
    assert res["status"] == "synced"
    assert res["mode"] == "dry-run"
    assert res["sku"] == "BK-BB-MOODIE-1"
    assert res["condition"] == "LIKE_NEW"
    assert res["condition_id"] == "2750"
    assert res["listing_draft_id"] == "ebay-draft-BK-BB-MOODIE-1"
    assert res["price_cad"] == 195.0

    # 2. Delist inventory item
    delist_ok = EbayService.delist_item("BK-BB-MOODIE-1", dry_run=True)
    assert delist_ok is True


def test_ebay_adapter_contract():
    adapter = EbayListingAdapter(dry_run=True)
    assert adapter.channel_name == "ebay"

    draft_id = adapter.list_book(
        sku="BK-BB-FRYE-1",
        title="The Bush Garden",
        condition="Very Good",
        price_cad=Decimal("45.00"),
        details={
            "authors": ["Northrop Frye"],
            "format": "paperback",
            "section": "Literary Criticism",
            "shelf": "L-01",
        },
    )
    assert draft_id == "ebay-draft-BK-BB-FRYE-1"
    assert adapter.delist_book("BK-BB-FRYE-1") is True
    assert adapter.cancel_order("EBAY-ORD-12345") is True


def test_ebay_webhook_challenge():
    challenge = "test_challenge_code_999"
    token = "secret_verification_token_777"
    endpoint = "https://ops-core-khaki.vercel.app/webhooks/ebay"

    expected_hash = hashlib.sha256(f"{challenge}{token}{endpoint}".encode("utf-8")).hexdigest()
    res = handle_ebay_challenge(challenge, token, endpoint)
    assert res["challengeResponse"] == expected_hash


def test_ebay_sale_webhook_corvus_auto_approves_and_delists_store():
    # Model client: The Book Bazaar (Bank St, Ottawa)
    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)
    book = InventoryItem(
        sku="BK-BB-TWO-SOLITUDES",
        title="Two Solitudes (First Edition)",
        authors=["Hugh MacLennan"],
        format="hardcover",
        condition="Near Fine",
        in_store_price_cad=Decimal("120.00"),
        online_price_cad=Decimal("135.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Canadiana Rare",
        shelf="R-01",
    )
    store.add(book, tenant_id=1)
    assert store.get("BK-BB-TWO-SOLITUDES", tenant_id=1).status == "active"

    # Setup operator with executor wired to the store
    queue = ReviewQueue()
    executor = ApprovedActionExecutor(client=None, inventory_store=store)
    operator = OperatorService(queue, executor)

    # Inbound eBay order notification event
    event = {
        "metadata": {
            "topic": "MARKETPLACE_ORDER_CREATED"
        },
        "data": {
            "order": {
                "orderId": "EBAY-1974-998811",
                "lineItems": [
                    {
                        "sku": "BK-BB-TWO-SOLITUDES",
                        "title": "Two Solitudes (First Edition)",
                        "quantity": 1,
                    }
                ]
            }
        }
    }

    result = handle_ebay_order_webhook(event, operator, tenant_id=1)
    assert result["status"] == 200
    assert result["processed_skus"] == ["BK-BB-TWO-SOLITUDES"]

    # Verify Ops-Core store is delisted
    updated = store.get("BK-BB-TWO-SOLITUDES", tenant_id=1)
    assert updated is not None
    assert updated.status == "delisted"

    # Verify Corvus executed the review item
    reviews = queue.list_items(pending_only=False, tenant_id=1)
    assert len(reviews) == 1
    review = reviews[0]
    assert review.status.value == "EXECUTED"
    assert review.executed_by == "agent:corvus"
    assert review.result["inventory_store"] == "delisted"
    # External channels delisted
    sync_res = review.result["sync_results"]
    assert sync_res["square"] == "delisted"
    assert sync_res["amazon_spapi"] == "delisted"
    assert sync_res["abebooks"] == "delisted"
    assert sync_res["shopify"] == "delisted"
    assert "ebay" not in sync_res  # eBay is source channel, skipped


def test_ebay_http_endpoints(settings):
    app = create_app(order_repository=OrderStore(), settings=settings)
    client = TestClient(app)

    # 1. Webhook GET challenge
    resp_challenge = client.get("/webhooks/ebay?challenge_code=chal123")
    assert resp_challenge.status_code == 200
    assert "challengeResponse" in resp_challenge.json()

    # 2. Push single book to eBay draft
    resp_draft = client.post(
        "/inventory/BK-DUNE-1965/ebay/draft",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp_draft.status_code == 200
    data = resp_draft.json()
    assert data["status"] == "synced"
    assert data["sku"] == "BK-DUNE-1965"
    assert data["listing_draft_id"] == "ebay-draft-BK-DUNE-1965"

    # 3. Sync all books to eBay
    resp_all = client.post(
        "/inventory/ebay/sync-all",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp_all.status_code == 200
    all_data = resp_all.json()
    assert all_data["status"] == "completed"
    assert all_data["synced_count"] >= 1

    # 4. Sync orders endpoint
    resp_orders = client.post(
        "/inventory/ebay/sync-orders",
        headers=auth(ADMIN_TOKEN),
    )
    assert resp_orders.status_code == 200
    assert resp_orders.json()["marketplace"] == "ebay"
