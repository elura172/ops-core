from __future__ import annotations

import urllib.parse
from decimal import Decimal
from typing import Any

from app.amazon.scraper import AmazonScraperClient
from app.discovery.pricing import AbeBooksCompsProvider, round_to_retail_ending
from app.observability.logging import get_logger

logger = get_logger("pricing_engine")

FORMAT_DEFAULTS: dict[str, tuple[Decimal, Decimal]] = {
    "mass_market": (Decimal("5.00"), Decimal("8.95")),
    "mass_market_paperback": (Decimal("5.00"), Decimal("8.95")),
    "paperback": (Decimal("7.50"), Decimal("12.95")),
    "trade_paperback": (Decimal("7.50"), Decimal("12.95")),
    "hardcover": (Decimal("14.00"), Decimal("22.00")),
    "hardcover_with_jacket": (Decimal("14.00"), Decimal("22.00")),
    "hardcover_no_jacket": (Decimal("9.00"), Decimal("15.00")),
    "oversized": (Decimal("15.00"), Decimal("25.00")),
}

CONDITION_MULTIPLIERS: dict[str, Decimal] = {
    "As New": Decimal("1.0"),
    "Fine": Decimal("1.0"),
    "Near Fine": Decimal("1.0"),
    "Very Good": Decimal("0.95"),
    "Good": Decimal("0.90"),
    "Fair": Decimal("0.80"),
    "Poor": Decimal("0.75"),
    "Acceptable": Decimal("0.80"),
}


class PricingEngine:
    def __init__(self, scraper: AmazonScraperClient, comps_provider: Any = None):
        self.scraper = scraper
        self.comps_provider = comps_provider or AbeBooksCompsProvider()

    def discover_price(
        self,
        title: str,
        author: str,
        condition_grade: str = "Good",
        format_hint: str = "hardcover",
    ) -> dict[str, Any]:
        """
        Discover pricing evidence for a given book title and author.
        Queries live marketplace comps (Amazon / AbeBooks) and applies antiquarian condition adjustments.
        """
        search_term = f"{title} {author}".strip()
        encoded_query = urllib.parse.quote_plus(search_term)
        search_url = f"https://www.abebooks.com/servlet/SearchResults?sortby=17&kn={encoded_query}"

        evidence: dict[str, Any] = {
            "amazon_lowest_fba": None,
            "ebay_recent_average": None,
            "comps_found": 0,
            "confidence_score": 0.0,
            "confidence_label": "Catalog Baseline",
            "search_url": search_url,
            "min_comp_cad": None,
            "median_comp_cad": None,
            "p30_comp_cad": None,
            "max_comp_cad": None,
            "top_comps": [],
            "strategy": "Priced to win Buy Box turnover (30th percentile of landed competitor copies).",
        }

        # 1. Try Amazon Scraper if live client is available
        amazon_comps = []
        try:
            if hasattr(self.scraper, "live") and self.scraper.live:
                results = self.scraper.search(search_term)
                for r in results:
                    if r.price > 0:
                        amazon_comps.append(r.price)
                if amazon_comps:
                    evidence["amazon_lowest_fba"] = float(min(amazon_comps))
                    evidence["comps_found"] += len(amazon_comps)
        except Exception as exc:
            logger.debug("pricing.amazon.skipped_or_failed", error=str(exc))

        # 2. Try AbeBooks keyless live comps
        raw_comps = ()
        if not amazon_comps:
            try:
                raw_comps = self.comps_provider.fetch_comps(title=title, author=author)
            except Exception as exc:
                logger.warning("pricing.abebooks.failed", error=str(exc))

        mult = CONDITION_MULTIPLIERS.get(condition_grade, Decimal("0.90"))

        if raw_comps:
            raw_comps_sorted = sorted(raw_comps, key=lambda c: c.total_price_cad)
            total_prices = [c.total_price_cad for c in raw_comps_sorted if c.total_price_cad > 0]

            if total_prices:
                p30_idx = min(len(total_prices) - 1, max(0, int(len(total_prices) * 0.30)))
                med_idx = len(total_prices) // 2
                market_comp = total_prices[p30_idx]

                evidence["comps_found"] = len(total_prices)
                evidence["min_comp_cad"] = float(total_prices[0])
                evidence["p30_comp_cad"] = float(total_prices[p30_idx])
                evidence["median_comp_cad"] = float(total_prices[med_idx])
                evidence["max_comp_cad"] = float(total_prices[-1])
                evidence["confidence_score"] = min(1.0, 0.4 + len(total_prices) * 0.02)
                evidence["confidence_label"] = (
                    "High Market Liquidity" if len(total_prices) >= 10 else "Moderate Liquidity"
                )

                evidence["top_comps"] = [
                    {
                        "source": c.source.title(),
                        "item_price_cad": float(c.item_price_cad),
                        "shipping_price_cad": float(c.shipping_price_cad),
                        "total_price_cad": float(c.total_price_cad),
                        "condition": getattr(c, "condition_reported", "Used"),
                    }
                    for c in raw_comps_sorted[:3]
                ]

                raw_online = market_comp * mult
                raw_in_store = raw_online * Decimal("0.75")
                online_price = round_to_retail_ending(max(raw_online, Decimal("4.00")))
                in_store_price = round_to_retail_ending(max(raw_in_store, Decimal("3.00")))

                rationale = (
                    f"Priced from {len(total_prices)} live marketplace comps "
                    f"(market benchmark: ${market_comp:.2f} CAD). "
                    f"Applied condition adjustment for '{condition_grade}'."
                )

                return {
                    "evidence": evidence,
                    "recommended_price_cad": f"{online_price:.2f}",
                    "in_store_price_cad": f"{in_store_price:.2f}",
                    "rationale": rationale,
                }

        if amazon_comps:
            amazon_price = evidence["amazon_lowest_fba"]
            evidence["confidence_score"] = min(1.0, len(amazon_comps) * 0.2)
            evidence["confidence_label"] = "Amazon Marketplace Data"
            base_price = Decimal(str(amazon_price)) * Decimal("0.90")
            raw_online = base_price * mult
            raw_in_store = raw_online * Decimal("0.75")
            online_price = round_to_retail_ending(max(raw_online, Decimal("4.00")))
            in_store_price = round_to_retail_ending(max(raw_in_store, Decimal("3.00")))
            rationale = (
                f"Lowest Amazon FBA comp is ${amazon_price:.2f}. "
                f"Undercutting by 10% for turnover, adjusted for '{condition_grade}'."
            )
            return {
                "evidence": evidence,
                "recommended_price_cad": f"{online_price:.2f}",
                "in_store_price_cad": f"{in_store_price:.2f}",
                "rationale": rationale,
            }

        # Format-based heuristic fallback (realistic catalog baseline, never $100 placeholder)
        fmt_key = str(format_hint).lower().replace(" ", "_")
        def_in_store, def_online = FORMAT_DEFAULTS.get(fmt_key, (Decimal("8.00"), Decimal("14.00")))
        online_price = round_to_retail_ending(def_online * mult)
        in_store_price = round_to_retail_ending(def_in_store * mult)
        evidence["confidence_score"] = 0.35
        evidence["confidence_label"] = "Catalog Baseline (No Comps)"
        rationale = (
            f"No live marketplace comps found. Applied standard {fmt_key.replace('_', ' ')} "
            f"baseline adjusted for condition '{condition_grade}'."
        )

        return {
            "evidence": evidence,
            "recommended_price_cad": f"{online_price:.2f}",
            "in_store_price_cad": f"{in_store_price:.2f}",
            "rationale": rationale,
        }
