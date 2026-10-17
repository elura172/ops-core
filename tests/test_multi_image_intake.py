from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.domain.books import BookFormat, BookStackExtraction, SpineExtractionItem
from app.domain.intake_batch import (
    BatchExtractionItem,
    BatchSpineDeduplicator,
    MultiAngleBookMerger,
)
from app.domain.order_store import OrderStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewQueue


@pytest.fixture
def dashboard_client(settings):
    queue = ReviewQueue()
    queue.submit(ReviewItem("draft", "create_product_draft", {"title": "<script>bad()</script>"}))
    operator = OperatorService(queue, ApprovedActionExecutor(TikTokClient("test", "test")))
    return TestClient(create_app(operator, OrderStore(), settings))


def test_batch_spine_deduplicator_exact_and_fuzzy_matches():
    item1 = SpineExtractionItem(
        stack_order=1,
        box_2d=(10, 10, 100, 100),
        title="The Left Hand of Darkness",
        subtitle=None,
        authors=("Ursula K. Le Guin",),
        publisher_imprint="Ace",
        format=BookFormat.MASS_MARKET_PAPERBACK,
        visible_condition_cues=("clean_as_new",),
        edition_or_series_markers=(),
        raw_spine_text="The Left Hand of Darkness Ace",
        confidence="high",
    )
    # Overlapping photo with slightly different title string, missing "The", different author initial format
    item2 = SpineExtractionItem(
        stack_order=2,
        box_2d=(20, 20, 120, 120),
        title="Left Hand of Darkness",
        subtitle=None,
        authors=("Ursula Le Guin",),
        publisher_imprint="Ace Books",
        format=BookFormat.MASS_MARKET_PAPERBACK,
        visible_condition_cues=("sunned_faded_spine",),
        edition_or_series_markers=("First Printing",),
        raw_spine_text="Left Hand of Darkness Ursula Le Guin",
        confidence="medium",
    )
    # Distinct book
    item3 = SpineExtractionItem(
        stack_order=3,
        box_2d=(30, 30, 130, 130),
        title="Neuromancer",
        subtitle=None,
        authors=("William Gibson",),
        publisher_imprint="Ace",
        format=BookFormat.MASS_MARKET_PAPERBACK,
        visible_condition_cues=(),
        edition_or_series_markers=(),
        raw_spine_text="Neuromancer William Gibson",
        confidence="high",
    )

    batch_items = [
        BatchExtractionItem(book=item1, source_image_index=0, source_filename="shelf_left.jpg"),
        BatchExtractionItem(book=item2, source_image_index=1, source_filename="shelf_center.jpg"),
        BatchExtractionItem(book=item3, source_image_index=1, source_filename="shelf_center.jpg"),
    ]

    kept, suppressed = BatchSpineDeduplicator.deduplicate(batch_items)

    assert len(kept) == 2
    assert len(suppressed) == 1

    # Suppressed entry points to the duplicate
    sup = suppressed[0]
    assert "Left Hand of Darkness" in sup.title
    assert sup.duplicate_image_filename == "shelf_center.jpg"
    assert sup.kept_image_filename == "shelf_left.jpg"

    # Kept item should have enriched metadata (merged condition cues and edition markers)
    kept_left_hand = next(k for k in kept if "Left Hand of Darkness" in k.book.title)
    assert "clean_as_new" in kept_left_hand.book.visible_condition_cues
    assert "sunned_faded_spine" in kept_left_hand.book.visible_condition_cues
    assert "First Printing" in kept_left_hand.book.edition_or_series_markers


def test_batch_spine_deduplicator_distinct_books_not_merged():
    dune = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 10, 10),
        title="Dune",
        authors=("Frank Herbert",),
        confidence="high",
    )
    dune_messiah = SpineExtractionItem(
        stack_order=2,
        box_2d=(0, 0, 10, 10),
        title="Dune Messiah",
        authors=("Frank Herbert",),
        confidence="high",
    )
    batch = [
        BatchExtractionItem(book=dune, source_image_index=0, source_filename="photo1.jpg"),
        BatchExtractionItem(book=dune_messiah, source_image_index=1, source_filename="photo2.jpg"),
    ]
    kept, suppressed = BatchSpineDeduplicator.deduplicate(batch)
    assert len(kept) == 2
    assert len(suppressed) == 0


def test_multi_angle_book_merger_synthesizes_views():
    # View 1: Spine shot (has raw spine text, imprint, wear)
    spine_view = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Hyperion",
        subtitle=None,
        authors=("Dan Simmons",),
        publisher_imprint="Doubleday",
        format=BookFormat.HARDCOVER_NO_JACKET,
        visible_condition_cues=("bumped_headcap_or_tail",),
        edition_or_series_markers=(),
        raw_spine_text="HYPERION SIMMONS DOUBLEDAY",
        confidence="medium",
    )
    # View 2: Cover shot (reveals full title, jacket present, clean condition)
    cover_view = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Hyperion: A Cantos Novel",
        subtitle="A Cantos Novel",
        authors=("Dan Simmons",),
        publisher_imprint="Doubleday",
        format=BookFormat.HARDCOVER_WITH_JACKET,
        visible_condition_cues=("clean_as_new",),
        edition_or_series_markers=(),
        raw_spine_text="Hyperion Dan Simmons A Cantos Novel",
        confidence="high",
    )
    # View 3: Copyright page shot (reveals First Edition statement)
    copyright_view = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Hyperion",
        subtitle=None,
        authors=("Dan Simmons",),
        publisher_imprint="Doubleday Foundation",
        format=BookFormat.UNKNOWN,
        visible_condition_cues=(),
        edition_or_series_markers=("Stated First Edition", "Numberline 1 3 5 7 9 10 8 6 4 2"),
        raw_spine_text="Copyright 1989 by Dan Simmons First Edition",
        confidence="high",
    )

    merged = MultiAngleBookMerger.merge_angles([spine_view, cover_view, copyright_view])
    assert merged is not None
    assert merged.title == "Hyperion: A Cantos Novel"
    assert merged.subtitle == "A Cantos Novel"
    assert merged.format == BookFormat.HARDCOVER_WITH_JACKET
    assert "bumped_headcap_or_tail" in merged.visible_condition_cues
    assert "clean_as_new" in merged.visible_condition_cues
    assert "Stated First Edition" in merged.edition_or_series_markers
    assert merged.confidence == "high"


def test_api_multi_image_stack_deduplication(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    # Mock extract_stack returning overlapping results across 2 calls
    ext1 = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="Foundation",
                authors=("Isaac Asimov",),
                format=BookFormat.TRADE_PAPERBACK,
                visible_condition_cues=("clean_as_new",),
                confidence="high",
            ),
            SpineExtractionItem(
                stack_order=2,
                box_2d=(10, 10, 20, 20),
                title="Foundation and Empire",
                authors=("Isaac Asimov",),
                format=BookFormat.TRADE_PAPERBACK,
                visible_condition_cues=(),
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )
    # Photo 2 overlaps on "Foundation and Empire" and adds "Second Foundation"
    ext2 = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="Foundation & Empire",
                authors=("Isaac Asimov",),
                format=BookFormat.TRADE_PAPERBACK,
                visible_condition_cues=("sunned_faded_spine",),
                confidence="medium",
            ),
            SpineExtractionItem(
                stack_order=2,
                box_2d=(10, 10, 20, 20),
                title="Second Foundation",
                authors=("Isaac Asimov",),
                format=BookFormat.TRADE_PAPERBACK,
                visible_condition_cues=(),
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    mock_extract = MagicMock(side_effect=[ext1, ext2])

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch("app.ai.book_vision.GeminiBookVisionProvider.extract_stack", mock_extract),
    ):
        files = [
            ("files", ("stack_left.jpg", b"\xff\xd8\xff\xe0fake1", "image/jpeg")),
            ("files", ("stack_right.jpg", b"\xff\xd8\xff\xe0fake2", "image/jpeg")),
        ]
        resp = dashboard_client.post(
            "/intake/camera",
            files=files,
            data={"intake_mode": "multi_stack", "section": "Sci-Fi"},
            headers=reviewer,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_images"] == 2
        assert data["successful_images"] == 2
        assert len(data["failed_images"]) == 0
        # 3 unique books: Foundation, Foundation and Empire, Second Foundation
        assert data["processed"] == 3
        assert len(data["proposals"]) == 3
        # 1 duplicate suppressed
        assert len(data["duplicates_suppressed"]) == 1
        assert "Foundation" in data["duplicates_suppressed"][0]["title"]


def test_api_multi_image_partial_failure_tolerance(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    valid_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="Solaris",
                authors=("Stanislaw Lem",),
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    def side_effect_fn(bytes_val, mime):
        if b"corrupt" in bytes_val:
            raise RuntimeError("Corrupted image frame cannot be decoded")
        return valid_ext

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_stack",
            side_effect=side_effect_fn,
        ),
    ):
        files = [
            ("files", ("good_shelf.jpg", b"\xff\xd8\xff\xe0good", "image/jpeg")),
            ("files", ("corrupt_shelf.jpg", b"\xff\xd8\xff\xe0corrupt", "image/jpeg")),
        ]
        resp = dashboard_client.post(
            "/intake/camera",
            files=files,
            data={"intake_mode": "multi_stack"},
            headers=reviewer,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_images"] == 2
        assert data["successful_images"] == 1
        assert len(data["failed_images"]) == 1
        assert data["failed_images"][0]["filename"] == "corrupt_shelf.jpg"
        assert data["processed"] == 1
        assert len(data["proposals"]) == 1


def test_api_multi_angle_single_volume_mode(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    multi_angle_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 100, 100),
                title="The Fellowship of the Ring",
                subtitle=None,
                authors=("J.R.R. Tolkien",),
                publisher_imprint="George Allen & Unwin",
                format=BookFormat.HARDCOVER_WITH_JACKET,
                visible_condition_cues=("clean_as_new",),
                edition_or_series_markers=("First Edition, Second Impression",),
                raw_spine_text="The Fellowship of the Ring Tolkien Allen Unwin",
                confidence="high",
            ),
        ),
        orientation="mixed_or_scattered",
    )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_multi_angle",
            return_value=multi_angle_ext,
        ),
    ):
        files = [
            ("files", ("spine.jpg", b"\xff\xd8\xff\xe0spine", "image/jpeg")),
            ("files", ("cover.jpg", b"\xff\xd8\xff\xe0cover", "image/jpeg")),
            ("files", ("title_page.jpg", b"\xff\xd8\xff\xe0title", "image/jpeg")),
        ]
        resp = dashboard_client.post(
            "/intake/camera",
            files=files,
            data={"intake_mode": "multi_angle_single_book"},
            headers=reviewer,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["intake_mode"] == "multi_angle_single_book"
        assert data["total_images"] == 3
        assert len(data["proposals"]) == 1
        review_id = data["proposals"][0]["review_id"]
        detail_resp = dashboard_client.get(f"/reviews/{review_id}", headers=reviewer)
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert "Synthesized from 3 multi-angle photographs" in detail_data["payload"]["disambiguation"]["triage_reason"]


def test_api_single_image_backwards_compatibility(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    mock_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="Ubik",
                authors=("Philip K. Dick",),
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_stack",
            return_value=mock_ext,
        ),
    ):
        files = {"file": ("single_photo.jpg", b"\xff\xd8\xff\xe0data", "image/jpeg")}
        resp = dashboard_client.post("/intake/camera", files=files, headers=reviewer)
        assert resp.status_code == 200
        data = resp.json()
        assert data["processed"] == 1
        assert data["total_images"] == 1
        assert data["successful_images"] == 1
        assert len(data["proposals"]) == 1


def test_api_empty_upload_returns_400(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    with patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"):
        files = {"file": ("empty.jpg", b"", "image/jpeg")}
        resp = dashboard_client.post("/intake/camera", files=files, headers=reviewer)
        assert resp.status_code == 400


def test_api_multi_image_collector_want_interception(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    # Register a collector and active want
    c_resp = dashboard_client.post(
        "/collectors",
        json={"name": "Dr. Eleanor Vance", "phone": "555-0199", "notes": "Gothic & weird fiction"},
        headers=reviewer,
    )
    assert c_resp.status_code == 200
    c_id = c_resp.json()["id"]

    w_resp = dashboard_client.post(
        f"/collectors/{c_id}/wants",
        json={
            "title": "The Haunting of Hill House",
            "author": "Shirley Jackson",
            "max_price_cad": 250.0,
        },
        headers=reviewer,
    )
    assert w_resp.status_code == 200

    mock_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="The Haunting of Hill House",
                authors=("Shirley Jackson",),
                format=BookFormat.HARDCOVER_WITH_JACKET,
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_stack",
            return_value=mock_ext,
        ),
    ):
        files = [("files", ("stack1.jpg", b"\xff\xd8\xff\xe0data", "image/jpeg"))]
        resp = dashboard_client.post("/intake/camera", files=files, headers=reviewer)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["proposals"]) == 1

        review_id = data["proposals"][0]["review_id"]
        detail_resp = dashboard_client.get(f"/reviews/{review_id}", headers=reviewer)
        assert detail_resp.status_code == 200
        payload = detail_resp.json()["payload"]

        # Assert priority courtesy hold triggered
        assert "want_match" in payload
        assert payload["want_match"]["matched"] is True
        assert payload["want_match"]["collector_name"] == "Dr. Eleanor Vance"
        assert "MATCHES COLLECTOR WANT" in payload["disambiguation"]["triage_reason"]


def test_api_camera_auto_register_generates_sku_and_bookshelf_record(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    mock_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="Hyperion",
                authors=("Dan Simmons",),
                format=BookFormat.HARDCOVER_WITH_JACKET,
                visible_condition_cues=("clean_as_new",),
                confidence="high",
            ),
        ),
        orientation="horizontal_stack",
    )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake-key"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_stack",
            return_value=mock_ext,
        ),
    ):
        files = [("files", ("hyperion.jpg", b"\xff\xd8\xff\xe0data", "image/jpeg"))]
        resp = dashboard_client.post(
            "/intake/camera",
            files=files,
            data={"auto_register": "true", "section": "Sci-Fi", "shelf": "SF-04"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["auto_register"] is True
        proposal = data["proposals"][0]
        assert proposal["executed"] is True
        sku = proposal["sku"]
        assert sku.startswith("BK-")

        # Verify book now exists in inventory
        inv_resp = dashboard_client.get("/inventory", headers=reviewer)
        assert inv_resp.status_code == 200
        items = inv_resp.json()
        match = next((item for item in items if item["sku"] == sku), None)
        assert match is not None
        assert match["title"] == "Hyperion"
        assert match["section"] == "Sci-Fi"
        assert match["shelf"] == "SF-04"


def test_api_sample_dune_endpoint_and_want_matching(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    # Register collector want for Dune
    c_resp = dashboard_client.post(
        "/collectors",
        json={"name": "Paul Atreides", "phone": "555-0100", "notes": "Frank Herbert firsts"},
        headers=reviewer,
    )
    assert c_resp.status_code == 200
    c_id = c_resp.json()["id"]

    dashboard_client.post(
        f"/collectors/{c_id}/wants",
        json={"title": "Dune", "author": "Frank Herbert", "max_price_cad": 1000.0},
        headers=reviewer,
    )

    # Call sample Dune endpoint
    resp = dashboard_client.post(
        "/intake/sample-dune",
        data={"auto_register": "true", "section": "Vault", "shelf": "V-01"},
        headers=reviewer,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 1
    assert data["extracted_items"][0]["title"] == "Dune"
    payload = data["analyzed_payloads"][0]
    assert payload["issue_point_analysis"]["is_first_edition"] is True
    assert payload["issue_point_analysis"]["is_first_impression"] is True
    assert payload["want_match"]["matched"] is True
    assert payload["want_match"]["collector_name"] == "Paul Atreides"

    sku = data["proposals"][0]["sku"]
    assert sku.startswith("BK-")

    # Verify inventory record
    inv_resp = dashboard_client.get("/inventory", headers=reviewer)
    match = next((i for i in inv_resp.json() if i["sku"] == sku), None)
    assert match is not None
    assert match["title"] == "Dune"
    assert match["section"] == "Vault"


def test_api_intake_direct_endpoint(dashboard_client):
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    body = {
        "title": "Solaris",
        "authors": ["Stanislaw Lem"],
        "format": "hardcover_with_jacket",
        "condition": "Very Good",
        "section": "Sci-Fi",
        "shelf": "Shelf-9",
        "in_store_price_cad": 35.0,
        "online_price_cad": 45.0,
        "pricing_rationale": "Collectible Polish sci-fi in translation",
    }
    resp = dashboard_client.post("/inventory/intake-direct", json=body, headers=reviewer)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("EXECUTED", "approved")
    assert data["executed"] is True
    assert data["sku"].startswith("BK-")
