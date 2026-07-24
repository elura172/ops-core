from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from app.discovery.models import TrendCandidate


class TrendSource(Protocol):
    """Implement with an official or explicitly licensed data provider."""

    def fetch(self, market: str) -> Iterable[TrendCandidate]: ...


class StaticTrendSource:
    def __init__(self, candidates: Iterable[TrendCandidate] = ()) -> None:
        self._candidates = tuple(candidates)

    def fetch(self, market: str) -> Iterable[TrendCandidate]:
        return (candidate for candidate in self._candidates if candidate.market == market)
