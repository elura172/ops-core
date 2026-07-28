from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.amazon.fees import SizeTier
from app.amazon.models import AmazonListing, RankPoint
from app.net import RetryPolicy
from app.observability.logging import get_logger

logger = get_logger("amazon_scraper")

BASE_URL = "https://api.amazonscraperapi.com/api/v1/amazon/product"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

SCHEMA = """
CREATE TABLE IF NOT EXISTS product_snapshots (
  asin TEXT NOT NULL,
  observed_on TEXT NOT NULL,
  category_rank INTEGER,
  price TEXT,
  rating TEXT,
  reviews_count INTEGER,
  seller_count INTEGER,
  payload TEXT NOT NULL,
  PRIMARY KEY (asin, observed_on)
);
CREATE TABLE IF NOT EXISTS request_log (
  requested_at TEXT NOT NULL,
  asin TEXT NOT NULL,
  outcome TEXT NOT NULL
);
"""


class ScraperError(RuntimeError):
    """Base for scraper failures."""


class TransientScraperError(ScraperError):
    """Retryable — timeout, network error, rate limit or 5xx."""


class TerminalScraperError(ScraperError):
    """Not retryable — bad request, bad key, unusable response."""


class IncompleteResponse(TransientScraperError):
    """The page came back partial — a null price, an empty buybox, no rank.

    Retryable rather than terminal: a scraper hitting a bot check or a slow
    render returns a stub, and the next attempt usually gets the real page.
    Treating it as valid is the dangerous option — a null price coerced to zero
    looks like a free product to everything downstream.
    """


class RequestBudgetExceeded(ScraperError):
    """The monthly request allowance is spent; no request was made."""


@dataclass(frozen=True)
class Snapshot:
    """One observation of a listing at a point in time.

    The scraper returns a single moment, not a history. Rank *stability* is what
    separates a business from a seasonal trap, so every fetch is persisted and
    history accumulates: thirty days of snapshots is thirty rank points.
    """

    asin: str
    observed_on: date
    title: str
    brand: str | None
    category: str
    category_rank: int | None
    price: Decimal
    rating: Decimal
    reviews_count: int
    seller_count: int
    negative_share: Decimal
    weight_kg: Decimal | None
    is_amazon_fulfilled: bool

    @property
    def size_tier(self) -> SizeTier:
        """Estimate the FBA tier from weight alone.

        Dimensions are frequently absent from the response, and weight is the
        coarser but more reliable signal. Deliberately pessimistic: a wrong guess
        should overstate the fee, never understate it.
        """
        if self.weight_kg is None:
            return SizeTier.LARGE_STANDARD
        if self.weight_kg <= Decimal("0.45"):
            return SizeTier.SMALL_STANDARD
        if self.weight_kg <= Decimal("9"):
            return SizeTier.LARGE_STANDARD
        if self.weight_kg <= Decimal("23"):
            return SizeTier.LARGE_BULKY
        return SizeTier.EXTRA_LARGE


class SnapshotStore:
    """Durable snapshot history and request accounting.

    The request log is the budget: an allowance of a thousand a month is a real
    constraint, and a client that forgets what it already spent will burn it.
    """

    def __init__(self, path: str | Path = "snapshots.db") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.executescript(SCHEMA)

    def record(self, snapshot: Snapshot, payload: dict[str, Any]) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO product_snapshots"
                "(asin, observed_on, category_rank, price, rating, reviews_count,"
                " seller_count, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.asin,
                    snapshot.observed_on.isoformat(),
                    snapshot.category_rank,
                    str(snapshot.price),
                    str(snapshot.rating),
                    snapshot.reviews_count,
                    snapshot.seller_count,
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def log_request(self, asin: str, outcome: str) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO request_log(requested_at, asin, outcome) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(), asin, outcome),
            )

    def requests_this_month(self) -> int:
        month = datetime.now(UTC).strftime("%Y-%m")
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS n FROM request_log WHERE requested_at LIKE ?", (f"{month}%",)
            ).fetchone()
        return int(row["n"])

    def rank_history(self, asin: str) -> tuple[RankPoint, ...]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT observed_on, category_rank FROM product_snapshots "
                "WHERE asin = ? AND category_rank IS NOT NULL ORDER BY observed_on",
                (asin,),
            ).fetchall()
        return tuple(
            RankPoint(date.fromisoformat(r["observed_on"]), int(r["category_rank"])) for r in rows
        )

    def tracked_asins(self) -> list[str]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT DISTINCT asin FROM product_snapshots ORDER BY asin"
            ).fetchall()
        return [r["asin"] for r in rows]

    def close(self) -> None:
        self.connection.close()


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


_WEIGHT = re.compile(r"([\d.]+)\s*(kg|g|pounds?|lbs?|ounces?|oz)", re.IGNORECASE)


def _weight_kg(details: dict[str, Any] | None) -> Decimal | None:
    raw = (details or {}).get("item_weight")
    if not raw:
        return None
    match = _WEIGHT.search(str(raw))
    if not match:
        return None
    amount, unit = Decimal(match.group(1)), match.group(2).lower()
    factors = {
        "kg": Decimal("1"),
        "g": Decimal("0.001"),
        "pound": Decimal("0.4536"),
        "pounds": Decimal("0.4536"),
        "lb": Decimal("0.4536"),
        "lbs": Decimal("0.4536"),
        "ounce": Decimal("0.02835"),
        "ounces": Decimal("0.02835"),
        "oz": Decimal("0.02835"),
    }
    return amount * factors.get(unit, Decimal("1"))


def _category_rank(payload: dict[str, Any]) -> tuple[str, int | None]:
    """Take the broadest ranked category, not the narrowest.

    A response ranks the product in several nested categories at once. "#10 in
    Garlic Keepers" flatters a niche of a few dozen listings; "#35,786 in Kitchen
    & Dining" is the number that reflects real volume. The largest rank is the
    broadest category.
    """
    entries = payload.get("sales_rank") or []
    best: tuple[str, int] | None = None
    for entry in entries:
        rank = entry.get("rank")
        ladder = entry.get("ladder") or [{}]
        name = ladder[0].get("name") or "unknown"
        if isinstance(rank, int) and (best is None or rank > best[1]):
            best = (name, rank)
    return best if best else ("unknown", None)


def _negative_share(payload: dict[str, Any]) -> Decimal:
    """Fraction of reviews at one or two stars.

    A better gap signal than the star average: 4.6 stars with 5% one-star reviews
    and 4.6 stars with none are different products, and only the first has an
    opening.
    """
    total = Decimal("0")
    for band in payload.get("rating_stars_distribution") or []:
        if band.get("rating") in (1, 2):
            total += _decimal(band.get("percentage"))
    return total / Decimal("100")


def to_snapshot(payload: dict[str, Any], observed_on: date | None = None) -> Snapshot:
    asin = payload.get("asin")
    if not asin:
        raise TerminalScraperError("response has no ASIN")

    # A price is the one field that must be present and sane. Everything
    # downstream — margin, break-even, the whole screen — is meaningless without
    # it, and a null coerced to zero is worse than no data at all.
    raw_price = payload.get("price_buybox") or payload.get("price")
    price = _decimal(raw_price) if raw_price is not None else Decimal("0")
    if price <= 0:
        raise IncompleteResponse(
            f"{asin}: response carried no price (stock={payload.get('stock')!r},"
            f" offers={len(payload.get('buybox') or [])}) — partial page"
        )

    category, rank = _category_rank(payload)
    details = payload.get("product_details")
    merchant = payload.get("featured_merchant") or {}
    return Snapshot(
        asin=str(asin),
        observed_on=observed_on or datetime.now(UTC).date(),
        title=str(payload.get("title") or payload.get("product_name") or ""),
        brand=payload.get("brand"),
        category=category,
        category_rank=rank,
        price=price,
        rating=_decimal(payload.get("rating")),
        reviews_count=int(payload.get("reviews_count") or 0),
        # pricing_count is the number of offers on the listing; absent, assume one.
        seller_count=int(payload.get("pricing_count") or 1),
        negative_share=_negative_share(payload),
        weight_kg=_weight_kg(details),
        is_amazon_fulfilled=bool(merchant.get("is_amazon_fulfilled")),
    )


def to_listing(snapshot: Snapshot, history: tuple[RankPoint, ...]) -> AmazonListing:
    """Build a screenable listing from a snapshot plus accumulated history."""
    return AmazonListing(
        asin=snapshot.asin,
        title=snapshot.title,
        category="home_kitchen",
        price=snapshot.price,
        review_count=snapshot.reviews_count,
        rating=snapshot.rating,
        seller_count=snapshot.seller_count,
        size_tier=snapshot.size_tier,
        rank_history=history,
        brand=snapshot.brand,
        # The scraper cannot see Brand Registry directly. A single seller who is
        # also the brand and fulfils through Amazon is the usual signature.
        brand_registered=bool(snapshot.brand) and snapshot.seller_count <= 1,
    )


@dataclass
class AmazonScraperClient:
    """Boundary for the scraper API, with the monthly allowance enforced.

    Dry-run by default: a client constructed without ``live=True`` never spends a
    request, which keeps tests and exploration free.
    """

    api_key: str
    store: SnapshotStore
    monthly_budget: int = 1000
    live: bool = False
    domain: str = "com"
    timeout: float = 45
    retries: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep

    @property
    def remaining(self) -> int:
        return max(0, self.monthly_budget - self.store.requests_this_month())

    def _get(self, asin: str) -> dict[str, Any]:
        query = urlencode({"api_key": self.api_key, "query": asin, "domain": self.domain})
        request = Request(f"{BASE_URL}?{query}", method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status in RETRYABLE_STATUS:
                    raise TransientScraperError(f"scraper returned HTTP {response.status}")
                payload = json.loads(response.read())
        except HTTPError as exc:
            if exc.code in RETRYABLE_STATUS:
                raise TransientScraperError(f"scraper returned HTTP {exc.code}") from exc
            raise TerminalScraperError(f"scraper returned HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise TransientScraperError(f"scraper request failed: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise TerminalScraperError("scraper returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise TerminalScraperError("scraper returned an unexpected body")
        return payload

    def fetch(self, asin: str) -> Snapshot:
        """Fetch, persist, and return one snapshot. Costs one request."""
        if not self.api_key:
            raise ValueError("api_key is required")
        if not self.live:
            raise PermissionError(
                "client is in dry-run; construct with live=True to spend the allowance"
            )
        if self.remaining <= 0:
            raise RequestBudgetExceeded(
                f"monthly allowance of {self.monthly_budget} requests is spent"
            )

        last: Exception | None = None
        for attempt in range(1, self.retries.max_attempts + 1):
            # Logged before the call: a request that fails still consumed quota.
            self.store.log_request(asin, "attempted")
            try:
                payload = self._get(asin)
                # Mapping is inside the retry: a partial page is only detectable
                # after parsing, and it is exactly the case worth re-fetching.
                snapshot = to_snapshot(payload)
            except TransientScraperError as exc:
                last = exc
                if attempt == self.retries.max_attempts:
                    break
                self.sleep(self.retries.backoff(attempt))
                continue
            self.store.record(snapshot, payload)
            logger.info(
                "amazon.snapshot.recorded",
                asin=asin,
                rank=snapshot.category_rank,
                reviews=snapshot.reviews_count,
                remaining=self.remaining,
            )
            return snapshot

        assert last is not None
        logger.error("amazon.snapshot.failed", asin=asin, reason=type(last).__name__)
        raise last
