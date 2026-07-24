from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.discovery.models import TrendCandidate


@dataclass(frozen=True)
class DiscoveryPolicy:
    min_views: int = 50_000
    min_like_ratio: float = 0.04
    min_comment_ratio: float = 0.003
    min_share_ratio: float = 0.001


def rank_candidates(candidates: Iterable[TrendCandidate], policy: DiscoveryPolicy = DiscoveryPolicy()) -> list[TrendCandidate]:
    """Rank signals only; this does not claim product-market fit or authorize listing."""
    selected = [
        candidate for candidate in candidates
        if candidate.views >= policy.min_views
        and candidate.like_ratio >= policy.min_like_ratio
        and candidate.comment_ratio >= policy.min_comment_ratio
        and candidate.share_ratio >= policy.min_share_ratio
    ]
    return sorted(selected, key=lambda candidate: candidate.engagement_score, reverse=True)
