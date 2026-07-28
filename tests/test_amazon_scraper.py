import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.amazon.fees import SizeTier
from app.amazon.scraper import (
    AmazonScraperClient,
    RequestBudgetExceeded,
    SnapshotStore,
    TerminalScraperError,
    TransientScraperError,
    to_listing,
    to_snapshot,
)
from app.net import RetryPolicy

PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "asa_product.json").read_text())


@pytest.fixture
def store(tmp_path):
    s = SnapshotStore(tmp_path / "snap.db")
    yield s
    s.close()


def client(store, **kw):
    kw.setdefault("live", True)
    kw.setdefault("sleep", lambda _: None)
    return AmazonScraperClient("key", store, **kw)


def test_mapping_reads_the_real_response():
    snap = to_snapshot(PAYLOAD, observed_on=date(2026, 7, 27))
    assert snap.asin == "B0DJHFR2GT"
    assert snap.price == Decimal("39.99")
    assert snap.rating == Decimal("4.6")
    assert snap.reviews_count == 331
    assert snap.seller_count == 1
    assert snap.brand == "Okavango"


def test_broadest_category_rank_is_used_not_the_flattering_niche():
    """The response ranks the product #10 in 'Garlic Keepers' and #35,786 in
    'Kitchen & Dining'. The niche number says nothing about volume."""
    snap = to_snapshot(PAYLOAD)
    assert snap.category_rank == 35786
    assert snap.category == "Kitchen & Dining"


def test_negative_review_share_is_extracted():
    """4.6 stars with 5% one-and-two-star reviews is a different product from 4.6
    stars with none, and only one of them has an opening."""
    assert to_snapshot(PAYLOAD).negative_share == Decimal("0.05")


def test_weight_drives_a_pessimistic_size_tier():
    snap = to_snapshot(PAYLOAD)
    assert snap.weight_kg == Decimal("0.73")
    assert snap.size_tier is SizeTier.LARGE_STANDARD


def test_missing_weight_assumes_the_more_expensive_tier():
    """A wrong guess must overstate the FBA fee, never understate it."""
    stripped = {**PAYLOAD, "product_details": None}
    assert to_snapshot(stripped).size_tier is SizeTier.LARGE_STANDARD


def test_history_accumulates_across_fetches(store):
    """One fetch is a moment. Rank stability needs a series, so every snapshot is
    kept and history grows with each run."""
    for day, rank in ((1, 30000), (2, 31000), (3, 29500)):
        payload = {**PAYLOAD, "sales_rank": [{"ladder": [{"name": "Kitchen"}], "rank": rank}]}
        store.record(to_snapshot(payload, observed_on=date(2026, 7, day)), payload)
    history = store.rank_history("B0DJHFR2GT")
    assert [p.best_seller_rank for p in history] == [30000, 31000, 29500]

    listing = to_listing(to_snapshot(PAYLOAD), history)
    assert listing.rank_stability > 0.9


def test_dry_run_never_spends_a_request(store):
    with pytest.raises(PermissionError, match="dry-run"):
        AmazonScraperClient("key", store).fetch("B0DJHFR2GT")
    assert store.requests_this_month() == 0


def test_budget_is_enforced_before_the_request(store, monkeypatch):
    c = client(store, monthly_budget=2)
    monkeypatch.setattr("app.amazon.scraper.urlopen", _fake_urlopen(PAYLOAD))
    c.fetch("B0DJHFR2GT")
    c.fetch("B0DJHFR2GT")
    assert c.remaining == 0
    with pytest.raises(RequestBudgetExceeded):
        c.fetch("B0DJHFR2GT")


def test_failed_attempts_still_consume_quota(store, monkeypatch):
    """A request that errors was still served. Not counting it overspends the
    allowance silently."""
    monkeypatch.setattr("app.amazon.scraper.urlopen", _raise(TimeoutError()))
    c = client(store, retries=RetryPolicy(max_attempts=3))
    with pytest.raises(TransientScraperError):
        c.fetch("B0DJHFR2GT")
    assert store.requests_this_month() == 3


def test_bad_key_is_terminal_not_retried(store, monkeypatch):
    from urllib.error import HTTPError

    monkeypatch.setattr(
        "app.amazon.scraper.urlopen", _raise(HTTPError("u", 401, "unauthorized", {}, None))
    )
    with pytest.raises(TerminalScraperError):
        client(store).fetch("B0DJHFR2GT")
    assert store.requests_this_month() == 1


def _fake_urlopen(payload):
    class R:
        status = 200

        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda req, timeout=None: R()


def _raise(exc):
    def go(req, timeout=None):
        raise exc

    return go


def test_a_partial_page_is_rejected_not_stored_as_zero():
    """The scraper returned null price, empty buybox and empty sales_rank for a
    live product. Coerced to zero it looks like a free product to every
    downstream calculation."""
    from app.amazon.scraper import IncompleteResponse

    degraded = {**PAYLOAD, "price": None, "price_buybox": None, "buybox": [], "sales_rank": []}
    with pytest.raises(IncompleteResponse, match="no price"):
        to_snapshot(degraded)


def test_a_partial_page_is_retried(store, monkeypatch):
    """Partial pages are usually a bot check or a slow render, so the next
    attempt normally succeeds."""
    degraded = {**PAYLOAD, "price": None, "price_buybox": None}
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        return _fake_urlopen(degraded if calls["n"] == 1 else PAYLOAD)(req)

    monkeypatch.setattr("app.amazon.scraper.urlopen", flaky)
    snap = client(store).fetch("B0DJHFR2GT")
    assert snap.price == Decimal("39.99")
    assert calls["n"] == 2


def test_nothing_is_recorded_when_every_attempt_is_partial(store, monkeypatch):
    degraded = {**PAYLOAD, "price": None, "price_buybox": None}
    monkeypatch.setattr("app.amazon.scraper.urlopen", _fake_urlopen(degraded))
    with pytest.raises(TransientScraperError):
        client(store, retries=RetryPolicy(max_attempts=2)).fetch("B0DJHFR2GT")
    assert store.rank_history("B0DJHFR2GT") == ()


SEARCH_PAYLOAD = {
    "page": 1,
    "products": [
        {
            "asin": "B0GOOD00001",
            "title": "Corner Bread Box Bamboo Countertop",
            "price": 31.95,
            "rating": 4.3,
            "reviews_count": 800,
            "pricing_count": 1,
            "sales_volume": "400+ bought in past month",
            "is_sponsored": False,
        },
        {
            "asin": "B0SPON00002",
            "title": "Sponsored Bread Box",
            "price": 29.99,
            "rating": 4.2,
            "reviews_count": 500,
            "pricing_count": 1,
            "sales_volume": "2K+ bought in past month",
            "is_sponsored": True,
        },
        {"asin": "B0NULL00003", "title": "No price row", "price": None, "rating": 4.1},
    ],
}


def test_search_maps_rows_and_skips_unusable_ones():
    from app.amazon.scraper import to_search_results

    results = to_search_results(SEARCH_PAYLOAD)
    assert [r.asin for r in results] == ["B0GOOD00001", "B0SPON00002"]
    assert results[0].monthly_sales == 400
    assert results[1].monthly_sales == 2000  # "2K+" scales
    assert results[1].is_sponsored is True


def test_search_spends_exactly_one_request(store, monkeypatch):
    """Sixty candidates for one request is the whole point of searching first."""
    monkeypatch.setattr("app.amazon.scraper.urlopen", _fake_urlopen(SEARCH_PAYLOAD))
    results = client(store).search("bread box")
    assert len(results) == 2
    assert store.requests_this_month() == 1


def test_sponsored_rows_are_triaged_out():
    """A paid placement says the seller has an ad budget, not that buyers want it."""
    from app.amazon.scraper import to_search_results
    from scripts.scan import triage

    organic, sponsored = to_search_results(SEARCH_PAYLOAD)
    assert triage(organic) == []
    assert any("sponsored" in r for r in triage(sponsored))
