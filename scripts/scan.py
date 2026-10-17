#!/usr/bin/env python3
"""Search Amazon for candidates and triage them, one request per search term.

    python scripts/scan.py "garlic keeper" "bread box" "utensil crock"
    python scripts/scan.py --terms terms.txt
    python scripts/scan.py --dry-run "garlic keeper"     # spends nothing

Each term costs one request and returns up to sixty products. Those are filtered
on title, price, rating, review count and seller count — all of which the search
page already carries — so the per-product requests are spent only on the handful
worth a closer look.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amazon.prefilter import QuickCandidate, prefilter  # noqa: E402
from app.amazon.scraper import (  # noqa: E402
    AmazonScraperClient,
    ScraperError,
    SearchResult,
    SnapshotStore,
)

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[36m",
    "\033[0m",
)

#: A candidate must show a real quality gap and unproven-but-present demand.
#: These mirror ScreenPolicy but run on what a search page alone can tell us.
MIN_REVIEWS, MAX_REVIEWS = 75, 5_000
MAX_RATING = Decimal("4.4")
MAX_SELLERS = 8


def triage(result: SearchResult) -> list[str]:
    """Reasons this row is not worth a product request. Empty means look closer."""
    reasons = [f.message for f in prefilter(QuickCandidate(result.asin, result.title, result.price)) if not f.soft]
    if result.is_sponsored:
        reasons.append("sponsored placement — not an organic demand signal")
    if result.reviews_count < MIN_REVIEWS:
        reasons.append(f"{result.reviews_count} reviews — demand unproven")
    if result.reviews_count > MAX_REVIEWS:
        reasons.append(f"{result.reviews_count:,} reviews — entrenched")
    if result.rating > MAX_RATING:
        reasons.append(f"{result.rating} stars — no quality gap")
    if result.seller_count > MAX_SELLERS:
        reasons.append(f"{result.seller_count} sellers — reseller pile-on")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="*", help="search terms")
    parser.add_argument("--terms", dest="file", type=Path, help="file with one term per line")
    parser.add_argument("--dry-run", action="store_true", help="spend no requests")
    parser.add_argument("--db", default="snapshots.db")
    args = parser.parse_args()

    terms = list(args.terms)
    if args.file:
        terms += [t.strip() for t in args.file.read_text().splitlines() if t.strip()]
    if not terms:
        print(__doc__)
        return 2

    key = os.getenv("ASA_API_KEY", "")
    if not key:
        print(f"{RED}ASA_API_KEY is not set (source your .env){RESET}")
        return 2

    store = SnapshotStore(args.db)
    client = AmazonScraperClient(key, store, live=not args.dry_run)
    print(f"\n  {DIM}budget: {client.remaining} requests remaining{RESET}")

    keepers: list[tuple[str, SearchResult]] = []
    for term in terms:
        try:
            results = client.search(term)
        except ScraperError as exc:
            print(f"\n  {RED}{term}: {exc}{RESET}")
            continue

        survivors = [(r, triage(r)) for r in results]
        passed = [r for r, reasons in survivors if not reasons]
        print(f"\n  {BOLD}{term}{RESET}  {DIM}{len(results)} found, {len(passed)} worth a look{RESET}")
        for result in passed:
            volume = f"{result.monthly_sales:,}/mo" if result.monthly_sales else "—"
            print(
                f"    {GREEN}{result.asin}{RESET}  ${result.price:>6}  {result.rating}* "
                f"{result.reviews_count:>5} rev  {volume:>9}  {result.title[:52]}"
            )
            keepers.append((term, result))

    print(f"\n  {BOLD}{len(keepers)} candidates across {len(terms)} terms{RESET}")
    print(f"  {DIM}budget: {client.remaining} remaining{RESET}")
    if keepers:
        asins = " ".join(r.asin for _, r in keepers)
        print(f"\n  {CYAN}Next — deep-fetch these (one request each):{RESET}")
        print(f"  {DIM}python scripts/enrich.py {asins}{RESET}")
    store.close()
    return 0 if keepers else 1


if __name__ == "__main__":
    raise SystemExit(main())
