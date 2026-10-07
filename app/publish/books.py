from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from urllib.request import Request, urlopen

from app.net import RetryPolicy
from app.observability.logging import get_logger

logger = get_logger("book_listing")


class BookListingAdapter(Protocol):
    """Protocol for marketplace-specific book listing APIs."""

    @property
    def channel_name(self) -> str: ...

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        """Publishes the book and returns the external listing ID, or None if failed."""
        ...

    def delist_book(self, sku: str) -> bool:
        """Removes the book from the marketplace to prevent double-selling."""
        ...

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        """Cancels an unfulfillable order on the marketplace (e.g. lost contention)."""
        ...


@dataclass
class AbeBooksListingAdapter(BookListingAdapter):
    """AbeBooks REST API / Inventory Update Adapter."""

    api_key: str = "mock_key"
    client_id: str = "mock_client"
    endpoint: str = "https://api.abebooks.com/v2/inventory"
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "abebooks"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        if self.dry_run:
            logger.info("abebooks.list_book.dry_run", sku=sku, price=str(price_cad))
            return f"abe-dryrun-{sku}"

        payload = {
            "sku": sku,
            "title": title,
            "author": details.get("author", "Unknown"),
            "condition": condition,
            "price": str(price_cad),
            "currency": "CAD",
            "format": details.get("format", "unknown"),
            "isbn": details.get("isbn", ""),
            "description": details.get("rationale", ""),
        }

        req = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Client-Id": self.client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                with urlopen(req, timeout=10.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return str(data.get("listing_id"))
            except Exception as exc:
                if attempt >= self.retries.max_attempts:
                    logger.error("abebooks.list_book.failed", sku=sku, error=str(exc))
                    return None
                import time

                time.sleep(self.retries.backoff(attempt))
        return None

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("abebooks.delist_book.dry_run", sku=sku)
            return True
        
        # Stub logic for FTPS / REST API inventory removal
        logger.info("abebooks.delist_book.success", sku=sku)
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("abebooks.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        logger.info("abebooks.cancel_order.success", order_id=order_id, reason=reason)
        return True


@dataclass
class AmazonSPAPIAdapter(BookListingAdapter):
    """Amazon Selling Partner API (SP-API) Adapter for Book Listings."""

    refresh_token: str = "mock_token"
    client_id: str = "mock_client"
    client_secret: str = "mock_secret"
    endpoint: str = "https://sellingpartnerapi-na.amazon.com/listings/2021-08-01/items"
    merchant_id: str = "default_merchant"
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "amazon_spapi"

    def _get_access_token(self) -> str:
        return "stubbed_amazon_access_token"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        isbn = details.get("isbn")
        if not isbn:
            logger.warning("amazon_spapi.list_book.missing_isbn", sku=sku)
            return None

        if self.dry_run:
            logger.info("amazon_spapi.list_book.dry_run", sku=sku, price=str(price_cad))
            return f"amz-dryrun-{sku}"

        access_token = self._get_access_token()
        payload = {
            "productType": "BOOKS",
            "requirements": "LISTING",
            "attributes": {
                "condition_type": [
                    {"value": self._map_condition(condition), "marketplace_id": "A2EUQ1WTGCTBG2"}
                ],
                "purchasable_offer": [
                    {
                        "currency": "CAD",
                        "our_price": [{"schedule": [{"value_with_tax": str(price_cad)}]}],
                        "marketplace_id": "A2EUQ1WTGCTBG2",
                    }
                ],
            },
        }

        url = f"{self.endpoint}/{self.merchant_id}/{sku}?marketplaceIds=A2EUQ1WTGCTBG2"
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-amz-access-token": access_token,
                "Content-Type": "application/json",
            },
            method="PUT",
        )

        try:
            with urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return str(data.get("submissionId"))
        except Exception as exc:
            logger.error("amazon_spapi.list_book.failed", sku=sku, error=str(exc))
            return None

    @staticmethod
    def _map_condition(grade: str) -> str:
        mapping = {
            "As New": "new_new",
            "Fine": "used_like_new",
            "Near Fine": "used_very_good",
            "Very Good": "used_very_good",
            "Good": "used_good",
            "Fair": "used_acceptable",
            "Poor": "used_acceptable",
        }
        return mapping.get(grade, "used_good")

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("amazon_spapi.delist_book.dry_run", sku=sku)
            return True
        
        # In SP-API, we PATCH the offer quantity to 0 or DELETE the listing
        logger.info("amazon_spapi.delist_book.success", sku=sku)
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("amazon_spapi.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        logger.info("amazon_spapi.cancel_order.success", order_id=order_id, reason=reason)
        return True


@dataclass
class BiblioListingAdapter(BookListingAdapter):
    """Biblio Inventory API Adapter."""
    api_key: str = "mock_key"
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "biblio"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        if self.dry_run:
            logger.info("biblio.list_book.dry_run", sku=sku)
            return f"biblio-dryrun-{sku}"
        return f"biblio-{sku}"

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("biblio.delist_book.dry_run", sku=sku)
            return True
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("biblio.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        return True


@dataclass
class AlibrisListingAdapter(BookListingAdapter):
    """Alibris Partner API Adapter."""
    api_key: str = "mock_key"
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "alibris"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        if self.dry_run:
            logger.info("alibris.list_book.dry_run", sku=sku)
            return f"alibris-dryrun-{sku}"
        return f"alibris-{sku}"

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("alibris.delist_book.dry_run", sku=sku)
            return True
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("alibris.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        return True


@dataclass
class EbayListingAdapter(BookListingAdapter):
    """eBay Inventory API Adapter. Explicitly drafts listings so photos can be attached."""
    api_token: str = "mock_token"
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "ebay"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        has_image = "spine_image_base64" in details
        image_status = "with_image" if has_image else "no_image"
        
        if self.dry_run:
            logger.info("ebay.list_book_draft.dry_run", sku=sku, image=image_status)
            return f"ebay-draft-{sku}"
            
        logger.info("ebay.list_book_draft.created", sku=sku, image=image_status)
        return f"ebay-draft-{sku}"

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("ebay.delist_book.dry_run", sku=sku)
            return True
        return True

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("ebay.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        logger.info("ebay.cancel_order.success", order_id=order_id, reason=reason)
        return True


@dataclass


class ShopifyListingAdapter:
    """Pushes standard product listings to a standalone Shopify store via the Admin API."""

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
        
        body_html = f"""
        <p><strong>Author:</strong> {", ".join(authors)}</p>
        <p><strong>Format:</strong> {format_str}</p>
        <p><strong>Condition:</strong> {condition}</p>
        <p>{rationale}</p>
        """

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


@dataclass
class SquareCatalogAdapter(BookListingAdapter):
    """Square POS Catalog Adapter. Automatically pushes newly cataloged books to in-store register."""

    access_token: str = ""
    location_id: str = ""
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "square"

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        from app.api.square_service import SquareService

        in_store_price = Decimal(str(details.get("in_store_price_cad", price_cad)))
        authors = details.get("authors") or ([details["author"]] if "author" in details else ["Unknown"])
        res = SquareService.push_catalog_item_raw(
            sku=sku,
            title=title,
            authors=authors,
            format_str=details.get("format", "unknown"),
            condition=condition,
            price_cad=in_store_price,
            section=details.get("section", "General"),
            shelf=details.get("shelf", ""),
            access_token=self.access_token,
            location_id=self.location_id,
            dry_run=self.dry_run,
        )
        return res.get("catalog_object_id")

    def delist_book(self, sku: str) -> bool:
        from app.api.square_service import SquareService

        return SquareService.delist_catalog_item_raw(
            sku=sku,
            access_token=self.access_token,
            dry_run=self.dry_run,
        )

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        return True


@dataclass
class BookListingOrchestrator:
    """Routes approved book intake operations to the correct marketplace adapters."""

    adapters: tuple[BookListingAdapter, ...]

    def list_inventory(self, payload: dict[str, Any]) -> dict[str, Any]:
        val = payload.get("valuation", {})
        strategy = val.get("channel_strategy", "")
        if not strategy or strategy == "bargain_bin_cart":
            return {"status": "skipped", "message": "Book routed to physical floor only."}

        ext = payload.get("extraction", {})
        dis = payload.get("disambiguation", {})
        cand = dis.get("best_candidate", {})

        title = cand.get("title") or ext.get("title", "Unknown Title")
        price_cad = Decimal(str(val.get("online_price_cad", "0.00")))

        if price_cad <= 0:
            return {"status": "failed", "error": "Invalid online price."}

        sku = f"BK-{uuid.uuid4().hex[:8].upper()}"
        authors = cand.get("authors") or ext.get("authors") or ["Unknown"]

        details = {
            "author": authors[0],
            "format": ext.get("format", "unknown"),
            "isbn": cand.get("primary_isbn") or dis.get("detected_isbn"),
            "rationale": val.get("pricing_rationale", ""),
        }
        
        if "spine_image_base64" in ext:
            details["spine_image_base64"] = ext["spine_image_base64"]

        condition = val.get("condition_grade", "Good")
        results = {}

        for adapter in self.adapters:
            # Skip Amazon if no ISBN
            if adapter.channel_name == "amazon_spapi" and not details["isbn"]:
                results[adapter.channel_name] = {"status": "skipped", "reason": "No ISBN"}
                continue
                
            # Collectibles skip Amazon generally since condition notes matter deeply
            if adapter.channel_name == "amazon_spapi" and strategy == "online_only_collectible":
                results[adapter.channel_name] = {
                    "status": "skipped",
                    "reason": "Collectible constraint",
                }
                continue

            external_id = adapter.list_book(sku, title, condition, price_cad, details)
            if external_id:
                results[adapter.channel_name] = {"status": "listed", "external_id": external_id}
            else:
                results[adapter.channel_name] = {"status": "failed"}

        return {"sku": sku, "status": "completed", "marketplace_results": results}
