from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from urllib.request import Request, urlopen

from app.amazon.sp_api import AmazonSPAPIClient, LwaTokenProvider
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
        from app.api.abebooks_service import AbeBooksService

        return AbeBooksService.delist_book(
            sku=sku,
            api_key=self.api_key,
            client_id=self.client_id,
            dry_run=self.dry_run,
        )

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("abebooks.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        logger.info("abebooks.cancel_order.success", order_id=order_id, reason=reason)
        return True


@dataclass
class AmazonSPAPIAdapter(BookListingAdapter):
    """Amazon Selling Partner API (SP-API) Adapter for Book Listings."""

    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    endpoint: str = "https://sellingpartnerapi-na.amazon.com"
    merchant_id: str = ""
    marketplace_id: str = "A2EUQ1WTGCTBG2"
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    dry_run: bool = True

    @property
    def channel_name(self) -> str:
        return "amazon_spapi"

    def _get_access_token(self) -> str:
        if not hasattr(self, "_token_provider"):
            self._token_provider = LwaTokenProvider(
                self.client_id,
                self.client_secret,
                self.refresh_token,
                retries=self.retries,
            )
        return self._token_provider.get_access_token()

    def _client(self) -> AmazonSPAPIClient:
        if not hasattr(self, "_sp_api_client"):
            provider = LwaTokenProvider(
                self.client_id,
                self.client_secret,
                self.refresh_token,
                retries=self.retries,
            )
            self._sp_api_client = AmazonSPAPIClient(provider, endpoint=self.endpoint)
        return self._sp_api_client

    def get_offer_definition(self) -> dict[str, Any]:
        """Fetch current Amazon offer requirements before preparing a live listing."""
        return self._client().get_offer_definition(self.marketplace_id)

    def list_book(
        self,
        sku: str,
        title: str,
        condition: str,
        price_cad: Decimal,
        details: dict[str, Any],
    ) -> str | None:
        if not details.get("isbn") and not details.get("asin"):
            logger.warning("amazon_spapi.list_book.missing_catalog_identifier", sku=sku)
            return None

        if self.dry_run:
            logger.info("amazon_spapi.list_book.dry_run", sku=sku, price=str(price_cad))
            return f"amz-dryrun-{sku}"

        try:
            # Amazon's offer-only schema changes by marketplace. Requiring
            # caller-prepared attributes prevents silently submitting stale,
            # hard-coded catalog fields from this adapter.
            attributes = details.get("amazon_listing_attributes")
            if not self.merchant_id or not isinstance(attributes, dict):
                logger.warning("amazon_sp_api.listing_not_prepared", sku=sku)
                return None
            result = self._client().submit_listing(self.merchant_id, sku, self.marketplace_id, attributes)
            if result.get("status") != "ACCEPTED":
                logger.warning(
                    "amazon_sp_api.listing_rejected",
                    sku=sku,
                    status=result.get("status"),
                    issues=result.get("issues", []),
                )
                return None
            return str(result.get("submissionId") or sku)
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

        if not self.merchant_id:
            logger.error("amazon_sp_api.delist.missing_seller_id", sku=sku)
            return False
        try:
            result = self._client().set_offer_quantity(self.merchant_id, sku, self.marketplace_id, 0)
            return result.get("status") == "ACCEPTED"
        except Exception as exc:
            logger.error("amazon_sp_api.delist.failed", sku=sku, error=str(exc))
            return False

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("amazon_spapi.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        logger.error("amazon_sp_api.cancel.unsupported", order_id=order_id)
        return False


@dataclass
class BiblioListingAdapter(BookListingAdapter):
    """Biblio Inventory API Adapter."""

    api_key: str = "mock_key"
    dealer_id: str = ""
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
        if self.dry_run or not self.api_key or "mock" in self.api_key.lower():
            logger.info("biblio.list_book.dry_run", sku=sku)
            return f"biblio-dryrun-{sku}"
        return f"biblio-{sku}"

    def delist_book(self, sku: str) -> bool:
        if self.dry_run or not self.api_key or "mock" in self.api_key.lower():
            logger.info("biblio.delist_book.dry_run", sku=sku)
            return True
        from app.api.biblio_service import BiblioService

        return BiblioService.delist_book(sku, api_key=self.api_key, dealer_id=self.dealer_id, dry_run=False)

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run or not self.api_key or "mock" in self.api_key.lower():
            logger.info("biblio.cancel_order.dry_run", order_id=order_id, reason=reason)
            return True
        from app.api.biblio_service import BiblioService

        return BiblioService.cancel_order(
            order_id, reason=reason, api_key=self.api_key, dealer_id=self.dealer_id, dry_run=False
        )


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
    """eBay Inventory API Adapter. Drafts listings on eBay so condition photos and attributes can be attached."""

    api_token: str = "mock_token"
    marketplace_id: str = "EBAY_CA"
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
        from app.api.ebay_service import EbayService

        authors = details.get("authors") or ([details["author"]] if "author" in details else ["Unknown"])
        format_str = details.get("format", "hardcover")
        section = details.get("section", "General")
        shelf = details.get("shelf", "")
        provenance = details.get("provenance", "")
        image_urls = details.get("image_urls")

        res = EbayService.push_inventory_item_raw(
            sku=sku,
            title=title,
            authors=authors,
            format_str=format_str,
            condition=condition,
            price_cad=price_cad,
            section=section,
            shelf=shelf,
            provenance=provenance,
            access_token=self.api_token,
            marketplace_id=self.marketplace_id,
            image_urls=image_urls,
            dry_run=self.dry_run,
        )
        return res.get("listing_draft_id")

    def delist_book(self, sku: str) -> bool:
        from app.api.ebay_service import EbayService

        return EbayService.delist_item(
            sku=sku,
            access_token=self.api_token,
            dry_run=self.dry_run,
        )

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        from app.api.ebay_service import EbayService

        return EbayService.cancel_order(
            order_id=order_id,
            reason=reason,
            access_token=self.api_token,
            dry_run=self.dry_run,
        )


@dataclass
class ShopifyListingAdapter:
    """Pushes standard product listings to a standalone Shopify store via the Admin API."""

    def __init__(self, store_url: str = "", access_token: str = "", dry_run: bool = True) -> None:
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
        if self.dry_run:
            logger.info("shopify.list_book.dry_run", sku=sku, price=str(price_cad))
            return f"gid://shopify/Product/dryrun-{sku}"

        logger.error("shopify.list_book.unsupported_live_operation", sku=sku)
        return None

    def delist_book(self, sku: str) -> bool:
        if self.dry_run:
            logger.info("shopify.delist_book.dry_run", sku=sku)
            return True

        logger.error("shopify.delist_book.unsupported_live_operation", sku=sku)
        return False

    def cancel_order(self, order_id: str, reason: str = "OUT_OF_STOCK") -> bool:
        if self.dry_run:
            logger.info("shopify.cancel_order.dry_run", order_id=order_id)
            return True

        logger.error("shopify.cancel_order.unsupported_live_operation", order_id=order_id)
        return False


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
        ext = payload.get("extraction", {})
        dis = payload.get("disambiguation", {})
        cand = dis.get("best_candidate", {})

        title = cand.get("title") or ext.get("title", "Unknown Title")
        raw_authors = cand.get("authors") or ext.get("authors") or ["Unknown"]
        authors = [raw_authors] if isinstance(raw_authors, str) else list(raw_authors)
        condition = val.get("condition_grade", "Good")
        fmt = ext.get("format", "unknown")
        in_store_price = Decimal(str(val.get("in_store_price_cad", "0.00")))
        price_cad = Decimal(str(val.get("online_price_cad", "0.00")))
        sku = payload.get("sku") or f"BK-{uuid.uuid4().hex[:8].upper()}"

        details = {
            "sku": sku,
            "title": title,
            "authors": authors,
            "author": authors[0] if authors else "Unknown",
            "format": fmt,
            "condition": condition,
            "isbn": cand.get("primary_isbn") or dis.get("detected_isbn"),
            "rationale": val.get("pricing_rationale", ""),
            "in_store_price_cad": in_store_price if in_store_price > 0 else price_cad,
            "online_price_cad": price_cad,
        }

        if "spine_image_base64" in ext:
            details["spine_image_base64"] = ext["spine_image_base64"]

        if not strategy or strategy == "bargain_bin_cart":
            return {
                "sku": sku,
                "status": "skipped",
                "message": "Book routed to physical floor only.",
                "marketplace_results": {
                    ad.channel_name: {"status": "skipped", "reason": "Physical floor only"} for ad in self.adapters
                },
                "details": details,
            }

        if price_cad <= 0 and in_store_price <= 0:
            return {
                "sku": sku,
                "status": "failed",
                "error": "Invalid price (both online and in-store are zero).",
                "details": details,
            }

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

        return {
            "sku": sku,
            "status": "completed",
            "marketplace_results": results,
            "details": {
                **details,
                "sku": sku,
                "title": title,
                "authors": authors,
                "condition": condition,
                "in_store_price_cad": val.get("in_store_price_cad", price_cad),
                "online_price_cad": price_cad,
            },
        }
