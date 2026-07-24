from decimal import Decimal

import pytest

from app.suppliers.base import FulfillmentRequest, SupplierOffer, validate_offer
from app.suppliers.dry_run import DryRunSupplier, select_offer


def offer(cost="4.00", dispatch=24, stock=10, sku="s-1"):
    return SupplierOffer("p-1", sku, Decimal(cost), stock, dispatch, "UK")


def test_select_offer_prefers_valid_low_cost_offer():
    selected = select_offer([offer(cost="3.00", dispatch=72), offer(cost="5.00")])
    assert selected.supplier_sku == "s-1"
    assert selected.cost_gbp == Decimal("5.00")


def test_select_offer_rejects_no_valid_stock_or_sla():
    with pytest.raises(LookupError):
        select_offer([offer(stock=0), offer(dispatch=72)])


def test_dry_run_fulfillment_keeps_only_address_reference():
    supplier = DryRunSupplier([offer()])
    result = supplier.create_fulfillment_order(FulfillmentRequest("o-1", "s-1", 1, "vault://address/o-1"))
    assert result.dry_run
    assert supplier.requests[0].recipient_address_ref == "vault://address/o-1"


def test_offer_validation_requires_region():
    with pytest.raises(ValueError):
        validate_offer(SupplierOffer("p", "s", Decimal("2"), 1, 24, ""))
