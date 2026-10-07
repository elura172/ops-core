import uuid
from typing import Any, Protocol

import shippo
from shippo.models import components


class CarrierAdapter(Protocol):
    def create_label(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

class ShippoAdapter:
    def __init__(self, api_key: str, dry_run: bool = False):
        self.api_key = api_key
        self.dry_run = dry_run
        if not dry_run:
            self.client = shippo.Shippo(api_key_header=f"shippo_{api_key}")

    def create_label(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Maps a standard operations payload to a Shippo Shipment and Transaction.
        """
        address_to = payload.get("shipping_address", {})
        # Map ops-core address to Shippo format
        shippo_address_to = {
            "name": address_to.get("name", "Customer"),
            "street1": address_to.get("line1", ""),
            "street2": address_to.get("line2", ""),
            "city": address_to.get("city", ""),
            "state": address_to.get("state", ""),
            "zip": address_to.get("postal_code", ""),
            "country": address_to.get("country", "CA"),
            "phone": address_to.get("phone", "+10000000000"),
            "email": address_to.get("email", "customer@example.com"),
        }

        # The sender address would typically be configured globally.
        shippo_address_from = {
            "name": "Ops-Core Bookstore",
            "street1": "123 Main St",
            "city": "Toronto",
            "state": "ON",
            "zip": "M5V 2K7",
            "country": "CA",
            "phone": "+14165550199",
            "email": "fulfillment@opscore.local",
        }

        # A standard parcel for a book order
        shippo_parcel = {
            "length": "10",
            "width": "8",
            "height": "4",
            "distance_unit": "in",
            "weight": "2",
            "mass_unit": "lb",
        }

        provider = payload.get("preferred_provider", "usps")

        if self.dry_run:
            mock_tracking = f"SHIPPO-DRYRUN-{uuid.uuid4().hex[:8].upper()}"
            return {
                "tracking_number": mock_tracking,
                "provider": provider,
                "label_url": f"https://example.com/mock-labels/{mock_tracking}.pdf",
                "status": "label_generated",
                "dry_run": True,
                # Include mapped payload for audit/debugging
                "debug_payload": {
                    "address_to": shippo_address_to,
                    "address_from": shippo_address_from,
                    "parcel": shippo_parcel,
                }
            }

        # Live API Call
        shipment = self.client.shipments.create(
            components.ShipmentCreateRequest(
                address_from=components.AddressCreateRequest(**shippo_address_from),
                address_to=components.AddressCreateRequest(**shippo_address_to),
                parcels=[components.ParcelCreateRequest(**shippo_parcel)],
                async_=False
            )
        )

        # In a real environment, you'd select the rate that matches the preferred_provider.
        # For simplicity, we just pick the first available rate, or you can purchase by object_id.
        rate_id = shipment.rates[0].object_id if shipment.rates else None
        if not rate_id:
            raise ValueError("No rates returned by Shippo")

        transaction = self.client.transactions.create(
            components.TransactionCreateRequest(
                rate=rate_id,
                label_file_type="PDF",
                async_=False
            )
        )

        if transaction.status != "SUCCESS":
            raise ValueError(f"Failed to generate label: {transaction.messages}")

        return {
            "tracking_number": transaction.tracking_number,
            "provider": transaction.rate.provider,
            "label_url": transaction.label_url,
            "status": "label_generated",
            "dry_run": False
        }
