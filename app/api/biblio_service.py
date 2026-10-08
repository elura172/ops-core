from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.api.operator import OperatorService
from app.db.want_list import WantListStore
from app.observability.logging import get_logger

logger = get_logger("biblio_service")

BIBLIO_API_BASE = "https://api.biblio.com"


class BiblioService:
    """Manages Biblio Direct API, active order synchronization, and collision arbitration."""

    @classmethod
    def fetch_orders(
        cls,
        api_key: str = "",
        dealer_id: str = "",
        dry_run: bool = True,
        demo_sample: bool = False,
    ) -> list[dict[str, Any]]:
        """Polls Biblio for newly placed open/unfulfilled orders.

        Supports live API polling with token authentication, and demo simulation in dry-run mode.
        """
        if dry_run or not api_key or "mock" in api_key.lower():
            logger.info("biblio.orders.fetch.dry_run", demo_sample=demo_sample)
            if demo_sample:
                return [
                    {
                        "order_id": "BIB-892410",
                        "status": "OPEN",
                        "ordered_at": "2026-10-08T02:00:00Z",
                        "buyer_name": "Antiquarian Bibliophile",
                        "items": [
                            {
                                "sku": "BK-DUNE-1965",
                                "title": "Dune (First Edition)",
                                "price": "180.00",
                                "quantity": 1,
                            }
                        ],
                    }
                ]
            return []

        url = f"{BIBLIO_API_BASE}/orders?status=open"
        headers = {
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
        }
        if dealer_id:
            headers["X-Biblio-Dealer-Id"] = dealer_id

        req = Request(url, headers=headers, method="GET")

        try:
            with urlopen(req, timeout=12.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                orders = data.get("orders") or data.get("items") or []
                logger.info("biblio.orders.fetch.success", count=len(orders))
                return orders
        except HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            logger.error("biblio.orders.fetch.http_error", code=exc.code, error=err)
            return []
        except URLError as exc:
            logger.error("biblio.orders.fetch.net_error", error=str(exc.reason))
            return []

    @classmethod
    def process_order(
        cls,
        order: dict[str, Any],
        operator: OperatorService,
        tenant_id: int = 1,
        want_list_store: WantListStore | None = None,
    ) -> dict[str, Any]:
        """Processes an incoming Biblio order.

        Detects collisions with active 48-hour Collector Courtesy Holds:
        - If active hold exists: Creates an urgent Contention Card on the Review Desk.
        - If no hold: Proposes instant inventory sync and triggers Corvus dual-delist.
        """
        order_id = str(order.get("order_id") or order.get("orderId") or "UNKNOWN")
        logger.info("biblio.order.processing", order_id=order_id, tenant_id=tenant_id)

        items = order.get("items") or order.get("line_items") or order.get("lineItems") or []
        if not items and "sku" in order:
            items = [{"sku": order["sku"]}]

        processed_skus: list[str] = []
        collisions: list[dict[str, Any]] = []

        for it in items:
            raw_sku = it.get("sku") or it.get("listing_id") or it.get("book_id") or it.get("id") or ""
            sku = str(raw_sku).strip()
            if not sku:
                continue

            if sku.startswith("biblio-dryrun-"):
                sku = sku.removeprefix("biblio-dryrun-")

            # 1. Check for active Collector Courtesy Hold
            active_hold = None
            if want_list_store:
                try:
                    active_hold = want_list_store.get_active_hold_for_sku(sku, tenant_id=tenant_id)
                except Exception as exc:
                    logger.warning("biblio.hold_lookup.failed", sku=sku, error=str(exc))

            if active_hold and getattr(active_hold, "status", "") == "active":
                # Contention detected: Active Patron Hold vs Incoming Biblio Order!
                logger.warning(
                    "biblio.order.contention_detected",
                    sku=sku,
                    order_id=order_id,
                    hold_id=active_hold.id,
                    collector=active_hold.collector_name,
                )
                collision_payload = {
                    "sku": sku,
                    "order_id": order_id,
                    "source_channel": "biblio",
                    "hold_id": active_hold.id,
                    "collector_id": active_hold.collector_id,
                    "collector_name": active_hold.collector_name,
                    "book_title": active_hold.book_title,
                    "hold_expires_at": active_hold.expires_at.isoformat() if hasattr(active_hold.expires_at, "isoformat") else str(active_hold.expires_at),
                    "buyer_name": str(order.get("buyer_name") or "Biblio Online Buyer"),
                    "price_cad": float(active_hold.price_cad or 0),
                    "reason": f"Active 48h courtesy hold for '{active_hold.collector_name}' collides with incoming Biblio order {order_id}.",
                }
                proposed = operator.propose(
                    operation="contention_hold_vs_marketplace",
                    payload=collision_payload,
                    tenant_id=tenant_id,
                )
                collisions.append({
                    "sku": sku,
                    "review_id": proposed["review_id"],
                    "hold_id": active_hold.id,
                })
                processed_skus.append(sku)
                continue

            # 2. Standard flow: propose inventory sync and auto-delist
            try:
                proposed = operator.propose(
                    operation="sync_inventory",
                    payload={
                        "sku": sku,
                        "source_channel": "biblio",
                        "tenant_id": tenant_id,
                        "order_id": order_id,
                    },
                    tenant_id=tenant_id,
                )
                processed_skus.append(sku)
                logger.info("biblio.sync_proposed", sku=sku, order_id=order_id, review_id=proposed["review_id"])
            except Exception as exc:
                logger.error("biblio.sync_failed", sku=sku, order_id=order_id, error=str(exc))

        triage_results = None
        # 3. Wake Corvus to triage normal syncs
        if processed_skus and not collisions:
            try:
                from app.agents.corvus import CorvusWarden
                warden = CorvusWarden(operator)
                triage_results = warden.wake_and_triage()
                logger.info("corvus.biblio_triage.complete", **triage_results)
            except Exception as exc:
                logger.error("corvus.biblio_triage.failed", error=str(exc))

        return {
            "status": "processed",
            "order_id": order_id,
            "processed_skus": processed_skus,
            "collisions": collisions,
            "corvus_triage": triage_results,
        }

    @classmethod
    def delist_book(
        cls,
        sku: str,
        api_key: str = "",
        dealer_id: str = "",
        dry_run: bool = True,
    ) -> bool:
        """Delists a volume on Biblio via Direct API."""
        if dry_run or not api_key or "mock" in api_key.lower():
            logger.info("biblio.delist.dry_run", sku=sku)
            return True

        url = f"{BIBLIO_API_BASE}/listings/{sku}"
        req = Request(
            url,
            headers={
                "Authorization": f"Token {api_key}",
                "Accept": "application/json",
            },
            method="DELETE",
        )
        try:
            with urlopen(req, timeout=10.0) as resp:
                logger.info("biblio.delist.success", sku=sku, status=resp.status)
                return True
        except HTTPError as exc:
            logger.error("biblio.delist.http_error", sku=sku, code=exc.code)
            return False
        except URLError as exc:
            logger.error("biblio.delist.net_error", sku=sku, error=str(exc.reason))
            return False

    @classmethod
    def cancel_order(
        cls,
        order_id: str,
        reason: str = "Reserved for established patron",
        api_key: str = "",
        dealer_id: str = "",
        dry_run: bool = True,
    ) -> bool:
        """Cancels an order on Biblio (e.g. when protecting a patron courtesy hold)."""
        if dry_run or not api_key or "mock" in api_key.lower():
            logger.info("biblio.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True

        url = f"{BIBLIO_API_BASE}/orders/{order_id}/cancel"
        body = json.dumps({"reason": reason}).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=10.0) as resp:
                logger.info("biblio.cancel_order.success", order_id=order_id, status=resp.status)
                return True
        except Exception as exc:
            logger.error("biblio.cancel_order.failed", order_id=order_id, error=str(exc))
            return False
