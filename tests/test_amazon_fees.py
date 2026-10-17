from decimal import Decimal

import pytest

from app.amazon.fees import (
    FeeSchedule,
    LandedCost,
    SizeTier,
    break_even_price,
    unit_economics,
)

VERIFIED = FeeSchedule(verified=True)


def landed(**kw):
    base = dict(
        fob_unit_cost=Decimal("4.00"),
        freight_per_unit=Decimal("1.20"),
        duty_rate=Decimal("0.03"),
        tariff_rate=Decimal("0.25"),
        prep_per_unit=Decimal("0.55"),
    )
    return LandedCost(**{**base, **kw})


def test_unverified_schedule_refuses_to_price():
    """An estimate must not quietly become the basis of a purchase order."""
    with pytest.raises(ValueError, match="unverified"):
        unit_economics(
            price=Decimal("29.99"),
            landed=landed(),
            category="home_kitchen",
            tier=SizeTier.SMALL_STANDARD,
        )


def test_estimates_are_opt_in_and_flagged():
    e = unit_economics(
        price=Decimal("29.99"),
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        accept_estimates=True,
    )
    assert e.estimates_only is True


def test_tariffs_are_charged_on_customs_value_not_freight():
    """Section 301 applies to the goods, not to what it cost to move them."""
    with_freight = landed(freight_per_unit=Decimal("10.00")).total
    no_freight = landed(freight_per_unit=Decimal("0")).total
    assert with_freight - no_freight == Decimal("10.00")


def test_section_301_materially_changes_landed_cost():
    """The line beginners omit. 25% on a $4 unit is a real slice of margin."""
    without = landed(tariff_rate=Decimal("0")).total
    with_tariff = landed(tariff_rate=Decimal("0.25")).total
    assert with_tariff - without == Decimal("1.00")


def test_margin_and_roi_are_different_numbers():
    """Margin decides whether it is worth doing; ROI decides how fast capital
    recycles. Conflating them is how sellers run out of cash while profitable."""
    e = unit_economics(
        price=Decimal("29.99"),
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        fees=VERIFIED,
    )
    assert e.margin != e.roi
    assert e.roi > e.margin  # landed cost is well below sale price


def test_costs_reconcile_to_the_price():
    e = unit_economics(
        price=Decimal("29.99"),
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        fees=VERIFIED,
    )
    assert e.total_cost + e.net_profit == e.price


def test_break_even_price_actually_breaks_even():
    floor = break_even_price(
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        target_margin=Decimal("0.25"),
        fees=VERIFIED,
    )
    e = unit_economics(
        price=floor,
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        fees=VERIFIED,
    )
    assert abs(e.margin - Decimal("0.25")) < Decimal("0.005")


def test_a_price_below_break_even_loses_money_at_the_target():
    floor = break_even_price(
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        fees=VERIFIED,
    )
    e = unit_economics(
        price=floor - Decimal("3.00"),
        landed=landed(),
        category="home_kitchen",
        tier=SizeTier.SMALL_STANDARD,
        fees=VERIFIED,
    )
    assert e.margin < Decimal("0.25")


def test_low_referral_categories_price_lower():
    kw = dict(landed=landed(), tier=SizeTier.SMALL_STANDARD, fees=VERIFIED)
    assert break_even_price(category="electronics", **kw) < break_even_price(category="home_kitchen", **kw)


def test_bigger_size_tiers_need_higher_prices():
    kw = dict(landed=landed(), category="home_kitchen", fees=VERIFIED)
    small = break_even_price(tier=SizeTier.SMALL_STANDARD, **kw)
    bulky = break_even_price(tier=SizeTier.LARGE_BULKY, **kw)
    assert bulky > small


def test_impossible_economics_are_refused_not_priced():
    with pytest.raises(ValueError, match="exceed the whole sale price"):
        break_even_price(
            landed=landed(),
            category="home_kitchen",
            tier=SizeTier.SMALL_STANDARD,
            target_margin=Decimal("0.90"),
            fees=VERIFIED,
        )


def test_minimum_referral_fee_applies_to_cheap_units():
    fees = FeeSchedule(verified=True)
    assert fees.referral_fee(Decimal("1.00"), "home_kitchen") == fees.minimum_referral


@pytest.mark.parametrize(
    "kw",
    [
        {"fob_unit_cost": Decimal("0")},
        {"freight_per_unit": Decimal("-1")},
        {"tariff_rate": Decimal("1.5")},
    ],
)
def test_impossible_landed_costs_are_refused(kw):
    with pytest.raises(ValueError):
        landed(**kw)
