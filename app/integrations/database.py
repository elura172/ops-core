from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from typing import Any


class Connection:
    """Small SQL dialect boundary. All integration state lives with inventory."""

    def __init__(self, conn: Any, postgres: bool):
        self.conn, self.postgres = conn, postgres

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> Any:
        return self.conn.execute(sql.replace("?", "%s") if self.postgres else sql, args)

    def now(self) -> float:
        sql = (
            "SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS value"
            if self.postgres
            else "SELECT (julianday('now') - 2440587.5) * 86400 AS value"
        )
        return float(self.execute(sql).fetchone()["value"])

    def inventory(self, tenant: int, sku: str) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if self.postgres else ""
        row = self.execute("SELECT * FROM inventory WHERE tenant_id=? AND sku=?" + suffix, (tenant, sku)).fetchone()
        return dict(row) if row else None


@contextmanager
def transaction(store: Any, tenant: int | None = None) -> Iterator[Connection]:
    if hasattr(store, "pool"):
        with store.pool.connection() as conn, conn.transaction():
            db = Connection(conn, True)
            # Serialize connection/idempotency and cross-SKU operations per tenant.
            if tenant is not None:
                db.execute("SELECT pg_advisory_xact_lock(183629, ?)", (tenant,))
            yield db
    else:
        with store._transaction() as conn:
            yield Connection(conn, False)


def install_sqlite(conn: Any) -> None:
    for statement in files("app.integrations").joinpath("schema.sql").read_text().split(";"):
        if statement.strip():
            conn.execute(statement)
    live = "(expires_at IS NULL OR expires_at > (julianday('now')-2440587.5)*86400)"
    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS stock_guard_update
    BEFORE UPDATE ON inventory WHEN
      OLD.tenant_id != NEW.tenant_id OR
      (OLD.status != NEW.status AND EXISTS (
       SELECT 1 FROM core_stock_claims WHERE tenant_id=OLD.tenant_id AND sku=OLD.sku
       AND {live} AND (kind='checkout' OR
        (kind='collector' AND NEW.status NOT IN ('held','on_hold')) OR
        (kind IN ('sale','blocked') AND NEW.status IN ('active','held','on_hold')))))
    BEGIN SELECT RAISE(ABORT, 'stock_claim_conflict'); END""")
    conn.execute(f"""CREATE TRIGGER IF NOT EXISTS stock_guard_delete
    BEFORE DELETE ON inventory WHEN EXISTS (
      SELECT 1 FROM core_stock_claims WHERE tenant_id=OLD.tenant_id AND sku=OLD.sku AND {live})
    BEGIN SELECT RAISE(ABORT, 'stock_claim_conflict'); END""")


def clear_for_relist(conn: Any, postgres: bool, tenant: int, skus: list[str]) -> None:
    """An explicit operator relist can clear a sale, never a live checkout/hold."""
    db = Connection(conn, postgres)
    for sku in sorted(skus):
        db.inventory(tenant, sku)
        db.execute(
            "DELETE FROM core_stock_claims WHERE tenant_id=? AND sku=? AND kind IN ('sale','blocked')",
            (tenant, sku),
        )
