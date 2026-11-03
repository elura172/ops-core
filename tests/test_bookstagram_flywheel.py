from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api.http import create_app
from app.auth import create_access_token
from app.config import Settings
from app.db.inventory import InventoryItem, SQLiteInventoryStore
from app.db.review_store import SQLiteReviewStore
from app.db.users import SQLiteUserStore
from app.db.want_list import SQLiteWantListStore
from app.domain.bookstagram import BookstagramDropGenerator


def test_bookstagram_drop_generator_first_edition_and_captions():
    now = datetime.now(UTC)
    item = InventoryItem(
        sku="BK-DUNE-1965",
        title="Dune (First Edition)",
        authors=["Frank Herbert"],
        format="hardcover_with_jacket",
        condition="Near Fine",
        in_store_price_cad=Decimal("750.00"),
        online_price_cad=Decimal("850.00"),
        status="active",
        created_at=now,
        updated_at=now,
        section="Sci-Fi / Rare",
        shelf="Vault-01",
        condition_notes="First Edition stated on copyright page; unclipped $5.95 Chilton jacket.",
        publisher="Chilton Books",
        year=1965,
    )

    drop = BookstagramDropGenerator.generate(
        item,
        shop_name="Elura Antiquarian Vault",
        instagram_handle="elura.antiquarian",
        affiliate_code="elura172",
    )

    assert drop.sku == "BK-DUNE-1965"
    assert drop.edition_badge == "TRUE 1ST EDITION"
    assert drop.instagram_handle == "@elura.antiquarian"
    assert drop.affiliate_code == "elura172"
    assert "ref=elura172&sku=BK-DUNE-1965" in drop.referral_url
    assert set(drop.captions.keys()) == {"dark_academia", "vault_drop", "stack_haul"}
    assert "Chilton Books · 1965" in drop.captions["dark_academia"]
    assert "#bookstagram" in drop.hashtags
    assert "#firstedition" in drop.hashtags
    assert "#vintagescifi" in drop.hashtags
    assert drop.card_spec["aspect_ratio"] == "4:5"
    assert drop.card_spec["price_badge"] == "$850.00 CAD"


def test_bookstagram_drop_and_dm_claim_hold_endpoints(tmp_path, settings):
    review_db = str(tmp_path / "reviews.db")
    inv_db = str(tmp_path / "inventory.db")
    want_db = str(tmp_path / "wants.db")
    user_db = str(tmp_path / "users.db")

    inv_store = SQLiteInventoryStore(inv_db)
    want_store = SQLiteWantListStore(want_db)
    user_store = SQLiteUserStore(user_db)
    rev_store = SQLiteReviewStore(review_db)

    from app.api.operator import OperatorService
    from app.review.executor import ApprovedActionExecutor

    executor = ApprovedActionExecutor(
        client=object(),
        inventory_store=inv_store,
        want_list_store=want_store,
    )
    operator = OperatorService(rev_store, executor)

    test_settings = Settings(
        tts_app_key=settings.tts_app_key,
        tts_app_secret=settings.tts_app_secret,
        tts_access_token=settings.tts_access_token,
        tts_shop_id=settings.tts_shop_id,
        webhook_secret=settings.webhook_secret,
        tokens=settings.tokens,
        review_db=review_db,
        inventory_db=inv_db,
        want_list_db=want_db,
        user_db=user_db,
        dry_run=True,
    )

    now = datetime.now(UTC)
    inv_store.add(
        InventoryItem(
            sku="BK-LEGUIN-1969",
            title="The Left Hand of Darkness",
            authors=["Ursula K. Le Guin"],
            format="hardcover_with_jacket",
            condition="Very Good",
            in_store_price_cad=Decimal("120.00"),
            online_price_cad=Decimal("145.00"),
            status="active",
            created_at=now,
            updated_at=now,
            section="Sci-Fi",
            shelf="SF-01",
            publisher="Walker & Co.",
            year=1969,
        ),
        tenant_id=1,
    )

    app = create_app(
        operator=operator,
        settings=test_settings,
        inventory_store=inv_store,
        user_store=user_store,
    )
    client = TestClient(app)
    jwt_token = create_access_token({"sub": "operator@opscore.io", "tenant_id": 1, "role": "admin"})
    headers = {"Authorization": f"Bearer {jwt_token}"}

    # 1. Generate Bookstagram Drop
    res_drop = client.post(
        "/inventory/BK-LEGUIN-1969/bookstagram/drop",
        headers=headers,
        json={
            "shop_name": "Elura Antiquarian Vault",
            "instagram_handle": "@elura.vault",
            "affiliate_code": "elura172",
        },
    )
    assert res_drop.status_code == 200
    drop_data = res_drop.json()
    assert drop_data["sku"] == "BK-LEGUIN-1969"
    assert drop_data["price_cad"] == "145.00"
    assert "@elura.vault" in drop_data["captions"]["dark_academia"]
    assert "ref=elura172" in drop_data["referral_url"]

    # 2. Lock 48-hour Courtesy Hold from an Instagram DM claim
    res_hold = client.post(
        "/inventory/BK-LEGUIN-1969/bookstagram/claim-hold",
        headers=headers,
        json={
            "instagram_handle": "scifi_collector_ottawa",
            "collector_name": "lara_reads",
            "email": "lara@readergram.ca",
            "hours": 48,
        },
    )
    assert res_hold.status_code == 200
    hold_data = res_hold.json()
    assert hold_data["status"] == "held"
    assert hold_data["instagram_handle"] == "@scifi_collector_ottawa"
    assert hold_data["hours"] == 48

    # Verify inventory status flipped to on_hold
    updated_book = inv_store.get("BK-LEGUIN-1969", tenant_id=1)
    assert updated_book is not None
    assert updated_book.status == "on_hold"

    # Verify duplicate active hold is rejected with 409 Conflict
    res_dup = client.post(
        "/inventory/BK-LEGUIN-1969/bookstagram/claim-hold",
        headers=headers,
        json={"instagram_handle": "@second_buyer"},
    )
    assert res_dup.status_code == 409
