from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.db.inventory import InventoryItem
from app.observability.logging import get_logger

logger = get_logger("square_service")

SQUARE_API_BASE = "https://connect.squareup.com/v2"


class SquareService:
    """Manages Square POS Catalog syncing and in-store payment checkout links."""

    @classmethod
    def push_catalog_item(
        cls,
        item: InventoryItem,
        access_token: str = "",
        location_id: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return cls.push_catalog_item_raw(
            sku=item.sku,
            title=item.title,
            authors=item.authors,
            format_str=item.format,
            condition=item.condition,
            price_cad=item.in_store_price_cad,
            section=item.section,
            shelf=item.shelf,
            access_token=access_token,
            location_id=location_id,
            dry_run=dry_run,
        )

    @classmethod
    def push_catalog_item_raw(
        cls,
        sku: str,
        title: str,
        authors: list[str],
        format_str: str,
        condition: str,
        price_cad: Decimal,
        section: str = "General",
        shelf: str = "",
        access_token: str = "",
        location_id: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        price_cents = int(price_cad * 100)

        if dry_run or not access_token or access_token.startswith("sq0atp-mock"):
            logger.info("square.catalog.push.dry_run", sku=sku, title=title, price_cents=price_cents)
            return {
                "status": "synced",
                "mode": "dry-run",
                "catalog_object_id": f"sq-cat-{sku}",
                "sku": sku,
                "title": title,
                "price_cad": float(price_cad),
            }

        url = f"{SQUARE_API_BASE}/catalog/object"
        loc_desc = f" | Location: {section} · {shelf}" if shelf else f" | Location: {section}"
        desc = f"Authors: {', '.join(authors)} | Format: {format_str} | Grade: {condition}{loc_desc}"

        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "object": {
                "type": "ITEM",
                "id": f"#item-{sku}",
                "item_data": {
                    "name": title,
                    "description": desc,
                    "variations": [
                        {
                            "type": "ITEM_VARIATION",
                            "id": f"#var-{sku}",
                            "item_variation_data": {
                                "name": "Regular Copy",
                                "sku": sku,
                                "pricing_type": "FIXED_PRICING",
                                "price_money": {
                                    "amount": price_cents,
                                    "currency": "CAD",
                                },
                            },
                        }
                    ],
                },
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Square-Version": "2024-01-18",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=15.0) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                cat_obj = res_body.get("catalog_object", {})
                cat_id = cat_obj.get("id", f"sq-cat-{sku}")
                logger.info("square.catalog.push.success", sku=sku, catalog_object_id=cat_id)
                return {
                    "status": "synced",
                    "mode": "live",
                    "catalog_object_id": cat_id,
                    "sku": sku,
                    "title": title,
                    "price_cad": float(price_cad),
                }
        except HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            logger.error("square.catalog.push.http_error", code=exc.code, error=err_text)
            return {
                "status": "error",
                "error": f"Square API error ({exc.code}): {err_text}",
                "sku": sku,
            }
        except URLError as exc:
            logger.error("square.catalog.push.net_error", error=str(exc.reason))
            return {
                "status": "error",
                "error": f"Network error: {exc.reason}",
                "sku": sku,
            }

    @classmethod
    def delist_catalog_item_raw(
        cls,
        sku: str,
        access_token: str = "",
        dry_run: bool = True,
    ) -> bool:
        if dry_run or not access_token or access_token.startswith("sq0atp-mock"):
            logger.info("square.catalog.delist.dry_run", sku=sku)
            return True

        url = f"{SQUARE_API_BASE}/catalog/search"
        search_payload = {
            "query": {
                "exact_query": {
                    "attribute_name": "sku",
                    "attribute_value": sku,
                }
            }
        }
        data = json.dumps(search_payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Square-Version": "2024-01-18",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=15.0) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                objects = res_body.get("objects") or []
                if not objects:
                    logger.info("square.catalog.delist.not_found", sku=sku)
                    return True

                object_ids = set()
                for obj in objects:
                    if obj.get("id"):
                        object_ids.add(obj["id"])
                    item_data = obj.get("item_variation_data")
                    if item_data and item_data.get("item_id"):
                        object_ids.add(item_data["item_id"])

                del_url = f"{SQUARE_API_BASE}/catalog/batch-delete"
                del_payload = {"object_ids": list(object_ids)}
                del_req = Request(
                    del_url,
                    data=json.dumps(del_payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Square-Version": "2024-01-18",
                    },
                    method="POST",
                )
                with urlopen(del_req, timeout=15.0) as del_resp:
                    del_data = json.loads(del_resp.read().decode("utf-8"))
                    deleted = del_data.get("deleted_object_ids") or []
                    logger.info("square.catalog.delist.success", sku=sku, deleted_ids=deleted)
                    return True
        except HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            logger.error("square.catalog.delist.http_error", sku=sku, code=exc.code, error=err_text)
            return False
        except URLError as exc:
            logger.error("square.catalog.delist.net_error", sku=sku, error=str(exc.reason))
            return False
        except Exception as exc:
            logger.error("square.catalog.delist.failed", sku=sku, error=str(exc))
            return False

    @classmethod
    def create_checkout_link(
        cls,
        item: InventoryItem,
        access_token: str = "",
        location_id: str = "",
        redirect_url: str = "https://ops-core-khaki.vercel.app/dashboard",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        price_cents = int(item.in_store_price_cad * 100)

        if dry_run or not access_token or access_token.startswith("sq0atp-mock"):
            mock_id = f"sq-ord-{uuid.uuid4().hex[:8]}"
            mock_url = f"https://square.link/u/mock-{item.sku.lower()}"
            logger.info("square.checkout.dry_run", sku=item.sku, price_cents=price_cents)
            return {
                "status": "ready",
                "mode": "dry-run",
                "payment_url": mock_url,
                "order_id": mock_id,
                "sku": item.sku,
                "title": item.title,
                "amount_cad": float(item.in_store_price_cad),
                "currency": "CAD",
                "qr_data": mock_url,
            }

        url = f"{SQUARE_API_BASE}/online-checkout/payment-links"
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "quick_pay": {
                "name": item.title,
                "price_money": {
                    "amount": price_cents,
                    "currency": "CAD",
                },
                "location_id": location_id or "MAIN_LOCATION",
            },
            "order": {
                "location_id": location_id or "MAIN_LOCATION",
                "line_items": [
                    {
                        "name": item.title,
                        "quantity": "1",
                        "note": item.sku,
                        "base_price_money": {
                            "amount": price_cents,
                            "currency": "CAD",
                        },
                    }
                ],
            },
            "checkout_options": {
                "redirect_url": redirect_url,
                "ask_for_shipping_address": False,
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Square-Version": "2024-01-18",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=15.0) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                link_obj = res_body.get("payment_link", {})
                pay_url = link_obj.get("url", "")
                order_id = link_obj.get("order_id", "")
                logger.info("square.checkout.success", sku=item.sku, order_id=order_id)
                return {
                    "status": "ready",
                    "mode": "live",
                    "payment_url": pay_url,
                    "order_id": order_id,
                    "sku": item.sku,
                    "title": item.title,
                    "amount_cad": float(item.in_store_price_cad),
                    "currency": "CAD",
                    "qr_data": pay_url,
                }
        except HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            logger.error("square.checkout.http_error", code=exc.code, error=err_text)
            return {
                "status": "error",
                "error": f"Square API error ({exc.code}): {err_text}",
                "sku": item.sku,
            }
        except URLError as exc:
            logger.error("square.checkout.net_error", error=str(exc.reason))
            return {
                "status": "error",
                "error": f"Network error: {exc.reason}",
                "sku": item.sku,
            }
