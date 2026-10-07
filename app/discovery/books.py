from __future__ import annotations

import difflib
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.domain.books import (
    BookFormat,
    DisambiguationCandidate,
    DisambiguationResult,
    SpineExtractionItem,
)
from app.net import RetryPolicy
from app.observability.logging import get_logger

logger = get_logger("book_discovery")

# Regex to find potential ISBNs (10 or 13 digits, optional hyphens)
ISBN_PATTERN = re.compile(
    r"\b(?:97[89][-\s]?)?[0-9]{1,5}[-\s]?[0-9]{1,7}[-\s]?[0-9]{1,7}[-\s]?[0-9X]\b",
    re.IGNORECASE,
)

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
OPEN_LIBRARY_FIELDS = (
    "key,title,author_name,first_publish_year,publisher,isbn,cover_i,edition_count"
)


def clean_isbn(raw: str) -> str:
    """Strip hyphens, spaces, and normalize to uppercase."""
    return re.sub(r"[^0-9X]", "", raw.upper())


def is_valid_isbn_format(cleaned: str) -> bool:
    """Basic length check for ISBN-10 or ISBN-13."""
    return len(cleaned) in (10, 13)


def extract_potential_isbn(text: str) -> str | None:
    """Extract first valid-looking ISBN from raw spine text."""
    if not text:
        return None
    matches = ISBN_PATTERN.findall(text)
    for m in matches:
        cleaned = clean_isbn(m)
        if is_valid_isbn_format(cleaned):
            return cleaned
    return None


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens."""
    return set(re.findall(r"\b[a-z0-9]+\b", text.lower()))


def compute_string_similarity(a: str, b: str) -> float:
    """Hybrid token Jaccard + SequenceMatcher similarity [0.0, 1.0]."""
    if not a or not b:
        return 0.0
    a_clean = a.strip().lower()
    b_clean = b.strip().lower()
    if a_clean == b_clean:
        return 1.0

    # Token overlap
    tokens_a = _tokenize(a_clean)
    tokens_b = _tokenize(b_clean)
    jaccard = (
        len(tokens_a & tokens_b) / len(tokens_a | tokens_b) if (tokens_a and tokens_b) else 0.0
    )

    # Sequence alignment
    seq_ratio = difflib.SequenceMatcher(None, a_clean, b_clean).ratio()
    return max(jaccard * 0.5 + seq_ratio * 0.5, seq_ratio)


def score_candidate(
    candidate: DisambiguationCandidate,
    spine: SpineExtractionItem,
    detected_isbn: str | None = None,
) -> float:
    """Score candidate alignment against extracted spine attributes [0.0, 1.0]."""
    # Direct ISBN match is an authoritative 1.0
    if detected_isbn:
        cleaned_detected = clean_isbn(detected_isbn)
        cleaned_candidate_isbns = {clean_isbn(i) for i in candidate.isbns}
        if cleaned_detected in cleaned_candidate_isbns:
            return 1.0

    # 1. Title match (45% weight)
    title_score = compute_string_similarity(candidate.title, spine.title)

    # 2. Author match (35% weight)
    author_score = 0.0
    if spine.authors and candidate.authors:
        author_scores = [
            compute_string_similarity(a1, a2)
            for a1 in spine.authors
            for a2 in candidate.authors
        ]
        author_score = max(author_scores) if author_scores else 0.0
    elif not spine.authors:
        author_score = 0.5

    # 3. Publisher match (20% bonus)
    publisher_bonus = 0.0
    if spine.publisher_imprint and candidate.publishers:
        spine_pub_tokens = _tokenize(spine.publisher_imprint)
        for pub in candidate.publishers:
            if spine_pub_tokens & _tokenize(pub):
                publisher_bonus = 0.20
                break

    total = (title_score * 0.45) + (author_score * 0.35) + publisher_bonus
    return min(max(total, 0.0), 1.0)


class MetadataResolver(Protocol):
    """Protocol for bibliographic catalog resolvers."""

    def resolve(self, spine: SpineExtractionItem) -> DisambiguationResult: ...


@dataclass
class OpenLibraryMetadataResolver(MetadataResolver):
    """Zero-key bibliographic resolver querying Open Library Search API."""

    base_url: str = "https://openlibrary.org/search.json"
    timeout: float = 12.0
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    user_agent: str = "BookshopIntakeOps/1.0 (contact@bookshop.local)"

    def resolve(self, spine: SpineExtractionItem) -> DisambiguationResult:
        detected_isbn = extract_potential_isbn(spine.raw_spine_text)

        params: dict[str, str] = {
            "title": spine.title,
            "fields": OPEN_LIBRARY_FIELDS,
            "limit": "10",
        }
        if spine.authors:
            params["author"] = spine.authors[0]

        docs = self._fetch_search_docs(params)
        if not docs and "author" in params:
            params.pop("author")
            docs = self._fetch_search_docs(params)

        candidates = [self._doc_to_candidate(doc, spine, detected_isbn) for doc in docs]
        candidates.sort(key=lambda c: (c.match_score, c.edition_count), reverse=True)

        if not candidates or candidates[0].match_score < 0.45:
            return DisambiguationResult(
                spine_item=spine,
                matched=False,
                best_candidate=None,
                alternate_candidates=(),
                detected_isbn=detected_isbn,
                needs_copyright_check=True,
                triage_reason="No catalog record matched title and author with confidence.",
                suggested_catalog_action="manual_lookup",
            )

        best = candidates[0]
        alternates = tuple(candidates[1:4])

        needs_check, reason, action = self._evaluate_triage(spine, best, detected_isbn)

        return DisambiguationResult(
            spine_item=spine,
            matched=True,
            best_candidate=best,
            alternate_candidates=alternates,
            detected_isbn=detected_isbn,
            needs_copyright_check=needs_check,
            triage_reason=reason,
            suggested_catalog_action=action,
        )

    def _evaluate_triage(
        self,
        spine: SpineExtractionItem,
        candidate: DisambiguationCandidate,
        detected_isbn: str | None,
    ) -> tuple[bool, str, str]:
        """Evaluates whether this book requires opening to copyright page."""
        has_first_edition_marker = any(
            re.search(r"\b(first|1st|special|limited)\b", m, re.IGNORECASE)
            for m in spine.edition_or_series_markers
        )
        if has_first_edition_marker:
            return (
                True,
                "First Edition marker detected: verify number line on copyright page.",
                "inspect_edition_points",
            )

        is_hardcover = spine.format in (
            BookFormat.HARDCOVER_WITH_JACKET,
            BookFormat.HARDCOVER_NO_JACKET,
        )
        if is_hardcover and candidate.first_publish_year and candidate.first_publish_year < 1995:
            return (
                True,
                "Pre-1995 Hardcover: check copyright page for Book Club Edition (BCE).",
                "inspect_edition_points",
            )

        if (
            candidate.first_publish_year
            and candidate.first_publish_year < 1970
            and not detected_isbn
        ):
            return (
                True,
                "Pre-1970 vintage title: check title/copyright page for exact print date.",
                "inspect_edition_points",
            )

        if candidate.match_score < 0.70:
            return (
                True,
                f"Moderate match confidence ({candidate.match_score:.0%}): confirm edition.",
                "manual_lookup",
            )

        return (
            False,
            "Standard catalog match. Ready for valuation and auto-listing.",
            "auto_list",
        )

    def _doc_to_candidate(
        self, doc: dict[str, Any], spine: SpineExtractionItem, detected_isbn: str | None
    ) -> DisambiguationCandidate:
        work_id = str(doc.get("key", "")).replace("/works/", "")
        title = str(doc.get("title", ""))
        authors = tuple(str(a) for a in doc.get("author_name", []) if a)
        first_year = doc.get("first_publish_year")
        publishers = tuple(str(p) for p in (doc.get("publisher") or [])[:5] if p)
        isbns = tuple(str(i) for i in (doc.get("isbn") or [])[:15] if i)
        cover_id = doc.get("cover_i")
        edition_count = int(doc.get("edition_count", 1))

        cand = DisambiguationCandidate(
            work_id=work_id,
            title=title,
            authors=authors,
            first_publish_year=int(first_year) if first_year else None,
            publishers=publishers,
            isbns=isbns,
            cover_id=int(cover_id) if cover_id else None,
            edition_count=edition_count,
        )
        score = score_candidate(cand, spine, detected_isbn)
        return DisambiguationCandidate(
            work_id=cand.work_id,
            title=cand.title,
            authors=cand.authors,
            first_publish_year=cand.first_publish_year,
            publishers=cand.publishers,
            isbns=cand.isbns,
            cover_id=cand.cover_id,
            edition_count=cand.edition_count,
            match_score=score,
        )

    def _fetch_search_docs(self, params: dict[str, str]) -> list[dict[str, Any]]:
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        req = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            method="GET",
        )

        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    if resp.status in RETRYABLE_STATUS:
                        raise HTTPError(url, resp.status, "Retryable", resp.headers, None)
                    payload = json.loads(resp.read().decode("utf-8"))
                    return payload.get("docs", [])
            except HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS or attempt >= self.retries.max_attempts:
                    logger.warning(f"OpenLibrary query failed: HTTP {exc.code} for {url}")
                    return []
                import time

                time.sleep(self.retries.backoff(attempt))
            except URLError as exc:
                if attempt >= self.retries.max_attempts:
                    logger.warning(f"OpenLibrary network failure: {exc.reason}")
                    return []
                import time

                time.sleep(self.retries.backoff(attempt))
            except Exception as exc:
                logger.warning(f"OpenLibrary unexpected parse failure: {exc}")
                return []

        return []
