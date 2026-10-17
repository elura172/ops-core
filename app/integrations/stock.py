from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn
from uuid import uuid4

from app.integrations.database import Connection, transaction
from app.integrations.protocol import IntegrationError, SiteConnection, canonical


def conflict(code: str = "stock_conflict", message: str = "A copy is no longer available.") -> NoReturn:
    raise IntegrationError(409, code, message)


class StockLedger:
    """Single-copy claims, reservations, public snapshots and jobs share one DB commit."""

    def __init__(self, inventory: Any, connections: tuple[SiteConnection, ...] = ()) -> None:
        self.inventory, self.connections = inventory, connections

    def _claim(self, db: Connection, tenant: int, sku: str) -> dict[str, Any] | None:
        row = db.execute("SELECT * FROM core_stock_claims WHERE tenant_id=? AND sku=?", (tenant, sku)).fetchone()
        if row and row["expires_at"] is not None and row["expires_at"] <= db.now():
            db.execute("DELETE FROM core_stock_claims WHERE tenant_id=? AND sku=?", (tenant, sku))
            return None
        return dict(row) if row else None

    def _put_claim(
        self, db: Connection, tenant: int, sku: str, kind: str, owner: str, expires: float | None = None
    ) -> None:
        db.execute(
            "INSERT INTO core_stock_claims(tenant_id,sku,kind,owner,expires_at) "
            "VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,sku) DO UPDATE SET "
            "kind=excluded.kind,owner=excluded.owner,expires_at=excluded.expires_at",
            (tenant, sku, kind, owner, expires),
        )

    def _job(self, db: Connection, connection: SiteConnection, sku: str, channel: str, payload: dict[str, Any]) -> str:
        jid, now = str(uuid4()), db.now()
        db.execute(
            "INSERT INTO site_outbox(id,connection_id,tenant_id,sku,channel,payload,"
            "created_at,next_attempt) VALUES(?,?,?,?,?,?,?,?)",
            (
                jid,
                connection.connection_id,
                connection.tenant_id,
                sku,
                channel,
                canonical(payload),
                now,
                now,
            ),
        )
        return jid

    def _content(self, row: dict[str, Any]) -> dict[str, Any]:
        authors = row["authors"]
        result = {
            "schema_version": "1.0",
            "sku": row["sku"],
            "title": row["title"],
            "authors": json.loads(authors) if isinstance(authors, str) else authors,
            "format": row["format"],
            "condition": row["condition"],
            "publisher": row.get("publisher") or None,
            "publication_year": row.get("year") or None,
            "condition_notes": row.get("condition_notes") or None,
            "edition": row.get("collation") or None,
            "images": [],
            "tags": [row["section"]] if row.get("section") else [],
            "price": {"amount": f"{Decimal(str(row['online_price_cad'])):.2f}", "currency": "CAD"},
        }
        url = row.get("spine_image_url", "")
        if url.startswith("https://"):
            result["images"] = [{"url": url, "alt": f"Copy of {row['title']}"}]
        return result

    def prepare_publication(self, tenant: int, sku: str) -> dict[str, Any]:
        with transaction(self.inventory, tenant) as db:
            row = db.inventory(tenant, sku)
            if not row:
                raise ValueError("Book not found in this workspace")
            content = self._content(row)
            return {
                "catalogue": content,
                "catalogue_digest": hashlib.sha256(canonical(content).encode()).hexdigest(),
            }

    def _snapshot(
        self,
        db: Connection,
        connection: SiteConnection,
        sku: str,
        publication: str | None = None,
        reviewed: bool = False,
    ) -> str | None:
        existing = db.execute(
            "SELECT * FROM site_publications WHERE connection_id=? AND sku=?",
            (connection.connection_id, sku),
        ).fetchone()
        row = db.inventory(connection.tenant_id, sku)
        if not row:
            if not existing:
                raise IntegrationError(404, "book_not_found", "Book not found.")
            snapshot = json.loads(existing["snapshot"])
            snapshot.update(publication="unpublished", quantity=0)
        else:
            claim = self._claim(db, connection.tenant_id, sku)
            status = row["status"]
            availability = (
                "sold"
                if status in ("sold", "delisted") or (claim and claim["kind"] in ("sale", "blocked"))
                else "held"
                if status != "active" or claim
                else "available"
            )
            if existing and not reviewed:
                snapshot = json.loads(existing["snapshot"])
                # Content changes need another approved publish. Stock and already-reviewed
                # operator price changes can be propagated under stock protection.
            else:
                snapshot = {**self._content(row), "publication": "published"}
            snapshot.update(
                availability=availability,
                price={"amount": f"{Decimal(str(row['online_price_cad'])):.2f}", "currency": "CAD"},
            )
            if publication:
                snapshot["publication"] = publication
            snapshot["quantity"] = int(snapshot["publication"] == "published" and availability == "available")
        previous = json.loads(existing["snapshot"]) if existing else None
        if previous and all(snapshot.get(k) == v for k, v in previous.items() if k not in ("revision", "updated_at")):
            return None
        snapshot["revision"] = existing["revision"] + 1 if existing else 1
        snapshot["updated_at"] = datetime.fromtimestamp(db.now(), UTC).isoformat().replace("+00:00", "Z")
        db.execute(
            "INSERT INTO site_publications(connection_id,tenant_id,sku,revision,snapshot) "
            "VALUES(?,?,?,?,?) ON CONFLICT(connection_id,sku) DO UPDATE SET "
            "revision=excluded.revision,snapshot=excluded.snapshot",
            (
                connection.connection_id,
                connection.tenant_id,
                sku,
                snapshot["revision"],
                canonical(snapshot),
            ),
        )
        return self._job(db, connection, sku, "custom_site", snapshot)

    def _stock_jobs(self, db: Connection, tenant: int, sku: str) -> list[str]:
        jobs: list[str] = []
        for c in self.connections:
            if c.tenant_id != tenant or not c.enabled or not c.stock_protection_enabled:
                continue
            if db.execute(
                "SELECT 1 FROM site_publications WHERE connection_id=? AND sku=?",
                (c.connection_id, sku),
            ).fetchone():
                jid = self._snapshot(db, c, sku)
                if jid:
                    jobs.append(jid)
        return jobs

    def publish(
        self, connection: SiteConnection, sku: str, publication: str, expected_digest: str | None = None
    ) -> dict[str, Any]:
        if publication not in ("published", "unpublished"):
            raise ValueError("publication must be published or unpublished")
        with transaction(self.inventory, connection.tenant_id) as db:
            row = db.inventory(connection.tenant_id, sku)
            if expected_digest and row:
                actual = hashlib.sha256(canonical(self._content(row)).encode()).hexdigest()
                if actual != expected_digest:
                    conflict(
                        "catalogue_changed",
                        "Book details changed after review. Create a new publication review.",
                    )
            if row and publication == "published" and Decimal(str(row["online_price_cad"])) <= 0:
                conflict("invalid_price", "Set a positive online price before publishing.")
            jid = self._snapshot(db, connection, sku, publication, reviewed=True)
            return {
                "connection_id": connection.connection_id,
                "sku": sku,
                "queued": bool(jid),
                "job_id": jid,
            }

    def reserve(self, db: Connection, c: SiteConnection, request: Any) -> dict[str, Any]:
        items = sorted((i.model_dump() for i in request.items), key=lambda i: i["sku"])
        old = db.execute(
            "SELECT * FROM site_reservations WHERE connection_id=? AND checkout_id=?",
            (c.connection_id, request.checkout_id),
        ).fetchone()
        if old:
            if old["items"] != canonical(items):
                conflict("checkout_conflict", "Checkout ID already has different items.")
            if old["status"] != "active" or old["expires_at"] <= db.now():
                conflict(
                    "reservation_expired",
                    "Start a new checkout; this reservation is no longer active.",
                )
            return self._reservation(old)
        for line in items:
            row = db.inventory(c.tenant_id, line["sku"])
            pub = db.execute(
                "SELECT snapshot FROM site_publications WHERE connection_id=? AND sku=?",
                (c.connection_id, line["sku"]),
            ).fetchone()
            if (
                not row
                or row["status"] != "active"
                or self._claim(db, c.tenant_id, line["sku"])
                or not pub
                or json.loads(pub["snapshot"])["publication"] != "published"
            ):
                conflict()
            if Decimal(str(row["online_price_cad"])) != Decimal(line["unit_price"]["amount"]):
                conflict("price_mismatch", "The price changed. Refresh the book before checkout.")
        rid, expiry = "res_" + uuid4().hex, db.now() + 900
        for line in items:
            self._put_claim(db, c.tenant_id, line["sku"], "checkout", rid, expiry)
            self._stock_jobs(db, c.tenant_id, line["sku"])
        db.execute(
            "INSERT INTO "
            "site_reservations(id,connection_id,tenant_id,checkout_id,items,expires_at,status) "
            "VALUES(?,?,?,?,?,?,'active')",
            (rid, c.connection_id, c.tenant_id, request.checkout_id, canonical(items), expiry),
        )
        return self._reservation(
            dict(id=rid, checkout_id=request.checkout_id, items=canonical(items), expires_at=expiry)
        )

    def _reservation(self, row: Any) -> dict[str, Any]:
        return {
            "reservation_id": row["id"],
            "checkout_id": row["checkout_id"],
            "status": "active",
            "expires_at": datetime.fromtimestamp(row["expires_at"], UTC).isoformat().replace("+00:00", "Z"),
            "items": json.loads(row["items"]),
        }

    def release(self, db: Connection, c: SiteConnection, rid: str) -> dict[str, str]:
        row = db.execute(
            "SELECT * FROM site_reservations WHERE connection_id=? AND id=?", (c.connection_id, rid)
        ).fetchone()
        if not row:
            raise IntegrationError(404, "reservation_not_found", "Reservation not found.")
        if row["status"] == "consumed":
            conflict("reservation_consumed", "A paid reservation cannot be released.")
        status = "expired" if row["expires_at"] <= db.now() else "released"
        db.execute(
            "DELETE FROM core_stock_claims WHERE tenant_id=? AND kind='checkout' AND owner=?",
            (c.tenant_id, rid),
        )
        db.execute("UPDATE site_reservations SET status=? WHERE id=?", (status, rid))
        for line in json.loads(row["items"]):
            self._stock_jobs(db, c.tenant_id, line["sku"])
        return {"reservation_id": rid, "status": status}

    def order(self, db: Connection, c: SiteConnection, event: Any) -> dict[str, str]:
        items = canonical(sorted((i.model_dump() for i in event.items), key=lambda i: i["sku"]))
        existing = db.execute(
            "SELECT * FROM site_orders WHERE connection_id=? AND order_id=?",
            (c.connection_id, event.order_id),
        ).fetchone()
        if existing and (existing["items"] != items or existing["reservation_id"] != event.reservation_id):
            conflict("order_conflict", "Order details do not match the original order.")
        duplicate = db.execute(
            "SELECT * FROM site_order_events WHERE connection_id=? AND order_id=? AND event_type=?",
            (c.connection_id, event.order_id, event.event_type),
        ).fetchone()
        if duplicate:
            # Record an alias event for status polling without duplicating work.
            old = json.loads(duplicate["payload"])
            if old["items"] != json.loads(items) or old["reservation_id"] != event.reservation_id:
                conflict("order_conflict", "Order details do not match.")
        elif event.event_type == "order.paid":
            if existing:
                conflict("order_conflict", "This order is already recorded.")
            reservation = db.execute(
                "SELECT * FROM site_reservations WHERE connection_id=? AND id=?",
                (c.connection_id, event.reservation_id),
            ).fetchone()
            if not reservation or reservation["items"] != items:
                conflict("reservation_mismatch", "Reservation and order items must match.")
            if reservation["status"] != "active" or reservation["expires_at"] <= db.now():
                conflict(
                    "reservation_expired",
                    "The payment arrived after the reservation ended. Staff review is required.",
                )
            for line in json.loads(items):
                db.inventory(c.tenant_id, line["sku"])
                claim = self._claim(db, c.tenant_id, line["sku"])
                if not claim or claim["kind"] != "checkout" or claim["owner"] != event.reservation_id:
                    conflict()
            for line in json.loads(items):
                sku = line["sku"]
                self._put_claim(db, c.tenant_id, sku, "sale", event.order_id)
                db.execute(
                    "UPDATE inventory SET status='sold',updated_at=? WHERE tenant_id=? AND sku=?",
                    (datetime.now(UTC).isoformat(), c.tenant_id, sku),
                )
            db.execute("UPDATE site_reservations SET status='consumed' WHERE id=?", (event.reservation_id,))
            db.execute(
                "INSERT INTO site_orders VALUES(?,?,?,?,?)",
                (c.connection_id, event.order_id, event.reservation_id, items, "paid"),
            )
        else:
            if not existing:
                conflict("order_not_found", "Deliver the paid event before cancellation.")
            db.execute(
                "UPDATE site_orders SET status='cancelled' WHERE connection_id=? AND order_id=?",
                (c.connection_id, event.order_id),
            )
        # Repeated business events return the canonical event ID, without duplicating work.
        if duplicate:
            return {
                "event_id": duplicate["event_id"],
                "order_id": event.order_id,
                "status": "accepted",
            }
        data = event.model_dump()
        data["items"] = json.loads(items)
        db.execute(
            "INSERT INTO site_order_events VALUES(?,?,?,?,?)",
            (c.connection_id, event.event_id, event.order_id, event.event_type, canonical(data)),
        )
        jobs: list[str] = []
        if event.event_type == "order.paid":
            for line in json.loads(items):
                jobs.extend(self._stock_jobs(db, c.tenant_id, line["sku"]))
                for channel in c.channels:
                    jobs.append(self._job(db, c, line["sku"], channel, {"sku": line["sku"]}))
        for jid in jobs:
            db.execute("INSERT INTO site_event_jobs VALUES(?,?,?)", (c.connection_id, event.event_id, jid))
        return {"event_id": event.event_id, "order_id": event.order_id, "status": "accepted"}

    def event_status(self, db: Connection, c: SiteConnection, eid: str) -> dict[str, Any]:
        event = db.execute(
            "SELECT * FROM site_order_events WHERE connection_id=? AND event_id=?",
            (c.connection_id, eid),
        ).fetchone()
        if not event:
            raise IntegrationError(404, "event_not_found", "Order event not found.")
        rows = db.execute(
            "SELECT o.channel,o.state FROM site_outbox o JOIN site_event_jobs j ON "
            "o.id=j.job_id WHERE j.connection_id=? AND j.event_id=?",
            (c.connection_id, eid),
        ).fetchall()
        states = [
            {
                "channel": r["channel"],
                "status": "succeeded"
                if r["state"] == "succeeded"
                else "needs_review"
                if r["state"] == "needs_review"
                else "pending",
            }
            for r in rows
        ]
        status = (
            "needs_review"
            if any(r["status"] == "needs_review" for r in states)
            else "processing"
            if any(r["status"] == "pending" for r in states)
            else "completed"
        )
        return {
            "event_id": eid,
            "order_id": event["order_id"],
            "status": status,
            "channel_results": states,
        }

    def hold(self, tenant: int, sku: str, owner: str, expires: float | None = None) -> None:
        with transaction(self.inventory, tenant) as db:
            row = db.inventory(tenant, sku)
            claim = self._claim(db, tenant, sku)
            if (
                not row
                or row["status"] not in ("active", "on_hold", "held")
                or (claim and (claim["kind"] != "collector" or claim["owner"] != owner))
            ):
                conflict()
            self._put_claim(db, tenant, sku, "collector", owner, expires)
            self._stock_jobs(db, tenant, sku)

    def release_hold(self, tenant: int, sku: str, owner: str) -> None:
        with transaction(self.inventory, tenant) as db:
            db.inventory(tenant, sku)
            claim = self._claim(db, tenant, sku)
            if claim and (claim["kind"] != "collector" or claim["owner"] != owner):
                conflict()
            db.execute(
                "DELETE FROM core_stock_claims WHERE tenant_id=? AND sku=? AND kind='collector' AND owner=?",
                (tenant, sku, owner),
            )
            db.execute(
                "UPDATE inventory SET status='active' WHERE tenant_id=? AND sku=? AND status IN ('held','on_hold')",
                (tenant, sku),
            )
            self._stock_jobs(db, tenant, sku)

    def observe_sale(self, tenant: int, sku: str, channel: str, order_id: str, hold_owner: str | None = None) -> str:
        """An external sale already happened. Freeze a contested copy; never pretend it didn't."""
        with transaction(self.inventory, tenant) as db:
            old = db.execute(
                "SELECT outcome FROM core_channel_sales WHERE tenant_id=? AND channel=? AND order_id=? AND sku=?",
                (tenant, channel, order_id, sku),
            ).fetchone()
            if old:
                return old["outcome"]
            row = db.inventory(tenant, sku)
            if not row:
                return "unknown"
            claim = self._claim(db, tenant, sku)
            contested = bool(claim and not (claim["kind"] == "collector" and claim["owner"] == hold_owner))
            if row["status"] not in ("active", "held", "on_hold"):
                contested = True
            if claim and claim["kind"] == "checkout":
                db.execute(
                    "UPDATE site_reservations SET status='conflicted' WHERE id=? AND status='active'",
                    (claim["owner"],),
                )
            self._put_claim(db, tenant, sku, "blocked" if contested else "sale", channel + ":" + order_id)
            db.execute("UPDATE inventory SET status='sold' WHERE tenant_id=? AND sku=?", (tenant, sku))
            db.execute(
                "INSERT INTO core_channel_sales VALUES(?,?,?,?,?)",
                (tenant, channel, order_id, sku, "conflict" if contested else "claimed"),
            )
            self._stock_jobs(db, tenant, sku)
            return "conflict" if contested else "claimed"
