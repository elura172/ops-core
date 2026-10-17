from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.db.inventory import InventoryItem, PostgresInventoryStore, SQLiteInventoryStore
from app.db.users import SQLiteUserStore
from app.domain.order_store import OrderStore
from app.integrations.database import transaction
from app.integrations.protocol import IntegrationError, ReservationRequest, canonical, signature
from app.integrations.stock import StockLedger
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from tests.conftest import ADMIN_TOKEN, auth

PREFIX = "/api/integrations/custom-site/v1"


@pytest.fixture(params=["sqlite", "postgres"])
def site(request, tmp_path, settings):
    if request.param == "postgres":
        dsn = request.getfixturevalue("postgres_dsn")
        from app.db.migrate import migrate

        migrate(dsn)
        inventory = PostgresInventoryStore(dsn)
        with inventory.pool.connection() as conn:
            conn.execute(
                "TRUNCATE inventory, core_stock_claims, site_publications, "
                "site_reservations, site_orders, site_order_events, site_requests, "
                "site_outbox, site_event_jobs, core_channel_sales, site_rate_limits"
            )
    else:
        inventory = SQLiteInventoryStore(str(tmp_path / "inventory.db"))
    connections = [
        {
            "connection_id": "client_shop",
            "tenant_id": 1,
            "inbound_key_id": "site_in",
            "inbound_secret": "a" * 40,
            "outbound_key_id": "site_out",
            "outbound_secret": "b" * 40,
            "website_url": "https://website.example",
            "checkout_enabled": True,
            "stock_protection_enabled": True,
            "delivery_enabled": True,
        },
        {
            "connection_id": "other_shop",
            "tenant_id": 1,
            "inbound_key_id": "other_in",
            "inbound_secret": "c" * 40,
            "stock_protection_enabled": True,
            "checkout_enabled": True,
        },
    ]
    users = SQLiteUserStore(":memory:")
    executor = ApprovedActionExecutor(None, inventory_store=inventory)
    operator = OperatorService(ReviewQueue(), executor)
    app = create_app(
        operator=operator,
        order_repository=OrderStore(),
        inventory_store=inventory,
        user_store=users,
        settings=replace(settings, custom_site_connections=json.dumps(connections)),
    )
    c = TestClient(app)
    service = app.state.custom_site_service
    connection = service.connections["client_shop"]
    now = datetime.now(UTC)
    for sku in ("BK-SITE-1", "BK-SITE-2"):
        inventory.add(
            InventoryItem(
                sku,
                "Example title",
                ["Example Author"],
                "trade_paperback",
                "Good",
                Decimal("20.00"),
                Decimal("24.00"),
                "active",
                now,
                now,
            )
        )
        service.ledger.publish(connection, sku, "published")
    yield dict(
        client=c,
        app=app,
        service=service,
        ledger=service.ledger,
        connection=connection,
        inventory=inventory,
        operator=operator,
        settings=settings,
    )
    inventory.close()
    users.close()


def signed(site, method, path, payload=None, eid=None, connection=None, stamp=None, raw=None):
    c = connection or site["connection"]
    eid = eid or str(uuid4())
    stamp = stamp or str(int(time.time()))
    body = raw if raw is not None else canonical(payload).encode() if payload is not None else b""
    headers = {
        "X-Ops-Key-Id": c.inbound_key_id,
        "X-Ops-Timestamp": stamp,
        "X-Ops-Event-Id": eid,
        "X-Ops-Signature": signature(c.inbound_secret, method, path, stamp, eid, body),
        "Content-Type": "application/json",
    }
    return site["client"].request(method, path, content=body, headers=headers)


def reserve(site, checkout="checkout_1", skus=("BK-SITE-1",), eid=None):
    payload = {
        "schema_version": "1.0",
        "checkout_id": checkout,
        "items": [{"sku": s, "quantity": 1, "unit_price": {"amount": "24.00", "currency": "CAD"}} for s in skus],
    }
    return signed(site, "POST", PREFIX + "/reservations", payload, eid), payload


def paid(site, reservation, event_type="order.paid", order="web_order_1", eid=None):
    eid = eid or str(uuid4())
    payload = {
        "schema_version": "1.0",
        "event_id": eid,
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "order_id": order,
        "reservation_id": reservation["reservation_id"],
        "items": reservation["items"],
    }
    return signed(site, "POST", PREFIX + "/order-events", payload, eid), payload


def test_reservation_and_sale_are_durable_idempotent_and_cancel_does_not_restock(site):
    eid = str(uuid4())
    first, payload = reserve(site, eid=eid)
    assert first.status_code == 201, first.text
    retry = signed(site, "POST", PREFIX + "/reservations", payload, eid)
    assert retry.json() == first.json()
    business_retry, _ = reserve(site)
    assert business_retry.json() == first.json()
    other, _ = reserve(site, checkout="checkout_other")
    assert other.status_code == 409
    result, event = paid(site, first.json())
    assert result.status_code == 202, result.text
    replay = signed(site, "POST", PREFIX + "/order-events", event, event["event_id"])
    assert replay.json() == result.json()
    duplicate, _ = paid(site, first.json())
    assert duplicate.json() == result.json()
    assert site["inventory"].get("BK-SITE-1").status == "sold"
    release = signed(
        site,
        "POST",
        PREFIX + "/reservations/" + first.json()["reservation_id"] + "/release",
        {"reason": "customer_cancelled"},
    )
    assert release.status_code == 409
    cancelled, _ = paid(site, first.json(), event_type="order.cancelled")
    assert cancelled.status_code == 202
    assert site["inventory"].get("BK-SITE-1").status == "sold"
    # A delayed paid notification cannot reopen a cancelled order.
    assert paid(site, first.json())[0].status_code == 202
    with transaction(site["inventory"]) as db:
        assert db.execute("SELECT status FROM site_orders").fetchone()["status"] == "cancelled"
        assert db.execute("SELECT COUNT(*) AS n FROM site_order_events").fetchone()["n"] == 2


def test_wrong_price_and_multicopy_checkout_are_all_or_nothing(site):
    site["ledger"].hold(1, "BK-SITE-2", "collector_x", time.time() + 3600)
    result, _ = reserve(site, skus=("BK-SITE-1", "BK-SITE-2"))
    assert result.status_code == 409
    assert reserve(site, checkout="single")[0].status_code == 201
    site["inventory"].update_prices("BK-SITE-2", Decimal("20"), Decimal("30"))
    site["ledger"].release_hold(1, "BK-SITE-2", "collector_x")
    result, _ = reserve(site, checkout="new_price", skus=("BK-SITE-2",))
    assert result.status_code == 409 and result.json()["error"]["code"] == "price_mismatch"


def test_expiry_uses_database_time_and_old_release_cannot_free_new_claim(site):
    first, _ = reserve(site)
    rid = first.json()["reservation_id"]
    with transaction(site["inventory"]) as db:
        db.execute("UPDATE site_reservations SET expires_at=? WHERE id=?", (db.now() - 1, rid))
        db.execute("UPDATE core_stock_claims SET expires_at=? WHERE owner=?", (db.now() - 1, rid))
    assert paid(site, first.json())[0].status_code == 409
    second, _ = reserve(site, checkout="fresh")
    assert second.status_code == 201
    result = signed(site, "POST", PREFIX + "/reservations/" + rid + "/release", {"reason": "checkout_abandoned"})
    assert result.status_code == 200 and result.json()["status"] == "expired"
    assert reserve(site, checkout="third")[0].status_code == 409
    assert paid(site, second.json())[0].status_code == 202


def test_racing_workers_one_copy_one_winner(site):
    inv = site["inventory"]
    other = PostgresInventoryStore(inv.pool.conninfo) if hasattr(inv, "pool") else SQLiteInventoryStore(inv.path)
    ledger2 = StockLedger(other, site["ledger"].connections)
    gate = threading.Barrier(2)

    def attempt(ledger, checkout):
        gate.wait()
        request = ReservationRequest.model_validate(
            {
                "schema_version": "1.0",
                "checkout_id": checkout,
                "items": [
                    {
                        "sku": "BK-SITE-1",
                        "quantity": 1,
                        "unit_price": {"amount": "24.00", "currency": "CAD"},
                    }
                ],
            }
        )
        try:
            with transaction(ledger.inventory, 1) as db:
                ledger.reserve(db, site["connection"], request)
            return "reserved"
        except IntegrationError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(attempt, site["ledger"], "racing_a")
            b = pool.submit(attempt, ledger2, "racing_b")
            assert sorted([a.result(), b.result()]) == ["reserved", "stock_conflict"]
    finally:
        other.close()


def test_collector_and_external_sale_share_claim_and_freeze_contested_copy(site):
    first, _ = reserve(site)
    with pytest.raises(IntegrationError):
        site["ledger"].hold(1, "BK-SITE-1", "hld_x", time.time() + 3600)
    result = site["operator"].propose(
        "sync_inventory", {"sku": "BK-SITE-1", "source_channel": "abebooks", "order_id": "abe_1"}
    )
    assert result["operation"] == "review_stock_conflict"
    assert site["inventory"].get("BK-SITE-1").status == "sold"
    assert paid(site, first.json())[0].status_code == 409
    assert reserve(site, checkout="new")[0].status_code == 409
    with pytest.raises(IntegrationError):
        site["ledger"].release_hold(1, "BK-SITE-1", "hld_x")


def test_stock_guards_block_legacy_reopen_delete_and_counter_hold(site):
    first, _ = reserve(site)
    with pytest.raises(Exception, match="stock_claim_conflict"):
        site["inventory"].delist("BK-SITE-1")
    with pytest.raises(Exception, match="stock_claim_conflict"):
        site["inventory"].delete("BK-SITE-1")
    if hasattr(site["inventory"], "create_hold"):
        with pytest.raises(Exception, match="stock_claim_conflict"):
            site["inventory"].create_hold("BK-SITE-1", "slip_x", "Test", "example@example.test")
    assert paid(site, first.json())[0].status_code == 202
    item = site["inventory"].get("BK-SITE-1")
    item.status = "active"
    with pytest.raises(Exception, match="stock_claim_conflict"):
        site["inventory"].add(item)
    # Explicit operator relisting is the only path that clears a sold claim.
    site["inventory"].relist("BK-SITE-1")
    assert reserve(site, checkout="approved_relist")[0].status_code == 201


def test_signed_requests_reject_tampering_timestamp_inactive_and_other_client(site):
    payload = {"schema_version": "1.0", "checkout_id": "x", "items": []}
    expired = signed(site, "POST", PREFIX + "/reservations", payload, stamp=str(int(time.time()) - 301))
    assert expired.status_code == 401
    body = canonical(payload).encode()
    eid = str(uuid4())
    stamp = str(int(time.time()))
    headers = {
        "X-Ops-Key-Id": "site_in",
        "X-Ops-Timestamp": stamp,
        "X-Ops-Event-Id": eid,
        "X-Ops-Signature": signature("a" * 40, "POST", PREFIX + "/reservations", stamp, eid, body),
        "Content-Type": "application/json",
    }
    assert site["client"].post(PREFIX + "/reservations", content=body + b" ", headers=headers).status_code == 401
    first, _ = reserve(site)
    other = site["service"].connections["other_shop"]
    release = signed(
        site,
        "POST",
        PREFIX + "/reservations/" + first.json()["reservation_id"] + "/release",
        {"reason": "customer_cancelled"},
        connection=other,
    )
    assert release.status_code == 404
    old = site["service"].connections["client_shop"]
    site["service"].connections["client_shop"] = replace(old, enabled=False)
    assert reserve(site, checkout="disabled")[0].status_code == 401


def test_payload_validation_and_idempotency_conflict(site):
    eid = str(uuid4())
    first, payload = reserve(site, eid=eid)
    payload["checkout_id"] = "different"
    assert signed(site, "POST", PREFIX + "/reservations", payload, eid).status_code == 409
    payload["items"].append(payload["items"][0])
    assert signed(site, "POST", PREFIX + "/reservations", payload).status_code == 422
    assert signed(site, "POST", PREFIX + "/reservations", raw=b"{").status_code == 400
    large = signed(site, "POST", PREFIX + "/reservations", raw=b" " * (256 * 1024 + 1))
    assert large.status_code == 413
    assert first.headers["cache-control"] == "no-store"


def test_delivery_retry_recovery_withdrawal_revision_and_event_status(site):
    first, _ = reserve(site)
    result, event = paid(site, first.json())
    path = PREFIX + "/order-events/" + result.json()["event_id"]
    assert signed(site, "GET", path).json()["status"] == "processing"
    calls = []

    def unavailable(c, job):
        calls.append(job["id"])
        raise TimeoutError("not persisted")

    site["service"].transport = unavailable
    assert site["service"].drain()["retrying"] > 0
    with transaction(site["inventory"]) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM site_outbox WHERE state='pending'").fetchone()["n"] > 0
        db.execute("UPDATE site_outbox SET next_attempt=0")
    site["service"].transport = lambda c, j: (True, False)
    assert site["service"].drain()["succeeded"] > 0
    assert signed(site, "GET", path).json()["status"] == "completed"
    site["ledger"].publish(site["connection"], "BK-SITE-2", "unpublished")
    site["service"].refresh_publications()
    with transaction(site["inventory"]) as db:
        rows = db.execute("SELECT snapshot,revision FROM site_publications WHERE sku='BK-SITE-2'").fetchall()
        assert all(json.loads(r["snapshot"])["publication"] == "unpublished" for r in rows)
        assert all(json.loads(r["snapshot"])["quantity"] == 0 for r in rows)
    assert reserve(site, checkout="withdrawn", skus=("BK-SITE-2",))[0].status_code == 409


def test_failed_storage_rolls_back_sale_and_receipt(site, monkeypatch):
    first, _ = reserve(site)
    original = site["ledger"]._stock_jobs

    def broken(*args):
        raise RuntimeError("storage failed")

    monkeypatch.setattr(site["ledger"], "_stock_jobs", broken)
    result, event = paid(site, first.json())
    assert result.status_code == 503
    assert site["inventory"].get("BK-SITE-1").status == "active"
    monkeypatch.setattr(site["ledger"], "_stock_jobs", original)
    assert signed(site, "POST", PREFIX + "/order-events", event, event["event_id"]).status_code == 202


def test_publication_requires_review_and_is_tenant_scoped(site):
    proposal = site["operator"].propose("publish_custom_site", {"connection_id": "client_shop", "sku": "BK-SITE-1"})
    rid = proposal["review_id"]
    assert site["operator"].review_detail(rid)["status"].value == "PENDING"
    site["operator"].decide(rid, True, "test reviewer")
    result = site["operator"].execute(rid, "test operator")
    assert result["status"] == "EXECUTED"
    service = site["service"]
    original = service.connections["client_shop"]
    service.connections["client_shop"] = replace(original, tenant_id=2)
    with pytest.raises(PermissionError):
        site["operator"].executor._publish_custom_site({"connection_id": "client_shop", "sku": "BK-SITE-1"})


def test_checkout_defaults_off_and_rate_limit_is_durable(site):
    connection = site["connection"]
    # The mutation switch is separate from authentication.
    original = site["service"].connections[connection.connection_id]
    site["service"].connections[connection.connection_id] = replace(original, checkout_enabled=False)
    assert reserve(site)[0].status_code == 403
    site["service"].connections[connection.connection_id] = original
    with transaction(site["inventory"]) as db:
        db.execute(
            "UPDATE site_rate_limits SET requests=60 WHERE connection_id=?",
            (connection.connection_id,),
        )
    result, _ = reserve(site)
    assert result.status_code == 429
    assert result.headers["retry-after"] == "60"


def test_restart_retains_claim_and_pending_delivery(site):
    first, payload = reserve(site)
    inv = site["inventory"]
    reopened = PostgresInventoryStore(inv.pool.conninfo) if hasattr(inv, "pool") else SQLiteInventoryStore(inv.path)
    try:
        with transaction(reopened) as db:
            assert (
                db.execute("SELECT owner FROM core_stock_claims WHERE sku=?", ("BK-SITE-1",)).fetchone()["owner"]
                == first.json()["reservation_id"]
            )
            assert db.execute("SELECT COUNT(*) AS n FROM site_outbox WHERE state='pending'").fetchone()["n"] > 0
            assert db.execute("SELECT COUNT(*) AS n FROM site_requests").fetchone()["n"] == 1
    finally:
        reopened.close()


def test_collector_store_claim_and_checkout_race(site):
    from app.db.want_list import Collector, CollectorHold, WantItem

    store = site["operator"].executor.want_list_store
    store.create_collector(Collector("col_test", 1, "Example Collector"))
    store.create_want(WantItem("want_test", "col_test", 1, "Example title"))
    now = datetime.now(UTC)
    hold = CollectorHold("hold_test", 1, "BK-SITE-1", "want_test", "col_test", now, now + timedelta(hours=48))
    gate = threading.Barrier(2)

    def collector():
        gate.wait()
        try:
            store.create_hold(hold)
            return "collector"
        except IntegrationError:
            return "blocked"

    def website():
        gate.wait()
        result, _ = reserve(site)
        return "website" if result.status_code == 201 else "blocked"

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(collector)
        b = pool.submit(website)
        results = [a.result(), b.result()]
    assert results.count("blocked") == 1
    assert ("collector" in results) != ("website" in results)


def test_existing_marketplace_webhooks_require_authentication(site):
    for path in ("/webhooks/ebay", "/webhooks/abebooks", "/webhooks/biblio"):
        assert site["client"].post(path, json={}).status_code == 401
    assert (
        site["client"]
        .post(
            "/api/shop/holds",
            json={
                "sku": "BK-SITE-1",
                "patron_name": "Example",
                "patron_email": "example@example.test",
            },
        )
        .status_code
        == 401
    )


def test_actual_sender_signs_exact_body_and_checks_receipt(site, monkeypatch):
    from urllib.parse import urlsplit

    from app.integrations.service import send_snapshot

    calls = []

    class Reply:
        status = 200

        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return canonical(self.value).encode()

    class Receiver:
        def open(self, request, timeout):
            headers = {k.lower(): v for k, v in request.header_items()}
            path = urlsplit(request.full_url).path
            assert headers["x-ops-signature"] == signature(
                "b" * 40,
                "PUT",
                path,
                headers["x-ops-timestamp"],
                headers["x-ops-event-id"],
                request.data,
            )
            snapshot = json.loads(request.data)
            calls.append(snapshot)
            return Reply({"sku": snapshot["sku"], "revision": snapshot["revision"], "applied": True})

    monkeypatch.setattr("app.integrations.service.build_opener", lambda *args: Receiver())
    site["service"].transport = send_snapshot
    assert site["service"].drain()["succeeded"] == 2
    site["ledger"].publish(site["connection"], "BK-SITE-1", "unpublished")
    assert site["service"].drain()["succeeded"] == 1
    assert calls[-1]["quantity"] == 0 and calls[-1]["publication"] == "unpublished"
    assert calls[-1]["revision"] > calls[0]["revision"]


def test_stale_delivery_worker_cannot_overwrite_new_lease_result(site):
    def late(c, job):
        with transaction(site["inventory"]) as db:
            db.execute(
                "UPDATE site_outbox SET state='succeeded',lease_until=? WHERE id=?",
                (job["lease_until"] + 60, job["id"]),
            )
        return False, True

    site["service"].transport = late
    site["service"].drain()
    with transaction(site["inventory"]) as db:
        assert db.execute("SELECT COUNT(*) AS n FROM site_outbox WHERE state!='succeeded'").fetchone()["n"] == 0


def test_publication_review_pins_real_content_and_rejects_later_price_change(site):
    proposal = site["operator"].propose("publish_custom_site", {"connection_id": "client_shop", "sku": "BK-SITE-1"})
    detail = site["operator"].review_detail(proposal["review_id"])
    assert detail["payload"]["catalogue"]["title"] == "Example title"
    assert detail["payload"]["catalogue"]["price"]["amount"] == "24.00"
    site["operator"].decide(proposal["review_id"], True, "reviewer")
    site["inventory"].update_prices("BK-SITE-1", Decimal("20"), Decimal("25"))
    from app.review.executor import ExecutionFailed

    with pytest.raises(ExecutionFailed, match="changed after review"):
        site["operator"].execute(proposal["review_id"], "operator")


def test_hold_cannot_take_over_checkout_with_matching_owner_id(site):
    first, _ = reserve(site)
    with pytest.raises(IntegrationError):
        site["ledger"].hold(1, "BK-SITE-1", first.json()["reservation_id"], time.time() + 3600)


def test_signing_key_rotation_preserves_connection_and_idempotency(site):
    c = site["connection"]
    site["service"].connections[c.connection_id] = replace(c, inbound_previous_keys={"site_previous": "d" * 40})
    old = replace(c, inbound_key_id="site_previous", inbound_secret="d" * 40)
    first, payload = reserve(site)
    replay = signed(site, "POST", PREFIX + "/reservations", payload, connection=old)
    assert replay.status_code == 201 and replay.json() == first.json()
    site["service"].connections[c.connection_id] = c
    assert signed(site, "POST", PREFIX + "/reservations", payload, connection=old).status_code == 401


def test_receiver_rate_limit_retry_after_is_honored(site, monkeypatch):
    from urllib.error import HTTPError

    from app.integrations.service import send_snapshot

    class Limited:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 429, "rate limit", {"Retry-After": "120"}, None)

    monkeypatch.setattr("app.integrations.service.build_opener", lambda *args: Limited())
    site["service"].transport = send_snapshot
    assert site["service"].drain()["retrying"] == 2
    with transaction(site["inventory"]) as db:
        rows = db.execute("SELECT next_attempt FROM site_outbox").fetchall()
        assert all(row["next_attempt"] > db.now() + 119 for row in rows)


def test_stub_marketplace_delivery_never_counts_as_success(site):
    c = replace(site["connection"], channels=("square",))
    site["service"].connections[c.connection_id] = c
    first, _ = reserve(site)
    result, event = paid(site, first.json())
    site["service"].transport = lambda c, job: (True, False)
    assert site["service"].drain()["needs_review"] == 1
    status = signed(site, "GET", PREFIX + "/order-events/" + result.json()["event_id"]).json()
    assert status["status"] == "needs_review"
    assert {"channel": "square", "status": "needs_review"} in status["channel_results"]


def test_approved_collector_to_marketplace_transfer_never_reopens_stock(site, monkeypatch):
    from app.db.want_list import Collector, CollectorHold, WantItem

    store = site["operator"].executor.want_list_store
    store.create_collector(Collector("transfer_col", 1, "Collector"))
    store.create_want(WantItem("transfer_want", "transfer_col", 1, "Example title"))
    now = datetime.now(UTC)
    store.create_hold(
        CollectorHold(
            "transfer_hold",
            1,
            "BK-SITE-1",
            "transfer_want",
            "transfer_col",
            now,
            now + timedelta(hours=48),
        )
    )
    original = store.update_hold

    def assert_transferred(*args, **kwargs):
        with transaction(site["inventory"]) as db:
            row = db.execute("SELECT kind FROM core_stock_claims WHERE sku=?", ("BK-SITE-1",)).fetchone()
            assert row["kind"] == "sale"
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "update_hold", assert_transferred)
    result = site["operator"].executor._resolve_contention_hold_vs_marketplace(
        {
            "sku": "BK-SITE-1",
            "hold_id": "transfer_hold",
            "source_channel": "abebooks",
            "order_id": "abe_transfer",
            "tenant_id": 1,
        }
    )
    assert result["status"] == "contention_resolved"
    assert reserve(site, checkout="after_transfer")[0].status_code == 409


def test_parallel_executor_context_keeps_tenants_and_credentials_separate():
    executor = ApprovedActionExecutor(None)
    barrier = threading.Barrier(2)

    def probe(payload):
        barrier.wait(timeout=5)
        return {"tenant": executor.current_tenant_id, "credentials": executor.current_credentials}

    executor.handlers["probe_context"] = probe
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(executor._dispatch, "probe_context", {}, {"square": "tenant_one"}, 1)
        b = pool.submit(executor._dispatch, "probe_context", {}, {"square": "tenant_two"}, 2)
        assert a.result() == {"tenant": 1, "credentials": {"square": "tenant_one"}}
        assert b.result() == {"tenant": 2, "credentials": {"square": "tenant_two"}}
    assert executor.current_tenant_id == 1 and executor.current_credentials == {}


def test_legacy_counter_holds_share_stock_and_release_on_both_backends(site):
    result = site["client"].post(
        "/api/shop/holds",
        json={"sku": "BK-SITE-1", "patron_name": "Example", "patron_email": "example@example.test"},
        headers=auth(ADMIN_TOKEN),
    )
    assert result.status_code == 200, result.text
    assert reserve(site)[0].status_code == 409
    release = site["client"].delete("/api/shop/holds/BK-SITE-1", headers=auth(ADMIN_TOKEN))
    assert release.status_code == 200, release.text
    assert reserve(site, checkout="after_counter")[0].status_code == 201
