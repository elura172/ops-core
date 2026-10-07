with open("app/api/http.py", "r") as f:
    content = f.read()

content = content.replace(
    "                inventory_store=inventory_store,\n                TikTokClient(",
    "                TikTokClient("
)

content = content.replace(
    "shippo_api_key=settings.shippo_api_key,\n            ),",
    "shippo_api_key=settings.shippo_api_key,\n                inventory_store=inventory_store,\n            ),"
)

with open("app/api/http.py", "w") as f:
    f.write(content)
