from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

PENNY = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


class SizeTier(StrEnum):
    """Amazon US FBA size tiers, coarsest useful granularity."""

    SMALL_STANDARD = "SMALL_STANDARD"
    LARGE_STANDARD = "LARGE_STANDARD"
    LARGE_BULKY = "LARGE_BULKY"
    EXTRA_LARGE = "EXTRA_LARGE"


@dataclass(frozen=True)
class FeeSchedule:
    """Amazon's cut of a sale, as data rather than constants.

    Every rate here is a **placeholder pending verification** against Amazon's
    published US fee schedule. They are in the right shape and the right
    neighbourhood; they are not authoritative, and the schedule is revised at
    least annually. `verified` stays False until someone has checked them against
    the current published tables, and `unit_economics` refuses to price when a
    schedule is unverified unless the caller explicitly accepts estimates.
    """

    marketplace: str = "amazon.com"
    currency: str = "USD"
    effective_date: date = date(2026, 1, 1)
    verified: bool = False
    source: str = "placeholder - verify against sellercentral fee schedule"

    #: Referral fee as a fraction of sale price, by category. Most categories sit
    #: at 15%; a handful are lower. `default` applies to anything unlisted.
    referral_rates: dict[str, Decimal] = field(
        default_factory=lambda: {
            "default": Decimal("0.15"),
            "electronics": Decimal("0.08"),
            "computers": Decimal("0.08"),
            "video_games": Decimal("0.15"),
            "grocery": Decimal("0.08"),
            "personal_care": Decimal("0.15"),
            "home_kitchen": Decimal("0.15"),
            "toys": Decimal("0.15"),
            "sports_outdoors": Decimal("0.15"),
            "tools": Decimal("0.15"),
            "office": Decimal("0.15"),
        }
    )
    #: Minimum referral fee per unit, applied when the percentage falls below it.
    minimum_referral: Decimal = Decimal("0.30")

    #: Flat FBA fulfilment fee per unit by size tier. Real fees also step with
    #: weight inside a tier; this is the entry-band figure and therefore optimistic
    #: for heavier units.
    fba_fulfilment: dict[SizeTier, Decimal] = field(
        default_factory=lambda: {
            SizeTier.SMALL_STANDARD: Decimal("3.25"),
            SizeTier.LARGE_STANDARD: Decimal("4.75"),
            SizeTier.LARGE_BULKY: Decimal("9.75"),
            SizeTier.EXTRA_LARGE: Decimal("26.00"),
        }
    )

    #: Monthly storage per cubic foot, off-peak. Q4 rates are several times this.
    storage_per_cubic_foot_month: Decimal = Decimal("0.87")

    def referral_rate(self, category: str) -> Decimal:
        return self.referral_rates.get(category, self.referral_rates["default"])

    def referral_fee(self, price: Decimal, category: str) -> Decimal:
        return _money(max(price * self.referral_rate(category), self.minimum_referral))

    def fulfilment_fee(self, tier: SizeTier) -> Decimal:
        return self.fba_fulfilment[tier]


@dataclass(frozen=True)
class LandedCost:
    """What a unit costs to get into an Amazon fulfilment centre.

    Sourcing from China into the US: no import VAT, but duty and Section 301
    tariffs both apply and are commonly omitted from beginner spreadsheets.
    Section 301 alone runs 7.5-25% on many Chinese goods and can decide whether a
    product is viable.
    """

    fob_unit_cost: Decimal
    freight_per_unit: Decimal = Decimal("0")
    duty_rate: Decimal = Decimal("0")
    tariff_rate: Decimal = Decimal("0")
    prep_per_unit: Decimal = Decimal("0")
    inspection_per_unit: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.fob_unit_cost <= 0:
            raise ValueError("FOB unit cost must be positive")
        negatives = (
            self.freight_per_unit,
            self.duty_rate,
            self.tariff_rate,
            self.prep_per_unit,
            self.inspection_per_unit,
        )
        if any(v < 0 for v in negatives):
            raise ValueError("landed cost components cannot be negative")
        if self.duty_rate >= 1 or self.tariff_rate >= 1:
            raise ValueError("duty and tariff rates are fractions below 1")

    @property
    def dutiable_value(self) -> Decimal:
        """Duty is assessed on the customs value, not on freight or prep."""
        return self.fob_unit_cost

    @property
    def total(self) -> Decimal:
        duties = self.dutiable_value * (self.duty_rate + self.tariff_rate)
        return _money(
            self.fob_unit_cost + self.freight_per_unit + duties + self.prep_per_unit + self.inspection_per_unit
        )


@dataclass(frozen=True)
class UnitEconomics:
    price: Decimal
    landed_cost: Decimal
    referral_fee: Decimal
    fulfilment_fee: Decimal
    storage_fee: Decimal
    returns_reserve: Decimal
    estimates_only: bool

    @property
    def total_cost(self) -> Decimal:
        return _money(
            self.landed_cost + self.referral_fee + self.fulfilment_fee + self.storage_fee + self.returns_reserve
        )

    @property
    def net_profit(self) -> Decimal:
        return _money(self.price - self.total_cost)

    @property
    def margin(self) -> Decimal:
        """Net profit as a fraction of sale price. Negative means selling at a loss."""
        if self.price <= 0:
            return Decimal("0")
        return (self.net_profit / self.price).quantize(Decimal("0.0001"))

    @property
    def roi(self) -> Decimal:
        """Return on the cash actually tied up in inventory — the number that
        decides how fast capital recycles, and the one sellers actually plan on."""
        if self.landed_cost <= 0:
            return Decimal("0")
        return (self.net_profit / self.landed_cost).quantize(Decimal("0.0001"))


def unit_economics(
    *,
    price: Decimal,
    landed: LandedCost,
    category: str,
    tier: SizeTier,
    fees: FeeSchedule | None = None,
    cubic_feet: Decimal = Decimal("0.1"),
    months_of_stock: Decimal = Decimal("2"),
    return_rate: Decimal = Decimal("0.05"),
    accept_estimates: bool = False,
) -> UnitEconomics:
    """Full per-unit economics at a given sale price.

    Refuses to run against an unverified fee schedule unless the caller opts in,
    so an estimate cannot quietly become the basis of a purchase order.
    """
    fees = fees or FeeSchedule()
    if not fees.verified and not accept_estimates:
        raise ValueError(
            "fee schedule is unverified; pass accept_estimates=True to screen with "
            "placeholder rates, and verify before committing capital"
        )
    if price <= 0:
        raise ValueError("price must be positive")
    if not 0 <= return_rate < 1:
        raise ValueError("return rate must be a fraction below 1")

    referral = fees.referral_fee(price, category)
    fulfilment = fees.fulfilment_fee(tier)
    storage = _money(fees.storage_per_cubic_foot_month * cubic_feet * months_of_stock)
    # A return costs the fulfilment fee and the landed unit, and Amazon retains a
    # portion of the referral fee. Reserving price * rate is the conservative
    # simplification and errs toward rejecting marginal products.
    returns = _money(price * return_rate)

    return UnitEconomics(
        price=_money(price),
        landed_cost=landed.total,
        referral_fee=referral,
        fulfilment_fee=fulfilment,
        storage_fee=storage,
        returns_reserve=returns,
        estimates_only=not fees.verified,
    )


def break_even_price(
    *,
    landed: LandedCost,
    category: str,
    tier: SizeTier,
    target_margin: Decimal = Decimal("0.25"),
    fees: FeeSchedule | None = None,
    cubic_feet: Decimal = Decimal("0.1"),
    months_of_stock: Decimal = Decimal("2"),
    return_rate: Decimal = Decimal("0.05"),
) -> Decimal:
    """Lowest price that still clears `target_margin` after every fee.

    Solves for price directly rather than iterating: referral fee and the returns
    reserve both scale with price, so they move to the left-hand side.
    """
    fees = fees or FeeSchedule()
    if not Decimal("0") <= target_margin < Decimal("1"):
        raise ValueError("target margin must be a fraction below 1")
    fixed = (
        landed.total
        + fees.fulfilment_fee(tier)
        + _money(fees.storage_per_cubic_foot_month * cubic_feet * months_of_stock)
    )
    variable = fees.referral_rate(category) + return_rate + target_margin
    if variable >= 1:
        raise ValueError("fees, returns and target margin exceed the whole sale price")
    return _money(fixed / (Decimal("1") - variable))
