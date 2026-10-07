import sqlite3
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
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

class UserStore:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)

    def create_tenant(self, name: str) -> int:
        from datetime import datetime, timezone
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO tenants(name, created_at) VALUES (?, ?)",
                (name, datetime.now(timezone.utc).isoformat())
            )
            return cursor.lastrowid

    def create_user(self, tenant_id: int, email: str, password_hash: str, role: str = "admin") -> int:
        with self._lock:
            cursor = self.connection.execute(
                "INSERT INTO users(tenant_id, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (tenant_id, email, password_hash, role)
            )
            return cursor.lastrowid

    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
        if row:
            return User(**dict(row))
        return None

