import re

with open("app/review/executor.py", "r") as f:
    content = f.read()

content = content.replace(
    "def __init__(\n        self,\n        # Deliberately untyped: handlers bind lazily, so a client only needs the\n        # methods for the operations it is actually asked to run.\n        client: Any,\n        audit: AuditSink | None = None,\n        *,",
    "def __init__(\n        self,\n        # Deliberately untyped: handlers bind lazily, so a client only needs the\n        # methods for the operations it is actually asked to run.\n        client: Any,\n        audit: AuditSink | None = None,\n        *,\n        inventory_store: Any = None,"
)
content = content.replace(
    "self.shippo_api_key = shippo_api_key",
    "self.shippo_api_key = shippo_api_key\n        self.inventory_store = inventory_store"
)

old_intake_book = """    def _intake_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapters = (
            AbeBooksListingAdapter(dry_run=True),
            AmazonSPAPIAdapter(dry_run=True),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True),
        )
        orchestrator = BookListingOrchestrator(adapters)
        return orchestrator.list_inventory(payload)"""

new_intake_book = """    def _intake_book(self, payload: dict[str, Any]) -> dict[str, Any]:
        adapters = (
            AbeBooksListingAdapter(dry_run=True),
            AmazonSPAPIAdapter(dry_run=True),
            BiblioListingAdapter(dry_run=True),
            AlibrisListingAdapter(dry_run=True),
            EbayListingAdapter(dry_run=True),
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
            
        return result"""

content = content.replace(old_intake_book, new_intake_book)

with open("app/review/executor.py", "w") as f:
    f.write(content)
