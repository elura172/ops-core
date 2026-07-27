from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.amazon.fees import FeeSchedule, LandedCost, SizeTier
from app.amazon.models import AmazonListing, RankPoint
from app.amazon.screen import ScreenPolicy, rejection_summary, screen

VERIFIED = FeeSchedule(verified=True)
START = date(2026, 1, 1)


def ranks(*values):
    return tuple(RankPoint(START + timedelta(days=30 * i), v) for i, v in enumerate(values))


def listing(**kw):
    base = dict(
        asin="B000TEST01",
        title="Stainless Steel Pour-Over Kettle",
        category="home_kitchen",
        price=Decimal("32.99"),
        review_count=900,
        rating=Decimal("3.9"),
        seller_count=2,
        size_tier=SizeTier.SMALL_STANDARD,
        rank_history=ranks(9_000, 9_400, 8_800, 9_100, 9_300, 8_900),
        brand_registered=False,
    )
    return AmazonListing(**{**base, **kw})


def landed(**kw):
    base = dict(
        fob_unit_cost=Decimal("5.50"),
        freight_per_unit=Decimal("1.40"),
        duty_rate=Decimal("0.032"),
        tariff_rate=Decimal("0.25"),
        prep_per_unit=Decimal("0.60"),
    )
    return LandedCost(**{**base, **kw})


def run(listings, costs=None, **policy_kw):
    costs = costs if costs is not None else {ls.asin: landed() for ls in listings}
    return screen(
        listings,
        costs,
        policy=ScreenPolicy(**policy_kw) if policy_kw else None,
        fees=VERIFIED,
    )


def test_a_good_candidate_survives():
    survivors, rejected = run([listing()])
    assert [o.asin for o in survivors] == ["B000TEST01"]
    assert rejected == {}
    assert survivors[0].headroom > 0


def test_every_rejection_reason_is_reported_not_just_the_first():
    """One marginal failure is worth revisiting; five is not. The caller needs to
    see which."""
    bad = listing(review_count=4, rating=Decimal("4.9"), seller_count=40)
    _, rejected = run([bad])
    assert len(rejected["B000TEST01"]) >= 3


def test_spiky_seasonal_demand_is_rejected():
    """Rank 800 in December and 200,000 the rest of the year ties up capital for
    eleven months."""
    seasonal = listing(rank_history=ranks(200_000, 180_000, 150_000, 800, 190_000, 210_000))
    _, rejected = run([seasonal])
    assert any("spiky or seasonal" in r for r in rejected["B000TEST01"])


def test_steady_demand_scores_above_spiky_demand_of_equal_volume():
    steady = listing(asin="STEADY", rank_history=ranks(9_000, 9_100, 8_900, 9_050, 9_000, 9_100))
    survivors, _ = run([steady], min_rank_stability=0.0)
    spiky = listing(asin="SPIKY", rank_history=ranks(2_000, 16_000, 3_000, 15_000, 2_500, 15_500))
    spiky_survivors, _ = run([spiky], min_rank_stability=0.0)
    assert survivors[0].score > spiky_survivors[0].score


def test_a_beloved_incumbent_is_rejected_for_having_no_gap():
    """4.8 stars means there is nothing for an improved product to fix."""
    _, rejected = run([listing(rating=Decimal("4.8"))])
    assert any("no quality gap" in r for r in rejected["B000TEST01"])


def test_crowded_listings_are_rejected():
    _, rejected = run([listing(seller_count=25)])
    assert any("reseller pile-on" in r for r in rejected["B000TEST01"])


def test_brand_registered_incumbents_are_rejected_by_default():
    _, rejected = run([listing(brand_registered=True)])
    assert any("brand-registered" in r for r in rejected["B000TEST01"])


def test_missing_landed_cost_is_rejected_not_assumed_cheap():
    """Without a supplier quote there is no honest margin, so no guess is made."""
    _, rejected = run([listing()], costs={})
    assert any("no landed-cost estimate" in r for r in rejected["B000TEST01"])


def test_thin_margin_is_rejected_even_when_demand_is_perfect():
    expensive = {"B000TEST01": landed(fob_unit_cost=Decimal("19.00"))}
    survivors, rejected = run([listing()], costs=expensive)
    assert survivors == []
    assert any("margin" in r or "ROI" in r for r in rejected["B000TEST01"])


def test_tariffs_alone_can_kill_a_candidate():
    """The clearest argument for modelling Section 301 rather than ignoring it.

    Same product, same $10.50 FOB, same $32.99 sale price. Without the tariff it
    clears at 30.7% margin / 78.9% ROI. With 25% applied it drops to 22.8% / 48.6%
    and fails both floors — a spreadsheet that omits the line says "build it".
    """
    marginal = landed(fob_unit_cost=Decimal("10.50"), tariff_rate=Decimal("0"))
    tariffed = landed(fob_unit_cost=Decimal("10.50"), tariff_rate=Decimal("0.25"))
    passes, _ = run([listing()], costs={"B000TEST01": marginal})
    fails, rejected = run([listing()], costs={"B000TEST01": tariffed})
    assert passes and not fails
    assert any("margin" in r for r in rejected["B000TEST01"])


def test_survivors_are_ranked_best_first():
    strong = listing(asin="STRONG", review_count=2_500, rating=Decimal("3.7"))
    weak = listing(asin="WEAK", review_count=120, rating=Decimal("4.3"))
    survivors, _ = run([weak, strong])
    assert [o.asin for o in survivors] == ["STRONG", "WEAK"]
    assert survivors[0].score > survivors[1].score


def test_rejection_summary_shows_which_constraint_dominates():
    """If one reason kills everything, the policy is miscalibrated — not the market
    empty. That distinction decides whether to widen filters or abandon a category."""
    crowd = [listing(asin=f"B{i:09d}", seller_count=30) for i in range(6)]
    _, rejected = run(crowd)
    summary = rejection_summary(rejected)
    assert next(iter(summary)).startswith("sellers on the listing")
    assert summary[next(iter(summary))] == 6


def test_screening_an_empty_corpus_is_not_an_error():
    survivors, rejected = run([])
    assert survivors == [] and rejected == {}


def test_policy_bounds_are_validated():
    with pytest.raises(ValueError):
        ScreenPolicy(min_price=Decimal("80"), max_price=Decimal("20"))
    with pytest.raises(ValueError):
        ScreenPolicy(min_reviews=900, max_reviews=100)
