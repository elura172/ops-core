-- Collector CRM, Want Lists & Priority Courtesy Holds

CREATE TABLE IF NOT EXISTS collectors (
    id text PRIMARY KEY,
    tenant_id integer NOT NULL DEFAULT 1,
    name text NOT NULL,
    email text NOT NULL DEFAULT '',
    phone text NOT NULL DEFAULT '',
    notes text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_collectors_tenant ON collectors(tenant_id);

CREATE TABLE IF NOT EXISTS want_items (
    id text PRIMARY KEY,
    collector_id text NOT NULL REFERENCES collectors(id) ON DELETE CASCADE,
    tenant_id integer NOT NULL DEFAULT 1,
    title text NOT NULL,
    author text NOT NULL DEFAULT '',
    max_price_cad numeric(10, 2),
    min_condition text,
    binding text,
    keywords text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wants_tenant_status ON want_items(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_wants_collector ON want_items(collector_id);

CREATE TABLE IF NOT EXISTS collector_holds (
    id text PRIMARY KEY,
    tenant_id integer NOT NULL DEFAULT 1,
    sku text NOT NULL,
    want_item_id text NOT NULL REFERENCES want_items(id),
    collector_id text NOT NULL REFERENCES collectors(id),
    held_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'active',
    notes text NOT NULL DEFAULT '',
    book_title text NOT NULL DEFAULT '',
    book_author text NOT NULL DEFAULT '',
    collector_name text NOT NULL DEFAULT '',
    price_cad numeric(10, 2),
    claimed_at timestamptz,
    released_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_holds_tenant_status ON collector_holds(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_holds_sku ON collector_holds(sku);
