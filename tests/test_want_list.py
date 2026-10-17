from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.http import create_app
from app.db.inventory import SQLiteInventoryStore
from app.db.want_list import (
    Collector,
    CollectorHold,
    SQLiteWantListStore,
    WantItem,
)
from app.domain.want_list import WantListMatcher
from app.review.executor import ApprovedActionExecutor
from tests.conftest import ADMIN_TOKEN, auth


def test_matcher_title_and_author_rules():
    matcher = WantListMatcher()

    # Title similarity
    assert matcher.title_similarity("Two Solitudes", "Two Solitudes") == 1.0
    assert matcher.title_similarity("Two Solitudes", "The Two Solitudes") >= 0.95
    assert matcher.title_similarity("Two Solitudes", "Two Solitudes: A Novel") >= 0.90
    assert matcher.title_similarity("Two Solitudes", "Barometer Rising") < 0.50

    # Author matching
    matched, score = matcher.author_matches("Hugh MacLennan", ["Hugh MacLennan"])
    assert matched is True
    assert score >= 0.9

    matched, score = matcher.author_matches("Hugh MacLennan", ["MacLennan, Hugh"])
    assert matched is True

    matched, score = matcher.author_matches("Hugh MacLennan", ["Robertson Davies"])
    assert matched is False


def test_matcher_constraints():
    matcher = WantListMatcher()
    collector = Collector(
        id="col-1",
        tenant_id=1,
        name="Dr. Arthur Pendelton",
        email="arthur@example.com",
        phone="(613) 555-0144",
    )
    want = WantItem(
        id="wnt-1",
        collector_id="col-1",
        tenant_id=1,
        title="Two Solitudes",
        author="Hugh MacLennan",
        max_price_cad=Decimal("150.00"),
        min_condition="Good",
        binding="Hardcover",
        status="active",
    )

    # 1. Matching candidate
    cand_ok = {
        "title": "Two Solitudes (First Edition)",
        "authors": ["Hugh MacLennan"],
        "price_cad": Decimal("125.00"),
        "condition": "Very Good",
        "format": "Hardcover",
    }
    res_ok = matcher.evaluate(want, collector, cand_ok)
    assert res_ok.matched is True
    assert res_ok.match_score > 0.8

    # 2. Over budget
    cand_expensive = {
        **cand_ok,
        "price_cad": Decimal("225.00"),
    }
    res_exp = matcher.evaluate(want, collector, cand_expensive)
    assert res_exp.matched is False
    assert any("exceeds collector budget" in r for r in res_exp.reasons)

    # 3. Below minimum condition
    cand_poor = {
        **cand_ok,
        "condition": "Poor",
    }
    res_poor = matcher.evaluate(want, collector, cand_poor)
    assert res_poor.matched is False
    assert any("below required" in r for r in res_poor.reasons)

    # 4. Incompatible binding
    cand_pb = {
        **cand_ok,
        "format": "Paperback",
    }
    res_pb = matcher.evaluate(want, collector, cand_pb)
    assert res_pb.matched is False
    assert any("Binding mismatch" in r for r in res_pb.reasons)


def test_sqlite_want_list_store():
    store = SQLiteWantListStore(":memory:")
    now = datetime.now(UTC)

    # Pre-seeded samples
    collectors = store.list_collectors(tenant_id=1)
    assert len(collectors) >= 3

    # Create collector
    store.create_collector(
        Collector(
            id="col-test",
            tenant_id=1,
            name="Alice Munro Collector",
            email="munro@example.com",
            phone="613-111-2222",
            notes="Wants early short story collections",
        )
    )
    assert store.get_collector("col-test", tenant_id=1) is not None

    # Create want
    store.create_want(
        WantItem(
            id="wnt-test",
            collector_id="col-test",
            tenant_id=1,
            title="Dance of the Happy Shades",
            author="Alice Munro",
            max_price_cad=Decimal("300.00"),
            min_condition="Very Good",
            binding="Hardcover",
        )
    )
    assert store.get_want("wnt-test", tenant_id=1) is not None
    wants = store.list_wants(tenant_id=1, collector_id="col-test")
    assert len(wants) == 1

    # Create hold
    store.create_hold(
        CollectorHold(
            id="hld-test",
            tenant_id=1,
            sku="BK-MUNRO-01",
            want_item_id="wnt-test",
            collector_id="col-test",
            held_at=now,
            expires_at=now + timedelta(hours=48),
            status="active",
            book_title="Dance of the Happy Shades",
            book_author="Alice Munro",
            collector_name="Alice Munro Collector",
            price_cad=Decimal("250.00"),
        )
    )
    assert store.get_hold("hld-test", tenant_id=1) is not None
    assert store.get_active_hold_for_sku("BK-MUNRO-01", tenant_id=1) is not None

    # Update hold
    updated = store.update_hold("hld-test", tenant_id=1, status="claimed")
    assert updated.status == "claimed"
    assert store.get_active_hold_for_sku("BK-MUNRO-01", tenant_id=1) is None


def test_intake_interception_courtesy_hold():
    want_store = SQLiteWantListStore(":memory:")
    inv_store = SQLiteInventoryStore(":memory:")

    class MockTikTok:
        pass

    executor = ApprovedActionExecutor(
        client=MockTikTok(),
        inventory_store=inv_store,
        want_list_store=want_store,
    )

    # Book that matches Dr. Arthur Pendelton's seeded want for "Two Solitudes"
    payload = {
        "section": "Canadiana",
        "shelf": "Rare-1",
        "valuation": {
            "channel_strategy": "multi_channel_antiquarian",
            "in_store_price_cad": "150.00",
            "online_price_cad": "165.00",
            "condition_grade": "Very Good",
        },
        "extraction": {
            "title": "Two Solitudes",
            "authors": ["Hugh MacLennan"],
            "format": "hardcover_with_jacket",
        },
        "disambiguation": {
            "best_candidate": {
                "title": "Two Solitudes",
                "authors": ["Hugh MacLennan"],
            }
        },
    }

    result = executor.handlers["intake_book"](payload)

    # Assert priority courtesy hold was triggered!
    assert result["status"] == "on_hold"
    assert "hold_id" in result
    assert result["collector_name"] == "Dr. Arthur Pendelton"
    assert "Two Solitudes" in result["details"]["title"]

    # Marketplaces withheld
    mr = result["marketplace_results"]
    assert mr["abebooks"]["status"] == "withheld"
    assert mr["ebay"]["status"] == "withheld"

    # Inventory stored as on_hold
    sku = result["sku"]
    inv_item = inv_store.get(sku, tenant_id=1)
    assert inv_item is not None
    assert inv_item.status == "on_hold"

    # Hold record active
    hold = want_store.get_hold(result["hold_id"], tenant_id=1)
    assert hold is not None
    assert hold.status == "active"
    assert (hold.expires_at - hold.held_at).total_seconds() == 48 * 3600


def test_claim_and_release_courtesy_hold():
    want_store = SQLiteWantListStore(":memory:")
    inv_store = SQLiteInventoryStore(":memory:")

    class MockTikTok:
        pass

    executor = ApprovedActionExecutor(
        client=MockTikTok(),
        inventory_store=inv_store,
        want_list_store=want_store,
    )

    # 1. Intake book to create hold
    payload = {
        "valuation": {
            "channel_strategy": "multi_channel_antiquarian",
            "online_price_cad": "110.00",
            "condition_grade": "Very Good",
        },
        "extraction": {
            "title": "Fifth Business",
            "authors": ["Robertson Davies"],
            "format": "hardcover_with_jacket",
        },
        "disambiguation": {
            "best_candidate": {
                "title": "Fifth Business",
                "authors": ["Robertson Davies"],
            }
        },
    }
    intake_res = executor.handlers["intake_book"](payload)
    hold_id = intake_res["hold_id"]
    sku = intake_res["sku"]

    # 2. Claim hold (sold to collector)
    claim_res = executor.handlers["claim_collector_hold"](
        {
            "hold_id": hold_id,
            "tenant_id": 1,
            "notes": "Paid by cash at shop counter",
        }
    )
    assert claim_res["status"] == "claimed"

    # Check inventory is delisted / sold
    inv_item = inv_store.get(sku, tenant_id=1)
    assert inv_item.status == "delisted"

    # Check hold is marked claimed
    hold = want_store.get_hold(hold_id, tenant_id=1)
    assert hold.status == "claimed"

    # Check want is fulfilled
    want = want_store.get_want(hold.want_item_id, tenant_id=1)
    assert want.status == "fulfilled"


def test_http_collector_crm_api(settings):
    app = create_app(settings=settings)
    client = TestClient(app)
    headers = auth(ADMIN_TOKEN)

    # 1. GET /collectors
    res = client.get("/collectors", headers=headers)
    assert res.status_code == 200
    collectors = res.json()
    assert len(collectors) >= 3

    # 2. POST /collectors
    new_col = {
        "name": "Margaret Atwood Society",
        "email": "atwood@example.org",
        "phone": "(613) 555-9988",
        "notes": "Collecting signed early poetry chapbooks",
    }
    res_c = client.post("/collectors", json=new_col, headers=headers)
    assert res_c.status_code == 200
    created_col = res_c.json()
    col_id = created_col["id"]
    assert created_col["name"] == new_col["name"]

    # 3. POST /collectors/{id}/wants
    new_want = {
        "title": "The Left Hand of Darkness",
        "author": "Ursula K. Le Guin",
        "max_price_cad": 450.00,
        "min_condition": "Good",
        "binding": "Hardcover",
        "keywords": "Walker 1969 first edition",
    }
    res_w = client.post(f"/collectors/{col_id}/wants", json=new_want, headers=headers)
    assert res_w.status_code == 200
    created_want = res_w.json()
    assert created_want["title"] == "The Left Hand of Darkness"

    # 4. GET /wants
    res_wants = client.get("/wants", headers=headers)
    assert res_wants.status_code == 200
    all_wants = res_wants.json()
    assert any(w["title"] == "The Left Hand of Darkness" for w in all_wants)

    # 5. GET /holds
    res_h = client.get("/holds", headers=headers)
    assert res_h.status_code == 200
    holds = res_h.json()
    assert isinstance(holds, list)

    # 6. Sample intake triggers courtesy hold proposal
    sample_res = client.post("/intake/sample", headers=headers)
    assert sample_res.status_code == 200
    proposals = sample_res.json()["proposals"]
    # Check proposals returned by /intake/sample
    assert len(proposals) == 3
    # The 3rd book is "The Left Hand of Darkness"
    target_p = proposals[-1]
    rev_detail = client.get(f"/reviews/{target_p['review_id']}", headers=headers).json()
    assert "The Left Hand of Darkness" in str(rev_detail.get("payload", {}))
    assert "MATCHES COLLECTOR WANT" in rev_detail["payload"]["disambiguation"]["triage_reason"]
    assert rev_detail["payload"]["want_match"]["matched"] is True

    # Approve and execute the proposal!
    dec_res = client.post(
        f"/reviews/{target_p['review_id']}/decision",
        json={"approve": True, "reason": "Authorized courtesy hold for Margaret Atwood Society"},
        headers=headers,
    )
    assert dec_res.status_code == 200
    exec_res = client.post(f"/reviews/{target_p['review_id']}/execute", headers=headers)
    assert exec_res.status_code == 200
    exec_outcome = exec_res.json()["result"]
    assert exec_outcome["status"] == "on_hold"
    hold_id = exec_outcome["hold_id"]

    # 7. Check /holds shows the new active hold with 48h countdown
    holds_after = client.get("/holds", headers=headers).json()
    active_hold = next((h for h in holds_after if h["id"] == hold_id), None)
    assert active_hold is not None
    assert active_hold["status"] == "active"
    assert active_hold["is_expired"] is False
    assert active_hold["remaining_hours"] > 40.0

    # 8. Extend hold
    ext_res = client.post(f"/holds/{hold_id}/extend", json={"hours": 24}, headers=headers)
    assert ext_res.status_code == 200
    assert ext_res.json()["status"] == "extended"

    # 9. Claim hold
    claim_res = client.post(f"/holds/{hold_id}/claim", json={"notes": "Collector confirmed via phone"}, headers=headers)
    assert claim_res.status_code == 200
    assert claim_res.json()["status"] == "claimed"
