from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from app.domain.books import BookFormat, SpineExtractionItem


@dataclass(frozen=True)
class SuppressedDuplicate:
    """Record of a duplicate book detected and suppressed during batch intake."""

    title: str
    author: str
    duplicate_image_index: int
    duplicate_image_filename: str
    kept_image_index: int
    kept_image_filename: str
    similarity_score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "duplicate_image_index": self.duplicate_image_index,
            "duplicate_image_filename": self.duplicate_image_filename,
            "kept_image_index": self.kept_image_index,
            "kept_image_filename": self.kept_image_filename,
            "similarity_score": round(self.similarity_score, 3),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FailedImageRecord:
    """Record of an image that failed vision processing within a batch."""

    image_index: int
    filename: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_index": self.image_index,
            "filename": self.filename,
            "error": self.error,
        }


@dataclass(frozen=True)
class BatchExtractionItem:
    """A book spine item tagged with its source image provenance."""

    book: SpineExtractionItem
    source_image_index: int
    source_filename: str

    def to_dict(self) -> dict[str, Any]:
        d = self.book.to_dict()
        d["source_image_index"] = self.source_image_index
        d["source_filename"] = self.source_filename
        return d


class BatchSpineDeduplicator:
    """Detects and resolves duplicate book spines across multiple photos of stacks or shelves."""

    TITLE_SIMILARITY_THRESHOLD = 0.82

    @staticmethod
    def normalize_title(title: str) -> str:
        """Normalizes a title by lowercasing, replacing symbols, stripping articles and punctuation."""
        if not title:
            return ""
        s = title.strip().lower()
        s = s.replace("&", " and ")
        # Remove punctuation except letters and digits
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        # Strip leading articles
        for article in ("the ", "a ", "an "):
            if s.startswith(article):
                s = s[len(article) :].strip()
                break
        return s

    @staticmethod
    def normalize_author(author: str) -> str:
        """Normalizes an author name by lowercasing and stripping punctuation."""
        if not author:
            return ""
        s = author.strip().lower()
        s = re.sub(r"[^\w\s]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    @classmethod
    def title_similarity(cls, t1: str, t2: str) -> float:
        """Computes combined sequence and token similarity between two titles."""
        n1 = cls.normalize_title(t1)
        n2 = cls.normalize_title(t2)
        if not n1 or not n2:
            return 0.0
        if n1 == n2:
            return 1.0

        # Sequence matcher ratio
        seq_ratio = difflib.SequenceMatcher(None, n1, n2).ratio()

        # Token Jaccard similarity
        toks1 = set(n1.split())
        toks2 = set(n2.split())
        if toks1 and toks2:
            intersection = toks1 & toks2
            union = toks1 | toks2
            jaccard = len(intersection) / len(union)
            return max(seq_ratio, jaccard)

        return seq_ratio

    @classmethod
    def authors_match(cls, a1: tuple[str, ...], a2: tuple[str, ...]) -> tuple[bool, str]:
        """Checks if two author lists are compatible or overlapping."""
        clean1 = [cls.normalize_author(a) for a in a1 if a and a.strip().lower() != "unknown"]
        clean2 = [cls.normalize_author(a) for a in a2 if a and a.strip().lower() != "unknown"]

        if not clean1 or not clean2:
            # One or both author lists are unknown -> neutral match
            return True, "neutral_author"

        # Check for token overlap across author names (e.g. "Ursula K. Le Guin" vs "Ursula Le Guin")
        tokens1 = set(" ".join(clean1).split())
        tokens2 = set(" ".join(clean2).split())
        # Filter out single-letter initials
        meaningful_tokens1 = {t for t in tokens1 if len(t) > 1}
        meaningful_tokens2 = {t for t in tokens2 if len(t) > 1}

        overlap = meaningful_tokens1 & meaningful_tokens2
        if overlap:
            return True, "matching_author_tokens"

        return False, "differing_authors"

    @classmethod
    def is_duplicate(cls, b1: SpineExtractionItem, b2: SpineExtractionItem) -> tuple[bool, float, str]:
        """Determines if two extracted spine items represent the same physical volume."""
        n1 = cls.normalize_title(b1.title)
        n2 = cls.normalize_title(b2.title)

        if not n1 or not n2:
            return False, 0.0, "missing_title"

        # Exact title match
        if n1 == n2:
            author_compat, auth_reason = cls.authors_match(b1.authors, b2.authors)
            if author_compat:
                return True, 1.0, f"exact_title_match ({auth_reason})"
            # Even with different author names, if both titles are long and unique (>15 chars), likely duplicate
            if len(n1) > 15:
                return True, 0.88, "exact_long_title_match"

        score = cls.title_similarity(b1.title, b2.title)
        if score >= cls.TITLE_SIMILARITY_THRESHOLD:
            author_compat, auth_reason = cls.authors_match(b1.authors, b2.authors)
            if author_compat:
                return True, score, f"high_title_similarity ({auth_reason})"
            if score >= 0.94:
                return True, score, "near_identical_title"

        return False, score, "distinct_volumes"

    @classmethod
    def candidate_richness_score(cls, b: SpineExtractionItem) -> int:
        """Scores candidate metadata completeness to decide which duplicate to retain."""
        score = 0
        conf = (b.confidence or "medium").lower()
        if conf == "high":
            score += 30
        elif conf == "medium":
            score += 20
        else:
            score += 10

        valid_authors = [a for a in b.authors if a and a.strip().lower() != "unknown"]
        if valid_authors:
            score += 20 + min(len(valid_authors) * 2, 6)

        if b.subtitle:
            score += 10
        if b.publisher_imprint:
            score += 10
        if b.format != BookFormat.UNKNOWN:
            score += 10

        score += len(b.visible_condition_cues) * 5
        score += len(b.edition_or_series_markers) * 10
        score += min(len(b.raw_spine_text), 20)
        return score

    @classmethod
    def merge_duplicate_metadata(
        cls, kept: SpineExtractionItem, suppressed: SpineExtractionItem
    ) -> SpineExtractionItem:
        """Enriches the kept item with any non-redundant metadata from the suppressed item."""
        # Merge authors if kept had none
        kept_authors = [a for a in kept.authors if a and a.strip().lower() != "unknown"]
        supp_authors = [a for a in suppressed.authors if a and a.strip().lower() != "unknown"]
        merged_authors = tuple(kept_authors or supp_authors or ["Unknown"])

        # Merge condition cues
        combined_cues = list(kept.visible_condition_cues)
        for cue in suppressed.visible_condition_cues:
            if cue and cue not in combined_cues:
                combined_cues.append(cue)

        # Merge edition markers
        combined_markers = list(kept.edition_or_series_markers)
        for marker in suppressed.edition_or_series_markers:
            if marker and marker not in combined_markers:
                combined_markers.append(marker)

        # Merge publisher if kept was empty
        publisher = kept.publisher_imprint or suppressed.publisher_imprint
        subtitle = kept.subtitle or suppressed.subtitle

        # Pick format
        chosen_format = kept.format
        if chosen_format == BookFormat.UNKNOWN and suppressed.format != BookFormat.UNKNOWN:
            chosen_format = suppressed.format

        return SpineExtractionItem(
            stack_order=kept.stack_order,
            box_2d=kept.box_2d,
            title=kept.title,
            subtitle=subtitle,
            authors=merged_authors,
            publisher_imprint=publisher,
            format=chosen_format,
            visible_condition_cues=tuple(combined_cues),
            edition_or_series_markers=tuple(combined_markers),
            raw_spine_text=kept.raw_spine_text or suppressed.raw_spine_text,
            raw_copyright_text=kept.raw_copyright_text or suppressed.raw_copyright_text,
            raw_flap_text=kept.raw_flap_text or suppressed.raw_flap_text,
            confidence=kept.confidence,
        )

    @classmethod
    def deduplicate(
        cls, items: list[BatchExtractionItem]
    ) -> tuple[list[BatchExtractionItem], list[SuppressedDuplicate]]:
        """Deduplicates items across images while preserving source image tracking."""
        if not items:
            return [], []

        kept_items: list[BatchExtractionItem] = []
        suppressed: list[SuppressedDuplicate] = []

        for candidate in items:
            dup_found = False
            for idx, existing in enumerate(kept_items):
                if candidate.source_image_index == existing.source_image_index:
                    continue  # Two visible spines in one photo represent separate copies.
                is_dup, sim_score, reason = cls.is_duplicate(candidate.book, existing.book)
                if is_dup:
                    dup_found = True
                    cand_score = cls.candidate_richness_score(candidate.book)
                    exist_score = cls.candidate_richness_score(existing.book)

                    primary_author = (candidate.book.authors[0] if candidate.book.authors else "") or (
                        existing.book.authors[0] if existing.book.authors else ""
                    )

                    if cand_score > exist_score:
                        # Candidate is richer: replace existing, suppress existing
                        enriched = cls.merge_duplicate_metadata(candidate.book, existing.book)
                        kept_items[idx] = BatchExtractionItem(
                            book=enriched,
                            source_image_index=candidate.source_image_index,
                            source_filename=candidate.source_filename,
                        )
                        suppressed.append(
                            SuppressedDuplicate(
                                title=existing.book.title,
                                author=primary_author,
                                duplicate_image_index=existing.source_image_index,
                                duplicate_image_filename=existing.source_filename,
                                kept_image_index=candidate.source_image_index,
                                kept_image_filename=candidate.source_filename,
                                similarity_score=sim_score,
                                reason=(
                                    "Overlapping volume across photos; kept richer photo "
                                    f"{candidate.source_filename}: {reason}"
                                ),
                            )
                        )
                    else:
                        # Existing is richer or equal: suppress candidate, enrich existing
                        enriched = cls.merge_duplicate_metadata(existing.book, candidate.book)
                        kept_items[idx] = BatchExtractionItem(
                            book=enriched,
                            source_image_index=existing.source_image_index,
                            source_filename=existing.source_filename,
                        )
                        suppressed.append(
                            SuppressedDuplicate(
                                title=candidate.book.title,
                                author=primary_author,
                                duplicate_image_index=candidate.source_image_index,
                                duplicate_image_filename=candidate.source_filename,
                                kept_image_index=existing.source_image_index,
                                kept_image_filename=existing.source_filename,
                                similarity_score=sim_score,
                                reason=(
                                    "Overlapping volume across photos; kept earlier photo "
                                    f"{existing.source_filename}: {reason}"
                                ),
                            )
                        )
                    break

            if not dup_found:
                kept_items.append(candidate)

        return kept_items, suppressed


class MultiAngleBookMerger:
    """Merges multiple angles/views of a single book into a consolidated collectible record."""

    @classmethod
    def merge_angles(cls, items: list[SpineExtractionItem]) -> SpineExtractionItem | None:
        """Merges spine, cover, and copyright page extractions of the same book."""
        if not items:
            return None
        if len(items) == 1:
            return items[0]

        # 1. Title: pick the most descriptive / complete title
        valid_titles = [i.title.strip() for i in items if i.title and i.title.strip().lower() != "unknown"]
        best_title = max(valid_titles, key=len) if valid_titles else items[0].title

        # 2. Subtitle: pick longest non-empty
        valid_subs = [i.subtitle.strip() for i in items if i.subtitle and i.subtitle.strip()]
        best_subtitle = max(valid_subs, key=len) if valid_subs else None

        # 3. Authors: combine distinct authors
        all_authors: list[str] = []
        for it in items:
            for a in it.authors:
                clean = a.strip()
                if clean and clean.lower() != "unknown" and clean not in all_authors:
                    all_authors.append(clean)
        authors_tuple = tuple(all_authors) if all_authors else ("Unknown",)

        # 4. Publisher / Imprint
        valid_pubs = [i.publisher_imprint.strip() for i in items if i.publisher_imprint and i.publisher_imprint.strip()]
        best_publisher = max(valid_pubs, key=len) if valid_pubs else None

        # 5. Format: prefer most specific binding
        format_priority = [
            BookFormat.HARDCOVER_WITH_JACKET,
            BookFormat.HARDCOVER_NO_JACKET,
            BookFormat.TRADE_PAPERBACK,
            BookFormat.MASS_MARKET_PAPERBACK,
            BookFormat.SPIRAL_OR_OTHER,
        ]
        best_format = BookFormat.UNKNOWN
        for fmt in format_priority:
            if any(it.format == fmt for it in items):
                best_format = fmt
                break

        # 6. Condition cues: union across all angles
        all_cues: list[str] = []
        for it in items:
            for cue in it.visible_condition_cues:
                if cue and cue not in all_cues:
                    all_cues.append(cue)

        # 7. Edition / Series markers: union across all angles
        all_markers: list[str] = []
        for it in items:
            for m in it.edition_or_series_markers:
                if m and m not in all_markers:
                    all_markers.append(m)

        # 8. Raw text: consolidate
        raw_texts = [it.raw_spine_text.strip() for it in items if it.raw_spine_text and it.raw_spine_text.strip()]
        consolidated_raw = " | ".join(raw_texts)

        # 9. Copyright & flap text
        copy_texts = [
            it.raw_copyright_text.strip() for it in items if it.raw_copyright_text and it.raw_copyright_text.strip()
        ]
        best_copyright = max(copy_texts, key=len) if copy_texts else ""
        flap_texts = [it.raw_flap_text.strip() for it in items if it.raw_flap_text and it.raw_flap_text.strip()]
        best_flap = max(flap_texts, key=len) if flap_texts else ""

        return SpineExtractionItem(
            stack_order=1,
            box_2d=items[0].box_2d,
            title=best_title,
            subtitle=best_subtitle,
            authors=authors_tuple,
            publisher_imprint=best_publisher,
            format=best_format,
            visible_condition_cues=tuple(all_cues),
            edition_or_series_markers=tuple(all_markers),
            raw_spine_text=consolidated_raw,
            raw_copyright_text=best_copyright,
            raw_flap_text=best_flap,
            confidence="high",
        )
