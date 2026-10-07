-- Multi-tenant schema additions: tenants, users, integrations, and tenant_id columns

CREATE TABLE IF NOT EXISTS tenants (
  tenant_id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  stripe_customer_id TEXT,
  subscription_status TEXT NOT NULL DEFAULT 'inactive'
);

CREATE TABLE IF NOT EXISTS users (
  user_id SERIAL PRIMARY KEY,
  tenant_id INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'operator'
);

CREATE TABLE IF NOT EXISTS tenant_integrations (
  tenant_id INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  encrypted_credentials TEXT NOT NULL,
  PRIMARY KEY (tenant_id, provider)
);

-- Seed default tenant 1 if it doesn't exist so legacy single-tenant rows have a valid foreign key
INSERT INTO tenants (tenant_id, name, created_at, subscription_status)
VALUES (1, 'Primary Operator', now(), 'active')
ON CONFLICT (tenant_id) DO NOTHING;

-- Add tenant_id to existing tables if not present
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='reviews' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE reviews ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
    CREATE INDEX idx_reviews_tenant ON reviews (tenant_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='audit_events' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE audit_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
    CREATE INDEX idx_audit_events_tenant ON audit_events (tenant_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='orders' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE orders ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
    CREATE INDEX idx_orders_tenant ON orders (tenant_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='processed_events' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE processed_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='quarantined_events' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE quarantined_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='inventory' AND column_name='tenant_id'
  ) THEN
    ALTER TABLE inventory ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(tenant_id);
    CREATE INDEX idx_inventory_tenant ON inventory (tenant_id);
  END IF;
END $$;
