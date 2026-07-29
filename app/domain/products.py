from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True)
class CategorySchema:
    category_id: str
    required_attributes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ProductSKU:
    seller_sku: str
    price_gbp: Decimal
    quantity: int
    warehouse_id: str
    supplier_sku: str | None = None


@dataclass(frozen=True)
class ProductCandidate:
    title: str
    description: str
    category_id: str
    attributes: dict[str, str]
    skus: tuple[ProductSKU, ...]
    image_urls: tuple[str, ...] = ()
    brand_authorized: bool = False
    certifications: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyResult:
    approved: bool
    reasons: tuple[str, ...] = ()


class ProductPolicyGate:
    """Conservative preflight checks; platform review remains authoritative."""

    banned_terms = re.compile(
        r"\b(cures?|detox|fat\s*burn(?:ing)?|weight\s*loss|guaranteed\s*results?)\b",
        re.IGNORECASE,
    )

    def check(self, candidate: ProductCandidate, schema: CategorySchema) -> PolicyResult:
        reasons: list[str] = []
        text = f"{candidate.title} {candidate.description}"
        if self.banned_terms.search(text):
            reasons.append("description contains a restricted health or performance claim")
        if candidate.category_id != schema.category_id:
            reasons.append("candidate category does not match the retrieved category schema")
        missing = sorted(schema.required_attributes - candidate.attributes.keys())
        if missing:
            reasons.append(f"missing required attributes: {', '.join(missing)}")
        if not candidate.image_urls:
            reasons.append("at least one product image is required")
        if len(candidate.image_urls) > 9:
            reasons.append("image count exceeds the local draft limit")
        if any(s.quantity < 0 or s.price_gbp <= 0 for s in candidate.skus):
            reasons.append("SKU quantity or price is invalid")
        return PolicyResult(not reasons, tuple(reasons))


@dataclass(frozen=True)
class PricingModel:
    """Everything between the listed price and what actually lands in the bank.

    Defaults are conservative placeholders — confirm commission and VAT for your
    category and market before pricing anything real.
    """

    commission_rate: Decimal = Decimal("0.09")
    transaction_fee_rate: Decimal = Decimal("0.02")
    vat_rate: Decimal = Decimal("0.20")
    refund_reserve_rate: Decimal = Decimal("0.03")
    shipping_gbp: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        rates = (
            self.commission_rate,
            self.transaction_fee_rate,
            self.vat_rate,
            self.refund_reserve_rate,
        )
        if any(rate < 0 or rate >= 1 for rate in rates) or self.shipping_gbp < 0:
            raise ValueError("pricing rates must be within [0, 1) and shipping cannot be negative")

    @property
    def deduction_rate(self) -> Decimal:
        """Fraction of the listed price that never reaches contribution margin."""
        vat_share = self.vat_rate / (Decimal("1") + self.vat_rate)
        return (
            vat_share + self.commission_rate + self.transaction_fee_rate + self.refund_reserve_rate
        )


def target_price(
    cost_gbp: Decimal,
    margin: Decimal = Decimal("0.50"),
    pricing: PricingModel | None = None,
) -> Decimal:
    """Listed price that leaves ``margin`` of net revenue after costs and fees.

    Marking cost up by the margin alone ignores commission, payment fees, VAT, and
    refunds, which is how a listing that looks 50% profitable sells at a loss.
    """
    if cost_gbp <= 0 or not Decimal("0") < margin < Decimal("1"):
        raise ValueError("cost must be positive and margin must be between 0 and 1")
    pricing = pricing or PricingModel()
    landed_cost = cost_gbp + pricing.shipping_gbp
    retained = Decimal("1") - pricing.deduction_rate
    net_share = retained * (Decimal("1") - margin)
    if net_share <= 0:
        raise ValueError("fees and margin leave no room in the price; lower the target margin")
    return (landed_cost / net_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class ProductDraftBuilder:
    policy_gate: ProductPolicyGate = field(default_factory=ProductPolicyGate)

    def build(self, candidate: ProductCandidate, schema: CategorySchema) -> dict[str, Any]:
        result = self.policy_gate.check(candidate, schema)
        if not result.approved:
            raise ValueError("product failed policy gate: " + "; ".join(result.reasons))
        return {
            "title": candidate.title[:120],
            "description": candidate.description,
            "category_id": candidate.category_id,
            "attributes": dict(candidate.attributes),
            "images": list(candidate.image_urls),
            "status": "DRAFT",
            "skus": [
                {
                    "seller_sku": sku.seller_sku,
                    "supplier_sku": sku.supplier_sku,
                    "price": {"amount": str(sku.price_gbp), "currency": "GBP"},
                    "inventory": {"warehouse_id": sku.warehouse_id, "quantity": sku.quantity},
                }
                for sku in candidate.skus
            ],
        }


def sync_inventory(local: dict[str, int], remote: Iterable[tuple[str, int]]) -> dict[str, int]:
    """Return changed quantities; caller decides whether to submit them."""
    changes = {}
    for sku, quantity in remote:
        if quantity < 0:
            raise ValueError(f"negative inventory for {sku}")
        if local.get(sku) != quantity:
            changes[sku] = quantity
    return changes
