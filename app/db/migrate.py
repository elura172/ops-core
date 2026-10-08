"""Forward-only SQL migrations for the Postgres backend.

Migrations are numbered files in ``migrations/`` (``0001_name.sql``). Each
unapplied file runs inside its own transaction together with the row that
records it, so a failed migration leaves nothing half-applied. An advisory
lock serializes concurrent runners (several API replicas booting at once);
whoever wins runs the migrations, the rest wait and then see them applied.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

import time

from app.observability.logging import get_logger

logger = get_logger("migrate")


def connect_with_retry(
    dsn: str,
    max_retries: int = 3,
    initial_delay: float = 0.5,
    timeout: int = 10,
) -> psycopg.Connection:
    """Connect to Postgres with retries to handle serverless cold-start delays."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return psycopg.connect(dsn, autocommit=True, connect_timeout=timeout)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.warning(
                    "db.connect_retry",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(exc),
                )
                time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("failed to connect to database")


def _default_migrations_dir() -> Path:
    """The repo checkout in development; the WORKDIR copy in the container."""
    checkout = Path(__file__).resolve().parents[2] / "migrations"
    if checkout.is_dir():
        return checkout
    return Path.cwd() / "migrations"


MIGRATIONS_DIR = _default_migrations_dir()
#: One lock for the whole schema; the value is arbitrary but must never change.
_ADVISORY_LOCK_KEY = 0x6F70735F
_FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    def sql(self) -> str:
        return self.path.read_text()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"migration filename does not match NNNN_name.sql: {path.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version:04d}: "
                f"{found[version].path.name} and {path.name}"
            )
        found[version] = Migration(version, match.group(2), path)
    return [found[version] for version in sorted(found)]


def migrate(dsn: str, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply every unapplied migration. Returns the migrations applied by this call."""
    migrations = discover(directory)
    applied: list[Migration] = []
    with connect_with_retry(dsn) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  version integer PRIMARY KEY,"
                "  name text NOT NULL,"
                "  applied_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )
            rows = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
            recorded = {row[0]: row[1] for row in rows}

            # Refuse drift instead of guessing: every applied migration must
            # still exist on disk under the same name.
            on_disk = {migration.version: migration.name for migration in migrations}
            for version, name in recorded.items():
                if on_disk.get(version) != name:
                    raise MigrationError(
                        f"applied migration {version:04d}_{name} does not match "
                        f"the files on disk; refusing to continue"
                    )

            for migration in migrations:
                if migration.version in recorded:
                    continue
                with connection.transaction():
                    connection.execute(migration.sql())
                    connection.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (migration.version, migration.name),
                    )
                logger.info("migrate.applied", version=migration.version, name=migration.name)
                applied.append(migration)
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
    return applied


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m app.db.migrate <postgres-dsn>", file=sys.stderr)
        return 2
    applied = migrate(args[0])
    for migration in applied:
        print(f"applied {migration.version:04d}_{migration.name}")
    if not applied:
        print("nothing to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
