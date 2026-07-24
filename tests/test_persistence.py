from datetime import datetime, timezone

from app.api.webhook_service import handle_order_webhook
from app.db.sqlite import SQLiteOrderRepository
from app.domain.order_store import OrderEvent
from app.domain.orders import OrderStatus


def test_sqlite_repository_is_durable(tmp_path):
    path = tmp_path / "orders.db"
    event = OrderEvent("e-1", "o-1", OrderStatus.PAID, datetime.now(timezone.utc))
    repo = SQLiteOrderRepository(path)
    assert repo.apply(event)
    repo.close()
    reopened = SQLiteOrderRepository(path)
    assert not reopened.apply(event)
    reopened.close()


def test_webhook_handler_verifies_and_deduplicates():
    import hashlib, hmac
    body = b'{"event_id":"e-1","order_id":"o-1","status":"PAID"}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    repo = SQLiteOrderRepository(":memory:")
    assert handle_order_webhook(body, signature, "secret", repo)["accepted"]
    result = handle_order_webhook(body, signature, "secret", repo)
    assert result["duplicate"] is True
    assert handle_order_webhook(body, "bad", "secret", repo)["status"] == 401
    repo.close()
