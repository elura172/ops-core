from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.parametrize("sequence_start", [1, 100])
def test_seeded_tenants_do_not_collide_or_rewind_sequence(postgres_dsn, sequence_start):
    import psycopg
    from psycopg import sql

    schema = "tenant_sequence_" + uuid4().hex
    migration = Path("migrations/0011_tenant_sequence.sql").read_text()
    with psycopg.connect(postgres_dsn) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        conn.execute("CREATE TABLE tenants (tenant_id SERIAL PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO tenants VALUES (1, 'seed'), (47, 'imported')")
        conn.execute("SELECT setval(pg_get_serial_sequence('tenants', 'tenant_id'), %s, false)", (sequence_start,))
        conn.execute(migration)
        tenant_id = conn.execute("INSERT INTO tenants (name) VALUES ('new') RETURNING tenant_id").fetchone()[0]
        assert tenant_id > max(47, sequence_start)
        conn.rollback()
