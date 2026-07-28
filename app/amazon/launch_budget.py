from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.amazon.fees import (
    FeeSchedule,
    LandedCost,
    SizeTier,
    UnitEconomics,
    unit_economics,
)

PENNY = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class FixedCosts:
    """Costs paid once per brand, before a single unit sells.

    Every figure is a **placeholder pending verification**. Fee schedules and
    filing fees change, and the trademark line in particular depends on where you
    are domiciled.
    """

    #: USPTO filing, per class of goods.
    trademark_filing: Decimal = Decimal("350")
    #: A US-licensed attorney. For an applicant domiciled outside the United
    #: States this is not optional — USPTO requires representation. Verify the
    #: current rule before budgeting it away.
    trademark_attorney: Decimal = Decimal("800")
    #: GS1 barcode. A single GTIN is cheap; a company prefix costs more and is
    #: worth it only past a handful of SKUs.
    barcodes: Decimal = Decimal("30")
    #: Professional selling plan, per month.
    seller_account_monthly: Decimal = Decimal("39.99")
    #: Product photography. Zero if you shoot it yourself; the main image must be
    #: a real photograph of the real product, so this cannot be generated.
    photography: Decimal = Decimal("250")
    #: Third-party inspection at the factory before the goods ship.
    inspection: Decimal = Decimal("150")
    #: Supplier samples, several suppliers, shipped express.
    samples: Decimal = Decimal("200")

    def total(self, months_before_revenue: int = 3) -> Decimal:
        if months_before_revenue < 1:
            raise ValueError("budget at least one month of the selling plan")
        return _money(
            self.trademark_filing
            + self.trademark_attorney
            + self.barcodes
            + self.photography
            + self.inspection
            + self.samples
            + self.seller_account_monthly * months_before_revenue
        )


@dataclass(frozen=True)
class LaunchPlan:
    """A first order for one product, priced end to end."""

    units: int
    landed: LandedCost
    sale_price: Decimal
    category: str
    size_tier: SizeTier
    ppc_budget: Decimal = Decimal("750")
    fixed: FixedCosts = field(default_factory=FixedCosts)
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    #: Production, sea freight and customs before the first unit is sellable.
    lead_time_days: int = 75
    #: A first order that sells out in this many days at the assumed rate.
    months_before_revenue: int = 3

    def __post_init__(self) -> None:
        if self.units < 1:
            raise ValueError("a launch needs at least one unit")
        if self.ppc_budget < 0:
            raise ValueError("advertising budget cannot be negative")

    @property
    def inventory_cost(self) -> Decimal:
        return _money(self.landed.total * self.units)

    @property
    def fixed_cost(self) -> Decimal:
        return self.fixed.total(self.months_before_revenue)

    @property
    def capital_required(self) -> Decimal:
        """Everything that leaves the bank before revenue arrives."""
        return _money(self.inventory_cost + self.fixed_cost + self.ppc_budget)

    def economics(self, *, accept_estimates: bool = True) -> UnitEconomics:
        return unit_economics(
            price=self.sale_price,
            landed=self.landed,
            category=self.category,
            tier=self.size_tier,
            fees=self.fees,
            accept_estimates=accept_estimates,
        )

    @property
    def profit_per_unit(self) -> Decimal:
        return Decimal(self.economics().net_profit)

    @property
    def revenue_if_sold_out(self) -> Decimal:
        return _money(self.sale_price * self.units)

    @property
    def profit_if_sold_out(self) -> Decimal:
        """Profit on the first order, after the one-off brand costs are absorbed."""
        return _money(self.profit_per_unit * self.units - self.fixed_cost - self.ppc_budget)

    @property
    def break_even_units(self) -> int | None:
        """Units that must sell before the launch has returned its own cost."""
        per_unit = self.profit_per_unit
        if per_unit <= 0:
            return None
        needed = (self.fixed_cost + self.ppc_budget) / per_unit
        return int(needed.to_integral_value(rounding=ROUND_HALF_UP))

    @property
    def recovers_on_first_order(self) -> bool:
        units = self.break_even_units
        return units is not None and units <= self.units

    def cash_cycle_days(self, sell_through_days: int) -> int:
        """Days between paying the supplier and the last unit turning into cash.

        The number that catches people. Capital is not merely spent, it is locked:
        production and freight before anything can sell, then however long the
        order takes to sell through, then Amazon's own payout delay on top.
        """
        if sell_through_days < 1:
            raise ValueError("sell-through must be at least a day")
        return self.lead_time_days + sell_through_days + 14
