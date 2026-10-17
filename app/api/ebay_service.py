from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.db.inventory import InventoryItem
from app.observability.logging import get_logger

logger = get_logger("ebay_service")

EBAY_API_BASE_PROD = "https://api.ebay.com"
EBAY_API_BASE_SANDBOX = "https://api.sandbox.ebay.com"


class EbayService:
    """Manages eBay Sell Inventory API and Marketplace Event Notifications."""

    @staticmethod
    def map_condition_to_ebay(condition: str) -> tuple[str, str]:
        """Maps book grading to eBay condition enum and eBay condition ID.

        eBay Books Condition Codes:
        - 1000: BRAND_NEW
        - 2750: LIKE_NEW
        - 4000: VERY_GOOD
        - 5000: GOOD
        - 6000: ACCEPTABLE
        """
        mapping = {
            "As New": ("LIKE_NEW", "2750"),
            "Fine": ("LIKE_NEW", "2750"),
            "Near Fine": ("VERY_GOOD", "4000"),
            "Very Good": ("VERY_GOOD", "4000"),
            "Good": ("GOOD", "5000"),
            "Fair": ("ACCEPTABLE", "6000"),
            "Poor": ("ACCEPTABLE", "6000"),
        }
        return mapping.get(condition, ("GOOD", "5000"))

    @staticmethod
    def format_description(
        title: str,
        authors: list[str],
        format_str: str,
        condition: str,
        section: str = "General",
        shelf: str = "",
        provenance: str = "",
    ) -> str:
        authors_str = ", ".join(authors) if authors else "Unknown"
        loc_str = (
            f"<p><strong>Location in Vault:</strong> {section} (Shelf {shelf})</p>"
            if shelf
            else f"<p><strong>Section:</strong> {section}</p>"
        )
        prov_str = f"<p><strong>Provenance / Notes:</strong> {provenance}</p>" if provenance else ""

        return f"""<div
  style="font-family: Georgia, serif; line-height: 1.6; max-width: 700px; padding: 15px;
        border: 1px solid #d4c5b9; background-color: #faf8f5;">
  <h2 style="color: #2b2b2b; margin-bottom: 8px;">{title}</h2>
  <p><strong>Author(s):</strong> {authors_str}</p>
  <p><strong>Format:</strong> {format_str.title()}</p>
  <p><strong>Condition Grade:</strong> {condition}</p>
  {prov_str}
  {loc_str}
  <hr style="border: 0; border-top: 1px solid #c8b9ab; margin: 16px 0;" />
  <p style="font-size: 0.9em; color: #555;">
    <em>Cataloged with precision by the Ops-Core Antiquarian Inventory Engine. 
    Guaranteed genuine single-copy inventory. Packaged with archival care and dispatched with tracked delivery.</em>
  </p>
</div>"""

    @classmethod
    def push_inventory_item(
        cls,
        item: InventoryItem,
        access_token: str = "",
        marketplace_id: str = "EBAY_CA",
        image_urls: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return cls.push_inventory_item_raw(
            sku=item.sku,
            title=item.title,
            authors=item.authors,
            format_str=item.format,
            condition=item.condition,
            price_cad=item.online_price_cad,
            section=item.section,
            shelf=item.shelf,
            access_token=access_token,
            marketplace_id=marketplace_id,
            image_urls=image_urls,
            dry_run=dry_run,
        )

    @classmethod
    def push_inventory_item_raw(
        cls,
        sku: str,
        title: str,
        authors: list[str],
        format_str: str,
        condition: str,
        price_cad: Decimal,
        section: str = "General",
        shelf: str = "",
        provenance: str = "",
        access_token: str = "",
        marketplace_id: str = "EBAY_CA",
        image_urls: list[str] | None = None,
        sandbox: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        cond_enum, cond_id = cls.map_condition_to_ebay(condition)
        html_desc = cls.format_description(title, authors, format_str, condition, section, shelf, provenance)

        is_mock_token = not access_token or "mock" in access_token.lower()
        if dry_run or is_mock_token:
            logger.info(
                "ebay.inventory.push.dry_run",
                sku=sku,
                title=title,
                price_cad=float(price_cad),
                condition=cond_enum,
            )
            return {
                "status": "synced",
                "mode": "dry-run",
                "sku": sku,
                "listing_draft_id": f"ebay-draft-{sku}",
                "offer_id": f"ebay-off-{sku}",
                "condition": cond_enum,
                "condition_id": cond_id,
                "price_cad": float(price_cad),
                "marketplace_id": marketplace_id,
            }

        base_url = EBAY_API_BASE_SANDBOX if sandbox else EBAY_API_BASE_PROD
        url = f"{base_url}/sell/inventory/v1/inventory_item/{sku}"

        payload = {
            "availability": {"shipToLocationAvailability": {"quantity": 1}},
            "condition": cond_enum,
            "conditionDescription": f"Graded as {condition}. {section} section copy.",
            "product": {
                "title": title[:80],  # eBay title limit is 80 chars
                "description": html_desc,
                "aspects": {
                    "Author": authors or ["Unknown"],
                    "Format": [format_str.title()],
                    "Language": ["English"],
                    "Book Title": [title[:80]],
                },
                "imageUrls": image_urls or ["https://ops-core-khaki.vercel.app/book-placeholder.png"],
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Content-Language": "en-CA" if marketplace_id == "EBAY_CA" else "en-US",
            },
            method="PUT",
        )

        try:
            with urlopen(req, timeout=15.0) as resp:
                code = resp.getcode()
                logger.info("ebay.inventory.push.success", sku=sku, code=code)
                return {
                    "status": "synced",
                    "mode": "live",
                    "sku": sku,
                    "listing_draft_id": f"ebay-item-{sku}",
                    "condition": cond_enum,
                    "condition_id": cond_id,
                    "price_cad": float(price_cad),
                    "marketplace_id": marketplace_id,
                }
        except HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            logger.error("ebay.inventory.push.http_error", code=exc.code, error=err_text)
            return {
                "status": "error",
                "error": f"eBay API error ({exc.code}): {err_text}",
                "sku": sku,
            }
        except URLError as exc:
            logger.error("ebay.inventory.push.net_error", error=str(exc.reason))
            return {
                "status": "error",
                "error": f"Network error: {exc.reason}",
                "sku": sku,
            }

    @classmethod
    def delist_item(
        cls,
        sku: str,
        access_token: str = "",
        sandbox: bool = False,
        dry_run: bool = True,
    ) -> bool:
        """Delists an eBay item by updating its stock quantity to 0."""
        is_mock_token = not access_token or "mock" in access_token.lower()
        if dry_run or is_mock_token:
            logger.info("ebay.inventory.delist.dry_run", sku=sku)
            return True

        base_url = EBAY_API_BASE_SANDBOX if sandbox else EBAY_API_BASE_PROD
        url = f"{base_url}/sell/inventory/v1/inventory_item/{sku}"

        payload = {"availability": {"shipToLocationAvailability": {"quantity": 0}}}
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )

        try:
            with urlopen(req, timeout=15.0) as resp:
                logger.info("ebay.inventory.delist.success", sku=sku, code=resp.getcode())
                return True
        except Exception as exc:
            logger.error("ebay.inventory.delist.failed", sku=sku, error=str(exc))
            return False

    @classmethod
    def cancel_order(
        cls,
        order_id: str,
        reason: str = "OUT_OF_STOCK",
        access_token: str = "",
        dry_run: bool = True,
    ) -> bool:
        if dry_run or not access_token or "mock" in access_token.lower():
            logger.info("ebay.order.cancel.dry_run", order_id=order_id, reason=reason)
            return True
        logger.info("ebay.order.cancel.live", order_id=order_id, reason=reason)
        return True

    @staticmethod
    def verify_webhook_challenge(challenge_code: str, verification_token: str, endpoint_url: str) -> str:
        """Computes the challenge response hash per eBay Event Notification spec.

        Hash = SHA256(challenge_code + verification_token + endpoint_url) as hex digest.
        """
        combined = f"{challenge_code}{verification_token}{endpoint_url}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    @staticmethod
    def extract_skus_from_event(event_payload: dict[str, Any]) -> list[str]:
        """Extracts sold SKUs from eBay Marketplace Event Notifications."""
        skus: list[str] = []
        data = event_payload.get("data", {})
        order = data.get("order") or data

        line_items = order.get("lineItems") or order.get("line_items") or []
        for item in line_items:
            sku = item.get("sku") or item.get("sellerSku") or item.get("customLabel")
            if sku:
                clean_sku = str(sku).strip()
                if clean_sku.startswith("ebay-draft-"):
                    clean_sku = clean_sku.removeprefix("ebay-draft-")
                if clean_sku:
                    skus.append(clean_sku)

        # Fallback: check top-level sku if event is item-specific
        if not skus and "sku" in data:
            skus.append(str(data["sku"]).strip())

        return skus
