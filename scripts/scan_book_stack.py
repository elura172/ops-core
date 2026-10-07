#!/usr/bin/env python3
"""Book Stack Vision Scanner & Disambiguation Engine for Used Bookstore Intake.

Usage:
    python scripts/scan_book_stack.py <image_path>
    python scripts/scan_book_stack.py <image_path> --crops ./spine_crops/
    python scripts/scan_book_stack.py <image_path> --json
    python scripts/scan_book_stack.py --demo
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import uuid
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.book_vision import (  # noqa: E402
    GeminiBookVisionProvider,
    resolve_gemini_api_key,
)
from app.db.factory import open_review_store  # noqa: E402
from app.discovery.books import OpenLibraryMetadataResolver  # noqa: E402
from app.discovery.pricing import BookValuationEngine  # noqa: E402
from app.domain.books import (  # noqa: E402
    BookStackExtraction,
    BookValuation,
    DisambiguationResult,
    SpineExtractionItem,
)
from app.review.queue import ReviewItem  # noqa: E402

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


MOCK_DEMO_EXTRACTION = {
    "orientation": "horizontal_stack",
    "unidentified_spines_count": 0,
    "books": [
        {
            "stack_order": 1,
            "box_2d": [50, 120, 220, 880],
            "title": "Dune",
            "subtitle": None,
            "authors": ["Frank Herbert"],
            "publisher_imprint": "Chilton Book Company",
            "format": "hardcover_with_jacket",
            "visible_condition_cues": ["clean_as_new"],
            "edition_or_series_markers": ["First Edition"],
            "raw_spine_text": "Dune Frank Herbert Chilton",
            "confidence": "high",
        },
        {
            "stack_order": 2,
            "box_2d": [230, 110, 410, 890],
            "title": "Neuromancer",
            "subtitle": None,
            "authors": ["William Gibson"],
            "publisher_imprint": "Ace Science Fiction",
            "format": "mass_market_paperback",
            "visible_condition_cues": ["creased_reading_spine", "sunned_faded_spine"],
            "edition_or_series_markers": [],
            "raw_spine_text": "Ace SF 0-441-56959-5 Neuromancer William Gibson",
            "confidence": "high",
        },
        {
            "stack_order": 3,
            "box_2d": [420, 105, 600, 900],
            "title": "The Left Hand of Darkness",
            "subtitle": None,
            "authors": ["Ursula K. Le Guin"],
            "publisher_imprint": "Walker and Company",
            "format": "hardcover_no_jacket",
            "visible_condition_cues": ["bumped_headcap_or_tail"],
            "edition_or_series_markers": ["SF Book Club"],
            "raw_spine_text": "The Left Hand of Darkness Ursula K. Le Guin Walker",
            "confidence": "high",
        },
        {
            "stack_order": 4,
            "box_2d": [610, 100, 850, 910],
            "title": "Fahrenheit 451",
            "subtitle": None,
            "authors": ["Ray Bradbury"],
            "publisher_imprint": "Ballantine Books",
            "format": "trade_paperback",
            "visible_condition_cues": ["clean_as_new"],
            "edition_or_series_markers": ["50th Anniversary Edition"],
            "raw_spine_text": "FAHRENHEIT 451 RAY BRADBURY Ballantine",
            "confidence": "high",
        },
    ],
}


def crop_spines(
    image_path: Path, books: list[SpineExtractionItem], output_dir: Path
) -> None:
    """Crop each spine from the image using box_2d coordinates [ymin, xmin, ymax, xmax]."""
    try:
        from PIL import Image
    except ImportError:
        print(f"{YELLOW}Pillow not installed; skipping image spine cropping.{RESET}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as img:
        width, height = img.size
        for book in books:
            ymin, xmin, ymax, xmax = book.box_2d
            left = int(xmin * width / 1000.0)
            top = int(ymin * height / 1000.0)
            right = int(xmax * width / 1000.0)
            bottom = int(ymax * height / 1000.0)

            left = max(0, min(left, width - 1))
            top = max(0, min(top, height - 1))
            right = max(left + 1, min(right, width))
            bottom = max(top + 1, min(bottom, height))

            crop = img.crop((left, top, right, bottom))
            safe_title = "".join(c if c.isalnum() else "_" for c in book.title)[:25]
            out_file = output_dir / f"spine_{book.stack_order:02d}_{safe_title}.jpg"
            crop.save(out_file, "JPEG", quality=90)
            print(f"  {DIM}Saved crop: {out_file}{RESET}")


def get_spine_crop_base64(image_path: Path, book: SpineExtractionItem) -> str | None:
    """Extract a spine as a base64 encoded JPEG string."""
    try:
        from PIL import Image
    except ImportError:
        return None

    import base64
    from io import BytesIO

    with Image.open(image_path) as img:
        width, height = img.size
        ymin, xmin, ymax, xmax = book.box_2d
        left = max(0, min(int(xmin * width / 1000.0), width - 1))
        top = max(0, min(int(ymin * height / 1000.0), height - 1))
        right = max(left + 1, min(int(xmax * width / 1000.0), width))
        bottom = max(top + 1, min(int(ymax * height / 1000.0), height))

        crop = img.crop((left, top, right, bottom))
        buffer = BytesIO()
        crop.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def print_spine_table(extraction: BookStackExtraction) -> None:
    print(f"\n{BOLD}{CYAN}=== 1. PHYSICAL STACK EXTRACTION ==={RESET}")
    unid_count = extraction.unidentified_spines_count
    unid_color = RED if unid_count else DIM
    print(
        f"Orientation: {BOLD}{extraction.orientation}{RESET} | "
        f"Identified Spines: {GREEN}{len(extraction.books)}{RESET} | "
        f"Unidentified: {unid_color}{unid_count}{RESET}\n"
    )

    fmt_header = (
        f"{BOLD}{'#':<3} {'TITLE':<32} {'AUTHOR':<22} "
        f"{'FORMAT':<22} {'COND CUES':<25} {'CONF':<6}{RESET}"
    )
    print(fmt_header)
    print("-" * 115)

    for b in extraction.books:
        author_str = ", ".join(b.authors) if b.authors else "Unknown"
        cues_str = (
            ", ".join(b.visible_condition_cues) if b.visible_condition_cues else "none"
        )

        title_disp = (b.title[:29] + "...") if len(b.title) > 32 else b.title
        author_disp = (author_str[:19] + "...") if len(author_str) > 22 else author_str
        format_disp = b.format.value
        cues_disp = (cues_str[:22] + "...") if len(cues_str) > 25 else cues_str

        conf_color = (
            GREEN
            if b.confidence == "high"
            else (YELLOW if b.confidence == "medium" else RED)
        )

        print(
            f"{b.stack_order:<3} "
            f"{BOLD}{title_disp:<32}{RESET} "
            f"{author_disp:<22} "
            f"{CYAN}{format_disp:<22}{RESET} "
            f"{DIM}{cues_disp:<25}{RESET} "
            f"{conf_color}{b.confidence:<6}{RESET}"
        )

        if b.publisher_imprint or b.edition_or_series_markers:
            extra = []
            if b.publisher_imprint:
                extra.append(f"Publisher: {b.publisher_imprint}")
            if b.edition_or_series_markers:
                extra.append(f"Markers: {', '.join(b.edition_or_series_markers)}")
            print(f"    {MAGENTA}↳ {', '.join(extra)}{RESET}")

    print("-" * 115)


def print_disambiguation_table(results: list[DisambiguationResult]) -> None:
    print(f"\n{BOLD}{CYAN}=== 2. CATALOG DISAMBIGUATION & HUMAN TRIAGE ==={RESET}\n")
    fmt_header = (
        f"{BOLD}{'#':<3} {'MATCHED CANONICAL TITLE':<30} {'WORK ID':<13} "
        f"{'PUB YEAR':<9} {'MATCH':<7} {'TRIAGE STATUS':<18} {'ACTION'}{RESET}"
    )
    print(fmt_header)
    print("=" * 115)

    for res in results:
        b = res.spine_item
        cand = res.best_candidate
        if cand:
            title_disp = (cand.title[:27] + "...") if len(cand.title) > 30 else cand.title
            work_disp = cand.work_id[:12]
            year_disp = str(cand.first_publish_year or "Unknown")
            score_disp = f"{cand.match_score:.0%}"
        else:
            title_disp = f"{RED}No match found{RESET}"
            work_disp = "-"
            year_disp = "-"
            score_disp = "0%"

        if res.needs_copyright_check:
            status_disp = f"{YELLOW}CHECK COPYRIGHT{RESET}"
            action_disp = f"{YELLOW}{res.suggested_catalog_action}{RESET}"
        else:
            status_disp = f"{GREEN}READY TO PRICE{RESET}"
            action_disp = f"{GREEN}{res.suggested_catalog_action}{RESET}"

        print(
            f"{b.stack_order:<3} "
            f"{BOLD}{title_disp:<30}{RESET} "
            f"{CYAN}{work_disp:<13}{RESET} "
            f"{year_disp:<9} "
            f"{score_disp:<7} "
            f"{status_disp:<27} "
            f"{action_disp}"
        )

        # Print triage explanation
        print(f"    {DIM}↳ Triage note: {res.triage_reason}{RESET}")
        if cand and cand.primary_isbn:
            isbn_info = f"Best ISBN: {cand.primary_isbn} | Editions in DB: {cand.edition_count}"
            print(f"    {DIM}↳ {isbn_info}{RESET}")

    print("\n" + "=" * 115)


def print_valuation_table(results: list[tuple[DisambiguationResult, BookValuation]]) -> None:
    print(f"\n{BOLD}{CYAN}=== 3. LIVE MARKET VALUATION & PRICING ==={RESET}\n")
    fmt_header = (
        f"{BOLD}{'#':<3} {'TITLE':<30} {'CONDITION':<12} "
        f"{'IN-STORE':<10} {'ONLINE':<10} {'STRATEGY':<22} {'CONFIDENCE'}{RESET}"
    )
    print(fmt_header)
    print("=" * 115)

    for res, val in results:
        b = res.spine_item
        cand = res.best_candidate
        title_to_use = cand.title if cand else b.title
        title_disp = (title_to_use[:27] + "...") if len(title_to_use) > 30 else title_to_use
        
        in_store = f"${val.in_store_price_cad:.2f}" if val.in_store_price_cad > 0 else "-"
        online = f"${val.online_price_cad:.2f}" if val.online_price_cad > 0 else "-"
        
        strat_color = (
            GREEN if val.channel_strategy == "in_store_and_online" 
            else (YELLOW if "collectible" in val.channel_strategy else DIM)
        )
        strat_disp = val.channel_strategy.replace("_", " ").title()

        conf_color = (
            GREEN if val.price_confidence == "high" 
            else (YELLOW if val.price_confidence == "medium" else RED)
        )

        print(
            f"{b.stack_order:<3} "
            f"{BOLD}{title_disp:<30}{RESET} "
            f"{val.condition_grade.value:<12} "
            f"{GREEN}{in_store:<10}{RESET} "
            f"{CYAN}{online:<10}{RESET} "
            f"{strat_color}{strat_disp:<22}{RESET} "
            f"{conf_color}{val.price_confidence}{RESET}"
        )
        print(f"    {DIM}↳ Rationale: {val.pricing_rationale}{RESET}")
        if val.comps:
            print(f"    {DIM}↳ Live Comps Found: {len(val.comps)}{RESET}")

    print("\n" + "=" * 115)


def resolve_extractions(
    extraction: BookStackExtraction, resolver: OpenLibraryMetadataResolver
) -> list[DisambiguationResult]:
    print(f"{CYAN}Resolving {len(extraction.books)} spines against Open Library...{RESET}")
    results = []
    for book in extraction.books:
        res = resolver.resolve(book)
        results.append(res)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract and disambiguate book stacks for Ottawa used bookstore intake."
    )
    parser.add_argument("image", nargs="?", help="Path to book stack image file")
    parser.add_argument(
        "--demo", action="store_true", help="Run with built-in mock sample data"
    )
    parser.add_argument(
        "--crops", type=Path, help="Directory to save cropped spine slices"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw structured JSON"
    )
    parser.add_argument(
        "--no-resolve", action="store_true", help="Skip catalog disambiguation lookup"
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini vision model (default: gemini-2.5-flash)",
    )
    parser.add_argument(
        "--api-key",
        help="Gemini API key (defaults to GEMINI_API_KEY env or ~/.hermes/.env)",
    )
    parser.add_argument(
        "--submit", action="store_true", help="Submit extracted valuations to the Review Desk queue"
    )
    parser.add_argument(
        "--db", default="review.db", help="Path to SQLite review database (default: review.db)"
    )

    args = parser.parse_args()

    if args.demo or not args.image:
        if not args.demo and not args.image:
            print(
                f"{YELLOW}No image supplied. Running in --demo mode to showcase output.{RESET}\n"
            )
        extraction = BookStackExtraction.from_dict(MOCK_DEMO_EXTRACTION)
    else:
        img_path = Path(args.image)
        if not img_path.is_file():
            print(f"{RED}Error: File not found: {img_path}{RESET}", file=sys.stderr)
            sys.exit(1)

        mime_type, _ = mimetypes.guess_type(img_path)
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/jpeg"

        try:
            api_key = resolve_gemini_api_key(args.api_key)
        except ValueError as exc:
            print(f"{RED}Error: {exc}{RESET}", file=sys.stderr)
            sys.exit(1)

        print(f"{CYAN}Scanning {img_path.name} with model {args.model}...{RESET}")
        provider = GeminiBookVisionProvider(api_key=api_key, model=args.model)

        image_bytes = img_path.read_bytes()
        try:
            extraction = provider.extract_stack(image_bytes, mime_type=mime_type)
        except Exception as exc:
            print(f"{RED}Vision extraction failed: {exc}{RESET}", file=sys.stderr)
            sys.exit(1)

        if args.crops:
            print(f"\n{CYAN}Extracting spine slices to {args.crops}...{RESET}")
            crop_spines(img_path, list(extraction.books), args.crops)

    # Disambiguation resolution
    disambiguation_results: list[DisambiguationResult] = []
    if not args.no_resolve:
        resolver = OpenLibraryMetadataResolver()
        disambiguation_results = resolve_extractions(extraction, resolver)

    # Valuation resolution
    valuations: list[tuple[DisambiguationResult, BookValuation]] = []
    if not args.no_resolve and disambiguation_results:
        print(
            f"\n{CYAN}Pricing {len(disambiguation_results)} items against "
            f"live market comps...{RESET}"
        )
        engine = BookValuationEngine()
        for res in disambiguation_results:
            val = engine.value_book(res)
            valuations.append((res, val))

    if args.json:
        payload = {
            "extraction": extraction.to_dict(),
            "disambiguation": [r.to_dict() for r in disambiguation_results],
            "valuations": [v.to_dict() for _, v in valuations],
        }
        print(json.dumps(payload, indent=2))
    else:
        print_spine_table(extraction)
        if disambiguation_results:
            print_disambiguation_table(disambiguation_results)
        if valuations:
            print_valuation_table(valuations)

    if args.submit and valuations:
        store = open_review_store(args.db)
        for res, val in valuations:
            review_id = str(uuid.uuid4())
            extraction_dict = res.spine_item.to_dict()
            
            if img_path and not args.demo:
                b64_img = get_spine_crop_base64(img_path, res.spine_item)
                if b64_img:
                    extraction_dict["spine_image_base64"] = b64_img

            payload = {
                "extraction": extraction_dict,
                "disambiguation": {
                    "matched": res.matched,
                    "best_candidate": res.best_candidate.to_dict() if res.best_candidate else None,
                    "triage_reason": res.triage_reason,
                    "suggested_action": res.suggested_catalog_action
                },
                "valuation": val.to_dict()
            }
            item = ReviewItem(
                review_id=review_id,
                operation="intake_book",
                payload=payload
            )
            store.submit(item)
        print(f"\n{GREEN}✓ Submitted {len(valuations)} items to Review Desk ({args.db}).{RESET}")


if __name__ == "__main__":
    main()
