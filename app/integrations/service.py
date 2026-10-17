from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import time
from collections.abc import Callable
from contextlib import suppress
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from app.integrations.database import Connection, transaction
from app.integrations.protocol import IntegrationError, SiteConnection, canonical, signature
from app.integrations.stock import StockLedger, conflict


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def send_snapshot(connection: SiteConnection, job: dict[str, Any]) -> tuple[bool, bool, float]:
    path = "/api/ops-core/v1/books/" + job["sku"]
    raw = job["payload"].encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Ops-Key-Id": connection.outbound_key_id,
        "X-Ops-Timestamp": timestamp,
        "X-Ops-Event-Id": job["id"],
        "X-Ops-Signature": signature(connection.outbound_secret, "PUT", path, timestamp, job["id"], raw),
    }
    request = Request(connection.website_url.rstrip("/") + path, data=raw, headers=headers, method="PUT")
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            status = response.status
            body = response.read(256 * 1024 + 1)
            if len(body) > 256 * 1024:
                return False, False, 0.0
            result = json.loads(body)
            snapshot = json.loads(raw)
            ok = (
                status == 200
                and result.get("sku") == job["sku"]
                and type(result.get("revision")) is int
                and result["revision"] >= snapshot["revision"]
                and type(result.get("applied")) is bool
            )
            return ok, not ok, 0.0
    except HTTPError as exc:
        retry_after = 0.0
        value = exc.headers.get("Retry-After", "") if exc.headers else ""
        with suppress(ValueError, TypeError, OverflowError):
            retry_after = (
                int(value) if value.isdecimal() else max(0, parsedate_to_datetime(value).timestamp() - time.time())
            )
        return False, exc.code in (408, 429) or exc.code >= 500, retry_after


class CustomSiteService:
    def __init__(
        self,
        ledger: StockLedger,
        user_store: Any,
        settings: Any,
        transport: Callable[..., tuple[bool, bool] | tuple[bool, bool, float]] | None = None,
    ) -> None:
        self.ledger, self.user_store, self.settings = ledger, user_store, settings
        self.connections = {c.connection_id: c for c in ledger.connections}
        self.transport = transport or send_snapshot

    def authenticate(self, method: str, path: str, query: str, headers: Any, body: bytes) -> tuple[SiteConnection, str]:
        c = next(
            (
                c
                for c in self.connections.values()
                if c.inbound_key_id == headers.get("x-ops-key-id")
                or headers.get("x-ops-key-id") in c.inbound_previous_keys
            ),
            None,
        )
        if not c or not c.enabled:
            raise IntegrationError(401, "invalid_signature", "Invalid integration credentials.")
        stamp = headers.get("x-ops-timestamp", "")
        eid = headers.get("x-ops-event-id", "")
        try:
            if not stamp.isdecimal() or str(UUID(eid)) != eid:
                raise ValueError()
            with transaction(self.ledger.inventory) as db:
                if abs(db.now() - int(stamp)) > 300:
                    raise ValueError()
        except (ValueError, OverflowError):
            raise IntegrationError(401, "invalid_signature", "Invalid or expired request signature.") from None
        key_id = headers.get("x-ops-key-id")
        secret = c.inbound_secret if key_id == c.inbound_key_id else c.inbound_previous_keys[key_id]
        expected = signature(secret, method, path, stamp, eid, body)
        received = headers.get("x-ops-signature", "")
        if not re.fullmatch(r"sha256=[0-9a-f]{64}", received) or not hmac.compare_digest(expected, received):
            raise IntegrationError(401, "invalid_signature", "Invalid request signature.")
        if query:
            raise IntegrationError(400, "query_not_allowed", "This API does not accept query parameters.")
        billing = self.user_store.get_tenant_billing_status(c.tenant_id)
        if billing.get("subscription_status") != "active":
            raise IntegrationError(403, "connection_inactive", "The connection needs an active subscription.")
        with transaction(self.ledger.inventory, c.tenant_id) as db:
            bucket = int(db.now() // 60)
            row = db.execute("SELECT * FROM site_rate_limits WHERE connection_id=?", (c.connection_id,)).fetchone()
            count = row["requests"] + 1 if row and row["bucket"] == bucket else 1
            if count > 60:
                raise IntegrationError(429, "rate_limit", "Too many requests. Retry after 60 seconds.")
            db.execute(
                "INSERT INTO site_rate_limits VALUES(?,?,?) ON CONFLICT(connection_id) "
                "DO UPDATE SET bucket=excluded.bucket,requests=excluded.requests",
                (c.connection_id, bucket, count),
            )
        return c, eid

    def mutate(
        self, c: SiteConnection, eid: str, method: str, path: str, body: bytes, callback: Callable[[Connection], Any]
    ) -> tuple[int, Any]:
        digest = hashlib.sha256(body).hexdigest()
        with transaction(self.ledger.inventory, c.tenant_id) as db:
            old = db.execute(
                "SELECT * FROM site_requests WHERE connection_id=? AND method=? AND path=? AND event_id=?",
                (c.connection_id, method, path, eid),
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    conflict("idempotency_conflict", "This event ID was used with different content.")
                return old["status"], json.loads(old["response"])
            if not c.checkout_enabled or not c.stock_protection_enabled:
                raise IntegrationError(403, "checkout_disabled", "Checkout is disabled for this connection.")
            status, response = callback(db)
            db.execute(
                "INSERT INTO site_requests VALUES(?,?,?,?,?,?,?)",
                (c.connection_id, method, path, eid, digest, status, canonical(response)),
            )
            return status, response

    def refresh_publications(self, tenant: int | None = None) -> None:
        for c in self.connections.values():
            if tenant is not None and c.tenant_id != tenant:
                continue
            if not c.enabled or not c.stock_protection_enabled:
                continue
            with transaction(self.ledger.inventory, c.tenant_id) as db:
                rows = db.execute(
                    "SELECT sku FROM site_publications WHERE connection_id=?", (c.connection_id,)
                ).fetchall()
                for row in rows:
                    self.ledger._snapshot(db, c, row["sku"])

    def drain(self, limit: int = 25, tenant: int | None = None, time_budget: float = 25) -> dict[str, int]:
        deadline = time.monotonic() + time_budget
        self.refresh_publications(tenant)
        counts = {"succeeded": 0, "retrying": 0, "needs_review": 0, "deferred": 0}
        for _ in range(min(limit, 100)):
            if time.monotonic() >= deadline:
                break
            with transaction(self.ledger.inventory) as db:
                now = db.now()
                clause = " AND tenant_id=?" if tenant is not None else ""
                suffix = " FOR UPDATE SKIP LOCKED" if db.postgres else ""
                args = (now, now, tenant) if tenant is not None else (now, now)
                job = db.execute(
                    "SELECT * FROM site_outbox WHERE state='pending' AND next_attempt<=? "
                    "AND lease_until<=?" + clause + " ORDER BY created_at,id LIMIT 1" + suffix,
                    args,
                ).fetchone()
                if not job:
                    break
                job = dict(job)
                job["lease_until"] = now + 60
                db.execute(
                    "UPDATE site_outbox SET lease_until=? WHERE id=?",
                    (job["lease_until"], job["id"]),
                )
            c = self.connections.get(job["connection_id"])
            success = False
            retryable = True
            retry_after = 0.0
            if not c or not c.enabled or not c.delivery_enabled:
                counts["deferred"] += 1
            else:
                try:
                    billing = self.user_store.get_tenant_billing_status(c.tenant_id)
                    if billing.get("subscription_status") != "active":
                        counts["deferred"] += 1
                    elif job["channel"] == "custom_site":
                        result = self.transport(c, job)
                        success, retryable = result[:2]
                        if len(result) > 2:
                            retry_after = result[2]
                    else:
                        success, retryable = self._deliver_channel(c, job)
                except Exception:
                    # Never persist raw HTTP errors, credentials or third-party bodies.
                    success, retryable = False, True
            with transaction(self.ledger.inventory) as db:
                attempts = job["attempts"] + 1
                now = db.now()
                terminal = not success and (not retryable or now - job["created_at"] >= 86400)
                state = "succeeded" if success else "needs_review" if terminal else "pending"
                delays = [2, 10, 30, 120, 600]
                delay = (delays[min(attempts - 1, 4)] if attempts <= 5 else 3600) * random.uniform(0.8, 1.2)
                delay = max(delay, min(retry_after, 86400))
                db.execute(
                    "UPDATE site_outbox SET "
                    "state=?,attempts=?,next_attempt=?,lease_until=0,last_error=? "
                    "WHERE id=? AND lease_until=? AND state='pending'",
                    (
                        state,
                        attempts,
                        now + delay,
                        "" if success else "delivery_not_confirmed",
                        job["id"],
                        job["lease_until"],
                    ),
                )
                if success:
                    counts["succeeded"] += 1
                elif terminal:
                    counts["needs_review"] += 1
                else:
                    counts["retrying"] += 1
        return counts

    def _deliver_channel(self, c: SiteConnection, job: dict[str, Any]) -> tuple[bool, bool]:
        channel = job["channel"]
        sku = job["sku"]

        if not self.user_store:
            return False, False

        try:
            enc_integrations = self.user_store.get_tenant_integrations(c.tenant_id)
        except Exception:
            return False, False

        enc_token = enc_integrations.get(channel)
        if not enc_token:
            return False, False

        try:
            from app.vault import decrypt_credential

            token = decrypt_credential(enc_token)
        except Exception:
            return False, False

        # If token is empty, mock placeholder, or system is in dry_run mode,
        # fail safe: never let a dry-run/stub response masquerade as real stock protection.
        if (
            not token
            or "mock" in token.lower()
            or token.startswith("sq0atp-mock")
            or getattr(self.settings, "dry_run", False)
        ):
            return False, False

        try:
            if channel == "square":
                from app.api.square_service import SquareService

                ok = SquareService.delist_catalog_item_raw(sku, access_token=token, dry_run=False)
                return ok, not ok
            elif channel == "abebooks":
                from app.api.abebooks_service import AbeBooksService

                client_id = getattr(self.settings, "abebooks_client_id", "ops_core_client")
                ok = AbeBooksService.delist_book(sku, api_key=token, client_id=client_id, dry_run=False)
                return ok, not ok
            elif channel == "ebay":
                from app.api.ebay_service import EbayService

                ok = EbayService.delist_item(sku, access_token=token, sandbox=False, dry_run=False)
                return ok, not ok
            elif channel == "biblio":
                from app.api.biblio_service import BiblioService

                ok = BiblioService.delist_book(sku, api_key=token, dry_run=False)
                return ok, not ok
            return False, False
        except Exception:
            return False, True
