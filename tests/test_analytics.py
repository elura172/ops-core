from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.analytics.models import AnalyticsEvent, CostLedger
from app.analytics.reporting import anomaly, deterministic_assignment, funnel, revenue_by_campaign


def event(name, value="0", campaign="c-1"):
    return AnalyticsEvent(name, name, datetime.now(UTC), campaign_id=campaign, value_gbp=Decimal(value))


def test_funnel_and_campaign_revenue():
    events = [event("view"), event("purchase", "20"), event("purchase", "5", None)]
    assert funnel(events)["purchase"] == 2
    assert revenue_by_campaign(events)["c-1"] == Decimal("20")


def test_contribution_margin_and_anomaly():
    ledger = CostLedger(Decimal("100"), supplier_cost_gbp=Decimal("50"), ad_spend_gbp=Decimal("20"))
    assert ledger.contribution_margin_gbp == Decimal("30")
    assert ledger.margin_ratio == Decimal("0.3")
    assert anomaly(ledger, minimum_margin_ratio=Decimal("0.4"))


def test_assignments_are_deterministic():
    a = deterministic_assignment("exp-1", "subject-1", ("a", "b"))
    assert a == deterministic_assignment("exp-1", "subject-1", ("a", "b"))


def test_assignment_is_stable_for_the_same_subject():
    first = deterministic_assignment("exp", "user-1", ("A", "B", "C"))
    assert first.variant == deterministic_assignment("exp", "user-1", ("A", "B", "C")).variant


def test_assignment_is_not_order_sensitive():
    """A character-sum hash put every anagram in the same bucket."""
    variants = ("A", "B", "C")
    pairs = [("user_ab", "user_ba"), ("abc123", "321cba"), ("xy9", "9yx")]
    collisions = sum(
        deterministic_assignment("exp", a, variants).variant == deterministic_assignment("exp", b, variants).variant
        for a, b in pairs
    )
    assert collisions < len(pairs)


def test_concurrent_experiments_are_independent():
    """Experiment IDs differing by one character used to shift every subject by
    exactly one variant, so two live experiments were perfectly correlated."""
    variants = ("A", "B", "C")
    subjects = [f"u{i}" for i in range(1500)]
    agreement = sum(
        deterministic_assignment("exp-a", s, variants).variant == deterministic_assignment("exp-b", s, variants).variant
        for s in subjects
    ) / len(subjects)
    assert 0.28 < agreement < 0.39


def test_assignment_distributes_evenly():
    variants = ("A", "B", "C", "D")
    counts = Counter(deterministic_assignment("exp", f"u{i}", variants).variant for i in range(4000))
    assert set(counts) == set(variants)
    assert all(850 < n < 1150 for n in counts.values())


def test_assignment_requires_inputs():
    with pytest.raises(ValueError):
        deterministic_assignment("", "u", ("A",))
    with pytest.raises(ValueError):
        deterministic_assignment("exp", "u", ())
