from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from app.observability.logging import get_logger

logger = get_logger("order_contention")


class ContentionOutcome(StrEnum):
    ALLOCATED = "allocated"
    CONTENTION_WON = "contention_won"
    CONTENTION_LOST = "contention_lost"
    SUBSTITUTED = "substituted"


@dataclass(frozen=True)
class OrderClaim:
    """Represents a claim on a physical book SKU from a sales channel."""

    order_id: str
    sku: str
    channel: str  # "amazon", "ebay", "abebooks", "square", "biblio", etc.
    ordered_at: datetime
    price_cad: Decimal = Decimal("0.00")
    customer_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContentionResolution:
    """The outcome of evaluating an order claim against current inventory allocations."""

    outcome: ContentionOutcome
    sku: str
    winner: OrderClaim
    loser: OrderClaim | None = None
    reason: str = ""
    substitute_sku: str | None = None
    action_required: str = "none"  # "cancel_loser_marketplace_order", "fulfill_substitute", "none"
    cancellation_reason: str = "OUT_OF_STOCK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "sku": self.sku,
            "winner_order_id": self.winner.order_id,
            "winner_channel": self.winner.channel,
            "loser_order_id": self.loser.order_id if self.loser else None,
            "loser_channel": self.loser.channel if self.loser else None,
            "reason": self.reason,
            "substitute_sku": self.substitute_sku,
            "action_required": self.action_required,
            "cancellation_reason": self.cancellation_reason,
            "winner_claim": {
                "order_id": self.winner.order_id,
                "channel": self.winner.channel,
                "ordered_at": self.winner.ordered_at.isoformat(),
                "price_cad": str(self.winner.price_cad),
            },
            "loser_claim": {
                "order_id": self.loser.order_id,
                "channel": self.loser.channel,
                "ordered_at": self.loser.ordered_at.isoformat(),
                "price_cad": str(self.loser.price_cad),
            }
            if self.loser
            else None,
        }


class SubstituteLocator(Protocol):
    """Protocol for finding alternative inventory copies for the same title/ISBN."""

    def find_substitute(self, sku: str) -> str | None: ...


class ContentionArbiter:
    """Arbitrates simultaneous or conflicting orders for the same one-of-a-kind SKU.

    Policy:
    1. Physical reality: In-store / Square POS physical walkout takes absolute precedence.
    2. Chronological priority: If one order occurred earlier by > time_delta_threshold seconds,
       the earlier buyer wins (First-Come, First-Served).
    3. Marketplace SLA / Risk weighting: When orders occur simultaneously (or within threshold):
       - Amazon: Priority 500 (Crucial: Amazon pre-fulfillment cancellation rate must stay < 2.5%,
         or account faces immediate suspension).
       - eBay: Priority 300 (High defect penalty, but slightly more forgiving cancellation flow).
       - AbeBooks: Priority 200.
       - Biblio / Alibris: Priority 100.
    4. Price tie-breaker: Higher revenue wins if channels have identical priority.
    """

    DEFAULT_CHANNEL_WEIGHTS: dict[str, int] = {
        "square": 1000,
        "pos": 1000,
        "in_store": 1000,
        "amazon": 500,
        "amazon_spapi": 500,
        "ebay": 300,
        "abebooks": 200,
        "biblio": 150,
        "alibris": 100,
        "direct": 50,
    }

    def __init__(
        self,
        channel_weights: dict[str, int] | None = None,
        time_delta_threshold_seconds: float = 2.0,
    ) -> None:
        self.weights = channel_weights or self.DEFAULT_CHANNEL_WEIGHTS
        self.time_threshold = time_delta_threshold_seconds

    def evaluate(self, claim_a: OrderClaim, claim_b: OrderClaim) -> tuple[OrderClaim, OrderClaim, str]:
        """Returns (winner, loser, reason)."""
        # 1. Check physical POS
        a_is_pos = self._is_physical_pos(claim_a.channel)
        b_is_pos = self._is_physical_pos(claim_b.channel)
        if a_is_pos and not b_is_pos:
            return (
                claim_a,
                claim_b,
                f"Physical POS ({claim_a.channel}) takes absolute precedence over remote order ({claim_b.channel}).",
            )
        if b_is_pos and not a_is_pos:
            return (
                claim_b,
                claim_a,
                f"Physical POS ({claim_b.channel}) takes absolute precedence over remote order ({claim_a.channel}).",
            )

        # 2. Chronological check
        t_a = claim_a.ordered_at if claim_a.ordered_at.tzinfo else claim_a.ordered_at.replace(tzinfo=UTC)
        t_b = claim_b.ordered_at if claim_b.ordered_at.tzinfo else claim_b.ordered_at.replace(tzinfo=UTC)
        diff_seconds = (t_b - t_a).total_seconds()

        if abs(diff_seconds) > self.time_threshold:
            if diff_seconds > 0:
                # A was ordered earlier
                return (
                    claim_a,
                    claim_b,
                    f"Order {claim_a.order_id} placed {abs(diff_seconds):.1f}s earlier "
                    f"on {claim_a.channel} (FCFS priority).",
                )
            else:
                # B was ordered earlier
                return (
                    claim_b,
                    claim_a,
                    f"Order {claim_b.order_id} placed {abs(diff_seconds):.1f}s earlier "
                    f"on {claim_b.channel} (FCFS priority).",
                )

        # 3. Channel SLA Priority weighting (simultaneous window)
        w_a = self.weights.get(claim_a.channel, 0)
        w_b = self.weights.get(claim_b.channel, 0)
        if w_a > w_b:
            return (
                claim_a,
                claim_b,
                f"Simultaneous sale detected ({abs(diff_seconds):.2f}s delta). {claim_a.channel} "
                f"prioritized over {claim_b.channel} due to critical seller cancellation SLA.",
            )
        elif w_b > w_a:
            return (
                claim_b,
                claim_a,
                f"Simultaneous sale detected ({abs(diff_seconds):.2f}s delta). {claim_b.channel} "
                f"prioritized over {claim_a.channel} due to critical seller cancellation SLA.",
            )

        # 4. Price tie-breaker
        if claim_a.price_cad > claim_b.price_cad:
            return (
                claim_a,
                claim_b,
                f"Tied channel priority; {claim_a.order_id} prioritized by higher sale price "
                f"(${claim_a.price_cad} vs ${claim_b.price_cad}).",
            )
        elif claim_b.price_cad > claim_a.price_cad:
            return (
                claim_b,
                claim_a,
                f"Tied channel priority; {claim_b.order_id} prioritized by higher sale price "
                f"(${claim_b.price_cad} vs ${claim_a.price_cad}).",
            )

        # 5. Deterministic lexicographical tie-break
        winner = min(claim_a, claim_b, key=lambda c: c.order_id)
        loser = claim_b if winner == claim_a else claim_a
        return winner, loser, f"Deterministic tie-breaker selected order {winner.order_id}."

    def _is_physical_pos(self, channel: str) -> bool:
        return channel.lower() in ("square", "pos", "in_store")


class OrderContentionManager:
    """Thread-safe coordinator for cross-channel inventory reservations and contention arbitration."""

    def __init__(
        self,
        arbiter: ContentionArbiter | None = None,
        substitute_locator: SubstituteLocator | None = None,
        sync_service: Any = None,
        operator: Any = None,
    ) -> None:
        self.arbiter = arbiter or ContentionArbiter()
        self.substitute_locator = substitute_locator
        self.sync_service = sync_service
        self.operator = operator
        self._allocations: dict[str, OrderClaim] = {}
        self._history: list[ContentionResolution] = []
        self._lock = threading.RLock()

    def get_allocation(self, sku: str) -> OrderClaim | None:
        with self._lock:
            return self._allocations.get(sku)

    def process_order_claim(self, claim: OrderClaim) -> ContentionResolution:
        """Processes an incoming sale claim for a SKU across channels."""
        with self._lock:
            existing = self._allocations.get(claim.sku)

            # Case 1: No contention, first claim for this SKU
            if existing is None:
                self._allocations[claim.sku] = claim
                logger.info(
                    "inventory.allocated",
                    sku=claim.sku,
                    order_id=claim.order_id,
                    channel=claim.channel,
                )

                # Immediately trigger cross-channel delist to protect against further races
                if self.sync_service:
                    try:
                        self.sync_service.process_sale(claim.sku, source_channel=claim.channel)
                    except Exception as exc:
                        logger.error("inventory.sync_trigger_failed", sku=claim.sku, error=str(exc))

                resolution = ContentionResolution(
                    outcome=ContentionOutcome.ALLOCATED,
                    sku=claim.sku,
                    winner=claim,
                    reason="First claim allocated successfully. Cross-channel delist broadcast.",
                )
                self._history.append(resolution)
                return resolution

            # Idempotent re-submission of the exact same order
            if existing.order_id == claim.order_id:
                return ContentionResolution(
                    outcome=ContentionOutcome.ALLOCATED,
                    sku=claim.sku,
                    winner=claim,
                    reason="Idempotent duplicate order claim.",
                )

            # Case 2: CONTENTION DETECTED!
            # Two different orders are competing for the exact same physical SKU.
            winner, loser, reason = self.arbiter.evaluate(existing, claim)
            logger.warning(
                "order.contention_detected",
                sku=claim.sku,
                winner_order=winner.order_id,
                winner_channel=winner.channel,
                loser_order=loser.order_id,
                loser_channel=loser.channel,
                reason=reason,
            )

            # Update the registered allocation to the winner
            self._allocations[claim.sku] = winner

            # Check if substitute inventory exists for the loser
            sub_sku = None
            if self.substitute_locator:
                try:
                    sub_sku = self.substitute_locator.find_substitute(loser.sku)
                except Exception as exc:
                    logger.error("inventory.substitute_lookup_failed", sku=loser.sku, error=str(exc))

            if sub_sku:
                # We have a second copy! Allocate it to the loser
                self._allocations[sub_sku] = loser
                logger.info(
                    "inventory.contention.substituted",
                    original_sku=loser.sku,
                    substitute_sku=sub_sku,
                    order_id=loser.order_id,
                )
                resolution = ContentionResolution(
                    outcome=ContentionOutcome.SUBSTITUTED,
                    sku=claim.sku,
                    winner=winner,
                    loser=loser,
                    reason=f"{reason} Substitute SKU {sub_sku} allocated for order {loser.order_id}.",
                    substitute_sku=sub_sku,
                    action_required="fulfill_substitute",
                )
            else:
                # No substitute available; loser order must be cancelled or escalated
                resolution = ContentionResolution(
                    outcome=ContentionOutcome.CONTENTION_LOST,
                    sku=claim.sku,
                    winner=winner,
                    loser=loser,
                    reason=reason,
                    action_required="cancel_loser_marketplace_order",
                    cancellation_reason="OUT_OF_STOCK",
                )

                # Submit to Review Queue for Corvus or Human Operator
                if self.operator:
                    try:
                        self._propose_contention_review(resolution)
                    except Exception as exc:
                        logger.error("order.contention.propose_failed", error=str(exc))

            self._history.append(resolution)
            return resolution

    def _propose_contention_review(self, resolution: ContentionResolution) -> str | None:
        """Pushes a contention resolution action into the Operator Review Desk."""
        payload = {
            "operation": "resolve_order_contention",
            "sku": resolution.sku,
            "action": resolution.action_required,
            "cancellation_reason": resolution.cancellation_reason,
            "reason": resolution.reason,
            "winner_order_id": resolution.winner.order_id,
            "winner_channel": resolution.winner.channel,
            "loser_order_id": resolution.loser.order_id if resolution.loser else None,
            "loser_channel": resolution.loser.channel if resolution.loser else None,
            "substitute_sku": resolution.substitute_sku,
            "winner_claim": {
                "order_id": resolution.winner.order_id,
                "channel": resolution.winner.channel,
                "ordered_at": resolution.winner.ordered_at.isoformat(),
                "price_cad": str(resolution.winner.price_cad),
            },
            "loser_claim": {
                "order_id": resolution.loser.order_id,
                "channel": resolution.loser.channel,
                "ordered_at": resolution.loser.ordered_at.isoformat(),
                "price_cad": str(resolution.loser.price_cad),
            }
            if resolution.loser
            else None,
        }

        proposed = self.operator.propose(
            operation="resolve_order_contention",
            payload=payload,
        )
        review_id = proposed.get("review_id")
        logger.info(
            "order.contention.review_proposed",
            review_id=review_id,
            sku=resolution.sku,
            loser_order=resolution.loser.order_id if resolution.loser else None,
        )

        # Trigger Corvus to triage newly proposed contention item if available
        try:
            from app.agents.corvus import CorvusWarden

            warden = CorvusWarden(self.operator)
            warden.wake_and_triage()
        except Exception as exc:
            logger.error("corvus.contention_triage_failed", error=str(exc))

        return review_id
