from decimal import Decimal

from app.publish.books import (
    AbeBooksListingAdapter,
    AlibrisListingAdapter,
    AmazonSPAPIAdapter,
    BiblioListingAdapter,
    BookListingOrchestrator,
    EbayListingAdapter,
    ShopifyListingAdapter,
)
from app.publish.inventory_sync import MultiChannelInventorySync


def test_abebooks_adapter_dry_run():
    adapter = AbeBooksListingAdapter(dry_run=True)
    assert adapter.channel_name == "abebooks"
    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={"author": "Test Author"},
    )
    assert listing_id == "abe-dryrun-BK-123"
    assert adapter.delist_book("BK-123") is True


def test_amazon_spapi_adapter_dry_run():
    adapter = AmazonSPAPIAdapter(dry_run=True)
    assert adapter.channel_name == "amazon_spapi"
    # Should fail if missing ISBN
    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={"author": "Test Author", "isbn": ""},
    )
    assert listing_id is None

    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={"author": "Test Author", "isbn": "9781234567890"},
    )
    assert listing_id == "amz-dryrun-BK-123"
    assert adapter.delist_book("BK-123") is True


def test_biblio_adapter_dry_run():
    adapter = BiblioListingAdapter(dry_run=True)
    assert adapter.channel_name == "biblio"
    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={},
    )
    assert listing_id == "biblio-dryrun-BK-123"
    assert adapter.delist_book("BK-123") is True


def test_shopify_defaults_to_dry_run_and_refuses_unsupported_live_operations():
    adapter = ShopifyListingAdapter("https://example.myshopify.com", "token")
    assert adapter.dry_run is True
    assert (
        adapter.list_book("BK-123", "Test Book", "Good", Decimal("10.00"), {}) == "gid://shopify/Product/dryrun-BK-123"
    )

    live_adapter = ShopifyListingAdapter("https://example.myshopify.com", "token", dry_run=False)
    assert live_adapter.list_book("BK-123", "Test Book", "Good", Decimal("10.00"), {}) is None
    assert live_adapter.delist_book("BK-123") is False
    assert live_adapter.cancel_order("ORDER-1") is False


def test_alibris_adapter_dry_run():
    adapter = AlibrisListingAdapter(dry_run=True)
    assert adapter.channel_name == "alibris"
    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={},
    )
    assert listing_id == "alibris-dryrun-BK-123"
    assert adapter.delist_book("BK-123") is True


def test_ebay_adapter_dry_run():
    adapter = EbayListingAdapter(dry_run=True)
    assert adapter.channel_name == "ebay"
    listing_id = adapter.list_book(
        sku="BK-123",
        title="Test Book",
        condition="Good",
        price_cad=Decimal("10.00"),
        details={},
    )
    assert listing_id == "ebay-draft-BK-123"
    assert adapter.delist_book("BK-123") is True


def test_orchestrator_routing():
    adapters = (
        AbeBooksListingAdapter(dry_run=True),
        AmazonSPAPIAdapter(dry_run=True),
        BiblioListingAdapter(dry_run=True),
        AlibrisListingAdapter(dry_run=True),
        EbayListingAdapter(dry_run=True),
    )
    orchestrator = BookListingOrchestrator(adapters=adapters)

    # Payload matching collectible logic without ISBN
    payload = {
        "valuation": {
            "channel_strategy": "online_only_collectible",
            "online_price_cad": "150.00",
            "condition_grade": "Very Good",
        },
        "extraction": {
            "title": "Rare Book",
            "authors": ["Famous Author"],
        },
        "disambiguation": {
            "best_candidate": {"title": "Rare Book", "authors": ["Famous Author"]},
            "detected_isbn": "",
        },
    }

    result = orchestrator.list_inventory(payload)
    assert result["status"] == "completed"
    assert "sku" in result
    mr = result["marketplace_results"]

    # Collectible strategy + no ISBN means Amazon skips
    assert mr["amazon_spapi"]["status"] == "skipped"
    assert mr["abebooks"]["status"] == "listed"
    assert mr["biblio"]["status"] == "listed"
    assert mr["alibris"]["status"] == "listed"
    assert mr["ebay"]["status"] == "listed"


def test_inventory_sync():
    adapters = (
        AbeBooksListingAdapter(dry_run=True),
        AmazonSPAPIAdapter(dry_run=True),
        BiblioListingAdapter(dry_run=True),
        AlibrisListingAdapter(dry_run=True),
        EbayListingAdapter(dry_run=True),
    )
    sync = MultiChannelInventorySync(adapters=adapters)

    result = sync.process_sale(sku="BK-999", source_channel="amazon_spapi")
    assert result["sku"] == "BK-999"
    sync_results = result["sync_results"]

    # Should not include source_channel
    assert "amazon_spapi" not in sync_results
    assert sync_results["abebooks"] == "delisted"
    assert sync_results["biblio"] == "delisted"
    assert sync_results["alibris"] == "delisted"
    assert sync_results["ebay"] == "delisted"


def test_orchestrator_physical_only_bargain_bin():
    adapters = (
        AbeBooksListingAdapter(dry_run=True),
        AmazonSPAPIAdapter(dry_run=True),
        BiblioListingAdapter(dry_run=True),
    )
    orchestrator = BookListingOrchestrator(adapters=adapters)

    payload = {
        "valuation": {
            "channel_strategy": "bargain_bin_cart",
            "in_store_price_cad": "8.00",
            "online_price_cad": "0.00",
            "condition_grade": "Fair",
        },
        "extraction": {
            "title": "Old Paperback",
            "authors": ["Local Author"],
            "format": "paperback",
        },
    }

    result = orchestrator.list_inventory(payload)
    assert result["status"] == "skipped"
    assert "sku" in result
    assert result["details"]["title"] == "Old Paperback"
    assert result["details"]["in_store_price_cad"] == Decimal("8.00")
