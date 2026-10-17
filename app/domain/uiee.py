from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.inventory import InventoryItem


@dataclass
class UIEERecord:
    """Represents a single record in the Universal Information Exchange Environment (UIEE) format.

    UIEE is the global standard plain-text exchange format for antiquarian,
    used, and out-of-print books used by AbeBooks, Biblio, Alibris, and ILAB.
    """

    sku: str
    title: str
    author: str
    price_cad: Decimal
    binding: str = "Hardcover"
    condition: str = "Good"
    jacket_condition: str = ""
    description: str = ""
    section: str = "General"
    shelf: str = ""
    isbn: str = ""
    publisher: str = ""
    year: str = ""
    edition: str = ""
    status: str = "Y"  # Y = in stock/active, N = sold/delisted, D = delete

    def to_uiee(self) -> str:
        """Formats the record into standard UIEE tag-delimited lines."""
        lines: list[str] = [
            f"UR|{self.sku}",
            f"TI|{self.title}",
            f"AA|{self.author}",
            f"BD|{self.binding.title()}",
            f"CO|{self.condition}",
        ]
        if self.jacket_condition:
            lines.append(f"JK|{self.jacket_condition}")
        if self.publisher:
            lines.append(f"PU|{self.publisher}")
        if self.year:
            lines.append(f"DP|{self.year}")
        if self.edition:
            lines.append(f"ED|{self.edition}")
        if self.isbn:
            lines.append(f"IS|{self.isbn}")

        lines.append(f"PR|{self.price_cad:.2f}")

        if self.section:
            lines.append(f"KW|{self.section}")
        if self.shelf:
            lines.append(f"LO|{self.shelf}")
        if self.description:
            # Clean newlines from description in single-line tag
            clean_desc = " ".join(self.description.splitlines())
            lines.append(f"DE|{clean_desc}")

        lines.append(f"SY|{self.status}")
        return "\n".join(lines)


class UIEEExporter:
    """Handles serializing inventory collections into UIEE files and parsing inbound UIEE files."""

    @staticmethod
    def item_to_record(item: InventoryItem, action: str = "active") -> UIEERecord:
        author_str = ", ".join(item.authors) if item.authors else "Unknown"
        status_flag = "Y" if item.status == "active" else "N"
        if action == "delist":
            status_flag = "N"
        elif action == "delete":
            status_flag = "D"

        desc = f"Format: {item.format}. Condition: {item.condition}."
        if item.section:
            desc += f" Section: {item.section}."
        if item.shelf:
            desc += f" Shelf: {item.shelf}."

        return UIEERecord(
            sku=item.sku,
            title=item.title,
            author=author_str,
            price_cad=item.online_price_cad,
            binding=item.format,
            condition=item.condition,
            description=desc,
            section=item.section,
            shelf=item.shelf,
            status=status_flag,
        )

    @classmethod
    def export_catalog(
        cls,
        items: list[InventoryItem],
        status_filter: str | None = None,
        section_filter: str | None = None,
    ) -> str:
        """Exports a list of InventoryItems into a standard multi-record UIEE document.

        Records in UIEE are separated by a blank line or record terminator.
        """
        filtered = items
        if status_filter and status_filter.lower() != "all":
            filtered = [i for i in filtered if i.status.lower() == status_filter.lower()]
        if section_filter and section_filter.lower() != "all":
            filtered = [i for i in filtered if i.section.lower() == section_filter.lower()]

        records: list[str] = []
        for item in filtered:
            rec = cls.item_to_record(item)
            records.append(rec.to_uiee())

        # Header comments are common and supported in UIEE using # or !
        header = (
            "# UIEE Export - Ops-Core Antiquarian Catalog Engine\n"
            f"# Generated for AbeBooks / Biblio / Alibris - Total Records: {len(records)}\n"
            "# ----------------------------------------------------------------------\n"
        )
        return header + "\n\n".join(records) + "\n"

    @classmethod
    def parse_uiee(cls, content: str) -> list[dict[str, str]]:
        """Parses a UIEE file string into a list of key-value dictionaries."""
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}

        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                if current and "UR" in current:
                    records.append(current)
                    current = {}
                continue

            if "|" in line:
                tag, _, val = line.partition("|")
                tag = tag.strip().upper()
                val = val.strip()
                current[tag] = val

        if current and "UR" in current:
            records.append(current)

        return records
