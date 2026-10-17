from __future__ import annotations

from datetime import UTC, datetime

from app.db.factory import open_review_store
from app.review.queue import ReviewItem, ReviewStatus


def seed() -> None:
    store = open_review_store("reviews.db")

    # 1. High-Value Order Contention (Held for Human Operator)
    item_contention = ReviewItem(
        review_id="rev-contend-dune",
        operation="resolve_order_contention",
        payload={
            "sku": "BK-DUNE-1965",
            "action": "cancel_loser_marketplace_order",
            "cancellation_reason": "OUT_OF_STOCK",
            "reason": (
                "Simultaneous collision (35ms delta). Amazon prioritized over eBay due to "
                "critical seller cancellation SLA. Value $950 CAD held for human consultation."
            ),
            "winner_order_id": "AMZ-112-984210",
            "winner_channel": "amazon",
            "loser_order_id": "EBAY-29482103",
            "loser_channel": "ebay",
            "substitute_sku": None,
            "winner_claim": {
                "order_id": "AMZ-112-984210",
                "channel": "amazon",
                "ordered_at": datetime.now(UTC).isoformat(),
                "price_cad": "950.00",
            },
            "loser_claim": {
                "order_id": "EBAY-29482103",
                "channel": "ebay",
                "ordered_at": datetime.now(UTC).isoformat(),
                "price_cad": "950.00",
            },
        },
        status=ReviewStatus.PENDING,
    )
    if store.get("rev-contend-dune") is None:
        store.submit(item_contention)

    # 2. Antiquarian Book Intake (Awaiting Pricing & Condition Signoff)
    item_intake = ReviewItem(
        review_id="rev-intake-gibson",
        operation="intake_book",
        payload={
            "extraction": {
                "title": "Neuromancer",
                "authors": ["William Gibson"],
                "format": "trade_paperback",
            },
            "disambiguation": {
                "best_candidate": {
                    "title": "Neuromancer (Ace Science Fiction Special)",
                    "match_score": 0.94,
                    "primary_isbn": "0441569560",
                },
                "triage_reason": (
                    "Single high-confidence antiquarian match confirmed via OpenLibrary / AbeBooks catalog."
                ),
            },
            "valuation": {
                "condition_grade": "Very Good",
                "channel_strategy": "multi_channel_antiquarian",
                "in_store_price_cad": "40.00",
                "online_price_cad": "45.00",
                "pricing_rationale": (
                    "Collectible 1984 first trade paperback edition. Moderate spine shelfwear, clean text block."
                ),
            },
        },
        status=ReviewStatus.PENDING,
    )
    if store.get("rev-intake-gibson") is None:
        store.submit(item_intake)

    # 3. Fulfillment Action (Ready to generate Shippo label & Packing Slip)
    item_fulfill = ReviewItem(
        review_id="rev-fulfill-tolkien",
        operation="fulfill_order",
        payload={
            "order_id": "ORD-TOLKIEN-99",
            "source_channel": "amazon_spapi",
            "preferred_provider": "Canada Post",
            "line_items": [{"sku": "BK-LOTR-SET", "quantity": 1, "title": "The Lord of the Rings (3 Vols)"}],
            "shipping_address": {
                "name": "Eowyn of Rohan",
                "line1": "144 Meduseld Way",
                "city": "Edoras",
                "state": "BC",
                "postal_code": "V8V 3K2",
                "country": "CA",
            },
        },
        status=ReviewStatus.PENDING,
    )
    if store.get("rev-fulfill-tolkien") is None:
        store.submit(item_fulfill)

    # 4. Previously Executed In-Store Sale (Auto-triaged by Corvus)
    if store.get("rev-corvus-pos-sync") is None:
        item_executed = ReviewItem(
            review_id="rev-corvus-pos-sync",
            operation="sync_inventory",
            payload={
                "sku": "BK-PHILIP-K-DICK",
                "source_channel": "square",
            },
            status=ReviewStatus.PENDING,
        )
        store.submit(item_executed)
        store.decide(
            "rev-corvus-pos-sync",
            approve=True,
            reviewer="agent:corvus",
            reason="Corvus: Deterministic inventory sync authorized. Protecting the vault.",
        )
        store.claim_for_execution("rev-corvus-pos-sync", actor="agent:corvus")
        store.complete_execution(
            "rev-corvus-pos-sync",
            result={
                "sku": "BK-PHILIP-K-DICK",
                "sync_results": {
                    "amazon_spapi": "delisted",
                    "ebay": "delisted",
                    "abebooks": "delisted",
                    "biblio": "delisted",
                },
            },
        )

    print("Successfully seeded 4 demo review items into reviews.db!")


if __name__ == "__main__":
    seed()
