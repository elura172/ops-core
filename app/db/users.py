import sqlite3
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  stripe_customer_id TEXT,
  subscription_status TEXT DEFAULT 'inactive'
);

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'operator',
  FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id)
);

CREATE TABLE IF NOT EXISTS tenant_integrations (
  tenant_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  encrypted_credentials TEXT NOT NULL,
  PRIMARY KEY (tenant_id, provider),
  FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id)
);
"""


@dataclass
class User:
    user_id: int
    tenant_id: int
    email: str
    password_hash: str
    role: str


class SQLiteUserStore:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

        # Migrations for existing DBs
        with suppress(sqlite3.OperationalError):
            self.connection.execute("ALTER TABLE tenants ADD COLUMN stripe_customer_id TEXT")
        with suppress(sqlite3.OperationalError):
            self.connection.execute("ALTER TABLE tenants ADD COLUMN subscription_status TEXT DEFAULT 'inactive'")

        # Keep the local sample tenant and mock integrations, but never provision
        # a shared public login. Remove only its historical seeded account.
        try:
            with self._lock:
                self.connection.execute(
                    "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at, subscription_status) "
                    "VALUES (1, 'Elura Antiquarian Vault', ?, 'active')",
                    (datetime.now(UTC).isoformat(),),
                )
                self.connection.execute("UPDATE tenants SET subscription_status = 'active' WHERE tenant_id = 1")
                self.connection.execute("DELETE FROM users WHERE tenant_id = 1 AND email = 'operator@opscore.io'")
                from app.vault import encrypt_credential

                self.connection.execute(
                    "INSERT OR IGNORE INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'square', ?)",
                    (encrypt_credential("sq0atp-mock-square-token"),),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'shopify', ?)",
                    (encrypt_credential("shpat_mock-shopify-token"),),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'ebay', ?)",
                    (encrypt_credential("v^1.1#mock-ebay-oauth-token"),),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'abebooks', ?)",
                    (encrypt_credential("abe_mock_api_key_123"),),
                )
        except Exception as exc:
            import logging

            logging.getLogger("ops_core.users").warning("seed.users.failed: %s", exc)

    def create_tenant(self, name: str) -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO tenants(name, created_at) VALUES (?, ?)",
                (name, datetime.now(UTC).isoformat()),
            )
            return cursor.lastrowid

    def create_user(self, tenant_id: int, email: str, password_hash: str, role: str = "admin") -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO users(tenant_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (tenant_id, email, password_hash, role),
            )
            return cursor.lastrowid

    def get_user_by_email(self, email: str) -> User | None:
        with self._lock:
            row = self.connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return User(**dict(row))
        return None

    def update_user_password(self, user_id: int, password_hash: str) -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            )
            self.connection.commit()

    def list_tenants(self) -> list[int]:
        with self._lock:
            rows = self.connection.execute("SELECT tenant_id FROM tenants").fetchall()
            return [row["tenant_id"] for row in rows]

    def get_tenant_integrations(self, tenant_id: int) -> dict[str, str]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT provider, encrypted_credentials FROM tenant_integrations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
            return {row["provider"]: row["encrypted_credentials"] for row in rows}

    def save_integration(self, tenant_id: int, provider: str, encrypted_credentials: str) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO tenant_integrations "
                "(tenant_id, provider, encrypted_credentials) VALUES (?, ?, ?)",
                (tenant_id, provider, encrypted_credentials),
            )

    def list_tenant_providers(self, tenant_id: int) -> list[str]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT provider FROM tenant_integrations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchall()
            return [row["provider"] for row in rows]

    def get_tenant_billing_status(self, tenant_id: int) -> dict:
        with self._lock:
            row = self.connection.execute(
                "SELECT stripe_customer_id, subscription_status FROM tenants WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if row:
                return dict(row)
            return {"stripe_customer_id": None, "subscription_status": "inactive"}

    def update_tenant_stripe_customer(self, tenant_id: int, stripe_customer_id: str):
        with self._lock:
            self.connection.execute(
                "UPDATE tenants SET stripe_customer_id = ? WHERE tenant_id = ?",
                (stripe_customer_id, tenant_id),
            )

    def update_subscription_status(self, stripe_customer_id: str, status: str):
        with self._lock:
            self.connection.execute(
                "UPDATE tenants SET subscription_status = ? WHERE stripe_customer_id = ?",
                (status, stripe_customer_id),
            )

    def close(self) -> None:
        self.connection.close()


class PostgresUserStore:
    def __init__(self, dsn: str, *, pool_size: int = 3) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            max_idle=60.0,
            timeout=30.0,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO tenants (tenant_id, name, created_at, subscription_status) "
                    "VALUES (1, 'Elura Antiquarian Vault', %s, 'active') "
                    "ON CONFLICT (tenant_id) DO UPDATE SET subscription_status = 'active'",
                    (datetime.now(UTC),),
                )
                conn.execute("DELETE FROM users WHERE tenant_id = 1 AND email = 'operator@opscore.io'")
                from app.vault import encrypt_credential

                conn.execute(
                    "INSERT INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'square', %s) "
                    "ON CONFLICT DO NOTHING",
                    (encrypt_credential("sq0atp-mock-square-token"),),
                )
                conn.execute(
                    "INSERT INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'shopify', %s) "
                    "ON CONFLICT DO NOTHING",
                    (encrypt_credential("shpat_mock-shopify-token"),),
                )
                conn.execute(
                    "INSERT INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'ebay', %s) "
                    "ON CONFLICT DO NOTHING",
                    (encrypt_credential("v^1.1#mock-ebay-oauth-token"),),
                )
                conn.execute(
                    "INSERT INTO tenant_integrations "
                    "(tenant_id, provider, encrypted_credentials) VALUES (1, 'abebooks', %s) "
                    "ON CONFLICT DO NOTHING",
                    (encrypt_credential("abe_mock_api_key_123"),),
                )
        except Exception as exc:
            import logging

            logging.getLogger("ops_core.users").warning("seed.pg_users.failed: %s", exc)

    def create_tenant(self, name: str) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO tenants (name, created_at) VALUES (%s, %s) RETURNING tenant_id",
                (name, datetime.now(UTC)),
            ).fetchone()
            return row["tenant_id"]

    def create_user(self, tenant_id: int, email: str, password_hash: str, role: str = "admin") -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO users (tenant_id, email, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING user_id",
                (tenant_id, email, password_hash, role),
            ).fetchone()
            return row["user_id"]

    def get_user_by_email(self, email: str) -> User | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = %s",
                (email,),
            ).fetchone()
            if row:
                return User(**row)
            return None

    def update_user_password(self, user_id: int, password_hash: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE user_id = %s",
                (password_hash, user_id),
            )

    def list_tenants(self) -> list[int]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT tenant_id FROM tenants ORDER BY tenant_id").fetchall()
            return [row["tenant_id"] for row in rows]

    def get_tenant_integrations(self, tenant_id: int) -> dict[str, str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT provider, encrypted_credentials FROM tenant_integrations WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()
            return {row["provider"]: row["encrypted_credentials"] for row in rows}

    def save_integration(self, tenant_id: int, provider: str, encrypted_credentials: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO tenant_integrations(tenant_id, provider, encrypted_credentials) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, provider) DO UPDATE SET "
                "encrypted_credentials = EXCLUDED.encrypted_credentials",
                (tenant_id, provider, encrypted_credentials),
            )

    def list_tenant_providers(self, tenant_id: int) -> list[str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT provider FROM tenant_integrations WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()
            return [row["provider"] for row in rows]

    def get_tenant_billing_status(self, tenant_id: int) -> dict:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT stripe_customer_id, subscription_status FROM tenants WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchone()
            if row:
                return dict(row)
            return {"stripe_customer_id": None, "subscription_status": "inactive"}

    def update_tenant_stripe_customer(self, tenant_id: int, stripe_customer_id: str):
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE tenants SET stripe_customer_id = %s WHERE tenant_id = %s",
                (stripe_customer_id, tenant_id),
            )

    def update_subscription_status(self, stripe_customer_id: str, status: str):
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE tenants SET subscription_status = %s WHERE stripe_customer_id = %s",
                (status, stripe_customer_id),
            )

    def close(self) -> None:
        self.pool.close()


UserStore = SQLiteUserStore


def open_user_store(target: str | Path):
    target_str = str(target)
    if target_str.startswith(("postgres://", "postgresql://")):
        from app.db.migrate import migrate

        migrate(target_str)
        return PostgresUserStore(target_str)
    return SQLiteUserStore(target)
