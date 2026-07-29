from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.amazon.fees import FeeSchedule, LandedCost, break_even_price, unit_economics
from app.amazon.models import AmazonListing, Opportunity
from app.observability.logging import get_logger

logger = get_logger("amazon_screen")


@dataclass(frozen=True)
class Rejection:
    """Why a candidate failed.

    Carries a stable `code` beside the human message so failures can be counted
    and compared without parsing prose — the message contains the specific numbers
    and is therefore unique per candidate.
    """

    code: str
    message: str
    #: A soft finding needs a human look rather than an automatic reject. Amazon
    #: titles are keyword-stuffed, so matching on words alone produces false
    #: positives — a garden spade whose title ends "Kids Sand Beach Shovels" is
    #: not a children's product.
    soft: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ScreenPolicy:
    """Thresholds a candidate must clear. Every default is deliberately strict:
    the scanner's job is to reject, and a screen that passes everything has
    told you nothing."""

    min_margin: Decimal = Decimal("0.25")
    min_roi: Decimal = Decimal("0.50")
    min_price: Decimal = Decimal("15")
    max_price: Decimal = Decimal("70")
    min_reviews: int = 100
    max_reviews: int = 5_000
    max_rating: Decimal = Decimal("4.4")
    max_sellers: int = 8
    min_rank_stability: float = 0.55
    max_median_rank: int = 60_000
    reject_brand_registered: bool = True

    def __post_init__(self) -> None:
        if self.min_price >= self.max_price:
            raise ValueError("min_price must be below max_price")
        if self.min_reviews >= self.max_reviews:
            raise ValueError("min_reviews must be below max_reviews")


def _rejections(listing: AmazonListing, policy: ScreenPolicy) -> list[Rejection]:
    """Every reason a listing fails, not just the first — a candidate rejected for
    one marginal reason is worth revisiting, one rejected for five is not."""
    out: list[Rejection] = []

    def fail(code: str, message: str) -> None:
        out.append(Rejection(code, message))

    if listing.price < policy.min_price:
        fail("price_too_low", f"price {listing.price} below floor {policy.min_price}")
    if listing.price > policy.max_price:
        fail("price_too_high", f"price {listing.price} above ceiling {policy.max_price}")
    if listing.review_count < policy.min_reviews:
        fail("demand_unproven", f"only {listing.review_count} reviews — demand unproven")
    if listing.review_count > policy.max_reviews:
        fail("entrenched_incumbent", f"{listing.review_count} reviews — entrenched incumbent")
    if listing.rating > policy.max_rating:
        fail("no_quality_gap", f"rating {listing.rating} leaves no quality gap to attack")
    if listing.seller_count > policy.max_sellers:
        fail("crowded_listing", f"{listing.seller_count} sellers on the listing — reseller pile-on")
    if policy.reject_brand_registered and listing.brand_registered:
        fail("brand_registered", "incumbent is brand-registered")
    stability = listing.rank_stability
    if stability < policy.min_rank_stability:
        fail("unstable_demand", f"rank stability {stability:.2f} — demand is spiky or seasonal")
    median = listing.median_rank
    if median is None:
        fail("no_rank_history", "no rank history — demand cannot be estimated")
    elif median > policy.max_median_rank:
        fail("too_little_volume", f"median rank {median:,} — too little volume")
    return out


def _score(listing: AmazonListing, margin: Decimal, roi: Decimal) -> float:
    """Rank survivors. Weighted toward the quality gap, because that is what an
    improved product can actually convert; margin only decides if it is worth doing."""
    median = listing.median_rank or 10**9
    demand = 1.0 / (1.0 + median / 20_000)
    gap = min(listing.review_velocity / 4_000, 1.0)
    economics = min(float(roi) / 2.0, 1.0)
    return round(0.35 * gap + 0.30 * demand + 0.25 * economics + 0.10 * listing.rank_stability, 4)


def screen(
    listings: Iterable[AmazonListing],
    landed_cost_for: dict[str, LandedCost],
    *,
    policy: ScreenPolicy | None = None,
    fees: FeeSchedule | None = None,
    accept_estimates: bool = False,
) -> tuple[list[Opportunity], dict[str, list[Rejection]]]:
    """Screen candidates. Returns survivors ranked best-first, plus every rejection
    reason keyed by ASIN so a thin result set is explainable rather than mysterious.

    `landed_cost_for` maps ASIN to an estimated landed cost. Without a supplier
    quote there is no honest way to compute margin, so a candidate with no estimate
    is rejected rather than assumed cheap.
    """
    policy = policy or ScreenPolicy()
    fees = fees or FeeSchedule()
    survivors: list[Opportunity] = []
    rejected: dict[str, list[Rejection]] = {}

    for listing in listings:
        reasons = _rejections(listing, policy)
        landed = landed_cost_for.get(listing.asin)
        if landed is None:
            reasons.append(
                Rejection("no_landed_cost", "no landed-cost estimate — cannot compute margin")
            )
        if reasons:
            rejected[listing.asin] = reasons
            continue

        assert landed is not None
        economics = unit_economics(
            price=listing.price,
            landed=landed,
            category=listing.category,
            tier=listing.size_tier,
            fees=fees,
            cubic_feet=listing.cubic_feet,
            accept_estimates=accept_estimates,
        )
        floor = break_even_price(
            landed=landed,
            category=listing.category,
            tier=listing.size_tier,
            target_margin=policy.min_margin,
            fees=fees,
            cubic_feet=listing.cubic_feet,
        )

        economic_reasons: list[Rejection] = []
        if economics.margin < policy.min_margin:
            economic_reasons.append(
                Rejection(
                    "margin_too_thin",
                    f"margin {economics.margin:.1%} below floor {policy.min_margin:.0%}",
                )
            )
        if economics.roi < policy.min_roi:
            economic_reasons.append(
                Rejection(
                    "roi_too_low", f"ROI {economics.roi:.1%} below floor {policy.min_roi:.0%}"
                )
            )
        if economic_reasons:
            rejected[listing.asin] = economic_reasons
            continue

        survivors.append(
            Opportunity(
                listing=listing,
                break_even_price=floor,
                margin_at_current_price=economics.margin,
                roi_at_current_price=economics.roi,
                score=_score(listing, economics.margin, economics.roi),
                reasons=(
                    f"median rank {listing.median_rank:,}",
                    f"{listing.review_count:,} reviews at {listing.rating}",
                    f"margin {economics.margin:.1%}, ROI {economics.roi:.1%}",
                    f"rank stability {listing.rank_stability:.2f}",
                ),
            )
        )

    survivors.sort(key=lambda o: o.score, reverse=True)
    logger.info(
        "amazon.screen.completed",
        candidates=len(rejected) + len(survivors),
        survivors=len(survivors),
        estimates_only=not fees.verified,
    )
    return survivors, rejected


def rejection_summary(rejected: dict[str, list[Rejection]]) -> dict[str, int]:
    """Which constraint is doing the killing, counted by code.

    If one reason dominates, the policy may be miscalibrated rather than the market
    empty — that distinction decides whether to widen a filter or leave a category.
    """
    counts: dict[str, int] = {}
    for reasons in rejected.values():
        for reason in reasons:
            counts[reason.code] = counts.get(reason.code, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
