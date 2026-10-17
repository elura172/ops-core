from __future__ import annotations

import copy
import io
from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.domain.books import BookFormat, BookStackExtraction, SpineExtractionItem
from app.domain.intake_batch import BatchExtractionItem, BatchSpineDeduplicator
from app.domain.order_store import OrderStore
from app.domain.shelf_capture import create_book_reviews, plan_capture, validate_decisions
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue
from tests.conftest import ADMIN_TOKEN, REVIEWER_TOKEN, auth


def photo(color="white"):
    out = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(out, "JPEG")
    return out.getvalue()


def sighting(title, index, x, order=1, **kw):
    book = SpineExtractionItem(
        order,
        (100, x, 800, x + 80),
        title,
        authors=("Example Author",),
        format=BookFormat.TRADE_PAPERBACK,
        **kw,
    )
    return BatchExtractionItem(book, index, f"photo_{index + 1}.jpg")


def example_plan():
    observations = [
        sighting("A distant harbour", 0, 100),
        sighting("The silent garden", 0, 300),
        sighting("Mountain pathways", 0, 500),
        sighting("A distant harbour", 1, 100),
        sighting("The silent garden", 1, 300),
        sighting("Mountain pathways", 1, 500),
    ]
    return plan_capture(observations, {0: photo("white"), 1: photo("gray")})


def test_neighbour_anchors_and_geometry_suggest_overlap_without_losing_evidence():
    plan = example_plan()
    assert len(plan["observations"]) == 6 and len(plan["suggested_groups"]) == 3
    assert len(plan["overlap_hints"]) == 3
    assert all(o["crop"].startswith("data:image/jpeg;base64,") for o in plan["observations"])
    assert plan["copy_count_confirmed"] is False and plan["copy_groups"] == []


def test_dense_photo_is_flagged_without_dropping_its_sightings():
    items = [sighting(f"Book {i}", 0, i * 20, order=i + 1) for i in range(41)]
    plan = plan_capture(items, {0: photo()})
    assert len(plan["observations"]) == 41
    assert len(plan["dense_photo_warnings"]) == 1
    warning = plan["dense_photo_warnings"][0]
    assert warning["sighting_count"] == 41
    assert "20–30 books each" in warning["message"]
    assert plan_capture(items[:30], {0: photo()})["dense_photo_warnings"] == []


def test_title_match_alone_does_not_merge_or_suppress_a_copy():
    items = [sighting("The same title", 0, 100), sighting("The same title", 1, 100)]
    plan = plan_capture(items, {0: photo("white"), 1: photo("gray")})
    assert len(plan["suggested_groups"]) == 2
    assert plan["possible_overlap_count"] == 1 and not plan["overlap_hints"]


def test_two_real_copies_with_matching_title_in_one_photo_stay_separate():
    items = [sighting("Duplicate title", 0, 100), sighting("Duplicate title", 0, 300)]
    assert len(plan_capture(items, {0: photo()})["suggested_groups"]) == 2
    assert len(BatchSpineDeduplicator.deduplicate(items)[0]) == 2
    plan = plan_capture(items, {0: photo()})
    with pytest.raises(ValueError, match="separate physical copies"):
        validate_decisions(
            plan,
            {
                "copy_count_confirmed": True,
                "copy_groups": [["p1-b1", "p1-b2"]],
                "excluded_observations": [],
            },
        )


def test_identical_upload_with_matching_box_can_suggest_one_copy():
    raw = photo()
    plan = plan_capture([sighting("One copy", 0, 100), sighting("One copy", 1, 100)], {0: raw, 1: raw})
    assert plan["suggested_groups"] == [["p1-b1", "p2-b1"]]


def test_zoomed_closeup_maps_to_wide_view_using_neighbours():
    wide = [
        sighting(title, 0, x)
        for title, x in [
            ("A distant harbour", 100),
            ("The silent garden", 300),
            ("Mountain pathways", 500),
        ]
    ]
    close = [
        BatchExtractionItem(
            replace(
                item.book,
                box_2d=(
                    50,
                    int((item.book.box_2d[1] - 100) * 1.5),
                    950,
                    int((item.book.box_2d[3] - 100) * 1.5),
                ),
            ),
            1,
            "closeup.jpg",
        )
        for item in wide
    ]
    plan = plan_capture(wide + close, {0: photo("white"), 1: photo("gray")})
    assert len(plan["suggested_groups"]) == 3


def test_true_duplicate_title_copies_are_matched_one_to_one_with_spatial_anchors():
    titles = [
        "A distant harbour",
        "Repeated title",
        "Repeated title",
        "The silent garden",
        "Mountain pathways",
    ]
    items = [
        sighting(title, index, 50 + position * 170, order=position + 1)
        for index in (0, 1)
        for position, title in enumerate(titles)
    ]
    plan = plan_capture(items, {0: photo("white"), 1: photo("gray")})
    assert len(plan["suggested_groups"]) == 5
    assert all(len(group) == 2 for group in plan["suggested_groups"])


def test_conflicting_edition_or_invalid_boxes_are_not_merged():
    items = [
        sighting("One book", 0, 100, edition_or_series_markers=("First edition",)),
        sighting("One book", 1, 100, edition_or_series_markers=("Second edition",)),
    ]
    raw = photo()
    plan = plan_capture(items, {0: raw, 1: raw})
    assert len(plan["suggested_groups"]) == 2
    assert "different edition" in plan["possible_overlaps"][0]["reason"]
    invalid = [items[0], replace(items[1], book=replace(items[1].book, box_2d=(0, 0, 0, 0)))]
    assert len(plan_capture(invalid, {0: raw, 1: raw})["suggested_groups"]) == 2


def test_every_observation_needs_one_explicit_decision_and_parent_cannot_be_rewritten():
    plan = example_plan()
    for update in [
        None,
        {"copy_count_confirmed": False},
        {"copy_count_confirmed": True, "copy_groups": [["p1-b1"]]},
        {"copy_count_confirmed": True, "copy_groups": plan["suggested_groups"], "observations": []},
    ]:
        with pytest.raises(ValueError):
            validate_decisions(plan, update)
    decisions = {
        "copy_count_confirmed": True,
        "copy_groups": plan["suggested_groups"],
        "excluded_observations": [],
    }
    assert validate_decisions(plan, decisions) == decisions
    excluded = copy.deepcopy(decisions)
    excluded["copy_groups"] = []
    excluded["excluded_observations"] = [o["id"] for o in plan["observations"]]
    assert validate_decisions(plan, excluded)["copy_groups"] == []


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def environment(settings, request, tmp_path):
    if request.param == "sqlite":
        from app.db.review_store import SQLiteReviewStore

        queue = SQLiteReviewStore(tmp_path / "shelf-reviews.db")
    elif request.param == "postgres":
        queue = request.getfixturevalue("postgres_store")
    else:
        queue = ReviewQueue()
    operator = OperatorService(queue, ApprovedActionExecutor(TikTokClient("test", "test")))
    client = TestClient(create_app(operator, OrderStore(), settings))
    return client, operator


def capture(environment, auto_register="true", failed=False):
    client, operator = environment
    photos = [photo("white"), photo("gray")]
    items = [sighting("The same title", i, 100) for i in (0, 1)]

    indexes = iter(range(2))

    def extract(raw, mime):
        index = next(indexes)
        if failed and index == 1:
            raise RuntimeError("Unreadable photo")
        return BookStackExtraction(
            (items[index].book,),
            orientation="vertical_shelf",
            unidentified_spines_count=1 if index == 0 else 0,
        )

    with (
        patch("app.ai.book_vision.resolve_gemini_api_key", return_value="fake"),
        patch(
            "app.ai.book_vision.GeminiBookVisionProvider.extract_shelf_stack",
            side_effect=extract,
        ),
    ):
        response = client.post(
            "/intake/camera",
            files=[("files", (f"photo{i}.jpg", raw, "image/jpeg")) for i, raw in enumerate(photos)],
            data={"intake_mode": "shelf_capture", "auto_register": auto_register},
            headers=auth(ADMIN_TOKEN),
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_api_returns_one_persisted_draft_and_ignores_auto_registration(environment):
    client, operator = environment
    initial = {r.review_id for r in operator.reviews.list_items() if r.operation == "intake_book"}
    body = capture(environment)
    assert body["auto_register"] is False and body["auto_register_requested"] is True
    assert body["shelf_draft"]["observation_count"] == 2
    assert body["shelf_draft"]["suggested_copy_count"] == 2
    assert len(body["proposals"]) == 1 and body["duplicates_suppressed"] == []
    parent = operator.review_detail(body["shelf_draft"]["review_id"])
    assert parent["operation"] == "review_shelf_capture"
    assert parent["payload"]["photos"][0]["preview"].startswith("data:image/jpeg;base64,")
    assert all("analysis" in o for o in parent["payload"]["observations"])
    assert {r.review_id for r in operator.reviews.list_items() if r.operation == "intake_book"} == initial
    assert "The same title" not in [i.title for i in operator.executor.inventory_store.list_all()]


def test_approval_requires_copy_decisions_and_execution_only_creates_child_reviews(environment):
    client, operator = environment
    body = capture(environment)
    rid = body["shelf_draft"]["review_id"]
    assert (
        client.post(f"/reviews/{rid}/decision", json={"approve": True}, headers=auth(REVIEWER_TOKEN)).status_code == 409
    )
    groups = [["p1-b1", "p2-b1"]]
    update = {"copy_count_confirmed": True, "copy_groups": groups, "excluded_observations": []}
    assert (
        client.post(
            f"/reviews/{rid}/decision",
            json={"approve": True, "payload_update": update},
            headers=auth(REVIEWER_TOKEN),
        ).status_code
        == 200
    )
    result = client.post(f"/reviews/{rid}/execute", headers=auth(ADMIN_TOKEN))
    assert result.status_code == 200, result.text
    children = result.json()["result"]["review_ids"]
    assert len(children) == 1
    child = operator.review_detail(children[0])
    assert child["status"].value == "PENDING" and child["operation"] == "intake_book"
    assert len(child["payload"]["shelf_evidence"]["observations"]) == 2
    assert client.post(f"/reviews/{rid}/execute", headers=auth(ADMIN_TOKEN)).json()["replayed"] is True
    assert "The same title" not in [i.title for i in operator.executor.inventory_store.list_all()]
    assert create_book_reviews(operator.reviews, operator.review_detail(rid)["payload"], 1)["review_ids"] == children


def test_partial_photo_failure_and_unreadable_spines_are_visible(environment):
    body = capture(environment, failed=True)
    payload = environment[1].review_detail(body["shelf_draft"]["review_id"])["payload"]
    assert len(payload["failed_images"]) == 1 and payload["unidentified_spines"] == 1
    assert body["successful_images"] == 1 and body["shelf_draft"]["observation_count"] == 1


def test_shelf_draft_rejects_invented_observation_ids_and_other_tenant(environment):
    client, operator = environment
    body = capture(environment)
    rid = body["shelf_draft"]["review_id"]
    update = {
        "copy_count_confirmed": True,
        "copy_groups": [["invented"]],
        "excluded_observations": [],
    }
    assert (
        client.post(
            f"/reviews/{rid}/decision",
            json={"approve": True, "payload_update": update},
            headers=auth(REVIEWER_TOKEN),
        ).status_code
        == 409
    )
    assert operator.reviews.get(rid, tenant_id=2) is None


def test_children_take_details_from_chosen_closeup_and_keep_both_photo_boxes():
    plan = example_plan()
    plan["capture_id"] = "capture_test"
    for o in plan["observations"]:
        o["analysis"] = {
            "extraction": {"title": o["title"], "authors": o["authors"], "format": o["format"]},
            "valuation": {"online_price_cad": "24.00"},
        }
    plan["observations"][3]["subtitle"] = "Visible on the close-up"
    plan.update(copy_count_confirmed=True, copy_groups=plan["suggested_groups"])
    queue = ReviewQueue()
    result = create_book_reviews(queue, plan, 1)
    child = queue.get(result["review_ids"][0])
    assert child.payload["extraction"]["subtitle"] == "Visible on the close-up"
    assert len(child.payload["extraction"]["source_observations"]) == 2
