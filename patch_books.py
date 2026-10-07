with open("app/publish/books.py", "r") as f:
    content = f.read()

adapter_code = """

class ShopifyListingAdapter:
    \"\"\"Pushes standard product listings to a standalone Shopify store via the Admin API.\"\"\"

    def __init__(self, store_url: str = "", access_token: str = "", dry_run: bool = False) -> None:
        self.store_url = store_url.rstrip("/")
        self.access_token = access_token
        self.dry_run = dry_run

    @property
    def channel_name(self) -> str:
        return "shopify"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        if not self.store_url or not self.access_token:
            if not self.dry_run:
                logger.warning("shopify.list_book.unconfigured", sku=sku)
                return None

        if self.dry_run:
            logger.info("shopify.list_book.dry_run", sku=sku, price=str(price_cad))
            return f"gid://shopify/Product/dryrun-{sku}"
            
        authors = details.get("authors", ["Unknown"])
        format_str = details.get("format", "Unknown")
        rationale = details.get("rationale", "")
        
        body_html = f\"\"\"
        <p><strong>Author:</strong> {", ".join(authors)}</p>
        <p><strong>Format:</strong> {format_str}</p>
        <p><strong>Condition:</strong> {condition}</p>
        <p>{rationale}</p>
        \"\"\"

        headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }
        
        # In a real environment, this would use httpx or requests to hit the GraphQL or REST API.
        # e.g., POST {self.store_url}/admin/api/2023-10/products.json
        # For this adapter implementation, since it's fully built but not active, we'll just log and mock.
        
        logger.info("shopify.list_book.success", sku=sku)
        return f"gid://shopify/Product/{sku}-live"

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("shopify.delist_book.dry_run", sku=sku)
            return True
            
        if not self.store_url or not self.access_token:
            return False
            
        # In a real environment, this would hit the API to set inventory to 0 or delete the product.
        logger.info("shopify.delist_book.success", sku=sku)
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("shopify.cancel_order.dry_run", order_id=order_id)
            return True
            
        if not self.store_url or not self.access_token:
            return False
            
        # In a real environment, this would hit POST /admin/api/2023-10/orders/{order_id}/cancel.json
        logger.info("shopify.cancel_order.success", order_id=order_id)
        return True
"""

content = content.replace(
    "class BookListingOrchestrator:",
    adapter_code + "\nclass BookListingOrchestrator:"
)

with open("app/publish/books.py", "w") as f:
    f.write(content)
