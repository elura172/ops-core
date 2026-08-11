from decimal import Decimal

import pytest

from app.amazon.fees import FeeSchedule, LandedCost, SizeTier
from app.amazon.launch_budget import FixedCosts, LaunchPlan

VERIFIED = FeeSchedule(verified=True)


def plan(**kw):
    base = dict(
        units=300,
        landed=LandedCost(
            fob_unit_cost=Decimal("7.50"),
            freight_per_unit=Decimal("1.60"),
            duty_rate=Decimal("0.03"),
            tariff_rate=Decimal("0.25"),
            prep_per_unit=Decimal("0.75"),
        ),
        sale_price=Decimal("31.95"),
        category="home_kitchen",
        size_tier=SizeTier.LARGE_STANDARD,
        fees=VERIFIED,
    )
    return LaunchPlan(**{**base, **kw})


def test_capital_is_inventory_plus_setup_plus_advertising():
    p = plan()
    assert p.capital_required == p.inventory_cost + p.fixed_cost + p.ppc_budget
    assert p.capital_required > Decimal("6000")


def test_a_typical_first_order_does_not_pay_back_the_brand_setup():
    """The finding that matters. A healthy 27% margin still leaves the first order
    at roughly break-even, because trademark, photography and advertising are paid
    once and amortise over units that a small MOQ does not provide."""
    p = plan(units=300)
    assert p.profit_per_unit > 0
    assert p.break_even_units is not None and p.break_even_units > 300
    assert p.recovers_on_first_order is False
    assert p.profit_if_sold_out < 0


def test_ordering_more_units_is_the_lever_that_fixes_it():
    """Nothing about the product changes — only how far the fixed costs spread."""
    small, large = plan(units=300), plan(units=600)
    assert small.profit_per_unit == large.profit_per_unit
    assert large.recovers_on_first_order is True
    assert large.profit_if_sold_out > 0


def test_a_loss_making_unit_has_no_break_even():
    p = plan(sale_price=Decimal("16.00"))
    assert p.profit_per_unit < 0
    assert p.break_even_units is None
    assert p.recovers_on_first_order is False


def test_lean_setup_lowers_the_barrier_but_not_the_unit_economics():
    lean = FixedCosts(photography=Decimal("0"), inspection=Decimal("0"), samples=Decimal("120"))
    assert lean.total() < FixedCosts().total()
    assert plan(fixed=lean).profit_per_unit == plan().profit_per_unit


def test_cash_is_locked_far_longer_than_the_money_is_spent():
    """Production and freight, then selling through, then Amazon's payout delay."""
    assert plan().cash_cycle_days(120) == 75 + 120 + 14


def test_setup_costs_scale_with_months_before_revenue():
    assert FixedCosts().total(6) > FixedCosts().total(3)
    with pytest.raises(ValueError):
        FixedCosts().total(0)


def test_a_launch_needs_units():
    with pytest.raises(ValueError):
        plan(units=0)


def test_break_even_never_reports_a_recovery_that_has_not_happened():
    """A partial unit does not sell, so break-even is a ceiling.

    Rounding to nearest under-reports it whenever the exact requirement lands
    just above an integer, and then `recovers_on_first_order` contradicts
    `profit_if_sold_out` on the same object -- the plan claims the launch paid
    for itself while the profit on that order is still negative.
    """
    exact = plan(units=1, ppc_budget=Decimal("700"))
    needed = (exact.fixed_cost + exact.ppc_budget) / exact.profit_per_unit
    assert Decimal("299") < needed < Decimal("300"), "fixture no longer straddles an integer"

    # Rounding to nearest would report 299 here, and 299 units lose $1.66.
    at_rounded = plan(units=299, ppc_budget=Decimal("700"))
    assert at_rounded.break_even_units == 300
    assert at_rounded.recovers_on_first_order is False
    assert at_rounded.profit_if_sold_out < 0

    # One more unit is what actually clears it.
    cleared = plan(units=300, ppc_budget=Decimal("700"))
    assert cleared.recovers_on_first_order is True
    assert cleared.profit_if_sold_out >= 0
