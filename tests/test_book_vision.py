from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.ai.book_vision import GeminiBookVisionProvider
from app.domain.books import (
    BookFormat,
    BookStackExtraction,
    SpineExtractionItem,
)


def test_book_format_enum_and_fallback():
    assert BookFormat.from_str("hardcover_with_jacket") == BookFormat.HARDCOVER_WITH_JACKET
    assert BookFormat.from_str("mass_market_paperback") == BookFormat.MASS_MARKET_PAPERBACK
    assert BookFormat.from_str("nonexistent_format") == BookFormat.UNKNOWN


def test_book_spine_item_from_and_to_dict():
    raw = {
        "stack_order": 1,
        "box_2d": [100, 200, 300, 400],
        "title": "Hyperion",
        "subtitle": "A Cantos Novel",
        "authors": ["Dan Simmons"],
        "publisher_imprint": "Doubleday",
        "format": "hardcover_with_jacket",
        "visible_condition_cues": ["clean_as_new"],
        "edition_or_series_markers": ["First Edition"],
        "raw_spine_text": "Hyperion Dan Simmons Doubleday",
        "confidence": "high",
    }
    item = SpineExtractionItem.from_dict(raw)
    assert item.title == "Hyperion"
    assert item.subtitle == "A Cantos Novel"
    assert item.authors == ("Dan Simmons",)
    assert item.box_2d == (100, 200, 300, 400)
    assert item.format == BookFormat.HARDCOVER_WITH_JACKET
    assert item.confidence == "high"

    # Round trip
    d = item.to_dict()
    assert d["title"] == "Hyperion"
    assert d["box_2d"] == [100, 200, 300, 400]
    assert d["format"] == "hardcover_with_jacket"


def test_book_stack_extraction_resilience():
    # Test with empty/sparse data
    empty_raw = {"books": [], "orientation": "vertical_shelf", "unidentified_spines_count": 2}
    extraction = BookStackExtraction.from_dict(empty_raw)
    assert len(extraction.books) == 0
    assert extraction.orientation == "vertical_shelf"
    assert extraction.unidentified_spines_count == 2


def test_gemini_provider_requires_image_bytes():
    provider = GeminiBookVisionProvider(api_key="fake-test-key")
    with pytest.raises(ValueError, match="image_bytes cannot be empty"):
        provider.extract_stack(b"")


def test_gemini_provider_parses_valid_response():
    mock_gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "books": [
                                        {
                                            "stack_order": 1,
                                            "box_2d": [10, 20, 100, 200],
                                            "title": "Snow Crash",
                                            "subtitle": None,
                                            "authors": ["Neal Stephenson"],
                                            "publisher_imprint": "Bantam Spectra",
                                            "format": "trade_paperback",
                                            "visible_condition_cues": ["clean_as_new"],
                                            "edition_or_series_markers": [],
                                            "raw_spine_text": "Snow Crash Neal Stephenson",
                                            "confidence": "high",
                                        }
                                    ],
                                    "orientation": "horizontal_stack",
                                    "unidentified_spines_count": 0,
                                }
                            )
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 350,
            "candidatesTokenCount": 120,
        },
    }

    provider = GeminiBookVisionProvider(api_key="fake-key")
    with patch.object(provider, "_post_with_retry", return_value=mock_gemini_payload):
        result = provider.extract_stack(b"fake-jpeg-bytes")
        assert len(result.books) == 1
        book = result.books[0]
        assert book.title == "Snow Crash"
        assert book.authors == ("Neal Stephenson",)
        assert book.format == BookFormat.TRADE_PAPERBACK
