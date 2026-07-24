from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TrendCandidate:
    candidate_id: str
    source: str
    market: str
    observed_at: datetime
    product_reference: str
    source_url: str
    views: int
    likes: int
    comments: int
    shares: int

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.source or not self.market:
            raise ValueError("candidate identity fields are required")
        if min(self.views, self.likes, self.comments, self.shares) < 0:
            raise ValueError("trend metrics cannot be negative")

    @property
    def like_ratio(self) -> float:
        return self.likes / max(self.views, 1)

    @property
    def comment_ratio(self) -> float:
        return self.comments / max(self.views, 1)

    @property
    def share_ratio(self) -> float:
        return self.shares / max(self.views, 1)

    @property
    def engagement_score(self) -> float:
        return (self.likes + self.comments * 2 + self.shares * 3) / max(self.views, 1)
