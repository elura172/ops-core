from decimal import Decimal

import pytest

from app.domain.products import (
    CategorySchema,
    PricingModel,
    ProductCandidate,
    ProductDraftBuilder,
    ProductPolicyGate,
    ProductSKU,
    sync_inventory,
    target_price,
)


def candidate(**overrides):
    values = dict(
        title="Glass storage box",
        description="Stackable kitchen storage.",
        category_id="cat-1",
        attributes={"material": "glass"},
        skus=(ProductSKU("sku-1", Decimal("9.99"), 5, "wh-1"),),
        image_urls=("https://example.test/image.jpg",),
    )
    values.update(overrides)
    return ProductCandidate(**values)


def test_draft_builder_creates_reviewable_draft():
    draft = ProductDraftBuilder().build(candidate(), CategorySchema("cat-1", frozenset({"material"})))
    assert draft["status"] == "DRAFT"
    assert draft["skus"][0]["price"]["currency"] == "GBP"


def test_policy_gate_rejects_missing_attributes_and_claims():
    result = ProductPolicyGate().check(
        candidate(description="Cures pain and guarantees weight loss", attributes={}),
        CategorySchema("cat-1", frozenset({"material", "colour"})),
    )
    assert not result.approved
    assert len(result.reasons) == 2


def test_price_and_inventory_helpers():
    # No fees or VAT: the price is the plain markup.
    free = PricingModel(
        commission_rate=Decimal("0"),
        transaction_fee_rate=Decimal("0"),
        vat_rate=Decimal("0"),
        refund_reserve_rate=Decimal("0"),
    )
    assert target_price(Decimal("5"), pricing=free) == Decimal("10.00")
    assert sync_inventory({"a": 1, "b": 2}, [("a", 3), ("b", 2)]) == {"a": 3}
    with pytest.raises(ValueError):
        sync_inventory({}, [("a", -1)])


def test_target_price_actually_leaves_the_requested_margin():
    """A plain markup ignores fees, which is how a '50% margin' sells at a loss."""
    cost, pricing = Decimal("10.00"), PricingModel()
    price = target_price(cost, Decimal("0.50"), pricing)
    net = price * (Decimal("1") - pricing.deduction_rate)
    assert (net - cost) / net == pytest.approx(Decimal("0.50"), abs=Decimal("0.001"))
    # The naive answer would have been £20.00 and netted well under half.
    assert price > Decimal("20.00")


def test_shipping_is_carried_into_the_price():
    posted = PricingModel(shipping_gbp=Decimal("3.50"))
    assert target_price(Decimal("10"), pricing=posted) > target_price(Decimal("10"))


def test_impossible_margin_is_refused_rather_than_priced():
    greedy = PricingModel(
        commission_rate=Decimal("0.50"),
        transaction_fee_rate=Decimal("0.30"),
        refund_reserve_rate=Decimal("0.10"),
    )
    with pytest.raises(ValueError, match="no room"):
        target_price(Decimal("10"), Decimal("0.95"), greedy)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"commission_rate": Decimal("-0.1")},
        {"vat_rate": Decimal("1")},
        {"shipping_gbp": Decimal("-1")},
    ],
)
def test_pricing_model_rejects_impossible_rates(kwargs):
    with pytest.raises(ValueError):
        PricingModel(**kwargs)
