with open("app/config.py", "r") as f:
    content = f.read()

content = content.replace(
    "shippo_api_key: str = \"\"",
    "shippo_api_key: str = \"\"\n    shopify_store_url: str = \"\"\n    shopify_access_token: str = \"\""
)

content = content.replace(
    "shippo_api_key=os.getenv(\"SHIPPO_API_KEY\", \"\"),",
    "shippo_api_key=os.getenv(\"SHIPPO_API_KEY\", \"\"),\n            shopify_store_url=os.getenv(\"SHOPIFY_STORE_URL\", \"\"),\n            shopify_access_token=os.getenv(\"SHOPIFY_ACCESS_TOKEN\", \"\"),"
)

with open("app/config.py", "w") as f:
    f.write(content)
