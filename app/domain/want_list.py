from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.db.want_list import Collector, WantItem


GRADE_RANKS: dict[str, int] = {
    "poor": 1,
    "reading copy": 1,
    "fair": 2,
    "acceptable": 3,
    "good": 4,
    "very good": 5,
    "near fine": 6,
    "fine": 7,
    "as new": 8,
    "mint": 8,
}

STOP_ARTICLES: set[str] = {"the", "a", "an", "le", "la", "les", "der", "die", "das", "un", "une"}


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    # Normalize unicode (accents, ligatures)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("utf-8").lower()
    # Replace punctuation with spaces
    cleaned = re.sub(r"[^\w\s]", " ", ascii_text)
    # Collapse multiple whitespaces
    return " ".join(cleaned.split())


def _extract_tokens(text: str) -> set[str]:
    norm = _normalize_text(text)
    tokens = set(norm.split())
    # Return non-article tokens if possible
    meaningful = tokens - STOP_ARTICLES
    return meaningful if meaningful else tokens


def _strip_leading_articles(text: str) -> str:
    norm = _normalize_text(text)
    words = norm.split()
    if words and words[0] in STOP_ARTICLES:
        return " ".join(words[1:])
    return norm


@dataclass
class WantMatchResult:
    matched: bool
    want_item: WantItem | None = None
    collector: Collector | None = None
    match_score: float = 0.0
    reasons: list[str] = field(default_factory=list)


class WantListMatcher:
    """Bibliographic matching engine for antiquarian collector want lists.
    
    Evaluates newly cataloged books against active wants using fuzzy title token
    similarity, normalized author attribution, price budget constraints,
    minimum condition grade hierarchies, and binding requirements.
    """

    def title_similarity(self, want_title: str, candidate_title: str) -> float:
        w_norm = _strip_leading_articles(want_title)
        c_norm = _strip_leading_articles(candidate_title)

        if not w_norm or not c_norm:
            return 0.0

        if w_norm == c_norm:
            return 1.0

        # Substring / sub-phrase match (e.g. "two solitudes" inside "two solitudes first edition")
        if w_norm in c_norm or c_norm in w_norm:
            return 0.95

        w_tokens = _extract_tokens(want_title)
        c_tokens = _extract_tokens(candidate_title)

        if not w_tokens or not c_tokens:
            return 0.0

        intersection = w_tokens & c_tokens
        if not intersection:
            return 0.0

        # Recall of wanted tokens (how many of the wanted title words are present)
        recall = len(intersection) / len(w_tokens)
        jaccard = len(intersection) / len(w_tokens | c_tokens)

        # If all wanted title tokens are in the candidate title:
        if recall >= 0.99:
            return 0.90

        # Weighted combination favoring recall
        return round((0.7 * recall) + (0.3 * jaccard), 3)

    def author_matches(self, want_author: str, candidate_authors: list[str] | str) -> tuple[bool, float]:
        if not want_author or not want_author.strip():
            return True, 1.0  # Open want without specific author

        if isinstance(candidate_authors, str):
            c_authors_list = [candidate_authors]
        else:
            c_authors_list = list(candidate_authors)

        w_tokens = _extract_tokens(want_author)
        if not w_tokens:
            return True, 1.0

        for cand_auth in c_authors_list:
            c_tokens = _extract_tokens(cand_auth)
            if not c_tokens:
                continue

            # Check surname or direct token overlap
            intersection = w_tokens & c_tokens
            if intersection:
                # If at least the primary surname token matches
                return True, round(len(intersection) / len(w_tokens), 2)

            # Check if want author normalized string is contained in cand author or vice-versa
            w_norm = _normalize_text(want_author)
            c_norm = _normalize_text(cand_auth)
            if w_norm in c_norm or c_norm in w_norm:
                return True, 0.9

        return False, 0.0

    def evaluate(
        self,
        want: WantItem,
        collector: Collector | None,
        candidate: dict[str, Any],
    ) -> WantMatchResult:
        reasons: list[str] = []

        cand_title = str(candidate.get("title", "")).strip()
        cand_authors = candidate.get("authors") or candidate.get("author") or []
        cand_price = candidate.get("price_cad") or candidate.get("online_price_cad") or candidate.get("in_store_price_cad")
        cand_condition = str(candidate.get("condition", candidate.get("condition_grade", ""))).strip()
        cand_format = str(candidate.get("format", candidate.get("binding", ""))).strip()

        # 1. Title match
        t_score = self.title_similarity(want.title, cand_title)
        if t_score < 0.65:
            return WantMatchResult(
                matched=False,
                want_item=want,
                collector=collector,
                match_score=t_score,
                reasons=[f"Title score {t_score:.2f} below threshold (want: '{want.title}', candidate: '{cand_title}')"],
            )
        reasons.append(f"Title match: {t_score:.2f} ('{want.title}' ~ '{cand_title}')")

        # 2. Author match
        a_matched, a_score = self.author_matches(want.author, cand_authors)
        if not a_matched:
            return WantMatchResult(
                matched=False,
                want_item=want,
                collector=collector,
                match_score=0.0,
                reasons=[f"Author mismatch (want: '{want.author}', candidate: '{cand_authors}')"],
            )
        if want.author:
            reasons.append(f"Author match: {a_score:.2f} ('{want.author}')")

        # 3. Budget constraint check
        if want.max_price_cad is not None and cand_price is not None:
            try:
                price_val = Decimal(str(cand_price))
                if price_val > want.max_price_cad:
                    return WantMatchResult(
                        matched=False,
                        want_item=want,
                        collector=collector,
                        match_score=0.0,
                        reasons=[
                            f"Price ${price_val:.2f} CAD exceeds collector budget ${want.max_price_cad:.2f} CAD"
                        ],
                    )
                reasons.append(f"Within budget (${price_val:.2f} <= ${want.max_price_cad:.2f} CAD)")
            except Exception:
                pass

        # 4. Minimum condition grade check
        if want.min_condition and cand_condition:
            w_rank = GRADE_RANKS.get(want.min_condition.lower(), 4)
            c_rank = GRADE_RANKS.get(cand_condition.lower(), 4)
            if c_rank < w_rank:
                return WantMatchResult(
                    matched=False,
                    want_item=want,
                    collector=collector,
                    match_score=0.0,
                    reasons=[
                        f"Condition '{cand_condition}' (grade {c_rank}) below required '{want.min_condition}' (grade {w_rank})"
                    ],
                )
            reasons.append(f"Condition satisfied ('{cand_condition}' >= '{want.min_condition}')")

        # 5. Binding / format check
        if want.binding and cand_format:
            w_bind = _normalize_text(want.binding)
            c_fmt = _normalize_text(cand_format)
            if "hardcover" in w_bind and "paperback" in c_fmt and "hardcover" not in c_fmt:
                return WantMatchResult(
                    matched=False,
                    want_item=want,
                    collector=collector,
                    match_score=0.0,
                    reasons=[f"Binding mismatch: requested '{want.binding}' but book is '{cand_format}'"],
                )
            if "paperback" in w_bind and "hardcover" in c_fmt and "paperback" not in c_fmt:
                return WantMatchResult(
                    matched=False,
                    want_item=want,
                    collector=collector,
                    match_score=0.0,
                    reasons=[f"Binding mismatch: requested '{want.binding}' but book is '{cand_format}'"],
                )
            reasons.append(f"Binding compatible ('{want.binding}' ~ '{cand_format}')")

        overall_score = round(t_score * 0.7 + a_score * 0.3, 3)
        return WantMatchResult(
            matched=True,
            want_item=want,
            collector=collector,
            match_score=overall_score,
            reasons=reasons,
        )

    def find_best_match(
        self,
        wants: list[WantItem],
        collectors_by_id: dict[str, Collector],
        candidate: dict[str, Any],
    ) -> WantMatchResult | None:
        best_match: WantMatchResult | None = None
        for want in wants:
            if want.status != "active":
                continue
            collector = collectors_by_id.get(want.collector_id)
            result = self.evaluate(want, collector, candidate)
            if result.matched:
                if best_match is None or result.match_score > best_match.match_score:
                    best_match = result
        return best_match
