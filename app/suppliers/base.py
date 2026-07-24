from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class SupplierOffer:
    supplier_product_id: str
    supplier_sku: str
    cost_gbp: Decimal
    available_quantity: int
    dispatch_hours: int
    warehouse_region: str


@dataclass(frozen=True)
class FulfillmentRequest:
    order_id: str
    supplier_sku: str
    quantity: int
    # Resolve this reference only inside a controlled fulfillment service.
    recipient_address_ref: str


@dataclass(frozen=True)
class FulfillmentResult:
    supplier_order_id: str
    tracking_number: str | None
    dry_run: bool


class Supplier(Protocol):
    def search(self, query: str) -> list[SupplierOffer]: ...
    def create_fulfillment_order(self, request: FulfillmentRequest) -> FulfillmentResult: ...


def validate_offer(offer: SupplierOffer, *, max_dispatch_hours: int = 48) -> None:
    if not offer.supplier_product_id or not offer.supplier_sku:
        raise ValueError("supplier product and SKU are required")
    if offer.cost_gbp <= 0 or offer.available_quantity <= 0:
        raise ValueError("supplier cost and stock are invalid")
    if offer.dispatch_hours > max_dispatch_hours:
        raise ValueError("supplier dispatch time exceeds configured SLA")
    if not offer.warehouse_region:
        raise ValueError("supplier warehouse region is required")


def validate_fulfillment_request(request: FulfillmentRequest) -> None:
    if not all((request.order_id, request.supplier_sku, request.recipient_address_ref)):
        raise ValueError("fulfillment request requires IDs and an address reference")
    if request.quantity < 1:
        raise ValueError("fulfillment quantity must be positive")
