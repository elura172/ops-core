from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.amazon.fees import SizeTier


@dataclass(frozen=True)
class RankPoint:
    observed_on: date
    best_seller_rank: int


@dataclass(frozen=True)
class AmazonListing:
    """One incumbent listing, as observed from a rank-history provider.

    Deliberately not named `TrendCandidate`: that model is engagement-shaped
    (views, likes, shares) because TikTok demand is attention. Amazon demand is
    rank-and-price shaped, and conflating the two would let a viral video score as
    a sellable product.
    """

    asin: str
    title: str
    category: str
    price: Decimal
    review_count: int
    rating: Decimal
    seller_count: int
    size_tier: SizeTier
    rank_history: tuple[RankPoint, ...]
    brand: str | None = None
    brand_registered: bool = False
    cubic_feet: Decimal = Decimal("0.1")

    def __post_init__(self) -> None:
        if not self.asin or not self.title:
            raise ValueError("ASIN and title are required")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.review_count < 0 or self.seller_count < 0:
            raise ValueError("review and seller counts cannot be negative")
        if not Decimal("0") <= self.rating <= Decimal("5"):
            raise ValueError("rating must be between 0 and 5")
        if any(p.best_seller_rank < 1 for p in self.rank_history):
            raise ValueError("best seller rank must be positive")

    @property
    def current_rank(self) -> int | None:
        if not self.rank_history:
            return None
        return max(self.rank_history, key=lambda p: p.observed_on).best_seller_rank

    @property
    def median_rank(self) -> int | None:
        if not self.rank_history:
            return None
        return int(statistics.median(p.best_seller_rank for p in self.rank_history))

    @property
    def rank_stability(self) -> float:
        """1.0 is perfectly steady demand, 0.0 is wild.

        Coefficient of variation inverted. A product that holds rank 8,000 all year
        is a business; one that spikes to 800 in December and sits at 200,000 the
        rest of the year is a seasonal trap that ties up capital for eleven months.
        """
        ranks = [p.best_seller_rank for p in self.rank_history]
        if len(ranks) < 2:
            return 0.0
        mean = statistics.fmean(ranks)
        if mean <= 0:
            return 0.0
        cv = statistics.pstdev(ranks) / mean
        return max(0.0, 1.0 - cv)

    @property
    def review_velocity(self) -> float:
        """Reviews per point of rating headroom.

        A 3.9 with 4,000 reviews signals a market that buys despite dissatisfaction
        — the opening. A 4.8 with 40 reviews signals neither demand nor a gap.
        """
        headroom = float(Decimal("5") - self.rating)
        return self.review_count * headroom

    @property
    def is_crowded(self) -> bool:
        """Many sellers on one ASIN means a reseller pile-on, not a private label."""
        return self.seller_count > 8


@dataclass(frozen=True)
class Opportunity:
    """A listing that survived screening, with the reason it did."""

    listing: AmazonListing
    break_even_price: Decimal
    margin_at_current_price: Decimal
    roi_at_current_price: Decimal
    score: float
    reasons: tuple[str, ...]

    @property
    def asin(self) -> str:
        return self.listing.asin

    @property
    def headroom(self) -> Decimal:
        """Gap between what the market pays and what we must charge to profit."""
        return self.listing.price - self.break_even_price
