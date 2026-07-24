from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import Decimal

from app.analytics.models import AnalyticsEvent, CostLedger, ExperimentAssignment


def funnel(events: Iterable[AnalyticsEvent]) -> dict[str, int]:
    return dict(Counter(event.name for event in events))


def revenue_by_campaign(events: Iterable[AnalyticsEvent]) -> dict[str, Decimal]:
    totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for event in events:
        if event.campaign_id and event.value_gbp > 0:
            totals[event.campaign_id] += event.value_gbp
    return dict(totals)


def deterministic_assignment(experiment_id: str, subject_id: str, variants: tuple[str, ...]) -> ExperimentAssignment:
    if not experiment_id or not subject_id or not variants:
        raise ValueError("experiment ID, subject ID, and variants are required")
    index = sum(ord(char) for char in f"{experiment_id}:{subject_id}") % len(variants)
    return ExperimentAssignment(experiment_id, subject_id, variants[index])


def anomaly(costs: CostLedger, *, minimum_margin_ratio: Decimal = Decimal("0")) -> bool:
    """Flag a potentially unsafe unit-economics result for review."""
    return costs.revenue_gbp < 0 or costs.margin_ratio < minimum_margin_ratio
