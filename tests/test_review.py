import pytest

from app.review.browser import DisabledBrowserAdapter
from app.review.queue import ReviewItem, ReviewQueue, ReviewStatus


def test_review_requires_explicit_decision():
    queue = ReviewQueue()
    queue.submit(ReviewItem("r-1", "create_product_draft", {"title": "Example"}))
    item = queue.decide("r-1", approve=True, reviewer="owner", reason="checked claims")
    assert item.status == ReviewStatus.APPROVED
    assert item.decided_at is not None


def test_review_cannot_be_decided_twice():
    queue = ReviewQueue()
    queue.submit(ReviewItem("r-2", "publish", {}))
    queue.decide("r-2", approve=False, reviewer="owner")
    with pytest.raises(ValueError):
        queue.decide("r-2", approve=True, reviewer="owner")


def test_browser_is_disabled_by_default():
    with pytest.raises(RuntimeError):
        DisabledBrowserAdapter().capture("https://example.test")
