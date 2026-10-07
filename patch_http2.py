import re

with open("app/api/http.py", "r") as f:
    content = f.read()

# Add open_inventory_store import
content = content.replace(
    "from app.db.factory import open_order_repository, open_review_store",
    "from app.db.factory import open_order_repository, open_review_store, open_inventory_store"
)

# Initialize inventory_store
content = content.replace(
    "review_store = open_review_store(settings.review_db)",
    "review_store = open_review_store(settings.review_db)\n    inventory_store = open_inventory_store(settings.review_db)"
)

# Inject into executor? We can just pass it directly. But it's easier to inject it via a global variable or closure.
# Wait, `inventory_store` will be in the local scope of `create_app` where endpoints are defined! That's perfect.

new_endpoints = """
    @app.get("/inventory")
    def list_inventory(principal: Principal = Depends(require_auth)) -> list[dict[str, Any]]:
        return [
            {
                "sku": i.sku, "title": i.title, "authors": i.authors, "format": i.format,
                "condition": i.condition, "in_store_price_cad": float(i.in_store_price_cad),
                "online_price_cad": float(i.online_price_cad), "status": i.status,
                "created_at": i.created_at.isoformat()
            }
            for i in inventory_store.list_all()
        ]

    @app.put("/inventory/{sku}")
    def update_inventory_item(sku: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        from decimal import Decimal
        in_store = Decimal(str(payload.get("in_store_price_cad", "0")))
        online = Decimal(str(payload.get("online_price_cad", "0")))
        inventory_store.update_prices(sku, in_store, online)
        operator.propose("update_prices", {"sku": sku, "in_store_price_cad": str(in_store), "online_price_cad": str(online)})
        return {"status": "ok"}

    @app.delete("/inventory/{sku}")
    def delist_inventory_item(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        inventory_store.delist(sku)
        operator.propose("update_inventory", {"sku": sku, "stock": 0})
        return {"status": "ok"}
"""

content = content.replace("return app", new_endpoints + "\n    return app")

# Inject inventory_store into ApprovedActionExecutor
content = content.replace(
    "ApprovedActionExecutor(",
    "ApprovedActionExecutor(\n                inventory_store=inventory_store,"
)

with open("app/api/http.py", "w") as f:
    f.write(content)
