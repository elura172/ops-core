from unittest.mock import MagicMock, patch

from app.domain.carriers import ShippoAdapter


def test_shippo_adapter_dry_run():
    adapter = ShippoAdapter(api_key="test_key", dry_run=True)
    payload = {
        "order_id": "TEST-123",
        "shipping_address": {
            "name": "Jane Doe",
            "line1": "456 Oak St",
            "city": "Vancouver",
            "state": "BC",
            "postal_code": "V6B 1A1",
            "country": "CA"
        },
        "preferred_provider": "Canada Post"
    }
    
    result = adapter.create_label(payload)
    
    assert result["dry_run"] is True
    assert result["status"] == "label_generated"
    assert "SHIPPO-DRYRUN" in result["tracking_number"]
    assert result["provider"] == "Canada Post"
    
    debug = result.get("debug_payload", {})
    assert debug["address_to"]["name"] == "Jane Doe"
    assert debug["address_to"]["state"] == "BC"
    assert debug["address_to"]["country"] == "CA"


@patch("app.domain.carriers.shippo.Shippo")
def test_shippo_adapter_live_call(mock_shippo_class):
    # Setup mock client
    mock_client = MagicMock()
    mock_shippo_class.return_value = mock_client
    
    # Mock shipment creation
    mock_shipment = MagicMock()
    mock_rate = MagicMock()
    mock_rate.object_id = "rate_123"
    mock_shipment.rates = [mock_rate]
    mock_client.shipments.create.return_value = mock_shipment
    
    # Mock transaction creation
    mock_transaction = MagicMock()
    mock_transaction.status = "SUCCESS"
    mock_transaction.tracking_number = "TRACK123"
    mock_transaction.rate.provider = "Canada Post"
    mock_transaction.label_url = "https://example.com/label.pdf"
    mock_client.transactions.create.return_value = mock_transaction
    
    adapter = ShippoAdapter(api_key="live_key", dry_run=False)
    payload = {
        "order_id": "TEST-123",
        "shipping_address": {
            "name": "Jane Doe",
            "line1": "456 Oak St",
            "city": "Vancouver",
            "state": "BC",
            "postal_code": "V6B 1A1",
            "country": "CA"
        },
        "preferred_provider": "Canada Post"
    }
    
    result = adapter.create_label(payload)
    
    assert result["dry_run"] is False
    assert result["tracking_number"] == "TRACK123"
    assert result["provider"] == "Canada Post"
    assert result["label_url"] == "https://example.com/label.pdf"
    assert result["status"] == "label_generated"
    
    # Verify the SDK was called
    mock_client.shipments.create.assert_called_once()
    mock_client.transactions.create.assert_called_once()
