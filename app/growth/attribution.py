from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.growth.models import CreativePerformance


def campaign_url(
    destination: str, *, source: str, medium: str, campaign: str, creative: str
) -> str:
    parsed = urlparse(destination)
    query = dict(parse_qsl(parsed.query))
    query.update(
        {
            "utm_source": source,
            "utm_medium": medium,
            "utm_campaign": campaign,
            "utm_content": creative,
        }
    )
    return urlunparse(parsed._replace(query=urlencode(query)))


def rank_creatives(
    performance: Iterable[CreativePerformance], min_conversions: int = 1
) -> list[CreativePerformance]:
    return sorted(
        (item for item in performance if item.conversions >= min_conversions),
        key=lambda item: (item.roas, item.ctr),
        reverse=True,
    )


def can_spend(current_spend, campaign, proposed_spend) -> bool:
    """Budget guardrail; callers must persist spend atomically."""
    return (
        current_spend >= 0
        and proposed_spend >= 0
        and current_spend + proposed_spend <= campaign.total_budget_gbp
    )
