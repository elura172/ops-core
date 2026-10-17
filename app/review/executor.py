from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
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
    SquareCatalogAdapter,
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
        want_list_store: Any = None,
        publish_gate: PublishGate | None = None,
        mailer: Mailer | None = None,
        shippo_api_key: str = "",
        amazon_sp_api_client_id: str = "",
        amazon_sp_api_client_secret: str = "",
        amazon_sp_api_refresh_token: str = "",
        amazon_sp_api_seller_id: str = "",
        amazon_sp_api_marketplace_id: str = "A2EUQ1WTGCTBG2",
    ) -> None:
        self._tenant_context = ContextVar("executor_tenant", default=1)
        self._credential_context = ContextVar("executor_credentials", default=None)
        self.client = client
        self.audit = audit
        self.shippo_api_key = shippo_api_key
        self.amazon_sp_api_client_id = amazon_sp_api_client_id
        self.amazon_sp_api_client_secret = amazon_sp_api_client_secret
        self.amazon_sp_api_refresh_token = amazon_sp_api_refresh_token
        self.amazon_sp_api_seller_id = amazon_sp_api_seller_id
        self.amazon_sp_api_marketplace_id = amazon_sp_api_marketplace_id
        self.inventory_store = inventory_store
        self.want_list_store = want_list_store
        if inventory_store is not None:
            from app.integrations.stock import StockLedger

            self.stock_ledger = StockLedger(inventory_store)
            if want_list_store is not None:
                want_list_store.stock_ledger = self.stock_ledger

        self.publish_gate = publish_gate or PublishGate(DryRunPublisher(), enabled=False)
        self.mailer = mailer or DryRunMailer()
        # Bound lazily: a client only needs the methods for operations it is
        # actually asked to run.
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "publish_custom_site": self._publish_custom_site,
            "review_stock_conflict": self._review_stock_conflict,
            "create_product_draft": lambda p: self.client.create_product_draft(p),
            "update_inventory": self._update_inventory,
            "update_prices": self._update_prices,
            "publish": self._publish,
            "send_customer_response": self._send_customer_response,
            "intake_book": self._intake_book,
            "review_shelf_capture": self._review_shelf_capture,
            "sync_inventory": self._sync_inventory,
            "fulfill_order": self._fulfill_order,
            "resolve_order_contention": self._resolve_order_contention,
            "update_location": self._update_location,
            "collector_hold_notification": self._collector_hold_notification,
            "claim_collector_hold": self._claim_collector_hold,
            "release_collector_hold": self._release_collector_hold,
            "contention_hold_vs_marketplace": self._resolve_contention_hold_vs_marketplace,
        }

    @property
    def current_tenant_id(self):
        return self._tenant_context.get()

    @current_tenant_id.setter
    def current_tenant_id(self, value):
        self._tenant_context.set(value)

    @property
    def current_credentials(self):
        return self._credential_context.get() or {}

    @current_credentials.setter
    def current_credentials(self, value):
        self._credential_context.set(value)

    @property
    def allowed_operations(self) -> frozenset[str]:
        return frozenset(self.handlers)

    def _review_shelf_capture(self, payload):
        raise PermissionError("Shelf captures execute through the durable review store")

    def _amazon_adapter(self, credentials: dict[str, str]):
        return AmazonSPAPIAdapter(
            # Keep this channel in simulation until Amazon sandbox, listing
            # requirements, order intake, and stock-claim checks are complete.
            dry_run=True,
            refresh_token=credentials.get("amazon_sp_api_refresh_token", self.amazon_sp_api_refresh_token),
            client_id=self.amazon_sp_api_client_id,
            client_secret=self.amazon_sp_api_client_secret,
            merchant_id=credentials.get("amazon_sp_api_seller_id", self.amazon_sp_api_seller_id),
            marketplace_id=self.amazon_sp_api_marketplace_id,
        )

    def _review_stock_conflict(self, payload):
        raise PermissionError("Resolve the competing orders and physical stock before relisting this copy")

    def _publish_custom_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = getattr(self, "custom_site_service", None)
        if not service:
            raise PermissionError("Custom website connection is not configured")
        connection = service.connections.get(payload.get("connection_id"))
        tenant_id = getattr(self, "current_tenant_id", 1)
        if not connection or connection.tenant_id != tenant_id or not connection.enabled:
            raise PermissionError("Custom website connection is not available for this workspace")
        return service.ledger.publish(
            connection,
            payload["sku"],
            payload.get("publication", "published"),
            expected_digest=payload.get("catalogue_digest"),
        )

    def _update_location(self, payload: dict[str, Any]) -> dict[str, Any]:
        sku = payload["sku"]
        section = payload.get("section", "General")
        shelf = payload.get("shelf", "")
        tenant_id = payload.get("tenant_id", 1)
        if self.inventory_store:
            self.inventory_store.update_location(sku, section, shelf, tenant_id=tenant_id)
        return {"status": "ok", "sku": sku, "section": section, "shelf": shelf}

    def _update_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        sku = payload.get("sku")
        stock = payload.get("stock")
        changes = payload.get("changes")
        if changes is None and sku is not None:
            changes = {sku: int(stock if stock is not None else 0)}
        if changes is None:
            changes = {}

        tenant_id = payload.get("tenant_id") or getattr(self, "current_tenant_id", 1)

        # If delisting or setting stock to 0, mark delisted in store and sync across sales channels
        if sku and (stock == 0 or (changes and changes.get(sku) == 0)):
            if self.inventory_store:
                try:
                    self.inventory_store.delist(sku, tenant_id=tenant_id)
                except Exception as exc:
                    logger.warning("inventory_store.delist_failed", sku=sku, error=str(exc))
            try:
                self._sync_inventory({"sku": sku, "source_channel": "delist", "tenant_id": tenant_id})
            except Exception as exc:
                logger.warning("channels.delist_sync_failed", sku=sku, error=str(exc))
        elif sku and stock and stock > 0:
            if self.inventory_store and hasattr(self.inventory_store, "relist"):
                try:
                    self.inventory_store.relist(sku, tenant_id=tenant_id)
                except Exception as exc:
                    logger.warning("inventory_store.relist_failed", sku=sku, error=str(exc))

        if hasattr(self.client, "update_inventory"):
            return self.client.update_inventory(changes)

        return {"operation": "update_inventory", "changes": changes, "dry_run": True}

    def _update_prices(self, payload: dict[str, Any]) -> dict[str, Any]:
        sku = payload.get("sku")
        changes = payload.get("changes")
        if changes is None and sku:
            price = str(payload.get("online_price_cad") or payload.get("price") or "0.00")
            changes = {sku: price}
        if changes is None:
            changes = {}

        tenant_id = payload.get("tenant_id") or getattr(self, "current_tenant_id", 1)
        if sku and self.inventory_store and ("in_store_price_cad" in payload or "online_price_cad" in payload):
            from decimal import Decimal

            try:
                in_store = Decimal(str(payload.get("in_store_price_cad", "0.00")))
                online = Decimal(str(payload.get("online_price_cad", "0.00")))
                self.inventory_store.update_prices(sku, in_store, online, tenant_id=tenant_id)
            except Exception as exc:
                logger.warning("inventory_store.update_prices_failed", sku=sku, error=str(exc))

        if hasattr(self.client, "update_prices"):
            return self.client.update_prices(changes)

        return {"operation": "update_prices", "changes": changes, "dry_run": True}

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
        import uuid
        from decimal import Decimal

        from app.db.inventory import InventoryItem
        from app.publish.books import SquareCatalogAdapter

        val = payload.get("valuation", {})
        ext = payload.get("extraction", {})
        dis = payload.get("disambiguation", {})
        cand = dis.get("best_candidate", {})

        title = cand.get("title") or ext.get("title", "Unknown Title")
        raw_authors = cand.get("authors") or ext.get("authors") or ["Unknown"]
        authors = [raw_authors] if isinstance(raw_authors, str) else list(raw_authors)
        primary_author = authors[0] if authors else "Unknown"

        condition = val.get("condition_grade", "Good")
        fmt = ext.get("format", "Hardcover")
        in_store_price = Decimal(str(val.get("in_store_price_cad", "0.00")))
        online_price = Decimal(str(val.get("online_price_cad", "0.00")))
        intake_tenant = getattr(self, "current_tenant_id", None) or payload.get("tenant_id") or 1
        effective_price = online_price if online_price > 0 else in_store_price

        # Check for Collector Want match (Priority Courtesy Hold)
        matched_want = None
        matched_collector = None
        if self.want_list_store:
            from app.domain.want_list import WantListMatcher

            matcher = WantListMatcher()
            active_wants = self.want_list_store.list_wants(tenant_id=intake_tenant, status="active")
            candidate_dict = {
                "title": title,
                "authors": authors,
                "format": fmt,
                "condition": condition,
                "price_cad": effective_price,
            }
            for want in active_wants:
                collector = self.want_list_store.get_collector(want.collector_id, tenant_id=intake_tenant)
                if not collector:
                    continue
                match_res = matcher.evaluate(want, collector, candidate_dict)
                if match_res.matched:
                    matched_want = want
                    matched_collector = collector
                    break

        if matched_want and matched_collector:
            sku = f"BK-{uuid.uuid4().hex[:8].upper()}"
            section = payload.get("section") or "Holds / Vault"
            shelf = payload.get("shelf") or "Courtesy Holds"
            now = datetime.now(UTC)
            expires_at = now + timedelta(hours=48)

            if self.inventory_store:
                item = InventoryItem(
                    sku=sku,
                    title=title,
                    authors=authors,
                    format=fmt,
                    condition=condition,
                    in_store_price_cad=in_store_price,
                    online_price_cad=online_price,
                    status="on_hold",
                    created_at=now,
                    updated_at=now,
                    section=section,
                    shelf=shelf,
                )
                self.inventory_store.add(item, tenant_id=intake_tenant)

            from app.db.want_list import CollectorHold

            hold = CollectorHold(
                id=f"hld-{uuid.uuid4().hex[:10]}",
                tenant_id=intake_tenant,
                sku=sku,
                want_item_id=matched_want.id,
                collector_id=matched_collector.id,
                held_at=now,
                expires_at=expires_at,
                status="active",
                notes=f"Auto-held on intake for want '{matched_want.title}'. 48-hour priority courtesy hold.",
                book_title=title,
                book_author=primary_author,
                collector_name=matched_collector.name,
                price_cad=effective_price,
            )
            self.want_list_store.create_hold(hold)

            channel_names = [
                "abebooks",
                "ebay",
                "amazon_spapi",
                "biblio",
                "alibris",
                "shopify",
                "square",
            ]
            marketplace_results = {
                ch: {
                    "status": "withheld",
                    "reason": f"Priority courtesy hold for {matched_collector.name} (expires in 48h)",
                }
                for ch in channel_names
            }

            return {
                "sku": sku,
                "status": "on_hold",
                "hold_id": hold.id,
                "collector_id": matched_collector.id,
                "collector_name": matched_collector.name,
                "collector_phone": matched_collector.phone,
                "collector_email": matched_collector.email,
                "expires_at": expires_at.isoformat(),
                "marketplace_results": marketplace_results,
                "details": {
                    "sku": sku,
                    "title": title,
                    "authors": authors,
                    "format": fmt,
                    "condition": condition,
                    "in_store_price_cad": in_store_price,
                    "online_price_cad": online_price,
                    "section": section,
                    "shelf": shelf,
                },
            }

        # Normal multi-channel marketplace listing
        credentials = getattr(self, "current_credentials", {})
        adapters = (
            AbeBooksListingAdapter(dry_run=True, api_key=credentials.get("abebooks", "mock_key")),
            self._amazon_adapter(credentials),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True, api_token=credentials.get("ebay", "mock_token")),
            ShopifyListingAdapter(dry_run=True),
            SquareCatalogAdapter(dry_run=True, access_token=credentials.get("square", "")),
        )
        orchestrator = BookListingOrchestrator(adapters)
        result = orchestrator.list_inventory(payload)

        if self.inventory_store:
            details = result.get("details") or {}
            sku = result.get("sku") or details.get("sku") or payload.get("sku") or f"BK-{uuid.uuid4().hex[:8].upper()}"
            res_title = details.get("title") or title
            res_authors = details.get("authors") or authors
            res_fmt = details.get("format") or fmt
            res_condition = details.get("condition") or condition
            res_in_store = Decimal(str(details.get("in_store_price_cad", in_store_price)))
            res_online = Decimal(str(details.get("online_price_cad", online_price)))
            section = payload.get("section") or details.get("section") or "General"
            shelf = payload.get("shelf") or details.get("shelf") or ""
            item = InventoryItem(
                sku=sku,
                title=res_title,
                authors=res_authors,
                format=res_fmt,
                condition=res_condition,
                in_store_price_cad=res_in_store,
                online_price_cad=res_online,
                status="active",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                section=section,
                shelf=shelf,
            )
            self.inventory_store.add(item, tenant_id=intake_tenant)

            if "details" not in result or not isinstance(result["details"], dict):
                result["details"] = {}
            result["details"]["sku"] = sku
            result["sku"] = sku

        return result

    def _collector_hold_notification(self, payload: dict[str, Any]) -> dict[str, Any]:
        collector_email = payload.get("collector_email")
        collector_name = payload.get("collector_name", "Collector")
        title = payload.get("title", "Requested Volume")
        price_cad = payload.get("price_cad", "0.00")
        expires_at = payload.get("expires_at", "")
        message = payload.get("message", "")

        if self.mailer and collector_email:
            try:
                self.mailer.send(
                    CustomerResponse(
                        recipient=collector_email,
                        subject=f"Priority Courtesy Hold: '{title}' at Elura Antiquarian Books",
                        body_text=(
                            f"Dear {collector_name},\n\n"
                            f"We have acquired a volume matching your active want list:\n\n"
                            f"'{title}'\n"
                            f"Price: ${price_cad} CAD\n\n"
                            "As a preferred collector, we have placed a 48-hour priority courtesy "
                            "hold on this book for you. "
                            f"Your hold is active until {expires_at}.\n\n"
                            "Please reply or call the shop to claim this volume before it is "
                            "released to the general public.\n\n"
                            f"Warm regards,\n"
                            f"Elura Antiquarian Vault"
                        ),
                    )
                )
            except Exception as exc:
                logger.warning("collector_hold_notification.mailer_failed", error=str(exc))

        return {
            "status": "notified",
            "collector_name": collector_name,
            "collector_email": collector_email,
            "message": message,
        }

    def _claim_collector_hold(self, payload: dict[str, Any]) -> dict[str, Any]:
        hold_id = payload["hold_id"]
        tenant_id = payload.get("tenant_id", 1)
        notes = payload.get("notes", "Claimed by collector")

        if not self.want_list_store:
            raise RuntimeError("WantListStore not configured")

        hold = self.want_list_store.get_hold(hold_id, tenant_id=tenant_id)
        if not hold:
            raise KeyError(f"Hold {hold_id} not found")

        ledger = getattr(self, "stock_ledger", None)
        if ledger:
            outcome = ledger.observe_sale(tenant_id, hold.sku, "collector", hold_id, hold_owner=hold_id)
            if outcome == "conflict":
                raise PermissionError("Collector claim conflicts with another stock claim")

        now = datetime.now(UTC)
        self.want_list_store.update_hold(hold_id, tenant_id=tenant_id, status="claimed", claimed_at=now, notes=notes)
        # Mark want item as fulfilled
        self.want_list_store.update_want(hold.want_item_id, tenant_id=tenant_id, status="fulfilled")

        # Mark inventory SKU as sold
        if self.inventory_store:
            self.inventory_store.delist(hold.sku, tenant_id=tenant_id)

        # Multi-channel delist
        sync_result = self._sync_inventory(
            {"sku": hold.sku, "source_channel": "collector_claim", "tenant_id": tenant_id}
        )

        return {
            "status": "claimed",
            "hold_id": hold_id,
            "sku": hold.sku,
            "collector_id": hold.collector_id,
            "collector_name": hold.collector_name,
            "sync_result": sync_result,
        }

    def _release_collector_hold(self, payload: dict[str, Any]) -> dict[str, Any]:
        hold_id = payload["hold_id"]
        tenant_id = payload.get("tenant_id", 1)
        reason = payload.get("reason", "manual_release")

        if not self.want_list_store:
            raise RuntimeError("WantListStore not configured")

        hold = self.want_list_store.get_hold(hold_id, tenant_id=tenant_id)
        if not hold:
            raise KeyError(f"Hold {hold_id} not found")

        now = datetime.now(UTC)
        self.want_list_store.update_hold(
            hold_id,
            tenant_id=tenant_id,
            status="released",
            released_at=now,
            notes=f"Released to floor: {reason}",
        )

        # Re-activate inventory item
        book_item = None
        if self.inventory_store:
            book_item = self.inventory_store.get(hold.sku, tenant_id=tenant_id)
            if book_item and book_item.status in ("on_hold", "held"):
                self.inventory_store.relist(hold.sku, tenant_id=tenant_id)

        # Trigger marketplace publishing now that the hold is released
        credentials = getattr(self, "current_credentials", {})
        adapters = (
            AbeBooksListingAdapter(dry_run=True, api_key=credentials.get("abebooks", "mock_key")),
            self._amazon_adapter(credentials),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True, api_token=credentials.get("ebay", "mock_token")),
            ShopifyListingAdapter(dry_run=True),
            SquareCatalogAdapter(dry_run=True, access_token=credentials.get("square", "")),
        )
        orchestrator = BookListingOrchestrator(adapters)
        publish_payload = {
            "valuation": {
                "channel_strategy": "multi_channel_antiquarian",
                "online_price_cad": str(hold.price_cad or (book_item.online_price_cad if book_item else "0.00")),
                "condition_grade": book_item.condition if book_item else "Good",
            },
            "extraction": {
                "title": hold.book_title or (book_item.title if book_item else "Unknown"),
                "authors": [hold.book_author] if hold.book_author else (book_item.authors if book_item else []),
                "format": book_item.format if book_item else "Hardcover",
            },
            "disambiguation": {
                "best_candidate": {
                    "title": hold.book_title or (book_item.title if book_item else "Unknown"),
                    "authors": [hold.book_author] if hold.book_author else (book_item.authors if book_item else []),
                }
            },
        }
        listing_result = orchestrator.list_inventory(publish_payload)

        return {
            "status": "released",
            "hold_id": hold_id,
            "sku": hold.sku,
            "marketplace_results": listing_result.get("marketplace_results", {}),
        }

    def _resolve_contention_hold_vs_marketplace(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolves an approved collision between an active collector hold and an incoming marketplace order.

        Operator approved: fulfill marketplace order, release courtesy hold, and delist everywhere.
        """
        hold_id = payload.get("hold_id")
        sku = payload["sku"]
        order_id = payload.get("order_id", "UNKNOWN")
        source_channel = payload.get("source_channel", "biblio")
        tenant_id = payload.get("tenant_id", 1)

        ledger = getattr(self, "stock_ledger", None)
        if ledger:
            outcome = ledger.observe_sale(tenant_id, sku, source_channel, order_id, hold_owner=hold_id)
            if outcome == "conflict":
                raise PermissionError("Competing stock claims need staff reconciliation")

        # 1. Release the courtesy hold
        if hold_id and self.want_list_store:
            now = datetime.now(UTC)
            self.want_list_store.update_hold(
                hold_id,
                tenant_id=tenant_id,
                status="released",
                stock_transferred=bool(ledger),
                released_at=now,
                notes=f"Released for {source_channel} order {order_id} via operator decision",
            )

        # 2. Mark inventory SKU as delisted/sold locally
        if self.inventory_store:
            self.inventory_store.delist(sku, tenant_id=tenant_id)

        # 3. Trigger multi-channel delisting across all channels
        sync_result = self._sync_inventory(
            {
                "sku": sku,
                "source_channel": source_channel,
                "tenant_id": tenant_id,
                "order_id": order_id,
            }
        )

        return {
            "status": "contention_resolved",
            "action": "fulfilled_marketplace_order",
            "sku": sku,
            "order_id": order_id,
            "channel": source_channel,
            "hold_id": hold_id,
            "sync_result": sync_result,
        }

    def _sync_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.publish.inventory_sync import MultiChannelInventorySync

        credentials = getattr(self, "current_credentials", {})

        adapters = (
            AbeBooksListingAdapter(dry_run=True, api_key=credentials.get("abebooks", "mock_key")),
            self._amazon_adapter(credentials),
            BiblioListingAdapter(dry_run=True, api_key=credentials.get("biblio", "mock_key")),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True, api_token=credentials.get("ebay", "mock_token")),
            ShopifyListingAdapter(dry_run=True, access_token=credentials.get("shopify", "")),
            SquareCatalogAdapter(dry_run=True, access_token=credentials.get("square", "")),
        )
        sync = MultiChannelInventorySync(adapters)

        sku = payload.get("sku", "")
        source_channel = payload.get("source_channel", "")
        if not sku:
            raise ValueError("sku is required for sync_inventory")

        result = sync.process_sale(sku, source_channel)

        tenant_id = payload.get("tenant_id") or getattr(self, "current_tenant_id", 1)
        if self.inventory_store:
            try:
                self.inventory_store.delist(sku, tenant_id=tenant_id)
                result["inventory_store"] = "delisted"
            except Exception as exc:
                result["inventory_store_error"] = str(exc)
                logger.error("inventory_store.delist.failed", sku=sku, tenant_id=tenant_id, error=str(exc))

        return result

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
                "amazon": self._amazon_adapter(credentials),
                "amazon_spapi": self._amazon_adapter(credentials),
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
        if operation == "review_stock_conflict":
            raise PermissionError("Staff must resolve the competing orders before relisting the copy")
        if operation == "publish" and not self.publish_gate.enabled:
            raise PermissionError("publishing is disabled")
        if operation == "send_customer_response" and not getattr(self.mailer, "enabled", True):
            raise PermissionError("customer email sending is disabled")

    def _dispatch(
        self,
        operation: str,
        payload: dict[str, Any],
        credentials: dict[str, str] = None,
        tenant_id: int = 1,
    ) -> dict[str, Any]:
        handler = self.handlers.get(operation)
        if handler is None:
            raise ValueError(f"operation is not allowlisted: {operation}")

        tenant_token = self._tenant_context.set(tenant_id)
        credential_token = self._credential_context.set(credentials or {})
        if operation in {
            "intake_book",
            "sync_inventory",
            "update_inventory",
            "update_prices",
            "update_location",
            "claim_collector_hold",
            "release_collector_hold",
            "contention_hold_vs_marketplace",
            "publish_custom_site",
            "review_stock_conflict",
        }:
            payload = {**payload, "tenant_id": tenant_id}
        try:
            result = handler(payload)
        finally:
            self._tenant_context.reset(tenant_token)
            self._credential_context.reset(credential_token)

        if not isinstance(result, dict):
            raise TypeError(f"handler for {operation} must return a dictionary")
        return result

    def _audit(self, action: str, actor: str, outcome: str, resource_id: str, **detail: str) -> None:
        if self.audit is not None:
            self.audit.record(AuditEvent(action, actor, outcome, resource_id, detail))

    def execute(
        self,
        store: ReviewStore,
        review_id: str,
        *,
        actor: str,
        tenant_id: int = 1,
        credentials: dict[str, str] = None,
    ) -> dict[str, Any]:
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
            if claimed.operation == "review_shelf_capture":
                from app.domain.shelf_capture import create_book_reviews

                result = create_book_reviews(store, claimed.payload, tenant_id)
            else:
                result = self._dispatch(
                    claimed.operation,
                    claimed.payload,
                    credentials=credentials,
                    tenant_id=tenant_id,
                )
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
