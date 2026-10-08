from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AbaaConditionGrade(StrEnum):
    """Authoritative Antiquarian Booksellers' Association of America (ABAA/ILAB) condition grades."""

    AS_NEW = "As New"
    FINE = "Fine"
    NEAR_FINE = "Near Fine"
    VERY_GOOD = "Very Good"
    GOOD = "Good"
    FAIR = "Fair"
    POOR = "Poor"

    @property
    def short_abbreviation(self) -> str:
        match self:
            case AbaaConditionGrade.AS_NEW:
                return "AN"
            case AbaaConditionGrade.FINE:
                return "F"
            case AbaaConditionGrade.NEAR_FINE:
                return "NF"
            case AbaaConditionGrade.VERY_GOOD:
                return "VG"
            case AbaaConditionGrade.GOOD:
                return "G"
            case AbaaConditionGrade.FAIR:
                return "Fair"
            case AbaaConditionGrade.POOR:
                return "Poor"


class DefectSeverity(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


@dataclass(frozen=True)
class AntiquarianDefect:
    """A specific bibliographic or physical blemish identified on a volume."""

    code: str
    category: str  # "binding", "jacket", "text_block", "provenance"
    severity: DefectSeverity
    label: str
    catalog_phrase: str
    disqualifies_fine: bool = True
    disqualifies_near_fine: bool = True


@dataclass(frozen=True)
class AntiquarianVirtue:
    """A positive bibliographic or physical attribute that preserves or enhances value."""

    code: str
    category: str
    label: str
    catalog_phrase: str


@dataclass
class DualConditionReport:
    """Dual-graded condition analysis and ABAA-compliant catalog description."""

    book_grade: AbaaConditionGrade
    jacket_grade: AbaaConditionGrade | None
    has_jacket: bool
    defects: list[AntiquarianDefect] = field(default_factory=list)
    virtues: list[AntiquarianVirtue] = field(default_factory=list)
    unclipped_price: str | None = None
    is_price_clipped: bool = False
    is_ex_library: bool = False
    is_signed_or_inscribed: bool = False
    valuation_multiplier: float = 1.0
    trade_catalog_blurb: str = ""
    diagnostic_summary: str = ""

    @property
    def dual_grade_code(self) -> str:
        """Standard trade shorthand: 'NF / VG', 'F / F', or 'VG (Without Jacket)'."""
        b_code = self.book_grade.short_abbreviation
        if not self.has_jacket or self.jacket_grade is None:
            return f"{b_code} (Without Jacket)"
        j_code = self.jacket_grade.short_abbreviation
        return f"{b_code} / {j_code}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dual_grade_code": self.dual_grade_code,
            "book_grade": self.book_grade.value,
            "jacket_grade": self.jacket_grade.value if self.jacket_grade else None,
            "has_jacket": self.has_jacket,
            "defects": [
                {
                    "code": d.code,
                    "category": d.category,
                    "severity": d.severity.value,
                    "label": d.label,
                    "catalog_phrase": d.catalog_phrase,
                }
                for d in self.defects
            ],
            "virtues": [
                {
                    "code": v.code,
                    "category": v.category,
                    "label": v.label,
                    "catalog_phrase": v.catalog_phrase,
                }
                for v in self.virtues
            ],
            "unclipped_price": self.unclipped_price,
            "is_price_clipped": self.is_price_clipped,
            "is_ex_library": self.is_ex_library,
            "is_signed_or_inscribed": self.is_signed_or_inscribed,
            "valuation_multiplier": round(self.valuation_multiplier, 2),
            "trade_catalog_blurb": self.trade_catalog_blurb,
            "diagnostic_summary": self.diagnostic_summary,
        }


class AntiquarianConditionInspector:
    """Expert diagnostic evaluator that produces ABAA/ILAB dual grades and authentic trade catalog copy."""

    @classmethod
    def inspect(
        cls,
        format_str: str,
        visible_cues: list[str] | tuple[str, ...],
        raw_spine_text: str = "",
        raw_copyright_text: str = "",
        raw_flap_text: str = "",
        edition_or_series_markers: list[str] | tuple[str, ...] = (),
        publisher_imprint: str = "",
        title: str = "",
        authors: list[str] | tuple[str, ...] = (),
    ) -> DualConditionReport:
        cues_set = {str(c).lower().strip() for c in visible_cues}
        flap_lower = str(raw_flap_text or "").lower()
        spine_lower = str(raw_spine_text or "").lower()
        copy_lower = str(raw_copyright_text or "").lower()
        markers_lower = " ".join(str(m).lower() for m in edition_or_series_markers)

        has_jacket = "hardcover_with_jacket" in format_str.lower() or "with_jacket" in format_str.lower()
        if not has_jacket and ("jacket" in flap_lower or "unclipped" in flap_lower or "flap" in flap_lower):
            has_jacket = True

        defects: list[AntiquarianDefect] = []
        virtues: list[AntiquarianVirtue] = []

        # ---------------------------------------------------------
        # 1. BINDING & SPINE DIAGNOSTICS
        # ---------------------------------------------------------
        if "cocked_spine" in cues_set or "cocked" in spine_lower:
            defects.append(
                AntiquarianDefect(
                    code="cocked_spine",
                    category="binding",
                    severity=DefectSeverity.MODERATE,
                    label="Cocked Spine (Leaning)",
                    catalog_phrase="spine somewhat cocked out of square",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        if "bumped_headcap_or_tail" in cues_set or "bumped spine" in spine_lower or "spine ends pushed" in spine_lower:
            defects.append(
                AntiquarianDefect(
                    code="bumped_spine_ends",
                    category="binding",
                    severity=DefectSeverity.MINOR,
                    label="Bumped Spine Extremities",
                    catalog_phrase="slight pushing and minor rubbing at spine crown and foot",
                    disqualifies_fine=True,
                    disqualifies_near_fine=False,
                )
            )

        if "bumped_corners" in cues_set or "bumped board" in cues_set or "blunt corners" in cues_set:
            defects.append(
                AntiquarianDefect(
                    code="bumped_corners",
                    category="binding",
                    severity=DefectSeverity.MINOR,
                    label="Bumped Board Corners",
                    catalog_phrase="tips lightly bumped with minor shelf wear",
                    disqualifies_fine=True,
                    disqualifies_near_fine=False,
                )
            )

        if "cracked_hinge" in cues_set or "starting hinge" in cues_set or "split hinge" in copy_lower:
            defects.append(
                AntiquarianDefect(
                    code="cracked_hinge",
                    category="binding",
                    severity=DefectSeverity.SEVERE,
                    label="Cracked Inner Hinge",
                    catalog_phrase="front inner hinge starting along gutter",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        if "sunned_faded_spine" in cues_set or "faded cloth" in spine_lower:
            defects.append(
                AntiquarianDefect(
                    code="sunned_spine",
                    category="binding",
                    severity=DefectSeverity.MODERATE,
                    label="Sun-Faded Spine Cloth",
                    catalog_phrase="spine cloth mildly sun-toned with gilt lettering remaining clear",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        if "creased_reading_spine" in cues_set or "creased spine" in spine_lower:
            defects.append(
                AntiquarianDefect(
                    code="creased_spine",
                    category="binding",
                    severity=DefectSeverity.MODERATE,
                    label="Reading Spine Creasing",
                    catalog_phrase="vertical reading creasing along spine",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        # ---------------------------------------------------------
        # 2. DUST JACKET DIAGNOSTICS & FLAP PRICE
        # ---------------------------------------------------------
        unclipped_price: str | None = None
        is_price_clipped = False

        price_match = re.search(r"\$\s*(\d+\.\d{2})", raw_flap_text or "")
        if price_match:
            unclipped_price = f"${price_match.group(1)}"
            virtues.append(
                AntiquarianVirtue(
                    code="unclipped_price",
                    category="jacket",
                    label=f"Original Price Intact ({unclipped_price})",
                    catalog_phrase=f"front flap unclipped with original {unclipped_price} publisher price intact",
                )
            )
        elif "unclipped" in flap_lower or "price intact" in flap_lower:
            unclipped_price = "Intact"
            virtues.append(
                AntiquarianVirtue(
                    code="unclipped_price",
                    category="jacket",
                    label="Price-Unclipped Dust Jacket",
                    catalog_phrase="front flap unclipped with original price intact",
                )
            )

        if "price_clipped" in cues_set or "clipped flap" in flap_lower or "price clipped" in flap_lower:
            is_price_clipped = True
            defects.append(
                AntiquarianDefect(
                    code="price_clipped",
                    category="jacket",
                    severity=DefectSeverity.MINOR,
                    label="Price-Clipped Front Flap",
                    catalog_phrase="front flap diagonally price-clipped at corner",
                    disqualifies_fine=True,
                    disqualifies_near_fine=False,
                )
            )

        if "torn_or_chipped_jacket" in cues_set or "chipped" in flap_lower or "tears" in flap_lower:
            defects.append(
                AntiquarianDefect(
                    code="chipped_jacket",
                    category="jacket",
                    severity=DefectSeverity.MODERATE,
                    label="Chipping & Minor Edgewear",
                    catalog_phrase="minor chipping to spine head and tail with short closed edge tears",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        # ---------------------------------------------------------
        # 3. TEXT BLOCK, PAPER & PROVENANCE DIAGNOSTICS
        # ---------------------------------------------------------
        is_ex_library = False
        if "ex_library_stickers_or_tape" in cues_set or "ex-library" in markers_lower or "ex library" in markers_lower:
            is_ex_library = True
            defects.append(
                AntiquarianDefect(
                    code="ex_library",
                    category="provenance",
                    severity=DefectSeverity.SEVERE,
                    label="Ex-Library Markings",
                    catalog_phrase="ex-library copy with card pocket, stamps, and library label remnants",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        if "remainder_mark_visible" in cues_set or "remainder mark" in cues_set or "remainder" in markers_lower:
            defects.append(
                AntiquarianDefect(
                    code="remainder_mark",
                    category="text_block",
                    severity=DefectSeverity.MINOR,
                    label="Remainder Mark to Edge",
                    catalog_phrase="discrete remainder spray mark to bottom text block edge",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        if "foxing" in cues_set or "foxing" in copy_lower or "foxed" in copy_lower:
            defects.append(
                AntiquarianDefect(
                    code="foxing",
                    category="text_block",
                    severity=DefectSeverity.MODERATE,
                    label="Foxing on Leaves / Edges",
                    catalog_phrase="light scattered foxing across prelims and text block edges",
                    disqualifies_fine=True,
                    disqualifies_near_fine=True,
                )
            )

        is_signed_or_inscribed = False
        if "signed" in markers_lower or "inscribed" in markers_lower or "signed" in copy_lower:
            is_signed_or_inscribed = True
            virtues.append(
                AntiquarianVirtue(
                    code="author_signature",
                    category="provenance",
                    label="Author Signed / Inscribed",
                    catalog_phrase="signed by the author on title page",
                )
            )

        # Virtues: Clean & Tight binding
        if "clean_as_new" in cues_set or (not defects and not is_ex_library):
            virtues.append(
                AntiquarianVirtue(
                    code="tight_square_binding",
                    category="binding",
                    label="Tight, Square Binding",
                    catalog_phrase="binding tight, square, and firm in cloth",
                )
            )
            virtues.append(
                AntiquarianVirtue(
                    code="clean_pages",
                    category="text_block",
                    label="Clean Text Block",
                    catalog_phrase="pages crisp, unmarked, and without prior owner inscriptions",
                )
            )

        # ---------------------------------------------------------
        # 4. COMPUTE DUAL ABAA CONDITION GRADES
        # ---------------------------------------------------------
        binding_defects = [d for d in defects if d.category == "binding"]
        text_defects = [d for d in defects if d.category in ("text_block", "provenance")]
        jacket_defects = [d for d in defects if d.category == "jacket"]

        # A. Book / Binding Grade
        book_relevant_defects = binding_defects + text_defects
        if is_ex_library:
            book_grade = AbaaConditionGrade.FAIR if any(d.severity == DefectSeverity.SEVERE for d in binding_defects) else AbaaConditionGrade.GOOD
        elif any(d.code == "cracked_hinge" for d in binding_defects):
            book_grade = AbaaConditionGrade.GOOD
        elif any(d.code == "cocked_spine" for d in binding_defects):
            book_grade = AbaaConditionGrade.VERY_GOOD
        elif book_relevant_defects:
            has_moderate = any(d.severity in (DefectSeverity.MODERATE, DefectSeverity.SEVERE) for d in book_relevant_defects)
            if has_moderate:
                book_grade = AbaaConditionGrade.VERY_GOOD
            else:
                book_grade = AbaaConditionGrade.NEAR_FINE
        else:
            book_grade = AbaaConditionGrade.FINE if "clean_as_new" in cues_set else AbaaConditionGrade.NEAR_FINE

        # B. Dust Jacket Grade
        jacket_grade: AbaaConditionGrade | None = None
        if has_jacket:
            if not jacket_defects:
                jacket_grade = AbaaConditionGrade.FINE if "clean_as_new" in cues_set else AbaaConditionGrade.NEAR_FINE
            elif any(d.severity == DefectSeverity.SEVERE for d in jacket_defects):
                jacket_grade = AbaaConditionGrade.GOOD
            elif any(d.severity == DefectSeverity.MODERATE for d in jacket_defects):
                jacket_grade = AbaaConditionGrade.VERY_GOOD
            else:
                # Minor defects (e.g. price-clipped only or micro rubbing)
                jacket_grade = AbaaConditionGrade.VERY_GOOD if is_price_clipped else AbaaConditionGrade.NEAR_FINE

        # ---------------------------------------------------------
        # 5. VALUATION MULTIPLIER (DISCOUNT / PREMIUM)
        # ---------------------------------------------------------
        # Antiquarian benchmark is standard Near Fine copy (1.0)
        grade_weights = {
            AbaaConditionGrade.AS_NEW: 1.25,
            AbaaConditionGrade.FINE: 1.15,
            AbaaConditionGrade.NEAR_FINE: 1.00,
            AbaaConditionGrade.VERY_GOOD: 0.70,
            AbaaConditionGrade.GOOD: 0.40,
            AbaaConditionGrade.FAIR: 0.20,
            AbaaConditionGrade.POOR: 0.10,
        }

        b_weight = grade_weights[book_grade]
        if has_jacket and jacket_grade is not None:
            j_weight = grade_weights[jacket_grade]
            # Dust jacket carries 60% of collectible value on modern 20th-century firsts
            mult = (b_weight * 0.40) + (j_weight * 0.60)
        else:
            mult = b_weight * 0.50  # Books lacking dust jacket suffer significant collectible penalty

        if is_ex_library:
            mult *= 0.35  # Severe penalty for library discards

        if is_signed_or_inscribed:
            mult *= 1.80  # Significant premium for author signature

        if is_price_clipped:
            mult *= 0.88  # 12% discount for clipped front flap

        # ---------------------------------------------------------
        # 6. TRADE CATALOG DESCRIPTION GENERATOR (ABAA STANDARD)
        # ---------------------------------------------------------
        blurb_parts: list[str] = []

        # Format & Binding statement
        fmt_label = "Octavo (8vo)"
        if "paperback" in format_str.lower():
            fmt_label = "Trade Paperback" if "trade" in format_str.lower() else "Mass Market Paperback (12mo)"
            binding_stmt = f"{fmt_label}. Original pictorial wraps"
        elif "cloth" in format_str.lower() or "hardcover" in format_str.lower():
            binding_stmt = f"{fmt_label}. Bound in publisher's original cloth, titles stamped to spine"
        else:
            binding_stmt = f"{fmt_label}. Publisher's binding"

        blurb_parts.append(f"{binding_stmt}.")

        # Book condition clause
        book_flaw_clauses = [d.catalog_phrase for d in binding_defects + text_defects]
        if book_grade in (AbaaConditionGrade.FINE, AbaaConditionGrade.AS_NEW) and not book_flaw_clauses:
            blurb_parts.append("A Fine copy, crisp, tight, and square.")
        elif book_grade == AbaaConditionGrade.NEAR_FINE and not book_flaw_clauses:
            blurb_parts.append("Near Fine with faint shelf rubbing to extremities.")
        elif book_flaw_clauses:
            flaw_str = "; ".join(book_flaw_clauses)
            blurb_parts.append(f"{book_grade.value}, with {flaw_str}.")
        else:
            blurb_parts.append(f"A solid {book_grade.value} copy.")

        # Dust Jacket clause
        if has_jacket and jacket_grade is not None:
            jacket_flaw_clauses = [d.catalog_phrase for d in jacket_defects]
            if unclipped_price and unclipped_price != "Intact":
                price_clause = f"({unclipped_price} price intact on flap)"
            elif is_price_clipped:
                price_clause = "(price-clipped)"
            else:
                price_clause = ""

            if jacket_grade in (AbaaConditionGrade.FINE, AbaaConditionGrade.NEAR_FINE) and not jacket_flaw_clauses:
                blurb_parts.append(f"In a {jacket_grade.value} dust jacket {price_clause}, vibrant and sharp.")
            elif jacket_flaw_clauses:
                j_flaw_str = "; ".join(jacket_flaw_clauses)
                blurb_parts.append(f"In a {jacket_grade.value} dust jacket {price_clause}, showing {j_flaw_str}.")
            else:
                blurb_parts.append(f"In a {jacket_grade.value} dust jacket {price_clause}.")
        elif not has_jacket and "hardcover" in format_str.lower():
            blurb_parts.append("Issued without dust jacket (or jacket lacking).")

        # Text block & interior note
        if is_signed_or_inscribed:
            blurb_parts.append("Boldly signed by the author on the title page.")
        if is_ex_library:
            blurb_parts.append("Ex-library copy with standard administrative markings.")
        elif not any(d.code in ("foxing", "remainder_mark", "cracked_hinge") for d in defects):
            blurb_parts.append("Text block clean, square, and firmly bound throughout.")

        trade_catalog_blurb = " ".join(blurb_parts)

        # Diagnostic Summary
        short_code = f"{book_grade.short_abbreviation} / {jacket_grade.short_abbreviation}" if (has_jacket and jacket_grade) else f"{book_grade.short_abbreviation}"
        flaw_summary = f"{len(defects)} defect{'s' if len(defects) != 1 else ''}" if defects else "zero major defects"
        diagnostic_summary = f"ABAA Dual Grade: {short_code} ({flaw_summary}, {mult:.2f}x valuation factor)"

        return DualConditionReport(
            book_grade=book_grade,
            jacket_grade=jacket_grade,
            has_jacket=has_jacket,
            defects=defects,
            virtues=virtues,
            unclipped_price=unclipped_price,
            is_price_clipped=is_price_clipped,
            is_ex_library=is_ex_library,
            is_signed_or_inscribed=is_signed_or_inscribed,
            valuation_multiplier=mult,
            trade_catalog_blurb=trade_catalog_blurb,
            diagnostic_summary=diagnostic_summary,
        )
