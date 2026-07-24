from fastapi.testclient import TestClient

from app.api.http import create_app
from app.domain.order_store import OrderStore


def test_health_and_review_routes():
    client = TestClient(create_app(order_repository=OrderStore()))
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/reviews").json() == []


def test_webhook_route_rejects_bad_signature():
    client = TestClient(create_app(order_repository=OrderStore()))
    response = client.post("/webhooks/orders", content=b"{}", headers={"X-Signature": "bad"})
    assert response.json()["status"] == 401
