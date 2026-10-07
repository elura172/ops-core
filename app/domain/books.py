from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class BookFormat(StrEnum):
    HARDCOVER_WITH_JACKET = "hardcover_with_jacket"
    HARDCOVER_NO_JACKET = "hardcover_no_jacket"
    TRADE_PAPERBACK = "trade_paperback"
    MASS_MARKET_PAPERBACK = "mass_market_paperback"
    SPIRAL_OR_OTHER = "spiral_or_other"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, val: str) -> BookFormat:
        try:
            return cls(val)
        except ValueError:
            return cls.UNKNOWN


class BookConditionGrade(StrEnum):
    """Standard antiquarian and used book grading terms (ILAB / ABAA conventions)."""

    AS_NEW = "As New"
    FINE = "Fine"
    NEAR_FINE = "Near Fine"
    VERY_GOOD = "Very Good"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"


@dataclass(frozen=True)
class SpineExtractionItem:
    """A single book spine identified and extracted from a stack or shelf image."""

    stack_order: int
    box_2d: tuple[int, int, int, int]  # [ymin, xmin, ymax, xmax] normalized 0..1000
    title: str
    subtitle: str | None = None
    authors: tuple[str, ...] = ()
    publisher_imprint: str | None = None
    format: BookFormat = BookFormat.UNKNOWN
    visible_condition_cues: tuple[str, ...] = ()
    edition_or_series_markers: tuple[str, ...] = ()
    raw_spine_text: str = ""
    confidence: str = "medium"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpineExtractionItem:
        raw_box = list(data.get("box_2d") or [])
        while len(raw_box) < 4:
            raw_box.append(0)
        box = (
            int(raw_box[0]),
            int(raw_box[1]),
            int(raw_box[2]),
            int(raw_box[3]),
        )
        return cls(
            stack_order=int(data.get("stack_order", 1)),
            box_2d=box,
            title=str(data.get("title", "")).strip(),
            subtitle=str(data["subtitle"]).strip() if data.get("subtitle") else None,
            authors=tuple(str(a).strip() for a in data.get("authors", []) if str(a).strip()),
            publisher_imprint=str(data["publisher_imprint"]).strip()
            if data.get("publisher_imprint")
            else None,
            format=BookFormat.from_str(str(data.get("format", "unknown"))),
            visible_condition_cues=tuple(
                str(c).strip() for c in data.get("visible_condition_cues", []) if str(c).strip()
            ),
            edition_or_series_markers=tuple(
                str(m).strip() for m in data.get("edition_or_series_markers", []) if str(m).strip()
            ),
            raw_spine_text=str(data.get("raw_spine_text", "")).strip(),
            confidence=str(data.get("confidence", "medium")).lower(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stack_order": self.stack_order,
            "box_2d": list(self.box_2d),
            "title": self.title,
            "subtitle": self.subtitle,
            "authors": list(self.authors),
            "publisher_imprint": self.publisher_imprint,
            "format": self.format.value,
            "visible_condition_cues": list(self.visible_condition_cues),
            "edition_or_series_markers": list(self.edition_or_series_markers),
            "raw_spine_text": self.raw_spine_text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class BookStackExtraction:
    """Full extraction result from an image of a pile or shelf of books."""

    books: tuple[SpineExtractionItem, ...]
    orientation: str = "horizontal_stack"
    unidentified_spines_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BookStackExtraction:
        items = tuple(
            SpineExtractionItem.from_dict(b)
            for b in data.get("books", [])
            if isinstance(b, dict)
        )
        return cls(
            books=items,
            orientation=str(data.get("orientation", "horizontal_stack")),
            unidentified_spines_count=int(data.get("unidentified_spines_count", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "books": [b.to_dict() for b in self.books],
            "orientation": self.orientation,
            "unidentified_spines_count": self.unidentified_spines_count,
        }


@dataclass(frozen=True)
class DisambiguationCandidate:
    """Bibliographic work record matched from a canonical catalog (e.g. Open Library)."""

    work_id: str
    title: str
    authors: tuple[str, ...]
    first_publish_year: int | None = None
    publishers: tuple[str, ...] = ()
    isbns: tuple[str, ...] = ()
    cover_id: int | None = None
    edition_count: int = 1
    match_score: float = 0.0

    @property
    def primary_isbn(self) -> str | None:
        """Best ISBN (prefers 13-digit ISBN-13 over 10-digit)."""
        if not self.isbns:
            return None
        # Sort by length descending, then canonical representation
        return sorted(self.isbns, key=lambda x: (len(x), x), reverse=True)[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "authors": list(self.authors),
            "first_publish_year": self.first_publish_year,
            "publishers": list(self.publishers),
            "isbns": list(self.isbns),
            "primary_isbn": self.primary_isbn,
            "cover_id": self.cover_id,
            "edition_count": self.edition_count,
            "match_score": round(self.match_score, 3),
        }


@dataclass(frozen=True)
class DisambiguationResult:
    """Disambiguation and domain triage resolution for an extracted book spine."""

    spine_item: SpineExtractionItem
    matched: bool
    best_candidate: DisambiguationCandidate | None = None
    alternate_candidates: tuple[DisambiguationCandidate, ...] = ()
    detected_isbn: str | None = None
    needs_copyright_check: bool = False
    triage_reason: str = ""
    # Actions: "auto_list" | "inspect_edition_points" | "manual_lookup"
    suggested_catalog_action: str = "auto_list"

    def to_dict(self) -> dict[str, Any]:
        return {
            "spine_item": self.spine_item.to_dict(),
            "matched": self.matched,
            "best_candidate": self.best_candidate.to_dict() if self.best_candidate else None,
            "alternate_candidates": [c.to_dict() for c in self.alternate_candidates],
            "detected_isbn": self.detected_isbn,
            "needs_copyright_check": self.needs_copyright_check,
            "triage_reason": self.triage_reason,
            "suggested_catalog_action": self.suggested_catalog_action,
        }


@dataclass(frozen=True)
class MarketComp:
    """An individual marketplace comparative price point."""

    source: str  # "abebooks", "ebay_sold", "amazon_used", "heuristic"
    item_price_cad: Decimal
    shipping_price_cad: Decimal = Decimal("0.00")
    total_price_cad: Decimal = Decimal("0.00")
    original_currency: str = "CAD"
    condition_reported: str = "Good"
    listing_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "item_price_cad": str(self.item_price_cad),
            "shipping_price_cad": str(self.shipping_price_cad),
            "total_price_cad": str(self.total_price_cad),
            "original_currency": self.original_currency,
            "condition_reported": self.condition_reported,
            "listing_url": self.listing_url,
        }


@dataclass(frozen=True)
class BookValuation:
    """Pricing recommendation and channel strategy for Ottawa used bookstore intake."""

    in_store_price_cad: Decimal
    online_price_cad: Decimal
    floor_price_cad: Decimal
    condition_grade: BookConditionGrade
    # "in_store_and_online" | "online_only_collectible" | "bargain_bin_cart"
    channel_strategy: str
    price_confidence: str  # "high" | "medium" | "low"
    pricing_rationale: str
    comps: tuple[MarketComp, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_store_price_cad": str(self.in_store_price_cad),
            "online_price_cad": str(self.online_price_cad),
            "floor_price_cad": str(self.floor_price_cad),
            "condition_grade": self.condition_grade.value,
            "channel_strategy": self.channel_strategy,
            "price_confidence": self.price_confidence,
            "pricing_rationale": self.pricing_rationale,
            "comps": [c.to_dict() for c in self.comps],
        }


