import logging
from typing import Any, Protocol

logger = logging.getLogger("corvus")

class Operator(Protocol):
    def list_reviews(self, pending_only: bool = False) -> list[dict[str, Any]]: ...
    def decide(self, review_id: str, approve: bool, reviewer: str, reason: str = "") -> dict[str, Any]: ...
    def execute(self, review_id: str, actor: str) -> dict[str, Any]: ...
    def review_detail(self, review_id: str) -> dict[str, Any]: ...


class CorvusWarden:
    """
    Corvus, the Vault Warden.
    
    A sharp, algorithmic entity dwelling in the rafters of the Review Desk. 
    Corvus watches the incoming streams of operations. He is highly protective 
    of the inventory but knows that physical sales (POS) are absolute. He swoops 
    in to auto-approve and execute deterministic tasks, leaving a single black 
    feather in the audit log. He leaves nuanced tasks (pricing, packing slips) 
    for the human operator.
    """
    
    IDENTITY = "agent:corvus"

    def __init__(self, operator: Operator):
        self.operator = operator

    def wake_and_triage(self) -> dict[str, int]:
        """
        Corvus inspects the pending queue.
        Returns a summary of his actions.
        """
        pending = self.operator.list_reviews(pending_only=True)
        actions_taken = {"approved": 0, "ignored": 0, "failed": 0}

        for item in pending:
            review_id = item["review_id"]
            operation = item["operation"]

            # Fetch the full detail to see the payload
            detail = self.operator.review_detail(review_id)
            payload = detail.get("payload", {})

            if self._should_auto_approve(operation, payload):
                logger.info("corvus.triage.approve", review_id=review_id, operation=operation)
                try:
                    reason = self._approval_reason(operation, payload)
                    # 1. Corvus stamps the approval
                    self.operator.decide(
                        review_id=review_id,
                        approve=True,
                        reviewer=self.IDENTITY,
                        reason=reason,
                    )
                    
                    # 2. Corvus executes immediately
                    self.operator.execute(review_id=review_id, actor=self.IDENTITY)
                    actions_taken["approved"] += 1
                except Exception as exc:
                    logger.error("corvus.triage.error", review_id=review_id, error=str(exc))
                    actions_taken["failed"] += 1
            else:
                logger.debug("corvus.triage.ignore", review_id=review_id, operation=operation)
                actions_taken["ignored"] += 1

        return actions_taken

    def _approval_reason(self, operation: str, payload: dict[str, Any]) -> str:
        if operation == "resolve_order_contention":
            loser = payload.get("loser_channel", "marketplace")
            winner = payload.get("winner_channel", "marketplace")
            action = payload.get("action", "")
            if action == "fulfill_substitute":
                return f"Corvus: Inventory collision resolved. Alternate copy discovered for {loser} order. Protecting both sales."
            return f"Corvus: Collision resolved ({winner} beat {loser}). Auto-cancelling loser order to protect seller metrics."
        return "Corvus: Deterministic inventory sync authorized. Protecting the vault."

    def _should_auto_approve(self, operation: str, payload: dict[str, Any]) -> bool:
        """
        Corvus's internal ruleset.
        """
        # Rule 1: Always auto-approve Square POS inventory syncs.
        # A physical book has left the building; the vault must be updated instantly to prevent online double-sells.
        if operation == "sync_inventory":
            source = payload.get("source_channel", "")
            if source in ("square", "in_store", "pos"):
                return True

        # Rule 2: Order contention handling.
        # When two marketplaces fight over a single copy (e.g. Amazon vs eBay), Corvus defends the vault.
        # If loser order is unfulfillable, Corvus quickly resolves it unless it's a high-value collector piece.
        if operation == "resolve_order_contention":
            action = payload.get("action")
            loser_claim = payload.get("loser_claim") or {}
            price_str = loser_claim.get("price_cad", "0.0")
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                price = 0.0

            # High-value antiquarian books (> $200 CAD) require human touch/call
            if price > 200.0:
                logger.info("corvus.contention.high_value_escalation", price=price)
                return False

            if action in ("cancel_loser_marketplace_order", "cancel_marketplace_order", "fulfill_substitute"):
                return True

        # Rule 3: Do not touch fulfill_order. The human must print the physical packing slip.
        if operation == "fulfill_order":
            return False
            
        # Rule 4: Do not touch intake_book. The human must verify the condition and valuation.
        if operation == "intake_book":
            return False

        # If it doesn't match a safe heuristic, Corvus leaves it alone.
        return False
