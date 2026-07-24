from decimal import Decimal

import pytest

from app.domain.products import (
    CategorySchema,
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
    draft = ProductDraftBuilder().build(
        candidate(), CategorySchema("cat-1", frozenset({"material"}))
    )
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
    assert target_price(Decimal("5")) == Decimal("10.00")
    assert sync_inventory({"a": 1, "b": 2}, [("a", 3), ("b", 2)]) == {"a": 3}
    with pytest.raises(ValueError):
        sync_inventory({}, [("a", -1)])
