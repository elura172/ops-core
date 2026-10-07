from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.api.operator import OperatorService
from app.db.inventory import InventoryItem
from app.domain.uiee import UIEEExporter
from app.observability.logging import get_logger

logger = get_logger("abebooks_service")

ABEBOOKS_API_BASE = "https://api.abebooks.com"


class AbeBooksService:
    """Manages AbeBooks Inventory API, UIEE Catalog exports, and Order synchronization."""

    @classmethod
    def export_catalog_uiee(
        cls,
        items: list[InventoryItem],
        status_filter: str | None = "active",
        section_filter: str | None = None,
    ) -> str:
        """Generates standard UIEE file content ready for upload to AbeBooks / Biblio / Alibris."""
        return UIEEExporter.export_catalog(
            items=items,
            status_filter=status_filter,
            section_filter=section_filter,
        )

    @classmethod
    def fetch_orders(
        cls,
        api_key: str = "",
        client_id: str = "",
        dry_run: bool = True,
    ) -> list[dict[str, Any]]:
        """Polls AbeBooks for newly placed orders awaiting processing."""
        if dry_run or not api_key or "mock" in api_key.lower():
            logger.info("abebooks.orders.fetch.dry_run")
            return []

        url = f"{ABEBOOKS_API_BASE}/orders?status=Ordered"
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Client-Id": client_id,
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=12.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                orders = data.get("orders") or data.get("items") or []
                logger.info("abebooks.orders.fetch.success", count=len(orders))
                return orders
        except HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            logger.error("abebooks.orders.fetch.http_error", code=exc.code, error=err)
            return []
        except URLError as exc:
            logger.error("abebooks.orders.fetch.net_error", error=str(exc.reason))
            return []

    @classmethod
    def process_order(
        cls,
        order: dict[str, Any],
        operator: OperatorService,
        tenant_id: int = 1,
    ) -> dict[str, Any]:
        """Processes an incoming AbeBooks order, extracting SKUs and triggering multi-channel dual delisting."""
        order_id = str(order.get("orderId") or order.get("order_id") or "UNKNOWN")
        logger.info("abebooks.order.processing", order_id=order_id, tenant_id=tenant_id)

        items = order.get("items") or order.get("lineItems") or order.get("line_items") or []
        if not items and "bookId" in order:
            items = [{"bookId": order["bookId"]}]
        elif not items and "sku" in order:
            items = [{"sku": order["sku"]}]

        processed_skus: list[str] = []
        for it in items:
            raw_sku = it.get("bookId") or it.get("sku") or it.get("listingId") or it.get("id") or ""
            sku = str(raw_sku).strip()
            if not sku:
                continue

            # Strip mock or test prefixes if present
            if sku.startswith("abe-dryrun-"):
                sku = sku.removeprefix("abe-dryrun-")

            try:
                # 1. Propose inventory sync
                proposed = operator.propose(
                    operation="sync_inventory",
                    payload={"sku": sku, "source_channel": "abebooks", "tenant_id": tenant_id, "order_id": order_id},
                    tenant_id=tenant_id,
                )
                review_id = proposed["review_id"]
                processed_skus.append(sku)
                logger.info("abebooks.sync_proposed", sku=sku, order_id=order_id, review_id=review_id)
            except Exception as exc:
                logger.error("abebooks.sync_failed", sku=sku, order_id=order_id, error=str(exc))

        # 2. Wake Corvus to triage and auto-delist everywhere
        try:
            from app.agents.corvus import CorvusWarden
            warden = CorvusWarden(operator)
            triage_results = warden.wake_and_triage()
            logger.info("corvus.abebooks_triage.complete", **triage_results)
        except Exception as exc:
            logger.error("corvus.abebooks_triage.failed", error=str(exc))

        return {
            "status": "processed",
            "order_id": order_id,
            "processed_skus": processed_skus,
        }

    @classmethod
    def delist_book(
        cls,
        sku: str,
        api_key: str = "",
        client_id: str = "",
        dry_run: bool = True,
    ) -> bool:
        """Delists a volume on AbeBooks via API."""
        if dry_run or not api_key or "mock" in api_key.lower():
            logger.info("abebooks.delist.dry_run", sku=sku)
            return True

        url = f"{ABEBOOKS_API_BASE}/inventory/{sku}/delist"
        req = Request(
            url,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Client-Id": client_id,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=10.0) as resp:
                logger.info("abebooks.delist.success", sku=sku, code=resp.getcode())
                return True
        except Exception as exc:
            logger.error("abebooks.delist.failed", sku=sku, error=str(exc))
            return False
