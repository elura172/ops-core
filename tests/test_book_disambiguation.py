from __future__ import annotations

from unittest.mock import patch

from app.discovery.books import (
    OpenLibraryMetadataResolver,
    clean_isbn,
    compute_string_similarity,
    extract_potential_isbn,
    score_candidate,
)
from app.domain.books import (
    BookFormat,
    DisambiguationCandidate,
    SpineExtractionItem,
)


def test_clean_and_extract_isbn():
    assert clean_isbn("0-441-56959-5") == "0441569595"
    assert clean_isbn("978-0-441-01203-9") == "9780441012039"

    raw_text = "Ace SF 0-441-56959-5 Neuromancer"
    detected = extract_potential_isbn(raw_text)
    assert detected == "0441569595"

    assert extract_potential_isbn("No numbers here") is None


def test_compute_string_similarity():
    # Exact match
    assert compute_string_similarity("Dune", "Dune") == 1.0
    # Case insensitive
    assert compute_string_similarity("dune", "DUNE") == 1.0
    # Minor differences
    score = compute_string_similarity("The Left Hand of Darkness", "Left Hand of Darkness")
    assert score > 0.80
    # Completely different
    assert compute_string_similarity("Dune", "Neuromancer") < 0.30


def test_score_candidate_with_exact_isbn():
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Neuromancer",
        authors=("William Gibson",),
        format=BookFormat.MASS_MARKET_PAPERBACK,
        raw_spine_text="0-441-56959-5",
    )
    cand = DisambiguationCandidate(
        work_id="OL27258W",
        title="Neuromancer",
        authors=("William Gibson",),
        isbns=("0441569595", "9780441012039"),
    )
    # Direct ISBN match must return 1.0
    assert score_candidate(cand, spine, detected_isbn="0441569595") == 1.0


def test_triage_rules_for_hardcover_and_first_edition():
    resolver = OpenLibraryMetadataResolver()

    # Case 1: Hardcover from 1965 (vintage classic)
    spine_dune = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Dune",
        authors=("Frank Herbert",),
        format=BookFormat.HARDCOVER_WITH_JACKET,
        edition_or_series_markers=("First Edition",),
    )
    cand_dune = DisambiguationCandidate(
        work_id="OL893414W",
        title="Dune",
        authors=("Frank Herbert",),
        first_publish_year=1965,
        match_score=0.95,
    )
    needs_check, reason, action = resolver._evaluate_triage(spine_dune, cand_dune, None)
    assert needs_check is True
    assert action == "inspect_edition_points"
    assert "First Edition marker detected" in reason

    # Case 2: Standard modern paperback
    spine_pb = SpineExtractionItem(
        stack_order=2,
        box_2d=(0, 0, 100, 100),
        title="Neuromancer",
        authors=("William Gibson",),
        format=BookFormat.MASS_MARKET_PAPERBACK,
    )
    cand_pb = DisambiguationCandidate(
        work_id="OL27258W",
        title="Neuromancer",
        authors=("William Gibson",),
        first_publish_year=1984,
        match_score=0.85,
    )
    needs_check, reason, action = resolver._evaluate_triage(spine_pb, cand_pb, "0441569595")
    assert needs_check is False
    assert action == "auto_list"


def test_resolver_mocked_fetch():
    mock_docs = [
        {
            "key": "/works/OL27258W",
            "title": "Neuromancer",
            "author_name": ["William Gibson"],
            "first_publish_year": 1984,
            "publisher": ["Ace Books", "Ace"],
            "isbn": ["0441569595"],
            "cover_i": 283860,
            "edition_count": 65,
        }
    ]

    resolver = OpenLibraryMetadataResolver()
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Neuromancer",
        authors=("William Gibson",),
        publisher_imprint="Ace",
        format=BookFormat.MASS_MARKET_PAPERBACK,
        raw_spine_text="Ace 0-441-56959-5 Neuromancer",
    )

    with patch.object(resolver, "_fetch_search_docs", return_value=mock_docs):
        res = resolver.resolve(spine)
        assert res.matched is True
        assert res.best_candidate is not None
        assert res.best_candidate.work_id == "OL27258W"
        assert res.detected_isbn == "0441569595"
        assert res.needs_copyright_check is False
        assert res.suggested_catalog_action == "auto_list"


def test_resolver_infers_author_for_uncredited_spine():
    mock_docs = [
        {
            "key": "/works/OL12345W",
            "title": "Total Consecration",
            "author_name": ["St. Louis de Montfort"],
            "first_publish_year": 1990,
            "publisher": ["TAN Books", "TAN"],
            "isbn": ["9780895556592"],
            "edition_count": 12,
        }
    ]

    resolver = OpenLibraryMetadataResolver()
    spine = SpineExtractionItem(
        stack_order=1,
        box_2d=(0, 0, 100, 100),
        title="Total Consecration",
        authors=(),
        publisher_imprint="TAN",
        format=BookFormat.TRADE_PAPERBACK,
        raw_spine_text="TOTAL CONSECRATION TAN",
    )

    with patch.object(resolver, "_fetch_search_docs", return_value=mock_docs):
        res = resolver.resolve(spine)
        assert res.matched is True
        assert res.best_candidate is not None
        assert res.best_candidate.authors == ("St. Louis de Montfort",)
