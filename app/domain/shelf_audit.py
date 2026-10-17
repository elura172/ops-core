from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.db.inventory import InventoryItem
from app.discovery.books import compute_string_similarity


@dataclass(frozen=True)
class VerifiedShelfItem:
    sku: str
    title: str
    authors: list[str]
    scanned_title: str
    match_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "authors": self.authors,
            "scanned_title": self.scanned_title,
            "match_score": round(self.match_score, 2),
            "status": "verified",
        }


@dataclass(frozen=True)
class MissingShelfItem:
    sku: str
    title: str
    authors: list[str]
    in_store_price_cad: float
    online_price_cad: float
    section: str
    shelf: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "authors": self.authors,
            "in_store_price_cad": self.in_store_price_cad,
            "online_price_cad": self.online_price_cad,
            "section": self.section,
            "shelf": self.shelf,
            "status": "missing",
        }


@dataclass(frozen=True)
class MisplacedShelfItem:
    sku: str
    title: str
    authors: list[str]
    registered_section: str
    registered_shelf: str
    scanned_title: str
    match_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "authors": self.authors,
            "registered_section": self.registered_section,
            "registered_shelf": self.registered_shelf,
            "scanned_title": self.scanned_title,
            "match_score": round(self.match_score, 2),
            "status": "misplaced",
        }


@dataclass(frozen=True)
class UncatalogedShelfItem:
    title: str
    authors: list[str]
    raw_spine_text: str
    format: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": self.authors,
            "raw_spine_text": self.raw_spine_text,
            "format": self.format,
            "status": "uncataloged",
        }


@dataclass
class ShelfAuditResult:
    id: str
    section: str
    shelf: str
    total_expected: int
    total_scanned: int
    verified_items: list[VerifiedShelfItem] = field(default_factory=list)
    missing_items: list[MissingShelfItem] = field(default_factory=list)
    misplaced_items: list[MisplacedShelfItem] = field(default_factory=list)
    uncataloged_items: list[UncatalogedShelfItem] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def verified_count(self) -> int:
        return len(self.verified_items)

    @property
    def missing_count(self) -> int:
        return len(self.missing_items)

    @property
    def misplaced_count(self) -> int:
        return len(self.misplaced_items)

    @property
    def uncataloged_count(self) -> int:
        return len(self.uncataloged_items)

    @property
    def compliance_rate(self) -> float:
        if self.total_expected == 0:
            return 1.0 if not self.misplaced_items else 0.5
        return round(self.verified_count / self.total_expected, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "shelf": self.shelf,
            "total_expected": self.total_expected,
            "total_scanned": self.total_scanned,
            "verified_count": self.verified_count,
            "missing_count": self.missing_count,
            "misplaced_count": self.misplaced_count,
            "uncataloged_count": self.uncataloged_count,
            "compliance_rate": self.compliance_rate,
            "verified_items": [v.to_dict() for v in self.verified_items],
            "missing_items": [m.to_dict() for m in self.missing_items],
            "misplaced_items": [mp.to_dict() for mp in self.misplaced_items],
            "uncataloged_items": [u.to_dict() for u in self.uncataloged_items],
            "created_at": self.created_at.isoformat(),
        }


def _book_match_score(item: InventoryItem, scanned_title: str, scanned_authors: list[str]) -> float:
    t_score = compute_string_similarity(item.title, scanned_title)
    if not scanned_authors or not item.authors:
        return t_score
    item_auth = ", ".join(item.authors)
    scanned_auth = ", ".join(scanned_authors)
    a_score = compute_string_similarity(item_auth, scanned_auth)
    return max(t_score * 0.65 + a_score * 0.35, t_score)


class ShelfAuditService:
    """Reconciles physical spine extraction against bookshop catalog coordinates."""

    MATCH_THRESHOLD = 0.70

    @classmethod
    def reconcile(
        cls,
        section: str,
        shelf: str,
        scanned_books: list[dict[str, Any]],
        expected_books: list[InventoryItem],
        store_inventory: list[InventoryItem],
        audit_id: str | None = None,
    ) -> ShelfAuditResult:
        audit_id = audit_id or f"aud-{uuid.uuid4().hex[:12]}"

        verified: list[VerifiedShelfItem] = []
        matched_expected_skus: set[str] = set()
        matched_other_skus: set[str] = set()

        other_inventory = [
            b for b in store_inventory if b.status == "active" and (b.section != section or b.shelf != shelf)
        ]

        misplaced: list[MisplacedShelfItem] = []
        uncataloged: list[UncatalogedShelfItem] = []

        for scanned in scanned_books:
            s_title = str(scanned.get("title", "")).strip()
            s_authors = scanned.get("authors", [])
            if isinstance(s_authors, str):
                s_authors = [s_authors]
            s_fmt = str(scanned.get("format", "unknown"))
            s_raw = str(scanned.get("raw_spine_text", ""))

            # 1. Search expected books on this shelf
            best_expected: InventoryItem | None = None
            best_expected_score: float = 0.0

            for exp in expected_books:
                if exp.sku in matched_expected_skus:
                    continue
                score = _book_match_score(exp, s_title, s_authors)
                if score > best_expected_score:
                    best_expected_score = score
                    best_expected = exp

            if best_expected and best_expected_score >= cls.MATCH_THRESHOLD:
                matched_expected_skus.add(best_expected.sku)
                verified.append(
                    VerifiedShelfItem(
                        sku=best_expected.sku,
                        title=best_expected.title,
                        authors=best_expected.authors,
                        scanned_title=s_title,
                        match_score=best_expected_score,
                    )
                )
                continue

            # 2. Check if misplaced from another shelf in the store
            best_other: InventoryItem | None = None
            best_other_score: float = 0.0

            for other in other_inventory:
                if other.sku in matched_other_skus:
                    continue
                score = _book_match_score(other, s_title, s_authors)
                if score > best_other_score:
                    best_other_score = score
                    best_other = other

            if best_other and best_other_score >= cls.MATCH_THRESHOLD:
                matched_other_skus.add(best_other.sku)
                misplaced.append(
                    MisplacedShelfItem(
                        sku=best_other.sku,
                        title=best_other.title,
                        authors=best_other.authors,
                        registered_section=best_other.section,
                        registered_shelf=best_other.shelf,
                        scanned_title=s_title,
                        match_score=best_other_score,
                    )
                )
                continue

            # 3. Book is on shelf but uncataloged
            uncataloged.append(
                UncatalogedShelfItem(
                    title=s_title,
                    authors=s_authors,
                    raw_spine_text=s_raw,
                    format=s_fmt,
                )
            )

        # 4. Any expected books not seen are missing
        missing: list[MissingShelfItem] = []
        for exp in expected_books:
            if exp.sku not in matched_expected_skus:
                missing.append(
                    MissingShelfItem(
                        sku=exp.sku,
                        title=exp.title,
                        authors=exp.authors,
                        in_store_price_cad=float(exp.in_store_price_cad),
                        online_price_cad=float(exp.online_price_cad),
                        section=exp.section,
                        shelf=exp.shelf,
                    )
                )

        return ShelfAuditResult(
            id=audit_id,
            section=section,
            shelf=shelf,
            total_expected=len(expected_books),
            total_scanned=len(scanned_books),
            verified_items=verified,
            missing_items=missing,
            misplaced_items=misplaced,
            uncataloged_items=uncataloged,
        )
