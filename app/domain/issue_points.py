from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EditionClassification(StrEnum):
    FIRST_EDITION_FIRST_PRINTING = "First Edition, First Printing"
    FIRST_EDITION_LATER_PRINTING = "First Edition, Later Printing"
    LATER_PRINTING = "Later Printing"
    BOOK_CLUB_EDITION = "Book Club Edition (BCE)"
    REPRINT_OR_TRADE = "Trade Reprint / Standard Issue"
    UNRESOLVED = "Unresolved Edition State"


@dataclass(frozen=True)
class IssuePointResult:
    """Deterministic bibliographic edition and collation audit result."""

    classification: EditionClassification
    is_first_edition: bool
    is_first_impression: bool
    is_book_club: bool
    impression_number: int | None
    publisher_rule_name: str
    reasoning_trace: str
    evidence_points: tuple[str, ...] = ()
    disqualification_flags: tuple[str, ...] = ()
    confidence: str = "conclusive"  # conclusive | probable | heuristic
    pricing_tier: str = "standard_used"  # collectible_first | reading_copy | standard_used

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "is_first_edition": self.is_first_edition,
            "is_first_impression": self.is_first_impression,
            "is_book_club": self.is_book_club,
            "impression_number": self.impression_number,
            "publisher_rule_name": self.publisher_rule_name,
            "reasoning_trace": self.reasoning_trace,
            "evidence_points": list(self.evidence_points),
            "disqualification_flags": list(self.disqualification_flags),
            "confidence": self.confidence,
            "pricing_tier": self.pricing_tier,
        }


class BibliographicIssuePointDecoder:
    """Algorithmic collation engine encoding antiquarian publisher identification rules

    (Digitized conventions from Zempel & Verkler, McBride, Boutell, and Currey).
    """

    @classmethod
    def decode(
        cls,
        publisher: str | None = None,
        raw_copyright_text: str = "",
        raw_flap_text: str = "",
        visible_condition_cues: tuple[str, ...] | list[str] = (),
        format_str: str = "",
    ) -> IssuePointResult:
        """Determines edition and printing state using deterministic publisher issue-point logic."""
        pub_clean = (publisher or "").strip().lower()
        copy_clean = (raw_copyright_text or "").strip().lower()
        flap_clean = (raw_flap_text or "").strip().lower()
        cues = set(str(c).lower() for c in (visible_condition_cues or ()))

        evidence: list[str] = []
        disqualifications: list[str] = []

        # ------------------------------------------------------------------
        # RULE 0: BOOK CLUB EDITION (BCE) DISQUALIFICATION
        # ------------------------------------------------------------------
        is_bce = False
        bce_reasons: list[str] = []

        # 0.1 Explicit text slugs
        if any(
            term in copy_clean
            for term in (
                "book club edition",
                "book-of-the-month",
                "bomc",
                "book-of-the-month club selection",
            )
        ):
            is_bce = True
            bce_reasons.append("Explicit 'Book Club Edition' / 'BOMC' slug on copyright page")

        if any(term in flap_clean for term in ("book club edition", "book-of-the-month", "bomc", "guild edition")):
            is_bce = True
            bce_reasons.append("Dust jacket flap declares Book Club Edition selection")

        # 0.2 Physical tells: Blind debossed stamp on rear board
        if any("blind" in c or "deboss" in c or "bce_stamp" in c for c in cues):
            is_bce = True
            bce_reasons.append("Blind debossed mark (dot/square/triangle) detected on rear board")

        # 0.3 Dust jacket price cues: Book clubs omit printed retail prices on the flap
        has_printed_price = bool(re.search(r"\$\s*\d+(\.\d{2})?", raw_flap_text))
        if flap_clean and not has_printed_price and len(flap_clean) > 25:
            # If flap text is present and substantial, but completely lacks any dollar price
            is_bce = True
            bce_reasons.append("Dust jacket front flap has copy but omits retail price (classic BCE tell)")

        # 0.4 Gutter date codes (e.g., Doubleday 5-digit code or letter-digit code like 'H37' in gutter)
        gutter_match = re.search(r"\b([a-m]\s*\d{2})\b", copy_clean)
        if (
            gutter_match
            and not any(k in pub_clean for k in ("harper", "brothers"))
            and ("doubleday" in pub_clean or "guild" in copy_clean)
        ):
            # Note: Harper uses letter-letter or letter-year, whereas gutter letter+2-digits on novels is Doubleday BCE
            is_bce = True
            bce_reasons.append(
                f"Book Club printing code detected in gutter/copyright: '{gutter_match.group(1).upper()}'"
            )

        if is_bce:
            disqualifications.extend(bce_reasons)
            trace = (
                "DISQUALIFIED AS COLLECTIBLE FIRST: Book Club Edition (BCE) signature confirmed: "
                + "; ".join(bce_reasons)
                + ". Capped at used reading copy valuation."
            )
            return IssuePointResult(
                classification=EditionClassification.BOOK_CLUB_EDITION,
                is_first_edition=False,
                is_first_impression=False,
                is_book_club=True,
                impression_number=None,
                publisher_rule_name="Book Club Disqualification Engine",
                reasoning_trace=trace,
                evidence_points=tuple(evidence),
                disqualification_flags=tuple(disqualifications),
                confidence="conclusive",
                pricing_tier="reading_copy",
            )

        # ------------------------------------------------------------------
        # EXTRACT NUMBERLINE / INTEGERS
        # ------------------------------------------------------------------
        numbers = cls._extract_numberline_integers(raw_copyright_text)
        if numbers:
            evidence.append(
                f"Parsed numberline sequence: {' '.join(str(n) for n in numbers)} (min integer: {min(numbers)})"
            )

        has_stated_first = bool(
            re.search(
                r"\b(first\s+edition|first\s+printing|first\s+published|stated\s+first)\b",
                copy_clean,
            )
        )
        if has_stated_first:
            evidence.append("Found explicit 'First Edition' / 'First Printing' statement")

        # ------------------------------------------------------------------
        # RULE 1: RANDOM HOUSE (1970–2002) CONVENTION
        # Random House, Knopf, Pantheon, Ballantine Hardcovers, Vintage
        # Rule: Stated "First Edition" + numberline terminating or containing min integer 2 = 1st Printing!
        # (Random House dropped "First Edition" on the 2nd printing, leaving 2 3 4 5...)
        # ------------------------------------------------------------------
        if any(p in pub_clean for p in ("random house", "pantheon", "knopf", "ballantine", "vintage")):
            if has_stated_first:
                if numbers:
                    min_num = min(numbers)
                    if min_num == 2:
                        evidence.append(
                            "Random House rule matched: 'First Edition' present with numberline starting/containing 2"
                        )
                        return IssuePointResult(
                            classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                            is_first_edition=True,
                            is_first_impression=True,
                            is_book_club=False,
                            impression_number=1,
                            publisher_rule_name="Random House (1970–2002) Convention",
                            reasoning_trace=(
                                "Matched Random House 1970–2002 syntax: Presence of stated 'First Edition' "
                                "with numberline starting at 2 marks the true first printing. "
                                "(Random House dropped 'First Edition' on the second impression while retaining the 2)."
                            ),
                            evidence_points=tuple(evidence),
                            confidence="conclusive",
                            pricing_tier="collectible_first",
                        )
                    elif min_num == 1:
                        # Post-2002 Random House adopted standard 1-10 numberline
                        evidence.append("Random House sequence includes 1: standard modern format")
                        return IssuePointResult(
                            classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                            is_first_edition=True,
                            is_first_impression=True,
                            is_book_club=False,
                            impression_number=1,
                            publisher_rule_name="Random House (Post-2002 Standard Numberline)",
                            reasoning_trace="Stated 'First Edition' with minimum integer 1 marks true first printing.",
                            evidence_points=tuple(evidence),
                            confidence="conclusive",
                            pricing_tier="collectible_first",
                        )
                    else:
                        return IssuePointResult(
                            classification=EditionClassification.FIRST_EDITION_LATER_PRINTING,
                            is_first_edition=False,
                            is_first_impression=False,
                            is_book_club=False,
                            impression_number=min_num,
                            publisher_rule_name="Random House (Later Impression)",
                            reasoning_trace=(
                                f"Numberline minimum integer is {min_num}; indicates later impression "
                                f"({min_num}th printing)."
                            ),
                            evidence_points=tuple(evidence),
                            confidence="conclusive",
                            pricing_tier="standard_used",
                        )
                else:
                    # Random House before 1970: Stated "First Edition" with no numberline = First Printing
                    evidence.append("Pre-1970 Random House: Stated 'First Edition' with no numberline present")
                    return IssuePointResult(
                        classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                        is_first_edition=True,
                        is_first_impression=True,
                        is_book_club=False,
                        impression_number=1,
                        publisher_rule_name="Random House (Pre-1970 Convention)",
                        reasoning_trace=(
                            "Stated 'First Edition' with no numbers on copyright page marks true "
                            "first printing for pre-1970 Random House volumes."
                        ),
                        evidence_points=tuple(evidence),
                        confidence="conclusive",
                        pricing_tier="collectible_first",
                    )
            else:
                # "First Edition" dropped
                if numbers and min(numbers) >= 2:
                    min_num = min(numbers)
                    evidence.append(f"Absence of 'First Edition' statement with sequence starting at {min_num}")
                    return IssuePointResult(
                        classification=EditionClassification.LATER_PRINTING,
                        is_first_edition=False,
                        is_first_impression=False,
                        is_book_club=False,
                        impression_number=min_num,
                        publisher_rule_name="Random House (Second or Later Impression)",
                        reasoning_trace=(
                            f"Absence of 'First Edition' statement indicates {min_num}nd/th impression. "
                            "Random House dropped 'First Edition' wording on subsequent printings."
                        ),
                        evidence_points=tuple(evidence),
                        confidence="conclusive",
                        pricing_tier="standard_used",
                    )

        # ------------------------------------------------------------------
        # RULE 2: CHARLES SCRIBNER'S SONS (CLASSIC)
        # Seal and capital letter "A" on the copyright page
        # ------------------------------------------------------------------
        if any(p in pub_clean for p in ("scribner", "charles scribner")):
            has_scribner_a = bool(re.search(r"\bA\b", raw_copyright_text)) or "letter a" in copy_clean
            has_scribner_seal = "seal" in copy_clean or "colophon" in copy_clean
            has_subsequent = bool(re.search(r"\b(second|2nd|reprinted|reprint)\b", copy_clean))

            if has_scribner_a and not has_subsequent:
                evidence.append("Scribner's rule matched: Capital letter 'A' present on copyright page")
                if has_scribner_seal:
                    evidence.append("Scribner colophon seal identified")
                return IssuePointResult(
                    classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                    is_first_edition=True,
                    is_first_impression=True,
                    is_book_club=False,
                    impression_number=1,
                    publisher_rule_name="Charles Scribner's Sons (Classic 'A' Convention)",
                    reasoning_trace=(
                        "Scribner's classic first edition point confirmed: Capital 'A' on copyright page "
                        "with absence of subsequent reprint dates designates true first edition."
                    ),
                    evidence_points=tuple(evidence),
                    confidence="conclusive",
                    pricing_tier="collectible_first",
                )

        # ------------------------------------------------------------------
        # RULE 3: DOUBLEDAY & COMPANY
        # Stated "First Edition" on copyright page. Dropped on 2nd printing without adding numbers.
        # ------------------------------------------------------------------
        if (
            "doubleday" in pub_clean
            and has_stated_first
            and not re.search(r"\b(second|2nd|third|3rd|reprinted)\b", copy_clean)
        ):
            evidence.append("Doubleday rule matched: Explicit 'First Edition' statement present")
            return IssuePointResult(
                classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                is_first_edition=True,
                is_first_impression=True,
                is_book_club=False,
                impression_number=1,
                publisher_rule_name="Doubleday & Company (Classic Convention)",
                reasoning_trace=(
                    "Doubleday classic first edition point confirmed: 'First Edition' explicitly stated. "
                    "(Doubleday dropped the statement on the second printing without adding a numberline)."
                ),
                evidence_points=tuple(evidence),
                confidence="conclusive",
                pricing_tier="collectible_first",
            )

        # ------------------------------------------------------------------
        # RULE 4: HARPER & ROW / HARPER & BROTHERS
        # Two-letter code pairing (e.g., M-C = Dec 1953) or stated First Edition
        # ------------------------------------------------------------------
        if any(p in pub_clean for p in ("harper & row", "harper & brothers", "harpercollins", "harper")):
            harper_code = re.search(r"\b([A-M]\s*-\s*[A-Z])\b", raw_copyright_text)
            if has_stated_first and (harper_code or numbers and min(numbers) == 1):
                code_str = harper_code.group(1).upper() if harper_code else "Standard sequence"
                evidence.append(f"Harper & Row rule: 'First Edition' stated with valid code/sequence ({code_str})")
                return IssuePointResult(
                    classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                    is_first_edition=True,
                    is_first_impression=True,
                    is_book_club=False,
                    impression_number=1,
                    publisher_rule_name="Harper & Row / Brothers (Date-Code Convention)",
                    reasoning_trace=(
                        f"Harper convention matched: Stated 'First Edition' with valid "
                        f"date-code/sequence ({code_str}). Confirms true first printing."
                    ),
                    evidence_points=tuple(evidence),
                    confidence="conclusive",
                    pricing_tier="collectible_first",
                )

        # ------------------------------------------------------------------
        # RULE 5: VIKING PRESS
        # "First published in [Year] by The Viking Press" with no subsequent reprint lines
        # ------------------------------------------------------------------
        if "viking" in pub_clean:
            viking_first = re.search(r"first\s+published\s+(?:in\s+)?(\d{4})", copy_clean)
            subsequent_viking = re.search(r"(?:second|2nd|third|reprinted)\s+(?:printing|impression)?", copy_clean)
            if viking_first and not subsequent_viking:
                year = viking_first.group(1)
                evidence.append(f"Viking Press rule: 'First published in {year}' with no subsequent impressions listed")
                return IssuePointResult(
                    classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                    is_first_edition=True,
                    is_first_impression=True,
                    is_book_club=False,
                    impression_number=1,
                    publisher_rule_name="The Viking Press (First Published Convention)",
                    reasoning_trace=(
                        f"Viking Press syntax confirmed: 'First published in {year}' without "
                        "subsequent impression lines."
                    ),
                    evidence_points=tuple(evidence),
                    confidence="conclusive",
                    pricing_tier="collectible_first",
                )

        # ------------------------------------------------------------------
        # RULE 6: STANDARD NUMBERLINE / DIGIT MATRIX (INDUSTRY GENERAL)
        # Modern publishers: 1 2 3 4 5 6 7 8 9 10 or 1 3 5 7 9 10 8 6 4 2
        # Lowest number indicates the impression.
        # ------------------------------------------------------------------
        if numbers:
            min_num = min(numbers)
            if min_num == 1:
                conf = "conclusive" if has_stated_first else "probable"
                evidence.append("Standard modern numberline: Minimum integer is 1")
                return IssuePointResult(
                    classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                    is_first_edition=True,
                    is_first_impression=True,
                    is_book_club=False,
                    impression_number=1,
                    publisher_rule_name="Standard Bibliographic Numberline",
                    reasoning_trace=(
                        "Standard numberline evaluation: Sequence contains lowest integer 1. "
                        + (
                            "Corroborated by explicit 'First Edition' statement."
                            if has_stated_first
                            else "Indicates first impression."
                        )
                    ),
                    evidence_points=tuple(evidence),
                    confidence=conf,
                    pricing_tier="collectible_first",
                )
            else:
                evidence.append(f"Standard numberline minimum integer is {min_num}")
                return IssuePointResult(
                    classification=EditionClassification.LATER_PRINTING,
                    is_first_edition=False,
                    is_first_impression=False,
                    is_book_club=False,
                    impression_number=min_num,
                    publisher_rule_name="Standard Bibliographic Numberline",
                    reasoning_trace=(
                        f"Standard numberline evaluation: Lowest integer is {min_num}, "
                        f"indicating {min_num}th impression (later printing)."
                    ),
                    evidence_points=tuple(evidence),
                    confidence="conclusive",
                    pricing_tier="standard_used",
                )

        # ------------------------------------------------------------------
        # RULE 7: FALLBACK TO STATED FIRST EDITION TEXT
        # ------------------------------------------------------------------
        if has_stated_first:
            evidence.append("Explicit statement 'First Edition' / 'First Printing' detected")
            return IssuePointResult(
                classification=EditionClassification.FIRST_EDITION_FIRST_PRINTING,
                is_first_edition=True,
                is_first_impression=True,
                is_book_club=False,
                impression_number=1,
                publisher_rule_name="Stated First Edition Heuristic",
                reasoning_trace=(
                    "Copyright text explicitly states 'First Edition' or 'First Printing' with "
                    "no conflicting subsequent reprint notes."
                ),
                evidence_points=tuple(evidence),
                confidence="probable",
                pricing_tier="collectible_first",
            )

        # Default fallback
        return IssuePointResult(
            classification=EditionClassification.UNRESOLVED,
            is_first_edition=False,
            is_first_impression=False,
            is_book_club=False,
            impression_number=None,
            publisher_rule_name="Standard Antiquarian Inspection",
            reasoning_trace=(
                "No distinctive publisher issue points or numberlines detected. Defaulting to standard cataloging."
            ),
            evidence_points=tuple(evidence),
            confidence="heuristic",
            pricing_tier="standard_used",
        )

    @classmethod
    def _extract_numberline_integers(cls, text: str) -> list[int]:
        """Extracts integers from common numberline sequences (e.g. 1 2 3 4 5, 2 4 6 8 9 7 5 3, 00 01 02)."""
        if not text:
            return []

        # Find line containing multiple numbers separated by spaces, dots, or dashes
        lines = text.splitlines()
        for line in lines:
            line_clean = line.strip()
            # Match tokens that are purely digits (1 to 2 digits)
            tokens = re.findall(r"\b(\d{1,2})\b", line_clean)
            if len(tokens) >= 3:
                try:
                    nums = [int(t) for t in tokens if 0 <= int(t) <= 99]
                    # Check if numbers look like a numberline sequence (e.g. at least 3 numbers from 1 to 20)
                    valid_nums = [n for n in nums if 1 <= n <= 20]
                    if len(valid_nums) >= 3:
                        return valid_nums
                except ValueError:
                    continue

        # Regex search for common contiguous sequences like "1 2 3 4 5 6 7 8 9 10" or "10 9 8 7 6 5 4 3 2 1"
        match = re.search(r"(?:(?:\b\d{1,2}\b[\s\.,-]+){3,}\b\d{1,2}\b)", text)
        if match:
            tokens = re.findall(r"\b(\d{1,2})\b", match.group(0))
            return [int(t) for t in tokens if 1 <= int(t) <= 25]

        return []
