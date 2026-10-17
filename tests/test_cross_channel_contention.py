from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from app.domain.audit import AuditLog
from app.publish.inventory_sync import MultiChannelInventorySync
from app.publish.order_contention import (
    ContentionOutcome,
    OrderClaim,
    OrderContentionManager,
)
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue


def make_claim(
    order_id: str,
    sku: str,
    channel: str,
    seconds_offset: float = 0.0,
    price: str = "45.00",
) -> OrderClaim:
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds_offset)
    return OrderClaim(
        order_id=order_id,
        sku=sku,
        channel=channel,
        ordered_at=now,
        price_cad=Decimal(price),
    )


def test_first_order_claims_sku_and_triggers_sync():
    mock_adapter1 = MagicMock()
    mock_adapter1.channel_name = "ebay"
    mock_adapter2 = MagicMock()
    mock_adapter2.channel_name = "amazon"

    sync_service = MultiChannelInventorySync(adapters=(mock_adapter1, mock_adapter2))
    manager = OrderContentionManager(sync_service=sync_service)

    claim = make_claim("amz-1", "BK-42", "amazon")
    resolution = manager.process_order_claim(claim)

    assert resolution.outcome is ContentionOutcome.ALLOCATED
    assert resolution.winner.order_id == "amz-1"
    assert manager.get_allocation("BK-42") == claim

    # Sync should delist from eBay (skipping Amazon)
    mock_adapter1.delist_book.assert_called_once_with("BK-42")
    mock_adapter2.delist_book.assert_not_called()


def test_simultaneous_amazon_vs_ebay_contention():
    """Amazon is prioritized in a simultaneous collision due to strict seller cancellation metrics."""
    manager = OrderContentionManager()

    amz_claim = make_claim("amz-100", "BK-RARE-1", "amazon", seconds_offset=0.0)
    ebay_claim = make_claim("ebay-200", "BK-RARE-1", "ebay", seconds_offset=0.1)  # 100ms later

    res_amz = manager.process_order_claim(amz_claim)
    assert res_amz.outcome is ContentionOutcome.ALLOCATED

    # eBay arrives before delisting takes effect on eBay
    res_ebay = manager.process_order_claim(ebay_claim)

    assert res_ebay.outcome is ContentionOutcome.CONTENTION_LOST
    assert res_ebay.winner.order_id == "amz-100"
    assert res_ebay.loser.order_id == "ebay-200"
    assert res_ebay.action_required == "cancel_loser_marketplace_order"
    assert "amazon prioritized over ebay due to critical seller cancellation SLA" in res_ebay.reason


def test_physical_pos_preempts_remote_order():
    """Physical book sale (Square POS) walks out of store and preempts remote order."""
    manager = OrderContentionManager()

    ebay_claim = make_claim("ebay-50", "BK-FOLIO-1", "ebay", seconds_offset=0.0)
    manager.process_order_claim(ebay_claim)

    # Customer at cash register buys the physical copy
    pos_claim = make_claim("sq-pos-1", "BK-FOLIO-1", "square", seconds_offset=0.5)
    res_pos = manager.process_order_claim(pos_claim)

    # Square POS wins
    assert res_pos.winner.order_id == "sq-pos-1"
    assert res_pos.loser.order_id == "ebay-50"
    assert "Physical POS (square) takes absolute precedence" in res_pos.reason
    assert manager.get_allocation("BK-FOLIO-1") == pos_claim


def test_chronological_first_come_first_served():
    """If an order is placed well earlier than another, timestamp rules over channel weight."""
    manager = OrderContentionManager()

    # eBay order placed 10 seconds before Amazon order
    ebay_claim = make_claim("ebay-1", "BK-99", "ebay", seconds_offset=0.0)
    amz_claim = make_claim("amz-1", "BK-99", "amazon", seconds_offset=10.0)

    manager.process_order_claim(ebay_claim)
    res_amz = manager.process_order_claim(amz_claim)

    assert res_amz.winner.order_id == "ebay-1"
    assert res_amz.loser.order_id == "amz-1"
    assert "FCFS priority" in res_amz.reason


def test_substitute_inventory_resolves_contention():
    """When a duplicate copy exists in catalog, both orders are fulfilled."""

    class MockSubstituteLocator:
        def find_substitute(self, sku: str) -> str | None:
            if sku == "BK-TOLKIEN-1":
                return "BK-TOLKIEN-COPY-2"
            return None

    manager = OrderContentionManager(substitute_locator=MockSubstituteLocator())

    amz_claim = make_claim("amz-1", "BK-TOLKIEN-1", "amazon")
    ebay_claim = make_claim("ebay-1", "BK-TOLKIEN-1", "ebay", seconds_offset=0.1)

    manager.process_order_claim(amz_claim)
    res = manager.process_order_claim(ebay_claim)

    assert res.outcome is ContentionOutcome.SUBSTITUTED
    assert res.substitute_sku == "BK-TOLKIEN-COPY-2"
    assert res.action_required == "fulfill_substitute"
    assert manager.get_allocation("BK-TOLKIEN-1") == amz_claim
    assert manager.get_allocation("BK-TOLKIEN-COPY-2") == ebay_claim


def test_corvus_auto_approves_deterministic_order_contention():
    """Corvus swoops in and auto-resolves standard marketplace contention."""
    from app.api.operator import OperatorService

    queue = ReviewQueue()
    mock_client = MagicMock()
    mock_client.dry_run = True
    executor = ApprovedActionExecutor(mock_client, AuditLog())
    operator = OperatorService(queue, executor)

    manager = OrderContentionManager(operator=operator)

    # Amazon sells the book
    amz_claim = make_claim("amz-1", "BK-POE-1", "amazon", price="35.00")
    manager.process_order_claim(amz_claim)

    # eBay sells at virtually same time
    ebay_claim = make_claim("ebay-1", "BK-POE-1", "ebay", seconds_offset=0.2, price="35.00")
    manager.process_order_claim(ebay_claim)

    # Check reviews: Corvus should have auto-approved and executed!
    reviews = operator.list_reviews(pending_only=False)
    contention_reviews = [r for r in reviews if r["operation"] == "resolve_order_contention"]
    assert len(contention_reviews) == 1

    item = contention_reviews[0]
    assert item["status"] == "EXECUTED"
    assert item["reviewer"] == "agent:corvus"
    assert "Auto-cancelling loser order" in item["reason"]


def test_corvus_leaves_high_value_contention_for_human():
    """For rare/expensive books (> $200 CAD), Corvus leaves the conflict for the human operator."""
    from app.api.operator import OperatorService

    queue = ReviewQueue()
    mock_client = MagicMock()
    mock_client.dry_run = True
    executor = ApprovedActionExecutor(mock_client, AuditLog())
    operator = OperatorService(queue, executor)

    manager = OrderContentionManager(operator=operator)

    # First edition Newton Principia or rare folio: $850 CAD
    amz_claim = make_claim("amz-high", "BK-RARE-PRINCIPIA", "amazon", price="850.00")
    manager.process_order_claim(amz_claim)

    ebay_claim = make_claim("ebay-high", "BK-RARE-PRINCIPIA", "ebay", seconds_offset=0.1, price="850.00")
    manager.process_order_claim(ebay_claim)

    # Review should be PENDING (Corvus refused to auto-approve)
    reviews = operator.list_reviews(pending_only=True)
    contention_reviews = [r for r in reviews if r["operation"] == "resolve_order_contention"]
    assert len(contention_reviews) == 1
    assert contention_reviews[0]["status"] == "PENDING"


def test_concurrent_threads_contention_safety():
    """8 threads attempting to claim the same 1-of-1 SKU simultaneously."""
    manager = OrderContentionManager()
    sku = "BK-RACE-1"
    channels = ["amazon", "ebay", "abebooks", "biblio", "alibris", "direct", "pos", "square"]

    results: list[ContentionOutcome] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(i: int):
        claim = make_claim(f"order-{i}", sku, channels[i % len(channels)])
        barrier.wait()
        res = manager.process_order_claim(claim)
        with lock:
            results.append(res.outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one thread is the winner (either ALLOCATED or won after POS preemption)
    # The final allocation must be exactly one claim
    allocated = manager.get_allocation(sku)
    assert allocated is not None
    # All other outcomes were CONTENTION_LOST
    contention_lost_count = sum(1 for o in results if o is ContentionOutcome.CONTENTION_LOST)
    assert contention_lost_count == 7


def test_executor_resolves_order_contention():
    """ApprovedActionExecutor successfully executes resolve_order_contention."""
    mock_client = MagicMock()
    executor = ApprovedActionExecutor(mock_client)

    payload = {
        "sku": "BK-123",
        "action": "cancel_loser_marketplace_order",
        "loser_order_id": "ebay-999",
        "loser_channel": "ebay",
        "cancellation_reason": "OUT_OF_STOCK",
    }

    result = executor.handlers["resolve_order_contention"](payload)
    assert result["status"] == "resolved"
    assert result["marketplace_cancellation"] == "success"
    assert result["loser_order_id"] == "ebay-999"
