import pytest

from app.ai.product_copy import (
    LocalCopyProvider,
    ProductCopy,
    ProductCopyRequest,
    validate_generated_copy,
)


def test_local_provider_is_grounded_and_deterministic():
    result = LocalCopyProvider().generate(
        ProductCopyRequest("Storage Box", ("Glass", "Stackable"), "GB")
    )
    assert result.claims == ()
    assert result.source_facts == ("Glass", "Stackable")


def test_provider_requires_verified_facts():
    with pytest.raises(ValueError):
        LocalCopyProvider().generate(ProductCopyRequest("Thing", (), "GB"))


def test_generated_claims_are_policy_checked():
    result = validate_generated_copy(ProductCopy("Thing", "Cures pain", (), ("Cures pain",)))
    assert result
