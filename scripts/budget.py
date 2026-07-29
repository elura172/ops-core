#!/usr/bin/env python3
"""What a first product actually costs to launch.

python scripts/budget.py --units 300 --fob 7.50 --price 31.95
python scripts/budget.py --units 500 --fob 6.00 --price 31.95 --lean
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amazon.fees import LandedCost, SizeTier  # noqa: E402
from app.amazon.launch_budget import FixedCosts, LaunchPlan  # noqa: E402

BOLD, DIM, GREEN, RED, YELLOW, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[0m",
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--units", type=int, default=300)
    p.add_argument("--fob", type=Decimal, required=True, help="supplier unit price")
    p.add_argument("--price", type=Decimal, required=True, help="planned sale price")
    p.add_argument("--freight", type=Decimal, default=Decimal("1.60"))
    p.add_argument("--tariff", type=Decimal, default=Decimal("0.25"))
    p.add_argument("--duty", type=Decimal, default=Decimal("0.03"))
    p.add_argument("--prep", type=Decimal, default=Decimal("0.75"))
    p.add_argument("--ppc", type=Decimal, default=Decimal("750"))
    p.add_argument("--category", default="home_kitchen")
    p.add_argument("--tier", default="LARGE_STANDARD")
    p.add_argument("--sell-through", type=int, default=120, help="days to clear the first order")
    p.add_argument("--lean", action="store_true", help="DIY photos, no inspection, fewer samples")
    args = p.parse_args()

    fixed = FixedCosts()
    if args.lean:
        fixed = FixedCosts(
            photography=Decimal("0"), inspection=Decimal("0"), samples=Decimal("120")
        )

    plan = LaunchPlan(
        units=args.units,
        landed=LandedCost(
            fob_unit_cost=args.fob,
            freight_per_unit=args.freight,
            duty_rate=args.duty,
            tariff_rate=args.tariff,
            prep_per_unit=args.prep,
        ),
        sale_price=args.price,
        category=args.category,
        size_tier=SizeTier(args.tier),
        ppc_budget=args.ppc,
        fixed=fixed,
    )
    e = plan.economics()

    print(f"\n  {BOLD}{args.units} units at ${args.fob} FOB, selling at ${args.price}{RESET}\n")
    print(f"  {BOLD}Capital out before any revenue{RESET}")
    landed_each = plan.landed.total
    print(f"    inventory ({args.units} x ${landed_each} landed){plan.inventory_cost:>13}")
    print(f"    brand setup, one-off                       {plan.fixed_cost:>12}")
    print(f"    launch advertising                         {plan.ppc_budget:>12}")
    print(f"    {BOLD}total{RESET}{BOLD}${plan.capital_required:>40}{RESET}\n")

    print(f"  {BOLD}Per unit{RESET}")
    print(f"    landed {e.landed_cost:>8}  referral {e.referral_fee:>7}  FBA {e.fulfilment_fee:>7}")
    print(f"    storage {e.storage_fee:>12}   returns  {e.returns_reserve:>7}")
    colour = GREEN if e.net_profit > 0 else RED
    print(
        f"    {colour}profit {e.net_profit:>9}  margin {e.margin:>7.1%}  ROI {e.roi:>8.1%}{RESET}\n"
    )

    be = plan.break_even_units
    print(f"  {BOLD}Getting it back{RESET}")
    if be is None:
        print(f"    {RED}never — the unit loses money at this price{RESET}")
    else:
        verdict = (
            GREEN + "within the first order"
            if plan.recovers_on_first_order
            else RED + "needs a second order"
        )
        print(f"    break-even at {be} of {args.units} units — {verdict}{RESET}")
        print(f"    profit if it all sells      ${plan.profit_if_sold_out}")
    days = plan.cash_cycle_days(args.sell_through)
    cycle = f"{plan.lead_time_days}d make+ship + {args.sell_through}d selling + 14d payout"
    print(f"    {YELLOW}cash locked ~{days} days{RESET} {DIM}({cycle}){RESET}")
    print(f"\n  {DIM}Fee schedule and fixed costs are unverified placeholders.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
