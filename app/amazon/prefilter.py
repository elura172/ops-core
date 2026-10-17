from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.amazon.screen import Rejection

#: Codes that flag for review rather than reject. These categories are the ones
#: sellers most often stuff into a title for search reach, so a match is weak
#: evidence about what the product actually is.
SOFT_CODES: frozenset[str] = frozenset({"kids_or_baby", "apparel", "fragile"})

#: Categories to reject on sight, with the reason. These are not demand
#: judgements — a product can sell wonderfully and still be a bad first
#: private-label bet because of compliance, shipping, or return economics.
EXCLUSIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ingestible",
        "mouth or body contact — FDA exposure, gated category, expiry management",
        (
            "toothpick",
            "toothpaste",
            "mouthwash",
            "supplement",
            "vitamin",
            "capsule",
            "gummies",
            "protein",
            "tea bag",
            "coffee bean",
            "edible",
            "drinking straw",
            "lip balm",
            "sunscreen",
            "serum",
            "lotion",
        ),
    ),
    (
        "health_claim",
        "therapeutic claim in the title — restricted, and our own policy gate blocks it",
        ("ayurvedic", "detox", "healing", "therapeutic", "immune", "antibacterial", "medicinal"),
    ),
    (
        "battery_or_electronic",
        "battery or electronics — shipping restrictions, defect rates, support burden",
        (
            "battery",
            "batteries",
            "rechargeable",
            "lithium",
            "bluetooth",
            "wireless",
            "led",
            "usb",
            "charger",
            "power bank",
            "electric",
        ),
    ),
    (
        "kids_or_baby",
        "children's product — CPSC testing and liability",
        ("baby", "infant", "toddler", "nursery", "kids", "children", "toy"),
    ),
    (
        "apparel",
        "apparel — size and colour SKU explosion, punishing return rates",
        ("shirt", "hoodie", "socks", "shoes", "leggings", "dress", "jacket", "hat"),
    ),
    (
        "bulk_commodity",
        "bulk commodity — low value density, FBA fees and storage eat the margin",
        (
            "case of",
            "reams",
            "sheets",
            "bulk",
            "wholesale",
            "pack of 100",
            "toilet paper",
            "paper towel",
            "printer paper",
            "copy paper",
        ),
    ),
    (
        "fragile",
        "fragile — breakage in FBA and on the way to it",
        ("glass", "ceramic", "porcelain", "crystal", "mirror", "terracotta", "stoneware"),
    ),
)

#: Brands you cannot out-list. Amazon's own labels are the sharpest case: they
#: own the shelf, the data and the buy box.
DOMINANT_BRANDS: frozenset[str] = frozenset(
    {
        "amazon basics",
        "amazonbasics",
        "solimo",
        "presto!",
        "bic",
        "sharpie",
        "paper mate",
        "pilot",
        "zebra",
        "staedtler",
        "faber-castell",
        "3m",
        "scotch",
        "post-it",
        "rubbermaid",
        "sterilite",
        "oxo",
        "pyrex",
        "stanley",
        "dewalt",
        "craftsman",
        "black+decker",
        "fiskars",
        "yeti",
        "hydro flask",
        "contigo",
        "thermos",
        "tupperware",
        "simple modern",
        "owala",
        "iron flask",
        "brumate",
        "nalgene",
        "klean kanteen",
        "takeya",
        "s'well",
        "camelbak",
        "nike",
        "adidas",
        "under armour",
    }
)

#: Categories so thoroughly private-labelled that a new entrant competes with
#: thousands of near-identical listings and a handful of brands with real
#: recognition. Demand is genuine and enormous; that is exactly the problem.
#: Soft, because a real differentiator can still work — but the bar is much
#: higher than the numbers alone suggest.
SATURATED: tuple[tuple[str, ...], ...] = (
    ("water bottle", "tumbler", "drinkware", "insulated bottle"),
    ("phone case", "screen protector"),
    ("yoga mat", "resistance band", "foam roller"),
    ("laptop stand", "phone holder", "ring light"),
    ("garlic press", "silicone spatula", "measuring cup"),
)


def _word_pattern(term: str) -> re.Pattern[str]:
    """Match a keyword as a whole word.

    Bare substring matching is unusable on product titles: "led" appears inside
    "non-sealed", "filled" and "handled", and matched a ceramic garlic keeper as
    an electronic product. A trailing plural is allowed, since titles say
    "Toothpicks" where the keyword says "toothpick", and "Glasses" where it says
    "glass".
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?:e?s)?(?![a-z0-9])", re.IGNORECASE)


ASIN_PATTERN = re.compile(r"\bB0[A-Z0-9]{8}\b")
PRICE_PATTERN = re.compile(r"\$\s*([0-9]+(?:\.[0-9]{2})?)")
MULTIPACK_PATTERN = re.compile(r"\b(\d{2,})\s*(?:pcs|pc|count|pack|ct)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QuickCandidate:
    """The fields you can read off a listing page or a bulk export without Keepa."""

    asin: str
    title: str
    price: Decimal

    @property
    def brand_guess(self) -> str:
        """First token or two of the title, which is where Amazon puts the brand."""
        return " ".join(self.title.split()[:2]).lower().strip(",")


_EXCLUSION_PATTERNS = tuple(
    (code, why, tuple((kw, _word_pattern(kw)) for kw in keywords)) for code, why, keywords in EXCLUSIONS
)
_SATURATED_PATTERNS = tuple(tuple((kw, _word_pattern(kw)) for kw in group) for group in SATURATED)
_BRAND_PATTERNS = tuple((brand, _word_pattern(brand)) for brand in sorted(DOMINANT_BRANDS))


def _matched_brand(title: str) -> str | None:
    return next((brand for brand, pattern in _BRAND_PATTERNS if pattern.search(title)), None)


def prefilter(
    candidate: QuickCandidate,
    *,
    min_price: Decimal = Decimal("15"),
    max_price: Decimal = Decimal("70"),
) -> list[Rejection]:
    """Reject on title and price alone, before spending any time on Keepa.

    This is the twenty-second check from the worksheet, made repeatable. Passing
    it means nothing about demand — it only means the candidate is worth the
    effort of gathering the data that does say something.

    Findings with ``soft=True`` are for a human to judge, not grounds to discard:
    see SOFT_CODES.
    """
    out: list[Rejection] = []
    title = candidate.title

    if candidate.price < min_price:
        out.append(
            Rejection(
                "price_too_low",
                f"${candidate.price} below floor ${min_price} — fees leave nothing",
            )
        )
    if candidate.price > max_price:
        out.append(Rejection("price_too_high", f"${candidate.price} above ceiling ${max_price}"))

    brand = _matched_brand(title)
    if brand:
        out.append(Rejection("dominant_brand", f"'{brand}' owns this shelf — unwinnable"))

    for code, why, patterns in _EXCLUSION_PATTERNS:
        hit = next((kw for kw, pattern in patterns if pattern.search(title)), None)
        if hit:
            out.append(Rejection(code, f"'{hit}' — {why}", soft=code in SOFT_CODES))

    for group in _SATURATED_PATTERNS:
        hit = next((kw for kw, pattern in group if pattern.search(title)), None)
        if hit:
            out.append(
                Rejection(
                    "saturated_category",
                    f"'{hit}' — one of the most heavily private-labelled shelves;"
                    " needs a real differentiator, not just better numbers",
                    soft=True,
                )
            )
            break

    multipack = MULTIPACK_PATTERN.search(title)
    if multipack and int(multipack.group(1)) >= 40:
        out.append(
            Rejection(
                "commodity_multipack",
                f"{multipack.group(1)}-count multipack — competes on price per unit, not on product",
            )
        )
    return out


def parse_bulk(text: str) -> list[QuickCandidate]:
    """Parse a pasted export tolerantly.

    Segments on the ASIN rather than on newlines: exports arrive with one product
    per line, several per line, or wrapped arbitrarily by whatever copied them,
    and the ASIN is the only reliable end-of-record marker. Column order is never
    assumed either — prices and title are found by shape. Records missing an ASIN,
    a price or a title are skipped rather than guessed at.
    """
    candidates: list[QuickCandidate] = []
    cursor = 0
    for match in ASIN_PATTERN.finditer(text):
        segment = text[cursor : match.start()]
        cursor = match.end()

        prices = [Decimal(p) for p in PRICE_PATTERN.findall(segment)]
        if not prices:
            continue
        fields = [f.strip() for f in re.split(r"[,\n]", segment)]
        titles = [f for f in fields if len(f) > 20 and "http" not in f and "$" not in f and not ASIN_PATTERN.search(f)]
        if not titles:
            continue
        candidates.append(
            QuickCandidate(
                asin=match.group(0),
                # Commas inside a title split it; rejoin the long fragments.
                title=", ".join(titles),
                # Exports list list-price, deal price and current price. The
                # lowest is the one a competitor actually has to beat.
                price=min(prices),
            )
        )
    return candidates
