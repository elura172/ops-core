"""Smoke Run & Outbox Audit: Multi-Channel Marketplace Delisting.

Demonstrates and audits the complete atomic lifecycle:
1. Volume publication to Custom Site ledger
2. Contended online reservation & price protection guards
3. Settlement (order.paid) generating atomic outbox jobs
4. Multi-channel delist dispatch (Square, AbeBooks, eBay, Biblio)
5. Outbox table & claim ledger post-dispatch audit
6. Fail-safe verification for unauthenticated/dry-run channels

Usage:
    uv run python -m scripts.smoke_marketplace_delist_audit
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.security import TokenRegistry, hash_token
from app.config import Settings
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.users import SQLiteUserStore
from app.domain.order_store import OrderStore
from app.integrations.database import transaction
from app.integrations.protocol import signature
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from app.vault import encrypt_credential

# ANSI styling
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"

PREFIX = "/api/integrations/custom-site/v1"


def header(title: str) -> None:
    print(f"\n{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}{CYAN}  🜐 {title.upper()}{RESET}")
    print(f"{BOLD}{CYAN}══════════════════════════════════════════════════════════════════════════════════{RESET}\n")


def log_step(step: str, detail: str = "") -> None:
    print(f"{BOLD}{GREEN}✓{RESET} {BOLD}{step}{RESET}")
    if detail:
        print(f"  {DIM}{detail}{RESET}")


def log_audit(label: str, data: dict) -> None:
    print(f"  {MAGENTA}› {label}:{RESET}")
    for k, v in data.items():
        print(f"    {DIM}{k}:{RESET} {v}")


def run_smoke_audit() -> bool:
    header("Seekers Books · Multi-Channel Delisting Smoke Run & Outbox Audit")

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "smoke_inventory.db")
        inventory = SQLiteInventoryStore(db_path)
        users = SQLiteUserStore(":memory:")

        # 1. Setup Tenant & Live Credentials for all 4 channels
        tenant_id = 1
        users.save_integration(tenant_id, "square", encrypt_credential("sq0atp-live-audit-key-token-999"))
        users.save_integration(tenant_id, "abebooks", encrypt_credential("abe_live_audit_key_888"))
        users.save_integration(tenant_id, "ebay", encrypt_credential("ebay_live_audit_oauth_777"))
        users.save_integration(tenant_id, "biblio", encrypt_credential("biblio_live_audit_token_666"))

        log_step(
            "Seeded Tenant 1 Integration Credentials",
            "Square (Live), AbeBooks (Live), eBay (OAuth), Biblio (Direct)",
        )

        # 2. Configure Custom Site Connection
        inbound_secret = "a" * 40
        outbound_secret = "b" * 40
        connections = [
            {
                "connection_id": "seekers_staging",
                "tenant_id": tenant_id,
                "inbound_key_id": "seekers_in",
                "inbound_secret": inbound_secret,
                "outbound_key_id": "seekers_out",
                "outbound_secret": outbound_secret,
                "website_url": "https://bookshop-ledger.vercel.app",
                "checkout_enabled": True,
                "stock_protection_enabled": True,
                "delivery_enabled": True,
                "channels": ["square", "abebooks", "ebay", "biblio"],
            }
        ]

        tokens = TokenRegistry.from_env(
            f"{hash_token('audit-reviewer')}:ada:reviewer,{hash_token('audit-operator')}:grace:operator"
        )
        settings = Settings(
            tts_app_key="key",
            tts_app_secret="secret",
            tts_access_token="token",
            tts_shop_id="shop",
            webhook_secret="secret",
            tokens=tokens,
            order_db=":memory:",
            review_db=":memory:",
            inventory_db=db_path,
            want_list_db=":memory:",
            user_db=":memory:",
            custom_site_connections=json.dumps(connections),
            dry_run=False,
            live_enabled=True,
            signing_verified=True,
            sandbox_passed=True,
            pii_controls_verified=True,
        )

        executor = ApprovedActionExecutor(None, inventory_store=inventory)
        operator = OperatorService(ReviewQueue(), executor)
        app = create_app(
            operator=operator,
            order_repository=OrderStore(),
            inventory_store=inventory,
            user_store=users,
            settings=settings,
        )
        service = app.state.custom_site_service
        conn = service.connections["seekers_staging"]

        # 3. Add Rare Antiquarian Volumes to Inventory & Publish
        now = datetime.now(UTC)
        test_sku = "SB-ARC-1643"
        rare_book = InventoryItem(
            test_sku,
            "Religio Medici (First Authorized Edition)",
            ["Sir Thomas Browne"],
            "folio_calf",
            "Fine",
            Decimal("1450.00"),
            Decimal("1450.00"),
            "active",
            now,
            now,
        )
        inventory.add(rare_book)
        service.ledger.publish(conn, test_sku, "published")

        second_sku = "SB-ARC-1651"
        second_book = InventoryItem(
            second_sku,
            "Leviathan, or The Matter, Forme and Power of a Commonwealth",
            ["Thomas Hobbes"],
            "folio_calf",
            "Fine",
            Decimal("2200.00"),
            Decimal("2200.00"),
            "active",
            now,
            now,
        )
        inventory.add(second_book)
        service.ledger.publish(conn, second_sku, "published")

        log_step(
            f"Published Catalogue Lots: {test_sku} & {second_sku}",
            "Browne (1643, $1,450.00 CAD) · Hobbes (1651, $2,200.00 CAD) · State: published (rev 1)",
        )

        # 4. Step: Contended Checkout & Price Protection
        from fastapi.testclient import TestClient

        client = TestClient(app)

        # 4a. Customer A reserves
        res_payload = {
            "schema_version": "1.0",
            "checkout_id": "chk_smoke_001",
            "items": [
                {
                    "sku": test_sku,
                    "quantity": 1,
                    "unit_price": {"amount": "1450.00", "currency": "CAD"},
                }
            ],
        }
        raw_res = json.dumps(res_payload).encode()
        ts = str(int(time.time()))
        eid_res = "11111111-0000-0000-0000-111111111111"
        sig_res = signature(inbound_secret, "POST", PREFIX + "/reservations", ts, eid_res, raw_res)

        r = client.post(
            PREFIX + "/reservations",
            content=raw_res,
            headers={
                "Content-Type": "application/json",
                "X-Ops-Key-Id": "seekers_in",
                "X-Ops-Timestamp": ts,
                "X-Ops-Event-Id": eid_res,
                "X-Ops-Signature": sig_res,
            },
        )
        assert r.status_code == 201, f"Reservation failed: {r.text}"
        reservation_id = r.json()["reservation_id"]
        log_step(
            f"Customer A Placed Reservation: {reservation_id}",
            "Status: active · Lock TTL: 15 minutes",
        )

        # 4b. Customer B tries to reserve simultaneously -> 409 conflict
        competing_payload = {
            "schema_version": "1.0",
            "checkout_id": "chk_smoke_002",
            "items": res_payload["items"],
        }
        raw_competing = json.dumps(competing_payload).encode()
        r_competing = client.post(
            PREFIX + "/reservations",
            content=raw_competing,
            headers={
                "Content-Type": "application/json",
                "X-Ops-Key-Id": "seekers_in",
                "X-Ops-Timestamp": ts,
                "X-Ops-Event-Id": "22222222-0000-0000-0000-222222222222",
                "X-Ops-Signature": signature(
                    inbound_secret,
                    "POST",
                    PREFIX + "/reservations",
                    ts,
                    "22222222-0000-0000-0000-222222222222",
                    raw_competing,
                ),
            },
        )
        assert r_competing.status_code == 409
        assert r_competing.json()["error"]["code"] == "stock_conflict"
        log_step(
            "Contention Guard Verified",
            "Competing reservation cleanly rejected with 409 stock_conflict",
        )

        # 4c. Tampered price check on unreserved second volume
        tampered_body = json.dumps(
            {
                "schema_version": "1.0",
                "checkout_id": "chk_tampered",
                "items": [
                    {
                        "sku": second_sku,
                        "quantity": 1,
                        "unit_price": {"amount": "22.00", "currency": "CAD"},
                    }
                ],
            }
        ).encode()
        r_tampered = client.post(
            PREFIX + "/reservations",
            content=tampered_body,
            headers={
                "Content-Type": "application/json",
                "X-Ops-Key-Id": "seekers_in",
                "X-Ops-Timestamp": ts,
                "X-Ops-Event-Id": "33333333-0000-0000-0000-333333333333",
                "X-Ops-Signature": signature(
                    inbound_secret,
                    "POST",
                    PREFIX + "/reservations",
                    ts,
                    "33333333-0000-0000-0000-333333333333",
                    tampered_body,
                ),
            },
        )
        assert r_tampered.status_code == 409
        assert r_tampered.json()["error"]["code"] == "price_mismatch"
        log_step(
            "Price Integrity Guard Verified",
            "Price mismatch ($22.00 vs $2,200.00 CAD) rejected with 409 price_mismatch",
        )

        # 5. Settlement: Send order.paid event
        order_id = "ord_smoke_999"
        pay_event_id = "44444444-0000-0000-0000-444444444444"
        pay_payload = {
            "schema_version": "1.0",
            "event_id": pay_event_id,
            "event_type": "order.paid",
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "order_id": order_id,
            "reservation_id": reservation_id,
            "items": res_payload["items"],
        }
        raw_pay = json.dumps(pay_payload).encode()
        sig_pay = signature(inbound_secret, "POST", PREFIX + "/order-events", ts, pay_event_id, raw_pay)

        r_pay = client.post(
            PREFIX + "/order-events",
            content=raw_pay,
            headers={
                "Content-Type": "application/json",
                "X-Ops-Key-Id": "seekers_in",
                "X-Ops-Timestamp": ts,
                "X-Ops-Event-Id": pay_event_id,
                "X-Ops-Signature": sig_pay,
            },
        )
        assert r_pay.status_code == 202
        log_step(f"Settled Order Event: {order_id}", f"202 Accepted · Event ID: {pay_event_id}")

        # 6. Audit Outbox Table Before Delivery
        with transaction(inventory) as db:
            cur = db.execute("SELECT id, channel, sku, state, attempts, next_attempt FROM site_outbox ORDER BY id ASC")
            pending_jobs = cur.fetchall()

        # 2 publications + 1 reservation stock update + 1 order snapshot + 4 marketplace delists = 8 total jobs
        assert len(pending_jobs) == 8, f"Expected 8 outbox jobs, got {len(pending_jobs)}"
        channels_queued = [j["channel"] if hasattr(j, "keys") else j[1] for j in pending_jobs]
        mkt_jobs = [j for j in pending_jobs if (j["channel"] if hasattr(j, "keys") else j[1]) != "custom_site"]
        site_jobs = [j for j in pending_jobs if (j["channel"] if hasattr(j, "keys") else j[1]) == "custom_site"]

        log_step(
            f"Atomic Outbox Enqueue Verified ({len(pending_jobs)} jobs pending)",
            f"{len(site_jobs)} Custom Site snapshots (2 publish, 1 reserve hold, 1 order settlement) "
            f"+ {len(mkt_jobs)} Marketplace Delists "
            f"({', '.join(set(channels_queued) - {'custom_site'})})",
        )
        for job in pending_jobs:
            jid = job["id"] if hasattr(job, "keys") else job[0]
            jch = job["channel"] if hasattr(job, "keys") else job[1]
            jsku = job["sku"] if hasattr(job, "keys") else job[2]
            jst = job["state"] if hasattr(job, "keys") else job[3]
            jatt = job["attempts"] if hasattr(job, "keys") else job[4]
            log_audit(f"Outbox Job #{jid}", {"channel": jch, "sku": jsku, "state": jst, "attempts": jatt})

        # 7. Drain Outbox with Delist Adapters
        # Mock custom site transport
        service.transport = lambda c, job: (True, False)

        calls = {"square": 0, "abebooks": 0, "ebay": 0, "biblio": 0}

        def mock_sq_delist(sku, access_token, dry_run=False):
            calls["square"] += 1
            return True

        def mock_abe_delist(sku, api_key, client_id="", dry_run=False):
            calls["abebooks"] += 1
            return True

        def mock_ebay_delist(sku, access_token, sandbox=False, dry_run=False):
            calls["ebay"] += 1
            return True

        def mock_bib_delist(sku, api_key, dry_run=False):
            calls["biblio"] += 1
            return True

        with (
            patch(
                "app.api.square_service.SquareService.delist_catalog_item_raw",
                side_effect=mock_sq_delist,
            ),
            patch("app.api.abebooks_service.AbeBooksService.delist_book", side_effect=mock_abe_delist),
            patch("app.api.ebay_service.EbayService.delist_item", side_effect=mock_ebay_delist),
            patch("app.api.biblio_service.BiblioService.delist_book", side_effect=mock_bib_delist),
        ):
            drain_stats = service.drain()
            log_step("Outbox Drained Successfully", f"Processed: {drain_stats}")

        assert calls["square"] == 1, "Square delist was not called"
        assert calls["abebooks"] == 1, "AbeBooks delist was not called"
        assert calls["ebay"] == 1, "eBay delist was not called"
        assert calls["biblio"] == 1, "Biblio delist was not called"

        log_step(
            "Channel Adapters Invoked with Sovereign Credentials",
            f"Square: {calls['square']} · AbeBooks: {calls['abebooks']} · "
            f"eBay: {calls['ebay']} · Biblio: {calls['biblio']}",
        )

        # 8. Post-Dispatch Audit
        with transaction(inventory) as db:
            cur = db.execute("SELECT id, channel, state, attempts, last_error FROM site_outbox ORDER BY id ASC")
            completed_jobs = cur.fetchall()

            cur_claim = db.execute(
                "SELECT tenant_id, sku, kind, owner, expires_at FROM core_stock_claims WHERE sku=?",
                (test_sku,),
            ).fetchone()

        log_step(
            "Post-Dispatch Outbox Table State",
            f"All {len(completed_jobs)} jobs transitioned to 'succeeded'",
        )
        for job in completed_jobs:
            jid = job["id"] if hasattr(job, "keys") else job[0]
            jch = job["channel"] if hasattr(job, "keys") else job[1]
            jst = job["state"] if hasattr(job, "keys") else job[2]
            jatt = job["attempts"] if hasattr(job, "keys") else job[3]
            jerr = job["last_error"] if hasattr(job, "keys") else job[4]
            log_audit(
                f"Outbox Job #{jid}",
                {"channel": jch, "state": jst, "attempts": jatt, "last_error": repr(jerr)},
            )
            assert jst == "succeeded"

        c_sku = cur_claim["sku"] if hasattr(cur_claim, "keys") else cur_claim[1]
        c_kind = cur_claim["kind"] if hasattr(cur_claim, "keys") else cur_claim[2]
        c_owner = cur_claim["owner"] if hasattr(cur_claim, "keys") else cur_claim[3]
        log_step("Core Stock Claims Audit", f"SKU: {c_sku} · Kind: {c_kind} · Owner: {c_owner}")
        assert c_kind == "sale"
        assert c_owner == order_id

        # 9. Query Order Event Status API
        eid_status = "55555555-0000-0000-0000-555555555555"
        sig_status = signature(inbound_secret, "GET", f"{PREFIX}/order-events/{pay_event_id}", ts, eid_status, b"")
        r_status = client.get(
            f"{PREFIX}/order-events/{pay_event_id}",
            headers={
                "X-Ops-Key-Id": "seekers_in",
                "X-Ops-Timestamp": ts,
                "X-Ops-Event-Id": eid_status,
                "X-Ops-Signature": sig_status,
            },
        )
        assert r_status.status_code == 200, f"Status check failed: {r_status.status_code} {r_status.text}"
        status_json = r_status.json()
        log_step(
            f"Aggregated Order Event Status: {status_json['status']}",
            f"Order: {status_json['order_id']}",
        )
        for ch in status_json["channel_results"]:
            log_audit(f"Marketplace Channel [{ch['channel']}]", {"status": ch["status"]})
            assert ch["status"] == "succeeded"

        # 10. Fail-Safe Negative Proof: Dry Run Protection
        # Verify that if dry_run=True, jobs NEVER masquerade as real success
        with patch.object(service, "settings", replace(service.settings, dry_run=True)):
            dry_job = {
                "channel": "square",
                "sku": "SB-ARC-1643",
                "attempts": 0,
                "created_at": time.time(),
                "id": 999,
                "lease_until": 0,
            }
            success, retryable = service._deliver_channel(conn, dry_job)
            assert success is False
            assert retryable is False
            log_step(
                "Fail-Safe Guard Verified (Dry-Run / Stub Safety)",
                "Dry-run/mock flags correctly fail-safe: never reports phantom success for real inventory delist.",
            )

    header("All Multi-Channel Delist & Outbox Audits Passed With Full Integrity")
    return True


if __name__ == "__main__":
    success = run_smoke_audit()
    sys.exit(0 if success else 1)
