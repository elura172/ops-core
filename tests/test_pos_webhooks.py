import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock

from app.api.pos_webhooks import handle_square_webhook, verify_square_signature


def sign_square(url: str, body: bytes, secret: str) -> str:
    content = url.encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), content, hashlib.sha256).digest()
    return base64.b64encode(expected).decode("utf-8")

def test_verify_square_signature():
    secret = "test_secret"
    url = "https://example.com/webhooks/square"
    body = b'{"type":"order.created"}'
    
    signature = sign_square(url, body, secret)
    assert verify_square_signature(url, body, signature, secret) is True
    assert verify_square_signature(url, body, "bad_signature", secret) is False

def test_handle_square_webhook_invalid_signature():
    operator = MagicMock()
    result = handle_square_webhook(
        url="https://example.com/webhooks/square",
        body=b'{"type":"order.created"}',
        signature="bad_signature",
        secret="test_secret",
        operator=operator
    )
    assert result["status"] == 401
    operator.propose.assert_not_called()

def test_handle_square_webhook_ignored_event():
    operator = MagicMock()
    secret = "test_secret"
    url = "https://example.com/webhooks/square"
    body = b'{"type":"payment.updated"}'
    signature = sign_square(url, body, secret)
    
    result = handle_square_webhook(url, body, signature, secret, operator)
    assert result["status"] == 200
    assert "ignored event type" in result["message"]
    operator.propose.assert_not_called()

def test_handle_square_webhook_valid_order():
    operator = MagicMock()
    operator.propose.return_value = {"review_id": "r-123"}
    
    secret = "test_secret"
    url = "https://example.com/webhooks/square"
    
    payload = {
        "type": "order.created",
        "data": {
            "object": {
                "order": {
                    "state": "OPEN",
                    "line_items": [
                        {"note": "BK-XYZ"},
                        {"catalog_object_id": "sq-cat-BK-ABC"},
                        {"catalog_object_id": "#item-BK-DEF"}
                    ]
                }
            }
        }
    }
    body = json.dumps(payload).encode("utf-8")
    signature = sign_square(url, body, secret)
    
    result = handle_square_webhook(url, body, signature, secret, operator, tenant_id=1)
    assert result["status"] == 200
    assert result["processed_skus"] == ["BK-XYZ", "BK-ABC", "BK-DEF"]
    
    assert operator.propose.call_count == 3
    
    operator.propose.assert_any_call(
        operation="sync_inventory",
        payload={"sku": "BK-XYZ", "source_channel": "square", "tenant_id": 1},
        tenant_id=1,
    )
    operator.propose.assert_any_call(
        operation="sync_inventory",
        payload={"sku": "BK-ABC", "source_channel": "square", "tenant_id": 1},
        tenant_id=1,
    )
    operator.propose.assert_any_call(
        operation="sync_inventory",
        payload={"sku": "BK-DEF", "source_channel": "square", "tenant_id": 1},
        tenant_id=1,
    )


def test_square_sync_delists_online_and_local_inventory():
    from datetime import UTC, datetime
    from decimal import Decimal
    from app.db.inventory import InventoryItem, SQLiteInventoryStore
    from app.review.executor import ApprovedActionExecutor

    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-SQUARE-SOLD-1",
        title="Snow Crash",
        authors=["Neal Stephenson"],
        format="paperback",
        condition="Fine",
        in_store_price_cad=Decimal("20.00"),
        online_price_cad=Decimal("25.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi",
        shelf="A-01",
    )
    store.add(item, tenant_id=1)

    assert store.get("BK-SQUARE-SOLD-1", tenant_id=1).status == "active"

    executor = ApprovedActionExecutor(client=None, inventory_store=store)

    # Simulate Square sale sync_inventory
    result = executor._sync_inventory({
        "sku": "BK-SQUARE-SOLD-1",
        "source_channel": "square",
        "tenant_id": 1,
    })

    assert result["sku"] == "BK-SQUARE-SOLD-1"
    # Online marketplaces delisted
    sync_res = result["sync_results"]
    assert sync_res["abebooks"] == "delisted"
    assert sync_res["amazon_spapi"] == "delisted"
    assert sync_res["biblio"] == "delisted"
    assert sync_res["alibris"] == "delisted"
    assert sync_res["ebay"] == "delisted"
    assert sync_res["shopify"] == "delisted"
    # Square source channel is skipped
    assert "square" not in sync_res

    # Internal inventory store updated to delisted
    assert result["inventory_store"] == "delisted"
    updated_item = store.get("BK-SQUARE-SOLD-1", tenant_id=1)
    assert updated_item.status == "delisted"


def test_online_sync_delists_square_and_local_inventory():
    from datetime import UTC, datetime
    from decimal import Decimal
    from app.db.inventory import InventoryItem, SQLiteInventoryStore
    from app.review.executor import ApprovedActionExecutor

    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-ONLINE-SOLD-1",
        title="Cryptonomicon",
        authors=["Neal Stephenson"],
        format="hardcover",
        condition="Fine",
        in_store_price_cad=Decimal("45.00"),
        online_price_cad=Decimal("50.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi",
        shelf="A-02",
    )
    store.add(item, tenant_id=1)

    executor = ApprovedActionExecutor(client=None, inventory_store=store)

    # Sale on Amazon SP-API
    result = executor._sync_inventory({
        "sku": "BK-ONLINE-SOLD-1",
        "source_channel": "amazon_spapi",
        "tenant_id": 1,
    })

    assert result["sku"] == "BK-ONLINE-SOLD-1"
    sync_res = result["sync_results"]
    assert sync_res["square"] == "delisted"
    assert sync_res["ebay"] == "delisted"
    assert result["inventory_store"] == "delisted"
    assert store.get("BK-ONLINE-SOLD-1", tenant_id=1).status == "delisted"


def test_square_webhook_to_corvus_auto_execution_delists_store():
    from datetime import UTC, datetime
    from decimal import Decimal
    from app.api.operator import OperatorService
    from app.db.inventory import InventoryItem, SQLiteInventoryStore
    from app.review.executor import ApprovedActionExecutor
    from app.review.queue import ReviewQueue

    # 1. Setup in-memory inventory with active book
    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-SQUARE-E2E-1",
        title="Count Zero",
        authors=["William Gibson"],
        format="paperback",
        condition="Very Good",
        in_store_price_cad=Decimal("18.00"),
        online_price_cad=Decimal("22.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi",
        shelf="A-03",
    )
    store.add(item, tenant_id=1)
    assert store.get("BK-SQUARE-E2E-1", tenant_id=1).status == "active"

    # 2. Wire operator with real executor and queue
    queue = ReviewQueue()
    executor = ApprovedActionExecutor(client=None, inventory_store=store)
    operator = OperatorService(queue, executor)

    # 3. Simulate Square webhook delivery
    secret = "pos_secret_123"
    url = "https://ops-core.example.com/webhooks/square"
    payload = {
        "type": "order.created",
        "data": {
            "object": {
                "order": {
                    "state": "COMPLETED",
                    "line_items": [
                        {"note": "BK-SQUARE-E2E-1"}
                    ]
                }
            }
        }
    }
    body = json.dumps(payload).encode("utf-8")
    signature = sign_square(url, body, secret)

    # 4. Handle webhook (triggers propose -> Corvus wake -> triage -> execute)
    res = handle_square_webhook(
        url=url,
        body=body,
        signature=signature,
        secret=secret,
        operator=operator,
        tenant_id=1,
    )

    assert res["status"] == 200
    assert res["processed_skus"] == ["BK-SQUARE-E2E-1"]

    # 5. Verify local inventory is now DELISTED!
    updated_book = store.get("BK-SQUARE-E2E-1", tenant_id=1)
    assert updated_book is not None
    assert updated_book.status == "delisted"

    # 6. Verify review item was auto-executed by Corvus
    reviews = queue.list_items(pending_only=False, tenant_id=1)
    assert len(reviews) == 1
    review = reviews[0]
    assert review.status.value == "EXECUTED"
    assert review.executed_by == "agent:corvus"
    assert review.result["inventory_store"] == "delisted"
    assert review.result["sync_results"]["abebooks"] == "delisted"
    assert review.result["sync_results"]["amazon_spapi"] == "delisted"


