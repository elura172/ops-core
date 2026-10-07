import re

with open("app/review/executor.py", "r") as f:
    content = f.read()

# Import the new adapter
content = content.replace(
    "    EbayListingAdapter,\n)",
    "    EbayListingAdapter,\n    ShopifyListingAdapter,\n)"
)

# Add to _intake_book
content = content.replace(
    "            EbayListingAdapter(dry_run=True),\n        )",
    "            EbayListingAdapter(dry_run=True),\n            ShopifyListingAdapter(dry_run=True),\n        )"
)

# Add to _sync_inventory
content = content.replace(
    "            EbayListingAdapter(dry_run=True),\n        )\n        sync = MultiChannelInventorySync(adapters)",
    "            EbayListingAdapter(dry_run=True),\n            ShopifyListingAdapter(dry_run=True),\n        )\n        sync = MultiChannelInventorySync(adapters)"
)

# Add to _resolve_order_contention
content = content.replace(
    "                \"ebay\": EbayListingAdapter(dry_run=True),\n            }",
    "                \"ebay\": EbayListingAdapter(dry_run=True),\n                \"shopify\": ShopifyListingAdapter(dry_run=True),\n            }"
)

with open("app/review/executor.py", "w") as f:
    f.write(content)
