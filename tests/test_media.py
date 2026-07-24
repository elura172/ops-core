import pytest

from app.media.models import AdDraft, AssetLedger, AssetLicense, MediaAsset
from app.media.pipeline import DisabledMediaRenderer, DryRunMediaRenderer


def asset(asset_id="a-1", match=True):
    return MediaAsset(asset_id, "https://assets.example/product.jpg", "image", AssetLicense.SUPPLIER_AUTHORIZED, product_match_confirmed=match)


def draft(assets=None):
    return AdDraft("ad-1", "product-1", tuple(assets or [asset()]), "See the difference", "Stackable glass storage.", "Shop now")


def test_asset_ledger_requires_product_match():
    ledger = AssetLedger()
    ledger.add(asset())
    assert ledger.product_assets("product-1", ("a-1",))[0].asset_id == "a-1"
    ledger.add(asset("a-2", match=False))
    with pytest.raises(ValueError):
        ledger.product_assets("product-1", ("a-2",))


def test_dry_run_renderer_creates_no_external_media():
    result = DryRunMediaRenderer().render(draft())
    assert result.dry_run
    assert result.output_uri.startswith("dry-run://")


def test_disabled_renderer_blocks_unapproved_rendering():
    with pytest.raises(RuntimeError):
        DisabledMediaRenderer().render(draft())


def test_draft_rejects_unlicensed_asset_metadata():
    bad = MediaAsset("a", "x", "image", AssetLicense.LICENSED)
    with pytest.raises(ValueError):
        draft([bad]).validate()
