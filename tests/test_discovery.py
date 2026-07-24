from datetime import datetime, timezone

import pytest

from app.discovery.models import TrendCandidate
from app.discovery.ranker import DiscoveryPolicy, rank_candidates
from app.discovery.sources import StaticTrendSource


def trend(candidate_id, *, likes=5000, comments=400, shares=200, views=100000, market="GB"):
    return TrendCandidate(candidate_id, "licensed-test", market, datetime.now(timezone.utc), "product-ref", "https://example.test/source", views, likes, comments, shares)


def test_ranker_filters_and_sorts():
    winner = trend("winner", likes=10000)
    loser = trend("loser", likes=1000, comments=20, shares=10)
    assert [c.candidate_id for c in rank_candidates([loser, winner])] == ["winner"]


def test_source_filters_market():
    source = StaticTrendSource([trend("gb"), trend("us", market="US")])
    assert [c.candidate_id for c in source.fetch("GB")] == ["gb"]


def test_negative_metrics_rejected():
    with pytest.raises(ValueError):
        trend("bad", likes=-1)


def test_custom_policy_can_be_stricter():
    assert rank_candidates([trend("x")], DiscoveryPolicy(min_views=200000)) == []
