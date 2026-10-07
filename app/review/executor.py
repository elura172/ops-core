from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

from app.customer_support.email import CustomerResponse, DryRunMailer, Mailer
from app.domain.audit import AuditEvent
from app.observability import metrics
from app.observability.logging import get_logger
from app.publish.books import (
    AbeBooksListingAdapter,
    AlibrisListingAdapter,
    AmazonSPAPIAdapter,
    BiblioListingAdapter,
    BookListingOrchestrator,
    EbayListingAdapter,
    ShopifyListingAdapter,
)
from app.publish.layer import DryRunPublisher, PublishGate, PublishRequest
from app.review.queue import AlreadyExecuted, ReviewItem, ReviewStore

logger = get_logger("executor")


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


class ApprovedActionExecutor:
    """Only executes a narrow allowlist of approved operations, and only once.

    The store owns every state transition, so a replay, a second worker, or a
    restart cannot run the same approved mutation twice. ``audit`` is optional:
    stores that write a durable trail in the same transaction pass ``None`` so the
    trail has exactly one sink.

    Every operation the agent layer can produce has a handler here, so an approval
    never dead-ends. Approval is necessary but not sufficient: `publish` still has
    to pass `PublishGate`, and both it and `send_customer_response` route through
    adapters that are disabled or dry-run by default.
    """

    def __init__(
        self,
        # Deliberately untyped: handlers bind lazily, so a client only needs the
        # methods for the operations it is actually asked to run.
        client: Any,
        audit: AuditSink | None = None,
        *,
        inventory_store: Any = None,
        publish_gate: PublishGate | None = None,
        mailer: Mailer | None = None,
        shippo_api_key: str = "",
    ) -> None:
        self.client = client
        self.audit = audit
        self.shippo_api_key = shippo_api_key
        self.inventory_store = inventory_store
        self.publish_gate = publish_gate or PublishGate(DryRunPublisher(), enabled=False)
        self.mailer = mailer or DryRunMailer()
        # Bound lazily: a client only needs the methods for operations it is
        # actually asked to run.
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "create_product_draft": lambda p: self.client.create_product_draft(p),
            "update_inventory": lambda p: self.client.update_inventory(p["changes"]),
            "update_prices": lambda p: self.client.update_prices(p["changes"]),
            "publish": self._publish,
            "send_customer_response": self._send_customer_response,
            "intake_book": self._intake_book,
            "sync_inventory": self._sync_inventory,
            "fulfill_order": self._fulfill_order,
            "resolve_order_contention": self._resolve_order_contention,
        }

    @property
    def allowed_operations(self) -> frozenset[str]:
        return frozenset(self.handlers)

    def _publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = PublishRequest(
            publish_id=payload["publish_id"],
            resource_type=payload["resource_type"],
            resource_id=payload["resource_id"],
            payload=payload.get("payload", {}),
        )
        # The review supplied the approval; the gate still decides whether
        # publishing is switched on at all.
        return asdict(self.publish_gate.execute(request, approved=True))

    def _intake_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapters = (
            AbeBooksListingAdapter(dry_run=True),
            AmazonSPAPIAdapter(dry_run=True),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True),
            ShopifyListingAdapter(dry_run=True),
        )
        orchestrator = BookListingOrchestrator(adapters)
        result = orchestrator.list_inventory(payload)
        
        if result.get("status") not in ("failed", "skipped") and self.inventory_store and "sku" in result.get("details", {}):
            from app.db.inventory import InventoryItem
            from datetime import UTC, datetime
            from decimal import Decimal
            details = result["details"]
            item = InventoryItem(
                sku=details["sku"],
                title=details["title"],
                authors=details["authors"],
                format=details["format"],
                condition=details["condition"],
                in_store_price_cad=Decimal(str(details["in_store_price_cad"])),
                online_price_cad=Decimal(str(details["online_price_cad"])),
                status="active",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC)
            )
            self.inventory_store.add(item)
            
        return result

    def _sync_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.publish.inventory_sync import MultiChannelInventorySync
        
        credentials = getattr(self, "current_credentials", {})
        
        adapters = (
            AbeBooksListingAdapter(dry_run=True),
            AmazonSPAPIAdapter(dry_run=True, refresh_token=credentials.get("amazon", "mock_token")),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True),
            ShopifyListingAdapter(dry_run=True, access_token=credentials.get("shopify", "")),
        )
        sync = MultiChannelInventorySync(adapters)
        
        sku = payload.get("sku", "")
        source_channel = payload.get("source_channel", "")
        if not sku:
            raise ValueError("sku is required for sync_inventory")
            
        return sync.process_sale(sku, source_channel)

    def _fulfill_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.domain.carriers import ShippoAdapter
        from app.domain.packing_slip import generate_packing_slip
        
        provider = payload.get("preferred_provider", "USPS")
        # In ops-core, keys typically come from settings.
        api_key = self.shippo_api_key or "shippo_test_mock_key"
        
        # Instantiate adapter in dry_run mode by default for safety in this environment
        adapter = ShippoAdapter(api_key=api_key, dry_run=True)
        label_data = adapter.create_label(payload)
        
        mock_tracking = label_data["tracking_number"]
        slip_b64 = generate_packing_slip(payload, mock_tracking, provider)
        
        # Combine the results
        label_data["packing_slip_base64"] = slip_b64
        
        return label_data

    def _resolve_order_contention(self, payload: dict[str, Any]) -> dict[str, Any]:
        sku = payload.get("sku", "")
        action = payload.get("action", "")
        loser_order_id = payload.get("loser_order_id")
        loser_channel = payload.get("loser_channel", "")
        cancellation_reason = payload.get("cancellation_reason", "OUT_OF_STOCK")

        results: dict[str, Any] = {
            "sku": sku,
            "action": action,
            "loser_order_id": loser_order_id,
            "loser_channel": loser_channel,
            "status": "resolved",
        }

        if action in ("cancel_loser_marketplace_order", "cancel_marketplace_order"):
            credentials = getattr(self, "current_credentials", {})
            adapters = {
                "abebooks": AbeBooksListingAdapter(dry_run=True),
                "amazon": AmazonSPAPIAdapter(dry_run=True, refresh_token=credentials.get("amazon", "mock_token")),
                "amazon_spapi": AmazonSPAPIAdapter(dry_run=True, refresh_token=credentials.get("amazon", "mock_token")),
                "biblio": BiblioListingAdapter(dry_run=True),
                "alibris": AlibrisListingAdapter(dry_run=True),
                "ebay": EbayListingAdapter(dry_run=True),
                "shopify": ShopifyListingAdapter(dry_run=True, access_token=credentials.get("shopify", "")),
            }
            adapter = adapters.get(str(loser_channel).lower())
            if adapter and loser_order_id:
                canceled = adapter.cancel_order(loser_order_id, reason=cancellation_reason)
                results["marketplace_cancellation"] = "success" if canceled else "failed"
            else:
                results["marketplace_cancellation"] = "recorded"
        elif action == "fulfill_substitute":
            results["substitute_sku"] = payload.get("substitute_sku")
            results["message"] = f"Substitute SKU {payload.get('substitute_sku')} routed for fulfillment."

        return results

    def _send_customer_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = CustomerResponse(
            response_id=payload["response_id"],
            recipient=payload["recipient"],
            subject=payload["subject"],
            body=payload["body"],
            order_id=payload.get("order_id"),
            approved=True,
        )
        self.mailer.send(response)
        # Deliberately no recipient or body in the result; the review payload is
        # the single place that content lives.
        return {"delivered": True, "response_id": response.response_id}

    def preflight(self, operation: str) -> None:
        """Refuse actions whose adapter is switched off, before claiming the item.

        Nothing was attempted, so the approval stays usable: the operator enables
        the adapter and executes again rather than going back for a re-review.
        """
        if operation == "publish" and not self.publish_gate.enabled:
            raise PermissionError("publishing is disabled")
        if operation == "send_customer_response" and not getattr(self.mailer, "enabled", True):
            raise PermissionError("customer email sending is disabled")

    def _dispatch(self, operation: str, payload: dict[str, Any], credentials: dict[str, str] = None) -> dict[str, Any]:
        handler = self.handlers.get(operation)
        if handler is None:
            raise ValueError(f"operation is not allowlisted: {operation}")
            
        self.current_credentials = credentials or {}
        
        result = handler(payload)
        
        self.current_credentials = {}
        
        if not isinstance(result, dict):
            raise TypeError(f"handler for {operation} must return a dictionary")
        return result

    def _audit(
        self, action: str, actor: str, outcome: str, resource_id: str, **detail: str
    ) -> None:
        if self.audit is not None:
            self.audit.record(AuditEvent(action, actor, outcome, resource_id, detail))

    def execute(self, store: ReviewStore, review_id: str, *, actor: str, tenant_id: int = 1, credentials: dict[str, str] = None) -> dict[str, Any]:
        item = store.get(review_id, tenant_id=tenant_id)
        if item is None:
            raise KeyError(review_id)

        # Reject unknown operations before claiming, so a typo does not burn the
        # approval and force a re-review.
        if item.operation not in self.handlers:
            raise ValueError(f"operation is not allowlisted: {item.operation}")
        self.preflight(item.operation)

        try:
            claimed = store.claim_for_execution(review_id, actor, tenant_id=tenant_id)
        except AlreadyExecuted as exc:
            metrics.executions_total().inc(outcome="replayed")
            logger.info("review.execute.replayed", review_id=review_id, actor=actor)
            return {"replayed": True, "result": exc.item.result, "item": exc.item}
        except (ValueError, PermissionError):
            # A racer lost mid-flight, or the item is terminal. Nothing ran.
            metrics.executions_total().inc(outcome="claim_rejected")
            raise

        started = time.monotonic()
        try:
            result = self._dispatch(claimed.operation, claimed.payload, credentials=credentials)
        except Exception as exc:
            metrics.executions_total().inc(outcome="failed")
            failed = store.fail_execution(review_id, str(exc), tenant_id=tenant_id)
            self._audit(
                "approved_action.execute",
                actor,
                "failed",
                review_id,
                operation=claimed.operation,
            )
            logger.error(
                "review.execute.failed",
                review_id=review_id,
                operation=claimed.operation,
                actor=actor,
            )
            raise ExecutionFailed(failed, str(exc)) from exc

        executed = store.complete_execution(review_id, result, tenant_id=tenant_id)
        metrics.executions_total().inc(outcome="completed")
        metrics.execution_seconds().observe(time.monotonic() - started)
        self._audit(
            "approved_action.execute",
            actor,
            "completed",
            review_id,
            operation=claimed.operation,
            approved_by=claimed.reviewer or "unknown",
        )
        logger.info(
            "review.execute.completed",
            review_id=review_id,
            operation=claimed.operation,
            actor=actor,
            approved_by=claimed.reviewer,
        )
        return {"replayed": False, "result": result, "item": executed}


class ExecutionFailed(Exception):
    """The mutation failed; the item is terminal and needs a fresh approval."""

    def __init__(self, item: ReviewItem, error: str) -> None:
        super().__init__(error)
        self.item = item
        self.error = error
