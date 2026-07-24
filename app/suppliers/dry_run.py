from __future__ import annotations

from collections.abc import Iterable

from app.suppliers.base import (
    FulfillmentRequest,
    FulfillmentResult,
    SupplierOffer,
    validate_fulfillment_request,
    validate_offer,
)


class DryRunSupplier:
    """Local adapter for integration tests; never contacts a supplier."""

    def __init__(self, offers: Iterable[SupplierOffer] = ()) -> None:
        self.offers = list(offers)
        self.requests: list[FulfillmentRequest] = []

    def search(self, query: str) -> list[SupplierOffer]:
        if not query.strip():
            raise ValueError("query cannot be empty")
        return list(self.offers)

    def create_fulfillment_order(self, request: FulfillmentRequest) -> FulfillmentResult:
        validate_fulfillment_request(request)
        self.requests.append(request)
        return FulfillmentResult(
            supplier_order_id=f"DRY-{request.order_id}",
            tracking_number=None,
            dry_run=True,
        )


def select_offer(offers: Iterable[SupplierOffer], *, max_dispatch_hours: int = 48) -> SupplierOffer:
    valid: list[SupplierOffer] = []
    for offer in offers:
        try:
            validate_offer(offer, max_dispatch_hours=max_dispatch_hours)
        except ValueError:
            continue
        valid.append(offer)
    if not valid:
        raise LookupError("no supplier offer satisfies stock and dispatch constraints")
    return min(valid, key=lambda offer: (offer.cost_gbp, offer.dispatch_hours))
