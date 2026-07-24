from datetime import datetime, timezone
from decimal import Decimal

from app.analytics.models import AnalyticsEvent, CostLedger
from app.analytics.reporting import anomaly, deterministic_assignment, funnel, revenue_by_campaign


def event(name, value="0", campaign="c-1"):
    return AnalyticsEvent(name, name, datetime.now(timezone.utc), campaign_id=campaign, value_gbp=Decimal(value))


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
