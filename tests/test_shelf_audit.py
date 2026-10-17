from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.db.inventory import InventoryItem, ShelfAuditRecord, SQLiteInventoryStore
from app.domain.shelf_audit import ShelfAuditService


def test_shelf_audit_reconciliation_matching():
    now = datetime.now(UTC)

    # 3 expected books on Sci-Fi / B-03
    expected_books = [
        InventoryItem(
            "BK-DUNE",
            "Dune",
            ["Frank Herbert"],
            "hardcover",
            "Fine",
            Decimal("50.00"),
            Decimal("55.00"),
            "active",
            now,
            now,
            section="Sci-Fi / Fantasy",
            shelf="B-03",
        ),
        InventoryItem(
            "BK-NEURO",
            "Neuromancer",
            ["William Gibson"],
            "paperback",
            "Very Good",
            Decimal("20.00"),
            Decimal("22.00"),
            "active",
            now,
            now,
            section="Sci-Fi / Fantasy",
            shelf="B-03",
        ),
        InventoryItem(
            "BK-F451",
            "Fahrenheit 451",
            ["Ray Bradbury"],
            "paperback",
            "Good",
            Decimal("15.00"),
            Decimal("18.00"),
            "active",
            now,
            now,
            section="Sci-Fi / Fantasy",
            shelf="B-03",
        ),
    ]

    # Store inventory including an item from another shelf
    foundation = InventoryItem(
        "BK-FOUND",
        "Foundation",
        ["Isaac Asimov"],
        "hardcover",
        "Good",
        Decimal("40.00"),
        Decimal("45.00"),
        "active",
        now,
        now,
        section="Classics",
        shelf="C-01",
    )
    store_inventory = [*expected_books, foundation]

    # Scanned spines from photo:
    # 1. Dune (Expected -> Verified)
    # 2. Neuromancer (Expected -> Verified)
    # 3. Foundation (Expected on Classics C-01 -> Misplaced here!)
    # 4. Hyperion (Not in store -> Uncataloged)
    scanned = [
        {
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "raw_spine_text": "HERBERT DUNE CHILTON",
            "format": "hardcover",
        },
        {
            "title": "Neuromancer",
            "authors": ["William Gibson"],
            "raw_spine_text": "GIBSON NEUROMANCER ACE",
            "format": "trade_paperback",
        },
        {
            "title": "Foundation",
            "authors": ["Isaac Asimov"],
            "raw_spine_text": "FOUNDATION ASIMOV",
            "format": "hardcover",
        },
        {
            "title": "Hyperion",
            "authors": ["Dan Simmons"],
            "raw_spine_text": "HYPERION DAN SIMMONS",
            "format": "trade_paperback",
        },
    ]

    result = ShelfAuditService.reconcile(
        section="Sci-Fi / Fantasy",
        shelf="B-03",
        scanned_books=scanned,
        expected_books=expected_books,
        store_inventory=store_inventory,
    )

    assert result.total_expected == 3
    assert result.total_scanned == 4
    assert result.verified_count == 2
    assert {v.sku for v in result.verified_items} == {"BK-DUNE", "BK-NEURO"}

    assert result.missing_count == 1
    assert result.missing_items[0].sku == "BK-F451"

    assert result.misplaced_count == 1
    assert result.misplaced_items[0].sku == "BK-FOUND"
    assert result.misplaced_items[0].registered_section == "Classics"
    assert result.misplaced_items[0].registered_shelf == "C-01"

    assert result.uncataloged_count == 1
    assert result.uncataloged_items[0].title == "Hyperion"

    assert result.compliance_rate == 0.67


def test_sqlite_inventory_location_and_audits():
    store = SQLiteInventoryStore(":memory:")
    now = datetime.now(UTC)

    item = InventoryItem(
        sku="BK-TEST-1",
        title="Solaris",
        authors=["Stanislaw Lem"],
        format="paperback",
        condition="Very Good",
        in_store_price_cad=Decimal("18.00"),
        online_price_cad=Decimal("22.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi",
        shelf="S-01",
    )
    store.add(item, tenant_id=1)

    # List by location
    by_loc = store.list_by_location("Sci-Fi", "S-01", tenant_id=1)
    assert len(by_loc) == 1
    assert by_loc[0].title == "Solaris"

    # Update location
    store.update_location("BK-TEST-1", "Rare Books", "R-02", tenant_id=1)
    updated = store.get("BK-TEST-1", tenant_id=1)
    assert updated.section == "Rare Books"
    assert updated.shelf == "R-02"

    # Mark audited
    assert updated.last_audited_at is None
    store.mark_audited("BK-TEST-1", tenant_id=1)
    audited = store.get("BK-TEST-1", tenant_id=1)
    assert audited.last_audited_at is not None

    # Record shelf audit
    audit = ShelfAuditRecord(
        id="aud-123",
        tenant_id=1,
        section="Rare Books",
        shelf="R-02",
        audited_by="clerk@bookshop.com",
        total_expected=1,
        total_scanned=1,
        verified_count=1,
        missing_count=0,
        misplaced_count=0,
        discrepancies=[],
        created_at=now,
    )
    store.record_shelf_audit(audit)

    audits = store.list_shelf_audits(tenant_id=1)
    assert len(audits) == 1
    assert audits[0].id == "aud-123"
    assert audits[0].audited_by == "clerk@bookshop.com"


def test_migration_0007_discovery():
    from app.db.migrate import discover

    migrations = discover()
    assert len(migrations) >= 7
    mig7 = migrations[6]
    assert mig7.version == 7
    assert mig7.name == "shelf_auditing"
    assert "CREATE TABLE IF NOT EXISTS shelf_audits" in mig7.sql()


def test_shelf_audit_api_endpoint(settings):
    from fastapi.testclient import TestClient

    from app.api.http import create_app
    from app.domain.order_store import OrderStore
    from tests.conftest import ADMIN_TOKEN, auth

    app = create_app(order_repository=OrderStore(), settings=settings)
    client = TestClient(app)

    # 1. Audit shelf via /inventory/audit/direct
    resp = client.post(
        "/inventory/audit/direct",
        headers=auth(ADMIN_TOKEN),
        json={
            "section": "Sci-Fi / Fantasy",
            "shelf": "B-03",
            "scanned_books": [
                {
                    "title": "Dune (First Edition)",
                    "authors": ["Frank Herbert"],
                    "raw_spine_text": "DUNE HERBERT",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["section"] == "Sci-Fi / Fantasy"
    assert data["shelf"] == "B-03"
    assert data["verified_count"] >= 1
    assert data["verified_items"][0]["sku"] == "BK-DUNE-1965"

    # 2. Verify audit history via /inventory/audits
    hist_resp = client.get("/inventory/audits", headers=auth(ADMIN_TOKEN))
    assert hist_resp.status_code == 200
    hist = hist_resp.json()
    assert len(hist) >= 1
    assert hist[0]["section"] == "Sci-Fi / Fantasy"
    assert hist[0]["shelf"] == "B-03"
