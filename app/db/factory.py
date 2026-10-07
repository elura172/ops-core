"""Choose storage backends from configuration.

``REVIEW_DB`` and ``ORDER_DB`` are each either a filesystem path (SQLite, the
single-node default) or a ``postgresql://`` DSN — usually the same DSN, since
both schemas live on one migration spine. Postgres is the backend for anything
with more than one worker process; ``app.db.migrate`` runs before a store
opens so a half-migrated database refuses to serve.
"""

from __future__ import annotations

from typing import Any

from app.review.queue import ReviewStore

_POSTGRES_PREFIXES = ("postgres://", "postgresql://")


def is_postgres_dsn(value: str | Path) -> bool:
    return str(value).startswith(_POSTGRES_PREFIXES)


def open_review_store(review_db: str) -> ReviewStore:
    if is_postgres_dsn(review_db):
        # Imported lazily so SQLite deployments do not need psycopg installed.
        from app.db.migrate import migrate
        from app.db.postgres import PostgresReviewStore

        migrate(review_db)
        return PostgresReviewStore(review_db)

    from app.db.review_store import SQLiteReviewStore

    return SQLiteReviewStore(review_db)


def open_order_repository(order_db: str) -> Any:
    if is_postgres_dsn(order_db):
        from app.db.migrate import migrate
        from app.db.postgres_orders import PostgresOrderRepository

        migrate(order_db)
        return PostgresOrderRepository(order_db)

    from app.db.sqlite import SQLiteOrderRepository

    return SQLiteOrderRepository(order_db)

def open_inventory_store(db_url: str) -> Any:
    if is_postgres_dsn(db_url):
        from app.db.migrate import migrate
        from app.db.inventory import PostgresInventoryStore
        migrate(db_url)
        return PostgresInventoryStore(db_url)
    
    from app.db.inventory import SQLiteInventoryStore
    return SQLiteInventoryStore(db_url)


def open_user_store(user_db: str) -> Any:
    if is_postgres_dsn(user_db):
        from app.db.migrate import migrate
        from app.db.users import PostgresUserStore

        migrate(user_db)
        return PostgresUserStore(user_db)

    from app.db.users import SQLiteUserStore

    return SQLiteUserStore(user_db)
