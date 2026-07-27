from decimal import Decimal

import pytest

from app.amazon.prefilter import QuickCandidate, parse_bulk, prefilter

BATCH = (
    "https://m.media-amazon.com/images/I/61x.jpg;https://m.media-amazon.com/images/I/71y.jpg,"
    "Copper Water Bottle - 34oz - Handcrafted Ayurvedic 100% Pure Copper Water Bottle,"
    "$ 28.49,$ 37.48,$ 28.49,,Copper,34 Ounces,B0D91MVL5P\n"
    "https://m.media-amazon.com/images/I/91m.jpg,"
    "Retrospec Sedona Meditation Cushion, Buckwheat Pillow, Meditation Pillow,"
    "$ 26.99,$ 26.99,$ 26.99,$ 24.56,Basil,1 Count (Pack of 1),B092DXFGVH\n"
)


def candidate(title="Gooseneck Pour-Over Kettle Stainless Steel", price="29.99"):
    return QuickCandidate("B0TEST0001", title, Decimal(price))


def codes(findings):
    return {f.code for f in findings}


def test_a_plain_product_passes():
    assert prefilter(candidate()) == []


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Mint Flavored Toothpicks Birch Wood", "ingestible"),
        ("Ayurvedic Copper Water Bottle", "health_claim"),
        ("Rechargeable LED Desk Lamp", "battery_or_electronic"),
        ("Toilet Paper Case of 96 Rolls", "bulk_commodity"),
    ],
)
def test_hard_exclusions_reject(title, code):
    findings = prefilter(candidate(title))
    assert code in codes(findings)
    assert all(not f.soft for f in findings if f.code == code)


def test_price_floor_and_ceiling():
    assert "price_too_low" in codes(prefilter(candidate(price="7.99")))
    assert "price_too_high" in codes(prefilter(candidate(price="120.00")))


def test_dominant_brands_are_unwinnable():
    assert "dominant_brand" in codes(prefilter(candidate("Amazon Basics Copy Paper 10 Reams")))
    assert "dominant_brand" in codes(prefilter(candidate("BIC Xtra-Smooth Mechanical Pencils")))


def test_large_multipacks_compete_on_price_not_product():
    assert "commodity_multipack" in codes(
        candidate("Gel Pens 60 Count Assorted")
        and prefilter(candidate("Gel Pens 60 Count Assorted"))
    )
    assert "commodity_multipack" not in codes(prefilter(candidate("Gel Pens 6 Count Assorted")))


def test_keyword_stuffed_titles_flag_softly_rather_than_reject():
    """Amazon titles are stuffed for search reach. A garden spade whose title ends
    'Kids Sand Beach Shovels' is not a children's product, and rejecting it
    outright would discard a legitimate candidate."""
    findings = prefilter(
        candidate("Garden Shovel for Digging, 31 inch Mini Spade, Camping, Kids Sand Beach Shovels")
    )
    assert [f.code for f in findings] == ["kids_or_baby"]
    assert findings[0].soft is True


def test_bulk_parsing_finds_fields_by_shape_not_position():
    parsed = parse_bulk(BATCH)
    assert [c.asin for c in parsed] == ["B0D91MVL5P", "B092DXFGVH"]
    assert "Copper Water Bottle" in parsed[0].title
    assert "http" not in parsed[0].title


def test_bulk_parsing_takes_the_lowest_price():
    """Exports carry list, deal and current price. A competitor has to beat the
    lowest of them, so that is the one that matters."""
    assert parse_bulk(BATCH)[1].price == Decimal("24.56")


def test_rows_without_an_asin_or_price_are_skipped_not_guessed():
    assert parse_bulk("just some text with no identifiers\n") == []
    assert parse_bulk("A title long enough to qualify but no asin,$ 19.99\n") == []


def test_dominant_drinkware_brands_are_recognised():
    """Simple Modern and Owala own drinkware the way BIC owns pens. The first
    version of this list omitted them and passed a Simple Modern bottle."""
    for brand in ("Simple Modern Ceramic-Lined Water Bottle", "Owala FreeSip Insulated"):
        assert "dominant_brand" in codes(prefilter(candidate(brand, price="29.97")))


def test_saturated_categories_flag_softly():
    """Demand is real and enormous; that is the problem. A differentiator can
    still win, so this warns rather than rejects."""
    findings = prefilter(candidate("Insulated Water Bottle 32oz Stainless", price="24.99"))
    saturated = [f for f in findings if f.code == "saturated_category"]
    assert saturated and saturated[0].soft is True


def test_an_ordinary_product_trips_neither():
    findings = prefilter(candidate("Gooseneck Pour-Over Kettle Stainless Steel", price="32.99"))
    assert not [f for f in findings if f.code in {"dominant_brand", "saturated_category"}]


def test_several_products_on_one_line_are_split():
    """Copied exports do not reliably carry newlines between records; the ASIN is
    the only dependable boundary."""
    one_line = (
        "https://x.jpg,DAYYET Garlic Keeper for Counter Ceramic with Acacia Wood Lid,"
        "$ 16.99,,White,Small,B0BGNWQLC8 "
        "https://y.jpg,Okavango Garlic Keeper Terracotta Container Countertop,"
        "$ 39.99,,Ivory,16oz,B0DJHFR2GT"
    )
    parsed = parse_bulk(one_line)
    assert [c.asin for c in parsed] == ["B0BGNWQLC8", "B0DJHFR2GT"]
    assert [c.price for c in parsed] == [Decimal("16.99"), Decimal("39.99")]
    assert "Okavango" in parsed[1].title
    assert "DAYYET" not in parsed[1].title
