from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.users import SQLiteUserStore
from app.domain.order_store import OrderStore
from app.integrations.protocol import signature
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from app.vault import encrypt_credential

PREFIX = "/api/integrations/custom-site/v1"


@pytest.fixture
def site_with_channels(tmp_path, settings):
    inventory = SQLiteInventoryStore(str(tmp_path / "inventory.db"))
    connections = [
        {
            "connection_id": "test_shop",
            "tenant_id": 1,
            "inbound_key_id": "site_in",
            "inbound_secret": "a" * 40,
            "outbound_key_id": "site_out",
            "outbound_secret": "b" * 40,
            "website_url": "https://website.example",
            "checkout_enabled": True,
            "stock_protection_enabled": True,
            "delivery_enabled": True,
            "channels": ["square", "abebooks", "ebay", "biblio"],
        }
    ]
    users = SQLiteUserStore(":memory:")
    # Seed live credentials for tenant 1
    users.save_integration(1, "square", encrypt_credential("sq0atp-live-test-token-real-123456789"))
    users.save_integration(1, "abebooks", encrypt_credential("abe_live_real_key_987654321"))
    users.save_integration(1, "ebay", encrypt_credential("ebay_live_oauth_token_456789123"))
    users.save_integration(1, "biblio", encrypt_credential("biblio_live_real_key_12345"))

    executor = ApprovedActionExecutor(None, inventory_store=inventory)
    operator = OperatorService(ReviewQueue(), executor)
    ATTESTED = {
        "signing_verified": True,
        "sandbox_passed": True,
        "pii_controls_verified": True,
    }
    app = create_app(
        operator=operator,
        order_repository=OrderStore(),
        inventory_store=inventory,
        user_store=users,
        settings=replace(
            settings,
            custom_site_connections=json.dumps(connections),
            dry_run=False,
            live_enabled=True,
            **ATTESTED,
        ),
    )
    c = TestClient(app)
    service = app.state.custom_site_service
    connection = service.connections["test_shop"]

    now = datetime.now(UTC)
    sku = "BK-DELIST-1"
    inventory.add(
        InventoryItem(
            sku,
            "Example Rare Book",
            ["Author Person"],
            "trade_paperback",
            "Good",
            Decimal("35.00"),
            Decimal("35.00"),
            "active",
            now,
            now,
        )
    )
    service.ledger.publish(connection, sku, "published")

    return {
        "client": c,
        "app": app,
        "service": service,
        "connection": connection,
        "inventory": inventory,
        "user_store": users,
        "sku": sku,
    }


def _reserve_and_pay(site_fixture):
    client = site_fixture["client"]
    sku = site_fixture["sku"]

    # 1. Reserve
    res_body = {
        "schema_version": "1.0",
        "checkout_id": "chk_delist_test",
        "items": [
            {
                "sku": sku,
                "quantity": 1,
                "unit_price": {"amount": "35.00", "currency": "CAD"},
            }
        ],
    }
    raw_res = json.dumps(res_body).encode()
    ts = str(int(datetime.now(UTC).timestamp()))
    eid_res = "11111111-1111-1111-1111-111111111111"
    sig_res = signature("a" * 40, "POST", PREFIX + "/reservations", ts, eid_res, raw_res)

    res_resp = client.post(
        PREFIX + "/reservations",
        content=raw_res,
        headers={
            "Content-Type": "application/json",
            "X-Ops-Key-Id": "site_in",
            "X-Ops-Timestamp": ts,
            "X-Ops-Event-Id": eid_res,
            "X-Ops-Signature": sig_res,
        },
    )
    assert res_resp.status_code == 201
    reservation_id = res_resp.json()["reservation_id"]

    # 2. Pay
    pay_body = {
        "schema_version": "1.0",
        "event_id": "22222222-2222-2222-2222-222222222222",
        "event_type": "order.paid",
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "order_id": "ord_delist_test",
        "reservation_id": reservation_id,
        "items": res_body["items"],
    }
    raw_pay = json.dumps(pay_body).encode()
    sig_pay = signature(
        "a" * 40,
        "POST",
        PREFIX + "/order-events",
        ts,
        pay_body["event_id"],
        raw_pay,
    )
    pay_resp = client.post(
        PREFIX + "/order-events",
        content=raw_pay,
        headers={
            "Content-Type": "application/json",
            "X-Ops-Key-Id": "site_in",
            "X-Ops-Timestamp": ts,
            "X-Ops-Event-Id": pay_body["event_id"],
            "X-Ops-Signature": sig_pay,
        },
    )
    assert pay_resp.status_code == 202
    return pay_body["event_id"]


def test_square_and_abebooks_marketplace_delist_delivery_success(site_with_channels):
    event_id = _reserve_and_pay(site_with_channels)
    service = site_with_channels["service"]
    client = site_with_channels["client"]

    # Mock custom_site website snapshot transport to succeed
    service.transport = lambda c, job: (True, False)

    # Patch real calls to Square, AbeBooks, eBay, and Biblio
    with (
        patch(
            "app.api.square_service.SquareService.delist_catalog_item_raw",
            return_value=True,
        ) as mock_sq,
        patch(
            "app.api.abebooks_service.AbeBooksService.delist_book",
            return_value=True,
        ) as mock_abe,
        patch(
            "app.api.ebay_service.EbayService.delist_item",
            return_value=True,
        ) as mock_ebay,
        patch(
            "app.api.biblio_service.BiblioService.delist_book",
            return_value=True,
        ) as mock_biblio,
    ):
        drain_result = service.drain()
        assert drain_result["succeeded"] >= 4  # custom_site snapshot + square + abebooks + ebay + biblio

        mock_sq.assert_called_once()
        mock_abe.assert_called_once()
        mock_ebay.assert_called_once()
        mock_biblio.assert_called_once()

    # Verify event status shows succeeded for all channels
    ts = str(int(datetime.now(UTC).timestamp()))
    eid_status = "33333333-3333-3333-3333-333333333333"
    sig_status = signature(
        "a" * 40,
        "GET",
        f"{PREFIX}/order-events/{event_id}",
        ts,
        eid_status,
        b"",
    )
    status_resp = client.get(
        f"{PREFIX}/order-events/{event_id}",
        headers={
            "X-Ops-Key-Id": "site_in",
            "X-Ops-Timestamp": ts,
            "X-Ops-Event-Id": eid_status,
            "X-Ops-Signature": sig_status,
        },
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "completed"
    results = {r["channel"]: r["status"] for r in status_data["channel_results"]}
    assert results["square"] == "succeeded"
    assert results["abebooks"] == "succeeded"
    assert results["ebay"] == "succeeded"
    assert results["biblio"] == "succeeded"


def test_square_delist_catalog_item_raw_live_search_and_delete():
    from app.api.square_service import SquareService

    calls = []

    class MockResponse:
        def __init__(self, data, code=200):
            self.data = data
            self.code = code

        def read(self):
            return json.dumps(self.data).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=15.0):
        url = req.full_url
        data = json.loads(req.data.decode("utf-8")) if req.data else {}
        calls.append({"url": url, "data": data, "headers": req.headers})
        if "/catalog/search" in url:
            assert data["query"]["exact_query"]["attribute_value"] == "BK-TEST-SQ-DEL"
            return MockResponse(
                {
                    "objects": [
                        {
                            "type": "ITEM_VARIATION",
                            "id": "VAR_999",
                            "item_variation_data": {
                                "item_id": "ITEM_888",
                                "sku": "BK-TEST-SQ-DEL",
                            },
                        }
                    ]
                }
            )
        elif "/catalog/batch-delete" in url:
            assert set(data["object_ids"]) == {"VAR_999", "ITEM_888"}
            return MockResponse({"deleted_object_ids": ["VAR_999", "ITEM_888"]})
        return MockResponse({})

    with patch("app.api.square_service.urlopen", side_effect=mock_urlopen):
        ok = SquareService.delist_catalog_item_raw(
            sku="BK-TEST-SQ-DEL",
            access_token="sq0atp-live-real-secret-123456",
            dry_run=False,
        )
        assert ok is True
        assert len(calls) == 2
        assert "/catalog/search" in calls[0]["url"]
        assert "/catalog/batch-delete" in calls[1]["url"]


def test_square_delist_catalog_item_raw_item_not_found():
    from app.api.square_service import SquareService

    class MockSearchEmpty:
        def read(self):
            return json.dumps({"objects": []}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch("app.api.square_service.urlopen", return_value=MockSearchEmpty()):
        ok = SquareService.delist_catalog_item_raw(
            sku="BK-NOT-IN-SQUARE",
            access_token="sq0atp-live-real-secret-123456",
            dry_run=False,
        )
        assert ok is True  # Not in catalog is treated as already delisted


def test_channel_delivery_network_error_triggers_retry(site_with_channels):
    event_id = _reserve_and_pay(site_with_channels)
    service = site_with_channels["service"]

    # Custom site succeeds, but Square throws network error
    service.transport = lambda c, job: (True, False)

    with (
        patch(
            "app.api.square_service.SquareService.delist_catalog_item_raw",
            side_effect=Exception("Connection timed out to Square Connect API"),
        ),
        patch(
            "app.api.abebooks_service.AbeBooksService.delist_book",
            return_value=True,
        ),
        patch(
            "app.api.ebay_service.EbayService.delist_item",
            return_value=True,
        ),
        patch(
            "app.api.biblio_service.BiblioService.delist_book",
            return_value=True,
        ),
    ):
        drain_result = service.drain()
        assert drain_result["retrying"] >= 1  # Square job is retried, not terminated immediately

    # Verify event status shows processing because Square is still retrying
    client = site_with_channels["client"]
    ts = str(int(datetime.now(UTC).timestamp()))
    eid_status = "44444444-4444-4444-4444-444444444444"
    sig_status = signature(
        "a" * 40,
        "GET",
        f"{PREFIX}/order-events/{event_id}",
        ts,
        eid_status,
        b"",
    )
    status_resp = client.get(
        f"{PREFIX}/order-events/{event_id}",
        headers={
            "X-Ops-Key-Id": "site_in",
            "X-Ops-Timestamp": ts,
            "X-Ops-Event-Id": eid_status,
            "X-Ops-Signature": sig_status,
        },
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "processing"
    results = {r["channel"]: r["status"] for r in status_data["channel_results"]}
    assert results["square"] == "pending"
    assert results["abebooks"] == "succeeded"
    assert results["ebay"] == "succeeded"
    assert results["biblio"] == "succeeded"
