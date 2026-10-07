from decimal import Decimal
from typing import Any

from app.amazon.scraper import AmazonScraperClient
from app.observability.logging import get_logger

logger = get_logger("pricing_engine")

class PricingEngine:
    def __init__(self, scraper: AmazonScraperClient):
        self.scraper = scraper

    def discover_price(self, title: str, author: str, condition_grade: str = "Good") -> dict[str, Any]:
        """
        Discover pricing evidence for a given book title and author.
        Returns the evidence payload and the final recommended online price.
        """
        search_term = f"{title} {author}".strip()
        evidence = {
            "amazon_lowest_fba": None,
            "ebay_recent_average": None,
            "comps_found": 0,
            "confidence_score": 0.0,
        }
        
        comps = []
        try:
            # 1. Ask Amazon Scraper
            results = self.scraper.search(search_term)
            
            # Find the best match
            for r in results:
                if r.price > 0:
                    comps.append(r.price)
                    
            if comps:
                evidence["amazon_lowest_fba"] = float(min(comps))
                evidence["comps_found"] += len(comps)
                
        except Exception as exc:
            logger.warning("pricing.amazon.failed", error=str(exc))
            
        # 2. Base Price Calculation
        if evidence["comps_found"] == 0:
            evidence["confidence_score"] = 0.0
            base_price = Decimal("100.00")
            rationale_base = "No comps found. Defaulting to safe $100 placeholder and flagging for manual review."
        else:
            amazon_price = evidence["amazon_lowest_fba"]
            # Confidence grows with more comps, maxing out at 5 comps
            evidence["confidence_score"] = min(1.0, len(comps) * 0.2)
            # Strategy: Slightly undercut the lowest FBA price by 10% to prioritize turnover
            base_price = Decimal(str(amazon_price)) * Decimal("0.9")
            rationale_base = f"Lowest FBA is ${amazon_price:.2f}. Undercutting by 10% to prioritize turnover."

        # 3. Condition Multiplier
        condition_multipliers = {
            "As New": Decimal("1.0"),
            "Fine": Decimal("1.0"),
            "Near Fine": Decimal("1.0"),
            "Very Good": Decimal("0.95"),
            "Good": Decimal("0.90"),
            "Fair": Decimal("0.80"),
            "Poor": Decimal("0.80"),
            "Acceptable": Decimal("0.80")
        }
        multiplier = condition_multipliers.get(condition_grade, Decimal("0.90"))
        
        online_price = base_price * multiplier
        
        if multiplier < Decimal("1.0"):
            rationale = f"{rationale_base} Applied {(Decimal('1.0') - multiplier) * 100}% discount for condition '{condition_grade}'."
        else:
            rationale = rationale_base
            
        # In-store is 25% off online
        in_store_price = online_price * Decimal("0.75")
            
        return {
            "evidence": evidence,
            "recommended_price_cad": f"{online_price:.2f}",
            "in_store_price_cad": f"{in_store_price:.2f}",
            "rationale": rationale
        }
