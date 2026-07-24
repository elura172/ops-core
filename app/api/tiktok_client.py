from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TikTokClient:
    """Boundary for the official API; live mutation is opt-in and unimplemented in MVP."""

    access_token: str
    shop_id: str
    dry_run: bool = True

    def create_product_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload:
            raise ValueError("payload cannot be empty")
        if self.dry_run:
            return {"dry_run": True, "operation": "create_product_draft", "payload": payload}
        raise NotImplementedError(
            "implement current signed Product API request after sandbox verification"
        )

    def update_inventory(self, changes: dict[str, int]) -> dict[str, Any]:
        if any(not sku or quantity < 0 for sku, quantity in changes.items()):
            raise ValueError("inventory changes require non-empty SKUs and non-negative quantities")
        if self.dry_run:
            return {"dry_run": True, "operation": "update_inventory", "changes": changes}
        raise NotImplementedError(
            "implement current signed inventory request after sandbox verification"
        )

    def update_prices(self, changes: dict[str, str]) -> dict[str, Any]:
        if any(not sku or not price for sku, price in changes.items()):
            raise ValueError("price changes require non-empty SKUs and prices")
        if self.dry_run:
            return {"dry_run": True, "operation": "update_prices", "changes": changes}
        raise NotImplementedError(
            "implement current signed price request after sandbox verification"
        )

    def get_order_list(self, cursor: str | None = None) -> dict[str, Any]:
        if self.dry_run:
            return {"dry_run": True, "operation": "get_order_list", "cursor": cursor, "orders": []}
        raise NotImplementedError(
            "implement current signed Order API request after sandbox verification"
        )

    def get_order_detail(self, order_id: str) -> dict[str, Any]:
        if not order_id:
            raise ValueError("order_id is required")
        if self.dry_run:
            return {"dry_run": True, "operation": "get_order_detail", "order_id": order_id}
        raise NotImplementedError(
            "implement current signed Order API request after sandbox verification"
        )

    def mark_package_shipped(
        self, order_id: str, tracking_number: str, provider_id: str
    ) -> dict[str, Any]:
        if not all((order_id, tracking_number, provider_id)):
            raise ValueError("order_id, tracking_number, and provider_id are required")
        if self.dry_run:
            return {"dry_run": True, "operation": "mark_package_shipped", "order_id": order_id}
        raise NotImplementedError(
            "implement current signed Fulfillment API request after sandbox verification"
        )
