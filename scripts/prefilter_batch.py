#!/usr/bin/env python3
"""Triage a pasted bulk export before gathering Keepa data on any of it.

    python scripts/prefilter_batch.py export.txt
    pbpaste | python scripts/prefilter_batch.py -

Reads title, price and ASIN out of whatever column layout the export uses, then
rejects on structure alone. Survivors are the only rows worth opening Keepa for.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amazon.prefilter import parse_bulk, prefilter  # noqa: E402

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[0m",
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = sys.argv[1]
    text = sys.stdin.read() if source == "-" else Path(source).read_text()

    candidates = parse_bulk(text)
    if not candidates:
        print(f"{RED}no rows with an ASIN, a price and a title{RESET}")
        return 2

    survivors, counts = [], Counter()
    print()
    for candidate in candidates:
        findings = prefilter(candidate)
        hard = [f for f in findings if not f.soft]
        soft = [f for f in findings if f.soft]
        short = candidate.title[:64] + ("…" if len(candidate.title) > 64 else "")

        if hard:
            label = f"{RED}SKIP{RESET}"
        elif soft:
            label = f"{YELLOW}{BOLD}CHECK{RESET}"
            survivors.append(candidate)
        else:
            label = f"{GREEN}{BOLD}LOOK{RESET}"
            survivors.append(candidate)

        print(f"  {label:>16}  {candidate.asin}  ${candidate.price:>6}  {short}")
        for finding in hard:
            counts[finding.code] += 1
            print(f"           {DIM}· {finding.message}{RESET}")
        for finding in soft:
            print(f"           {YELLOW}· check: {finding.message}{RESET}")

    print(f"\n  {BOLD}{len(survivors)} of {len(candidates)} worth opening Keepa for{RESET}")
    if counts:
        print(f"  {BOLD}Why the rest went{RESET}")
        for code, n in counts.most_common():
            print(f"    {n:>3} x {code}")
    if survivors:
        print(f"\n  {DIM}Next, for each survivor: rating, review count, seller count,")
        print(f"  six rank points from Keepa, and an Alibaba FOB estimate.{RESET}")
    return 0 if survivors else 1


if __name__ == "__main__":
    raise SystemExit(main())
