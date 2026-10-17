from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.domain.books import (
    BookConditionGrade,
    BookFormat,
    BookValuation,
    DisambiguationResult,
    MarketComp,
    SpineExtractionItem,
)
from app.net import RetryPolicy
from app.observability.logging import get_logger

logger = get_logger("book_pricing")

PENNY = Decimal("0.01")
USD_TO_CAD_RATE = Decimal("1.38")
MIN_FLOOR_PRICE_CAD = Decimal("4.00")
BARGAIN_BIN_PRICE_CAD = Decimal("2.00")

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# Regex to extract streaming Next.js pricing chunks from AbeBooks search responses
ABEBOOKS_PRICE_PATTERN = re.compile(
    r"priceInPurchaseCurrency[^0-9]+([0-9\.]+)[^A-Z]+([A-Z]{3})"
    r"[^0-9]+shippingPriceInPurchaseCurrency[^0-9]+([0-9\.]+)"
)


def _to_money(val: Decimal | float) -> Decimal:
    d = Decimal(str(val)) if isinstance(val, float) else val
    return d.quantize(PENNY, rounding=ROUND_HALF_UP)


def round_to_retail_ending(amount: Decimal) -> Decimal:
    """Round to friendly bookshop retail endings (.00, .50, or .95)."""
    if amount <= Decimal("3.00"):
        return Decimal("2.00")
    if amount <= Decimal("5.00"):
        return Decimal("4.50")

    # Round to nearest dollar, then adjust to .50 or .95
    base = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    diff = amount - base
    if diff < Decimal("-0.25"):
        return base - Decimal("0.50")
    if diff < Decimal("0.25"):
        return base - Decimal("0.05")  # e.g. 7.95
    return base + Decimal("0.50")


FAIR_CUES = frozenset(
    {
        "torn_or_chipped_jacket",
        "ex_library_stickers_or_tape",
        "cocked_spine",
    }
)
GOOD_CUES = frozenset({"creased_reading_spine", "bumped_headcap_or_tail"})


def infer_condition_grade(spine: SpineExtractionItem) -> BookConditionGrade:
    """Infer standard ILAB/ABAA condition grade from visible defect cues."""
    cues = set(spine.visible_condition_cues)
    if cues & FAIR_CUES:
        return BookConditionGrade.FAIR
    if cues & GOOD_CUES:
        return BookConditionGrade.GOOD
    if "sunned_faded_spine" in cues:
        return BookConditionGrade.VERY_GOOD
    if "clean_as_new" in cues:
        return BookConditionGrade.FINE
    return BookConditionGrade.VERY_GOOD


class CompsProvider(Protocol):
    """Protocol for market comps providers."""

    def fetch_comps(
        self,
        title: str,
        author: str | None = None,
        isbn: str | None = None,
    ) -> tuple[MarketComp, ...]: ...


@dataclass
class AbeBooksCompsProvider(CompsProvider):
    """Fast comps extractor querying AbeBooks and parsing Next.js streaming results."""

    timeout: float = 10.0
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    def fetch_comps(
        self,
        title: str,
        author: str | None = None,
        isbn: str | None = None,
    ) -> tuple[MarketComp, ...]:
        params: dict[str, str] = {"sortby": "17"}
        if isbn:
            params["isbn"] = isbn
        else:
            params["kn"] = f"{title} {author or ''}".strip()

        url = "https://www.abebooks.com/servlet/SearchResults?" + urllib.parse.urlencode(params)
        html = self._fetch_html(url)
        if not html:
            return ()

        # Extract next_f streaming data
        chunks = re.findall(r"self\.__next_f\.push\(\[1,\"(.*?)\"\]\)", html)
        combined = "".join(chunks) if chunks else html

        raw_matches = ABEBOOKS_PRICE_PATTERN.findall(combined)
        comps: list[MarketComp] = []

        for item_str, curr, ship_str in raw_matches:
            try:
                item_amt = Decimal(item_str)
                ship_amt = Decimal(ship_str)
                fx = USD_TO_CAD_RATE if curr == "USD" else Decimal("1.00")
                item_cad = _to_money(item_amt * fx)
                ship_cad = _to_money(ship_amt * fx)
                tot_cad = _to_money(item_cad + ship_cad)

                comps.append(
                    MarketComp(
                        source="abebooks",
                        item_price_cad=item_cad,
                        shipping_price_cad=ship_cad,
                        total_price_cad=tot_cad,
                        original_currency=curr,
                        condition_reported="Used",
                        listing_url=url,
                    )
                )
            except Exception:
                continue

        comps.sort(key=lambda c: c.total_price_cad)
        return tuple(comps)

    def _fetch_html(self, url: str) -> str | None:
        req = Request(url, headers={"User-Agent": self.user_agent}, method="GET")
        for attempt in range(1, self.retries.max_attempts + 1):
            try:
                with urlopen(req, timeout=self.timeout) as resp:
                    if resp.status in RETRYABLE_STATUS:
                        raise HTTPError(url, resp.status, "Retryable", resp.headers, None)
                    return resp.read().decode("utf-8", errors="replace")
            except HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS or attempt >= self.retries.max_attempts:
                    logger.debug(f"AbeBooks HTTP {exc.code} for {url}")
                    return None
                import time

                time.sleep(self.retries.backoff(attempt))
            except URLError as exc:
                if attempt >= self.retries.max_attempts:
                    logger.debug(f"AbeBooks network failure: {exc.reason}")
                    return None
                import time

                time.sleep(self.retries.backoff(attempt))
            except Exception as exc:
                logger.debug(f"AbeBooks unexpected error: {exc}")
                return None
        return None


CONDITION_MULTIPLIERS = {
    BookConditionGrade.AS_NEW: Decimal("1.00"),
    BookConditionGrade.FINE: Decimal("0.95"),
    BookConditionGrade.NEAR_FINE: Decimal("0.90"),
    BookConditionGrade.VERY_GOOD: Decimal("0.80"),
    BookConditionGrade.GOOD: Decimal("0.65"),
    BookConditionGrade.FAIR: Decimal("0.45"),
    BookConditionGrade.POOR: Decimal("0.25"),
}

FORMAT_DEFAULTS: dict[BookFormat, tuple[Decimal, Decimal]] = {
    BookFormat.MASS_MARKET_PAPERBACK: (Decimal("5.00"), Decimal("8.95")),
    BookFormat.TRADE_PAPERBACK: (Decimal("7.50"), Decimal("12.95")),
    BookFormat.HARDCOVER_WITH_JACKET: (Decimal("14.00"), Decimal("22.00")),
    BookFormat.HARDCOVER_NO_JACKET: (Decimal("9.00"), Decimal("15.00")),
    BookFormat.UNKNOWN: (Decimal("6.00"), Decimal("10.00")),
}


@dataclass
class BookValuationEngine:
    """Calculates in-store and online pricing for Ottawa bookstore inventory."""

    comps_provider: CompsProvider = field(default_factory=AbeBooksCompsProvider)
    usd_to_cad: Decimal = USD_TO_CAD_RATE

    def value_book(self, disambiguation: DisambiguationResult) -> BookValuation:
        spine = disambiguation.spine_item
        cand = disambiguation.best_candidate
        condition = infer_condition_grade(spine)

        isbn_to_query = disambiguation.detected_isbn or (cand.primary_isbn if cand else None)
        title = cand.title if cand else spine.title
        author = self._pick_author(cand, spine)

        comps = self.comps_provider.fetch_comps(title=title, author=author, isbn=isbn_to_query)

        mult = CONDITION_MULTIPLIERS.get(condition, Decimal("0.75"))

        if comps:
            in_store_price, online_price, confidence, rationale = self._price_from_comps(comps, mult, condition)
        else:
            in_store_price, online_price, confidence, rationale = self._price_from_heuristic(spine, mult, condition)

        channel_strategy, in_store_price, online_price, rationale = self._route_channel(
            disambiguation, in_store_price, online_price, rationale
        )

        return BookValuation(
            in_store_price_cad=in_store_price,
            online_price_cad=online_price,
            floor_price_cad=MIN_FLOOR_PRICE_CAD,
            condition_grade=condition,
            channel_strategy=channel_strategy,
            price_confidence=confidence,
            pricing_rationale=rationale,
            comps=comps,
        )

    @staticmethod
    def _pick_author(cand: object | None, spine: SpineExtractionItem) -> str | None:
        if cand and hasattr(cand, "authors") and cand.authors:  # type: ignore[union-attr]
            return cand.authors[0]  # type: ignore[union-attr]
        if spine.authors:
            return spine.authors[0]
        return None

    @staticmethod
    def _price_from_comps(
        comps: tuple[MarketComp, ...],
        mult: Decimal,
        condition: BookConditionGrade,
    ) -> tuple[Decimal, Decimal, str, str]:
        usable = comps[: max(1, int(len(comps) * 0.9))]
        p30_idx = min(len(usable) - 1, max(0, int(len(usable) * 0.30)))
        base_comp = usable[p30_idx]

        raw_online = base_comp.total_price_cad * mult
        raw_in_store = base_comp.item_price_cad * mult * Decimal("0.85")

        online_price = round_to_retail_ending(max(raw_online, MIN_FLOOR_PRICE_CAD))
        in_store_price = round_to_retail_ending(max(raw_in_store, Decimal("3.00")))
        confidence = "high" if len(comps) >= 5 else "medium"
        rationale = (
            f"Priced from {len(comps)} live AbeBooks comps "
            f"(30th pct: ${base_comp.total_price_cad} CAD). "
            f"Adjusted for {condition.value}."
        )
        return in_store_price, online_price, confidence, rationale

    @staticmethod
    def _price_from_heuristic(
        spine: SpineExtractionItem,
        mult: Decimal,
        condition: BookConditionGrade,
    ) -> tuple[Decimal, Decimal, str, str]:
        def_in_store, def_online = FORMAT_DEFAULTS.get(spine.format, (Decimal("6.00"), Decimal("10.00")))
        in_store_price = round_to_retail_ending(def_in_store * mult)
        online_price = round_to_retail_ending(def_online * mult)
        confidence = "low"
        rationale = f"No live comps found. {spine.format.value} baseline adjusted for {condition.value}."
        return in_store_price, online_price, confidence, rationale

    @staticmethod
    def _route_channel(
        disambiguation: DisambiguationResult,
        in_store_price: Decimal,
        online_price: Decimal,
        rationale: str,
    ) -> tuple[str, Decimal, Decimal, str]:
        if disambiguation.needs_copyright_check:
            rationale += " [FLAGGED] Potential collectible: verify copyright points before finalized pricing."
            return "online_only_collectible", in_store_price, online_price, rationale
        if in_store_price <= MIN_FLOOR_PRICE_CAD:
            return (
                "bargain_bin_cart",
                BARGAIN_BIN_PRICE_CAD,
                Decimal("0.00"),
                "Low market value (<$4 net). Route to $2 sidewalk bargain cart.",
            )
        return "in_store_and_online", in_store_price, online_price, rationale
