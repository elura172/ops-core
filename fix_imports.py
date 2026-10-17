with open("app/db/inventory.py") as f:
    content = f.read()

content = content.replace("import psycopg\nfrom psycopg.rows import dict_row", "")

content = content.replace(
    'self.pool = ConnectionPool(dsn, min_size=1, max_size=5, kwargs={"row_factory": dict_row})',
    (
        "from psycopg.rows import dict_row\n"
        "        self.pool = ConnectionPool(dsn, min_size=1, max_size=5, "
        'kwargs={"row_factory": dict_row})'
    ),
)

with open("app/db/inventory.py", "w") as f:
    f.write(content)
