from __future__ import annotations

import pytest

from app.api.http import create_app
from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.domain.books import BookFormat, BookStackExtraction, SpineExtractionItem
from app.domain.issue_points import (
    BibliographicIssuePointDecoder,
    EditionClassification,
)
from app.domain.order_store import OrderStore
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewItem, ReviewQueue
from fastapi.testclient import TestClient


@pytest.fixture
def dashboard_client(settings):
    queue = ReviewQueue()
    queue.submit(ReviewItem("draft", "create_product_draft", {"title": "<script>bad()</script>"}))
    operator = OperatorService(queue, ApprovedActionExecutor(TikTokClient("test", "test")))
    return TestClient(create_app(operator, OrderStore(), settings))


def test_random_house_first_impression_syntax():
    # True Random House 1st impression (1970–2002): Stated "First Edition" with numberline ending or starting in 2
    raw_copy = """
    Copyright © 1982 by Anne Rice
    All rights reserved under International and Pan-American Copyright Conventions.
    Published in the United States by Alfred A. Knopf, Inc., New York,
    and simultaneously in Canada by Random House of Canada Limited, Toronto.
    Distributed by Random House, Inc., New York.

    First Edition
    2 4 6 8 9 7 5 3
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Random House",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert res.is_first_impression is True
    assert res.is_book_club is False
    assert res.impression_number == 1
    assert "Random House (1970–2002) Convention" in res.publisher_rule_name
    assert "presence of stated 'first edition'" in res.reasoning_trace.lower()
    assert res.pricing_tier == "collectible_first"


def test_random_house_second_impression_syntax():
    # Random House 2nd printing: "First Edition" statement was dropped!
    raw_copy = """
    Copyright © 1982 by Anne Rice
    Published by Random House, Inc., New York.
    2 3 4 5 6 7 8 9
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Random House",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.LATER_PRINTING
    assert res.is_first_edition is False
    assert res.is_first_impression is False
    assert res.impression_number == 2
    assert "Second or Later Impression" in res.publisher_rule_name


def test_scribners_classic_a_rule():
    raw_copy = """
    Copyright, 1925, by Charles Scribner's Sons
    Printed in the United States of America
    All rights reserved. No part of this book may be reproduced.
    A
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Charles Scribner's Sons",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert res.is_first_impression is True
    assert "Classic 'A' Convention" in res.publisher_rule_name


def test_doubleday_classic_first_edition():
    raw_copy = """
    Copyright © 1968 by Philip K. Dick
    All Rights Reserved
    Printed in the United States of America
    First Edition
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Doubleday & Company, Inc.",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert "Doubleday & Company" in res.publisher_rule_name


def test_viking_press_first_published_rule():
    raw_copy = """
    The Viking Press, Inc.
    First published in 1962 by The Viking Press, Inc.
    625 Madison Avenue, New York, N.Y. 10022
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="The Viking Press",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert "The Viking Press" in res.publisher_rule_name


def test_harper_date_code_convention():
    raw_copy = """
    Copyright © 1953 by Ray Bradbury
    First Edition
    M-C
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Harper & Brothers",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert "Harper" in res.publisher_rule_name


def test_standard_modern_numberline_staggered():
    # Staggered numberline terminating in 1 = 1st printing
    raw_copy = """
    Tor Books
    First Edition: October 1990
    1 3 5 7 9 10 8 6 4 2
    """
    res = BibliographicIssuePointDecoder.decode(
        publisher="Tor Books",
        raw_copyright_text=raw_copy,
    )
    assert res.classification == EditionClassification.FIRST_EDITION_FIRST_PRINTING
    assert res.is_first_edition is True
    assert res.impression_number == 1

    # Later printing terminating in 4 = 4th printing
    raw_later = """
    Tor Books
    4 5 6 7 8 9 10
    """
    res_later = BibliographicIssuePointDecoder.decode(
        publisher="Tor Books",
        raw_copyright_text=raw_later,
    )
    assert res_later.classification == EditionClassification.LATER_PRINTING
    assert res_later.is_first_edition is False
    assert res_later.impression_number == 4


def test_bce_disqualification_via_rear_board_blind_stamp():
    res = BibliographicIssuePointDecoder.decode(
        publisher="Doubleday",
        raw_copyright_text="First Edition",
        visible_condition_cues=("clean_as_new", "blind_stamp_rear_board"),
    )
    assert res.is_book_club is True
    assert res.classification == EditionClassification.BOOK_CLUB_EDITION
    assert res.is_first_edition is False
    assert res.pricing_tier == "reading_copy"
    assert any("blind debossed mark" in d.lower() for d in res.disqualification_flags)


def test_bce_disqualification_via_missing_flap_price():
    flap_copy = "A magnificent space epic of galactic intrigue and destiny that spans millennia of human history."
    res = BibliographicIssuePointDecoder.decode(
        publisher="Chilton Books",
        raw_copyright_text="First Edition",
        raw_flap_text=flap_copy,
    )
    assert res.is_book_club is True
    assert res.classification == EditionClassification.BOOK_CLUB_EDITION
    assert any("omits retail price" in d.lower() for d in res.disqualification_flags)


def test_api_intake_with_true_first_edition_issue_point_elevation(dashboard_client):
    from unittest.mock import patch
    from tests.test_dashboard import REVIEWER_TOKEN, auth

    reviewer = auth(REVIEWER_TOKEN)

    mock_ext = BookStackExtraction(
        books=(
            SpineExtractionItem(
                stack_order=1,
                box_2d=(0, 0, 10, 10),
                title="The Vampire Lestat",
                authors=("Anne Rice",),
                publisher_imprint="Random House",
                format=BookFormat.HARDCOVER_WITH_JACKET,
                visible_condition_cues=("clean_as_new",),
                edition_or_series_markers=("First Edition",),
                raw_spine_text="The Vampire Lestat Anne Rice Knopf",
                raw_copyright_text="First Edition\n2 4 6 8 9 7 5 3\nPublished by Random House",
                raw_flap_text="$17.95",
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
        files = [("files", ("stack.jpg", b"\xff\xd8\xff\xe0data", "image/jpeg"))]
        resp = dashboard_client.post(
            "/intake/camera",
            files=files,
            data={"intake_mode": "multi_stack"},
            headers=reviewer,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["proposals"]) == 1

        rev_id = data["proposals"][0]["review_id"]
        detail_resp = dashboard_client.get(f"/reviews/{rev_id}", headers=reviewer)
        assert detail_resp.status_code == 200
        payload = detail_resp.json()["payload"]

        # Assert Issue Point Analysis was executed and attached
        assert "issue_point_analysis" in payload
        analysis = payload["issue_point_analysis"]
        assert analysis["is_first_edition"] is True
        assert analysis["is_first_impression"] is True
        assert analysis["classification"] == "First Edition, First Printing"
        assert "Random House" in analysis["publisher_rule_name"]
        assert payload["valuation"]["channel_strategy"] == "antiquarian_first_edition_vault"
        assert "TRUE 1st EDITION" in payload["disambiguation"]["triage_reason"]
