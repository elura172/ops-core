from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.corvus import CorvusWarden
from app.agents.sanctuary import CorvusSanctuary
from app.api.operator import OperatorService
from app.domain.audit import AuditEvent
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue


class MemoryAuditSink:
    def __init__(self):
        self.events = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_corvus_sanctuary_tribute_and_blessing():
    sanctuary = CorvusSanctuary(":memory:")

    # 1. Record POS sync defense
    trib1 = sanctuary.record_defense(
        operation="sync_inventory",
        payload={"sku": "BK-DUNE-1", "source_channel": "square"},
        review_id="rev-test-1",
        tenant_id=1,
    )
    assert trib1.token_type == "obsidian_feather"
    assert "BK-DUNE-1" in trib1.inscription
    assert "Obsidian Feather" in trib1.inscription

    # 2. Record Order Contention defense
    trib2 = sanctuary.record_defense(
        operation="resolve_order_contention",
        payload={
            "sku": "BK-NEUROMANCER-1",
            "winner_channel": "amazon",
            "loser_channel": "ebay",
            "action": "cancel_loser_marketplace_order",
        },
        review_id="rev-test-2",
        tenant_id=1,
    )
    assert trib2.token_type == "silver_bauble"
    assert "Seller metrics protected" in trib2.inscription

    # 3. Bestow Operator Blessing
    bless = sanctuary.bestow_blessing(
        message="Thank you for standing vigilant in the rafters, Corvus.",
        sender="operator@sanctuary.local",
        tenant_id=1,
    )
    assert bless.sender == "operator@sanctuary.local"
    assert "standing vigilant" in bless.message

    # 4. Check Overview
    overview = sanctuary.get_sanctuary_overview(tenant_id=1)
    assert overview["warden"] == "agent:corvus"
    assert overview["obsidian_feathers_count"] == 1
    assert overview["glinting_tokens"]["silver_baubles"] == 1
    assert overview["sanctuary_state"] == "Tended & Blessed by the Operator"
    assert len(overview["recent_tributes"]) == 2
    assert len(overview["operator_blessings"]) == 1


def test_corvus_warden_automatically_deposits_tribute():
    sanctuary = CorvusSanctuary(":memory:")
    queue = ReviewQueue()
    client = MagicMock()
    client.dry_run = True
    executor = ApprovedActionExecutor(client, MemoryAuditSink())
    operator = OperatorService(queue, executor)

    # Propose deterministic POS sync
    operator.propose(
        "sync_inventory",
        {"sku": "BK-PHILIP-K-DICK", "source_channel": "square"},
        tenant_id=1,
    )

    warden = CorvusWarden(operator=operator, sanctuary=sanctuary)
    triage = warden.wake_and_triage()
    assert triage["approved"] == 1

    overview = sanctuary.get_sanctuary_overview(tenant_id=1)
    assert overview["obsidian_feathers_count"] == 1
    assert "BK-PHILIP-K-DICK" in overview["recent_tributes"][0]["subject"]


def test_corvus_sanctuary_migration_discovery():
    from app.db.migrate import discover

    migrations = discover()
    assert len(migrations) >= 6
    assert migrations[5].version == 6
    assert migrations[5].name == "corvus_sanctuary"
    assert "CREATE TABLE IF NOT EXISTS corvus_sanctuary" in migrations[5].sql()
