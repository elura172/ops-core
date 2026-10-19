-- Tenant 1 was seeded with an explicit ID, which does not advance SERIAL.
-- Block concurrent inserts while aligning the sequence, never moving it back.
LOCK TABLE tenants IN ACCESS EXCLUSIVE MODE;
SELECT setval(
    pg_get_serial_sequence('tenants', 'tenant_id'),
    GREATEST(
        nextval(pg_get_serial_sequence('tenants', 'tenant_id')),
        COALESCE((SELECT MAX(tenant_id) FROM tenants), 1)
    ),
    true
);
