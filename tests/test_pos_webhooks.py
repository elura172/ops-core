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
                        {"catalog_object_id": "BK-ABC"}
                    ]
                }
            }
        }
    }
    body = json.dumps(payload).encode("utf-8")
    signature = sign_square(url, body, secret)
    
    result = handle_square_webhook(url, body, signature, secret, operator)
    assert result["status"] == 200
    assert result["processed_skus"] == ["BK-XYZ", "BK-ABC"]
    
    assert operator.propose.call_count == 2
    
    # Check the first call
    operator.propose.assert_any_call(
        operation="sync_inventory",
        payload={"sku": "BK-XYZ", "source_channel": "square"}
    )
