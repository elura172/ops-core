from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Channel(StrEnum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    YOUTUBE = "youtube"
    SHOPIFY = "shopify"


@dataclass(frozen=True)
class Creative:
    creative_id: str
    product_id: str
    channel: Channel
    asset_uri: str
    rights_verified: bool
    creator_id: str | None = None

    def validate(self) -> None:
        if not self.creative_id or not self.product_id or not self.asset_uri:
            raise ValueError("creative identity and asset URI are required")
        if not self.rights_verified:
            raise PermissionError("creative rights must be verified before promotion")


@dataclass(frozen=True)
class Campaign:
    campaign_id: str
    creative_id: str
    channel: Channel
    daily_budget_gbp: Decimal
    total_budget_gbp: Decimal
    destination_url: str

    def validate(self) -> None:
        if self.daily_budget_gbp <= 0 or self.total_budget_gbp < self.daily_budget_gbp:
            raise ValueError("campaign budget is invalid")
        if not self.destination_url.startswith("https://"):
            raise ValueError("campaign destination must use HTTPS")


@dataclass(frozen=True)
class ConversionEvent:
    event_id: str
    event_name: str
    channel: Channel
    occurred_at: datetime
    order_id: str | None = None
    value_gbp: Decimal = Decimal("0")
    creative_id: str | None = None
    campaign_id: str | None = None


@dataclass(frozen=True)
class CreativePerformance:
    creative_id: str
    impressions: int
    clicks: int
    conversions: int
    spend_gbp: Decimal
    revenue_gbp: Decimal

    @property
    def ctr(self) -> float:
        return self.clicks / max(self.impressions, 1)

    @property
    def roas(self) -> Decimal:
        return self.revenue_gbp / self.spend_gbp if self.spend_gbp else Decimal("0")
