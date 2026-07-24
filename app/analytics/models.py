from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class AnalyticsEvent:
    event_id: str
    name: str
    occurred_at: datetime
    channel: str | None = None
    product_id: str | None = None
    campaign_id: str | None = None
    creative_id: str | None = None
    order_id: str | None = None
    value_gbp: Decimal = Decimal("0")


@dataclass(frozen=True)
class CostLedger:
    revenue_gbp: Decimal
    supplier_cost_gbp: Decimal = Decimal("0")
    shipping_cost_gbp: Decimal = Decimal("0")
    platform_fees_gbp: Decimal = Decimal("0")
    ad_spend_gbp: Decimal = Decimal("0")
    refunds_gbp: Decimal = Decimal("0")

    @property
    def contribution_margin_gbp(self) -> Decimal:
        return self.revenue_gbp - sum((self.supplier_cost_gbp, self.shipping_cost_gbp, self.platform_fees_gbp, self.ad_spend_gbp, self.refunds_gbp), Decimal("0"))

    @property
    def margin_ratio(self) -> Decimal:
        return self.contribution_margin_gbp / self.revenue_gbp if self.revenue_gbp else Decimal("0")


@dataclass(frozen=True)
class ExperimentAssignment:
    experiment_id: str
    subject_id: str
    variant: str

