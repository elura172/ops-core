from __future__ import annotations

import pytest

from app.domain.condition import (
    AbaaConditionGrade,
    AntiquarianConditionInspector,
    DefectSeverity,
)


def test_inspector_pristine_collector_copy():
    report = AntiquarianConditionInspector.inspect(
        format_str="hardcover_with_jacket",
        visible_cues=["clean_as_new"],
        raw_flap_text="$5.95 price intact on front flap",
        raw_spine_text="DUNE FRANK HERBERT CHILTON",
    )
    assert report.has_jacket is True
    assert report.book_grade in (AbaaConditionGrade.FINE, AbaaConditionGrade.NEAR_FINE)
    assert report.jacket_grade in (AbaaConditionGrade.FINE, AbaaConditionGrade.NEAR_FINE)
    assert report.unclipped_price == "$5.95"
    assert report.is_price_clipped is False
    assert len(report.defects) == 0
    assert len(report.virtues) >= 2
    assert "5.95" in report.trade_catalog_blurb
    assert report.valuation_multiplier >= 1.0


def test_inspector_dual_grade_with_defects():
    report = AntiquarianConditionInspector.inspect(
        format_str="hardcover_with_jacket",
        visible_cues=["bumped_headcap_or_tail", "torn_or_chipped_jacket", "price_clipped"],
        raw_flap_text="clipped front corner",
        raw_spine_text="THE HOBBIT TOLKIEN",
    )
    assert report.has_jacket is True
    # Book has bumped spine ends -> Near Fine
    assert report.book_grade == AbaaConditionGrade.NEAR_FINE
    # Jacket has chipping and is price clipped -> Very Good
    assert report.jacket_grade == AbaaConditionGrade.VERY_GOOD
    assert report.dual_grade_code == "NF / VG"
    assert report.is_price_clipped is True
    assert any(d.code == "chipped_jacket" for d in report.defects)
    assert any(d.code == "bumped_spine_ends" for d in report.defects)
    assert "price-clipped" in report.trade_catalog_blurb
    assert "chipping" in report.trade_catalog_blurb


def test_inspector_cocked_spine_and_cracked_hinge():
    report = AntiquarianConditionInspector.inspect(
        format_str="hardcover_no_jacket",
        visible_cues=["cocked_spine", "cracked_hinge"],
        raw_spine_text="MOBY DICK MELVILLE",
    )
    assert report.has_jacket is False
    assert report.jacket_grade is None
    assert report.book_grade == AbaaConditionGrade.GOOD
    assert report.dual_grade_code == "G (Without Jacket)"
    assert report.valuation_multiplier < 0.50
    assert "cocked out of square" in report.trade_catalog_blurb
    assert "hinge starting" in report.trade_catalog_blurb


def test_inspector_ex_library_severe_penalty():
    report = AntiquarianConditionInspector.inspect(
        format_str="hardcover_with_jacket",
        visible_cues=["ex_library_stickers_or_tape"],
        edition_or_series_markers=["Ex-Library discard"],
    )
    assert report.is_ex_library is True
    assert report.book_grade in (AbaaConditionGrade.GOOD, AbaaConditionGrade.FAIR)
    assert report.valuation_multiplier < 0.40
    assert "Ex-library copy" in report.trade_catalog_blurb


def test_inspector_signed_author_premium():
    report = AntiquarianConditionInspector.inspect(
        format_str="hardcover_with_jacket",
        visible_cues=["clean_as_new"],
        edition_or_series_markers=["Signed by Margaret Atwood"],
        raw_flap_text="$24.95 unclipped",
    )
    assert report.is_signed_or_inscribed is True
    assert report.valuation_multiplier > 1.50
    assert "signed by the author" in report.trade_catalog_blurb.lower()
