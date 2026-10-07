with open("app/db/factory.py", "r") as f:
    content = f.read()

content += """
def open_inventory_store(db_url: str) -> Any:
    if is_postgres_dsn(db_url):
        from app.db.migrate import migrate
        from app.db.inventory import PostgresInventoryStore
        migrate(db_url)
        return PostgresInventoryStore(db_url)
    
    from app.db.inventory import SQLiteInventoryStore
    return SQLiteInventoryStore(db_url)
"""

with open("app/db/factory.py", "w") as f:
    f.write(content)
