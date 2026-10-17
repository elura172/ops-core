from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs

import pytest

from app.amazon.sp_api import AmazonSPAPIClient, LwaTokenProvider
from app.publish.books import AmazonSPAPIAdapter


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


def test_lwa_provider_exchanges_refresh_token_and_caches_access_token(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response({"access_token": "access-token", "expires_in": 3600})

    monkeypatch.setattr("app.amazon.sp_api.urlopen", fake_urlopen)
    provider = LwaTokenProvider("client", "secret", "refresh")

    assert provider.get_access_token() == "access-token"
    assert provider.get_access_token() == "access-token"
    assert len(calls) == 1
    request, _timeout = calls[0]
    form = parse_qs(request.data.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh"]
    assert form["client_id"] == ["client"]


def test_sp_api_client_sets_required_headers_and_sends_json(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        return Response({"status": "ACCEPTED"})

    monkeypatch.setattr("app.amazon.sp_api.urlopen", fake_urlopen)
    client = AmazonSPAPIClient(LwaTokenProvider("client", "secret", "refresh"))
    client.token_provider.get_access_token = lambda: "test-token"

    result = client.request("PUT", "/items", query={"marketplaceIds": "CA"}, body={"x": 1})

    assert result == {"status": "ACCEPTED"}
    request = seen[0]
    assert request.get_header("X-amz-access-token") == "test-token"
    assert request.get_header("X-amz-date")
    assert request.get_header("User-agent").startswith("OpsCore/")
    assert json.loads(request.data) == {"x": 1}


def test_offer_quantity_rejects_negative_values():
    client = AmazonSPAPIClient(LwaTokenProvider("client", "secret", "refresh"))
    with pytest.raises(ValueError, match="cannot be negative"):
        client.set_offer_quantity("seller", "sku", "marketplace", -1)


def test_live_adapter_will_not_submit_a_listing_without_schema_prepared_attributes():
    adapter = AmazonSPAPIAdapter(
        refresh_token="refresh",
        client_id="client",
        client_secret="secret",
        merchant_id="seller",
        dry_run=False,
    )
    with patch.object(adapter, "_client") as client:
        result = adapter.list_book("SKU-1", "A Book", "Good", Decimal("10.00"), {"isbn": "9780000000001"})
    assert result is None
    client.assert_not_called()
