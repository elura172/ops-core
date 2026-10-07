from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger
from app.publish.books import BookListingAdapter

logger = get_logger("inventory_sync")


@dataclass
class MultiChannelInventorySync:
    """Listens to cross-channel book sales and delists the SKU on remaining marketplaces."""

    adapters: tuple[BookListingAdapter, ...]

    def process_sale(self, sku: str, source_channel: str) -> dict[str, Any]:
        """Triggered when a book sells. Delists from all other channels."""
        logger.info("inventory.sale_detected", sku=sku, source=source_channel)
        results = {}

        for adapter in self.adapters:
            # Skip the channel that reported the sale, it is already handled.
            if adapter.channel_name == source_channel:
                continue

            success = adapter.delist_book(sku)
            results[adapter.channel_name] = "delisted" if success else "failed"

            if not success:
                logger.error(
                    "inventory.sync_failure",
                    sku=sku,
                    target_channel=adapter.channel_name,
                )

        return {"sku": sku, "sync_results": results}
