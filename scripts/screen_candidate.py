#!/usr/bin/env python3
"""Screen one hand-entered Amazon candidate. No API key, no subscription.

Pull the numbers off a product page and Keepa's free browser extension, type them
in, and get a verdict with every reason it passed or failed. The point is to test
whether the screening policy finds anything before paying for data at scale — if
twenty hand-picked candidates all die on the same constraint, that is worth
knowing for nothing.

    python scripts/screen_candidate.py                 # interactive
    python scripts/screen_candidate.py --example       # run a worked example
    python scripts/screen_candidate.py --json cand.json

JSON form (one object, or a list of them):

    {
      "asin": "B0XXXXXXX", "title": "Pour-over kettle",
      "category": "home_kitchen", "price": "32.99",
      "review_count": 900, "rating": "3.9", "seller_count": 2,
      "size_tier": "SMALL_STANDARD", "brand_registered": false,
      "ranks": [9000, 9400, 8800, 9100, 9300, 8900],
      "fob_unit_cost": "5.50", "freight_per_unit": "1.40",
      "duty_rate": "0.032", "tariff_rate": "0.25", "prep_per_unit": "0.60"
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amazon.fees import FeeSchedule, LandedCost, SizeTier  # noqa: E402
from app.amazon.models import AmazonListing, RankPoint  # noqa: E402
from app.amazon.screen import ScreenPolicy, rejection_summary, screen  # noqa: E402

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[0m",
)

EXAMPLE: dict[str, Any] = {
    "asin": "B0EXAMPLE1",
    "title": "Gooseneck Pour-Over Kettle, 1L",
    "category": "home_kitchen",
    "price": "32.99",
    "review_count": 900,
    "rating": "3.9",
    "seller_count": 2,
    "size_tier": "SMALL_STANDARD",
    "brand_registered": False,
    "ranks": [9000, 9400, 8800, 9100, 9300, 8900],
    "fob_unit_cost": "5.50",
    "freight_per_unit": "1.40",
    "duty_rate": "0.032",
    "tariff_rate": "0.25",
    "prep_per_unit": "0.60",
}

PROMPTS: list[tuple[str, str, str]] = [
    ("asin", "ASIN", ""),
    ("title", "Product title", ""),
    ("category", "Category key (home_kitchen, electronics, toys, tools...)", "home_kitchen"),
    ("price", "Current sale price", ""),
    ("review_count", "Review count", ""),
    ("rating", "Star rating (e.g. 3.9)", ""),
    ("seller_count", "Number of sellers on the listing", "1"),
    (
        "size_tier",
        "Size tier (SMALL_STANDARD / LARGE_STANDARD / LARGE_BULKY / EXTRA_LARGE)",
        "SMALL_STANDARD",
    ),
    ("brand_registered", "Is the incumbent brand-registered? (y/n)", "n"),
    ("ranks", "Sales ranks over time, oldest first, comma separated (from Keepa)", ""),
    ("fob_unit_cost", "Supplier FOB unit cost (quote or estimate)", ""),
    ("freight_per_unit", "Freight per unit", "1.40"),
    ("duty_rate", "Duty rate as a fraction (e.g. 0.032)", "0.03"),
    ("tariff_rate", "Section 301 tariff as a fraction (0, 0.075, 0.25)", "0.25"),
    ("prep_per_unit", "Prep centre cost per unit", "0.60"),
]


class NotInteractive(RuntimeError):
    """Raised when prompting is impossible — no TTY attached."""


def _ask() -> dict[str, Any]:
    # Prompting needs a real terminal. Agent shells, CI, and piped input have no
    # stdin, and input() raises EOFError on the first field; say so usefully
    # instead of dying with a traceback.
    if not sys.stdin.isatty():
        raise NotInteractive(
            "no terminal attached, so there is nothing to type into.\n"
            "  Use the file-based flow instead:\n"
            "    python scripts/screen_candidate.py --template > candidates.json\n"
            "    # fill candidates.json in, then\n"
            "    python scripts/screen_candidate.py --json candidates.json\n"
            "  Or see a worked example:\n"
            "    python scripts/screen_candidate.py --example"
        )
    print(f"{BOLD}Screen one candidate{RESET}  {DIM}(blank accepts the default){RESET}\n")
    data: dict[str, Any] = {}
    for key, label, default in PROMPTS:
        suffix = f" [{default}]" if default else ""
        while True:
            raw = input(f"  {label}{suffix}: ").strip() or default
            if raw:
                break
            print(f"    {RED}required{RESET}")
        data[key] = raw
    return data


def _build(data: dict[str, Any]) -> tuple[AmazonListing, LandedCost]:
    ranks = data["ranks"]
    if isinstance(ranks, str):
        ranks = [int(r.strip()) for r in ranks.split(",") if r.strip()]
    if not ranks:
        raise ValueError("at least one sales rank is required to estimate demand")

    start = date(2026, 1, 1)
    history = tuple(RankPoint(start + timedelta(days=30 * i), int(r)) for i, r in enumerate(ranks))
    registered = data["brand_registered"]
    if isinstance(registered, str):
        registered = registered.lower().startswith("y")

    listing = AmazonListing(
        asin=str(data["asin"]),
        title=str(data["title"]),
        category=str(data["category"]),
        price=Decimal(str(data["price"])),
        review_count=int(data["review_count"]),
        rating=Decimal(str(data["rating"])),
        seller_count=int(data["seller_count"]),
        size_tier=SizeTier(str(data["size_tier"]).upper()),
        rank_history=history,
        brand_registered=bool(registered),
    )
    landed = LandedCost(
        fob_unit_cost=Decimal(str(data["fob_unit_cost"])),
        freight_per_unit=Decimal(str(data.get("freight_per_unit", "0"))),
        duty_rate=Decimal(str(data.get("duty_rate", "0"))),
        tariff_rate=Decimal(str(data.get("tariff_rate", "0"))),
        prep_per_unit=Decimal(str(data.get("prep_per_unit", "0"))),
    )
    return listing, landed


def _report(listings: list[AmazonListing], costs: dict[str, LandedCost]) -> int:
    survivors, rejected = screen(
        listings,
        costs,
        policy=ScreenPolicy(),
        fees=FeeSchedule(),
        accept_estimates=True,
    )

    print()
    for opp in survivors:
        print(f"  {GREEN}{BOLD}PASS{RESET}  {opp.asin}  {opp.listing.title}")
        print(f"        score {opp.score}   headroom {opp.headroom} over break-even")
        for reason in opp.reasons:
            print(f"        {DIM}·{RESET} {reason}")
        print(f"        {DIM}break-even price {opp.break_even_price}{RESET}")

    for asin, reasons in rejected.items():
        title = next((ls.title for ls in listings if ls.asin == asin), "")
        print(f"  {RED}{BOLD}FAIL{RESET}  {asin}  {title}")
        for reason in reasons:
            print(f"        {DIM}·{RESET} {reason}")

    if len(rejected) > 1:
        print(f"\n  {BOLD}What is doing the killing{RESET}")
        for reason, count in rejection_summary(rejected).items():
            print(f"    {count:>3} x {reason}")
        print(
            f"  {DIM}If one reason dominates, the policy may be miscalibrated"
            f" rather than the market empty.{RESET}"
        )

    print(
        f"\n  {YELLOW}Fee schedule is unverified{RESET} — placeholder referral and FBA rates."
        f"\n  {DIM}Verify against Amazon's published US schedule before committing capital.{RESET}"
    )
    return 0 if survivors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="candidate JSON file (object or list)")
    parser.add_argument("--example", action="store_true", help="run a worked example")
    parser.add_argument(
        "--template", action="store_true", help="print a blank JSON candidate to fill in"
    )
    args = parser.parse_args()

    if args.template:
        blank = {
            k: ("" if k not in ("ranks", "brand_registered") else ([] if k == "ranks" else False))
            for k in EXAMPLE
        }
        blank["freight_per_unit"] = "1.40"
        blank["duty_rate"] = "0.03"
        blank["tariff_rate"] = "0.25"
        blank["prep_per_unit"] = "0.60"
        print(json.dumps([blank], indent=2))
        return 0

    if args.example:
        records = [EXAMPLE]
    elif args.json:
        loaded = json.loads(args.json.read_text())
        records = loaded if isinstance(loaded, list) else [loaded]
    else:
        try:
            records = [_ask()]
        except NotInteractive as exc:
            print(f"  {YELLOW}{exc}{RESET}")
            return 2
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}cancelled{RESET}")
            return 130

    listings: list[AmazonListing] = []
    costs: dict[str, LandedCost] = {}
    for record in records:
        try:
            listing, landed = _build(record)
        except (ValueError, KeyError, InvalidOperation) as exc:
            print(f"  {RED}skipped {record.get('asin', '?')}: {exc}{RESET}")
            continue
        listings.append(listing)
        costs[listing.asin] = landed

    if not listings:
        print(f"  {RED}no usable candidates{RESET}")
        return 2
    return _report(listings, costs)


if __name__ == "__main__":
    raise SystemExit(main())
