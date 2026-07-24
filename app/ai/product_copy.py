from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.products import CategorySchema, ProductCandidate, ProductPolicyGate


@dataclass(frozen=True)
class ProductCopyRequest:
    product_name: str
    verified_facts: tuple[str, ...]
    market: str


@dataclass(frozen=True)
class ProductCopy:
    title: str
    description: str
    claims: tuple[str, ...]
    source_facts: tuple[str, ...]


class CopyProvider(Protocol):
    def generate(self, request: ProductCopyRequest) -> ProductCopy: ...


class LocalCopyProvider:
    """Deterministic fallback for tests and dry runs; does not call a model."""

    def generate(self, request: ProductCopyRequest) -> ProductCopy:
        if not request.product_name.strip() or not request.verified_facts:
            raise ValueError("product name and verified facts are required")
        facts = tuple(f.strip() for f in request.verified_facts if f.strip())
        if not facts:
            raise ValueError("verified facts cannot be empty")
        return ProductCopy(
            title=request.product_name.strip()[:120],
            description=" ".join(facts),
            claims=(),
            source_facts=facts,
        )


def copy_to_candidate(copy: ProductCopy, *, category_id: str, attributes: dict[str, str], skus, image_urls) -> ProductCandidate:
    """Convert structured output into the existing policy-gated domain model."""
    if not copy.source_facts or any(claim.strip() == "" for claim in copy.claims):
        raise ValueError("AI output must contain grounded facts and non-empty claims")
    return ProductCandidate(
        title=copy.title,
        description=copy.description,
        category_id=category_id,
        attributes=attributes,
        skus=tuple(skus),
        image_urls=tuple(image_urls),
    )


def validate_generated_copy(copy: ProductCopy) -> tuple[str, ...]:
    """Return policy reasons; callers must block output when non-empty."""
    if not copy.title.strip() or not copy.description.strip():
        return ("AI output has an empty title or description",)
    candidate = ProductCandidate(copy.title, copy.description, "validation", {}, (), ("https://invalid",))
    result = ProductPolicyGate().check(candidate, CategorySchema("validation"))
    return result.reasons
