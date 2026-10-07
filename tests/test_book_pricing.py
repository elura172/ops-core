from __future__ import annotations

from decimal import Decimal

from app.discovery.pricing import (
    BookValuationEngine,
    infer_condition_grade,
    round_to_retail_ending,
)
from app.domain.books import (
    BookConditionGrade,
    BookFormat,
    BookValuation,
    DisambiguationCandidate,
    DisambiguationResult,
    MarketComp,
    SpineExtractionItem,
)


def test_infer_condition_grade():
    spine_fair = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Test",
        visible_condition_cues=("torn_or_chipped_jacket", "sunned_faded_spine"),
    )
    assert infer_condition_grade(spine_fair) == BookConditionGrade.FAIR

    spine_good = SpineExtractionItem(
        stack_order=2,
        box_2d=(0, 0, 100, 100),
        title="Test",
        visible_condition_cues=("creased_reading_spine",),
    )
    assert infer_condition_grade(spine_good) == BookConditionGrade.GOOD

    spine_fine = SpineExtractionItem(
        stack_order=3,
        box_2d=(0, 0, 100, 100),
        title="Test",
        visible_condition_cues=("clean_as_new",),
    )
    assert infer_condition_grade(spine_fine) == BookConditionGrade.FINE


def test_round_to_retail_ending():
    assert round_to_retail_ending(Decimal("2.50")) == Decimal("2.00")
    assert round_to_retail_ending(Decimal("4.80")) == Decimal("4.50")
    assert round_to_retail_ending(Decimal("7.30")) == Decimal("7.50")
    assert round_to_retail_ending(Decimal("12.10")) == Decimal("11.95")


def _mock_comps() -> tuple[MarketComp, ...]:
    return (
        MarketComp(
            source="abebooks",
            item_price_cad=Decimal("12.00"),
            shipping_price_cad=Decimal("8.00"),
            total_price_cad=Decimal("20.00"),
        ),
        MarketComp(
            source="abebooks",
            item_price_cad=Decimal("14.00"),
            shipping_price_cad=Decimal("7.00"),
            total_price_cad=Decimal("21.00"),
        ),
        MarketComp(
            source="abebooks",
            item_price_cad=Decimal("15.50"),
            shipping_price_cad=Decimal("6.50"),
            total_price_cad=Decimal("22.00"),
        ),
        MarketComp(
            source="abebooks",
            item_price_cad=Decimal("18.00"),
            shipping_price_cad=Decimal("5.00"),
            total_price_cad=Decimal("23.00"),
        ),
        MarketComp(
            source="abebooks",
            item_price_cad=Decimal("25.00"),
            shipping_price_cad=Decimal("4.00"),
            total_price_cad=Decimal("29.00"),
        ),
    )


class StaticCompsProvider:
    def __init__(self, comps: tuple[MarketComp, ...] = ()):
        self._comps = comps

    def fetch_comps(
        self,
        title: str,
        author: str | None = None,
        isbn: str | None = None,
    ) -> tuple[MarketComp, ...]:
        return self._comps


def test_valuation_engine_with_comps():
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Neuromancer",
        authors=("William Gibson",),
        format=BookFormat.MASS_MARKET_PAPERBACK,
        visible_condition_cues=("clean_as_new",),
    )
    cand = DisambiguationCandidate(
        work_id="OL27258W",
        title="Neuromancer",
        authors=("William Gibson",),
        first_publish_year=1984,
        isbns=("0441569595",),
        match_score=0.85,
    )
    disambiguation = DisambiguationResult(
        spine_item=spine,
        matched=True,
        best_candidate=cand,
        detected_isbn="0441569595",
    )

    engine = BookValuationEngine(comps_provider=StaticCompsProvider(_mock_comps()))
    val = engine.value_book(disambiguation)

    assert isinstance(val, BookValuation)
    assert val.condition_grade == BookConditionGrade.FINE
    assert val.price_confidence == "high"
    assert val.channel_strategy == "in_store_and_online"
    assert val.in_store_price_cad > Decimal("0")
    assert val.online_price_cad > Decimal("0")
    assert "AbeBooks" in val.pricing_rationale


def test_valuation_engine_heuristic_fallback():
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Obscure Pamphlet 1842",
        format=BookFormat.TRADE_PAPERBACK,
        visible_condition_cues=("sunned_faded_spine",),
    )
    disambiguation = DisambiguationResult(
        spine_item=spine,
        matched=False,
    )
    engine = BookValuationEngine(comps_provider=StaticCompsProvider())
    val = engine.value_book(disambiguation)

    assert val.price_confidence == "low"
    assert val.condition_grade == BookConditionGrade.VERY_GOOD
    assert "baseline" in val.pricing_rationale


def test_valuation_engine_bargain_bin():
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Outdated Textbook",
        format=BookFormat.MASS_MARKET_PAPERBACK,
        visible_condition_cues=("torn_or_chipped_jacket", "cocked_spine"),
    )
    disambiguation = DisambiguationResult(
        spine_item=spine,
        matched=False,
    )
    engine = BookValuationEngine(comps_provider=StaticCompsProvider())
    val = engine.value_book(disambiguation)

    assert val.channel_strategy == "bargain_bin_cart"
    assert val.in_store_price_cad == Decimal("2.00")


def test_valuation_engine_collectible_flag():
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Dune",
        authors=("Frank Herbert",),
        format=BookFormat.HARDCOVER_WITH_JACKET,
        visible_condition_cues=("clean_as_new",),
        edition_or_series_markers=("First Edition",),
    )
    cand = DisambiguationCandidate(
        work_id="OL893414W",
        title="Dune",
        authors=("Frank Herbert",),
        first_publish_year=1965,
        match_score=0.90,
    )
    disambiguation = DisambiguationResult(
        spine_item=spine,
        matched=True,
        best_candidate=cand,
        needs_copyright_check=True,
        suggested_catalog_action="inspect_edition_points",
    )
    engine = BookValuationEngine(comps_provider=StaticCompsProvider(_mock_comps()))
    val = engine.value_book(disambiguation)

    assert val.channel_strategy == "online_only_collectible"
    assert "FLAGGED" in val.pricing_rationale


def test_pricing_engine_with_abebooks_and_format_fallback():
    from app.pricing.engine import PricingEngine
    from app.amazon.scraper import AmazonScraperClient, SnapshotStore

    scraper = AmazonScraperClient("mock", SnapshotStore(), live=False)

    # 1. With static comps provider
    engine = PricingEngine(scraper, comps_provider=StaticCompsProvider(_mock_comps()))
    data = engine.discover_price("Neuromancer", "William Gibson", condition_grade="Very Good", format_hint="trade_paperback")
    assert data["evidence"]["comps_found"] == 5
    assert float(data["recommended_price_cad"]) > 0
    assert float(data["recommended_price_cad"]) < 50
    assert "Priced from 5 live marketplace comps" in data["rationale"]

    # 2. With no comps -> format baseline fallback (not $100 placeholder)
    empty_engine = PricingEngine(scraper, comps_provider=StaticCompsProvider(()))
    data2 = empty_engine.discover_price("Unknown Rare 1800", "Anonymous", condition_grade="Good", format_hint="mass_market_paperback")
    assert data2["evidence"]["comps_found"] == 0
    assert data2["recommended_price_cad"] == "7.95"  # retail rounded: $7.95
    assert "No live marketplace comps found" in data2["rationale"]
    assert "mass market paperback" in data2["rationale"]
