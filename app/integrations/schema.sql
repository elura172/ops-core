CREATE TABLE IF NOT EXISTS core_stock_claims (
 tenant_id INTEGER NOT NULL, sku TEXT NOT NULL, kind TEXT NOT NULL,
 owner TEXT NOT NULL, expires_at DOUBLE PRECISION,
 PRIMARY KEY (tenant_id, sku)
);
CREATE TABLE IF NOT EXISTS site_publications (
 connection_id TEXT NOT NULL, tenant_id INTEGER NOT NULL, sku TEXT NOT NULL,
 revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
 PRIMARY KEY (connection_id, sku)
);
CREATE TABLE IF NOT EXISTS site_reservations (
 id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
 checkout_id TEXT NOT NULL, items TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
 status TEXT NOT NULL, UNIQUE(connection_id, checkout_id)
);
CREATE TABLE IF NOT EXISTS site_orders (
 connection_id TEXT NOT NULL, order_id TEXT NOT NULL, reservation_id TEXT NOT NULL,
 items TEXT NOT NULL, status TEXT NOT NULL,
 PRIMARY KEY(connection_id, order_id), UNIQUE(connection_id, reservation_id)
);
CREATE TABLE IF NOT EXISTS site_order_events (
 connection_id TEXT NOT NULL, event_id TEXT NOT NULL, order_id TEXT NOT NULL,
 event_type TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(connection_id, event_id), UNIQUE(connection_id, order_id, event_type)
);
CREATE TABLE IF NOT EXISTS site_requests (
 connection_id TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
 event_id TEXT NOT NULL, digest TEXT NOT NULL, status INTEGER NOT NULL,
 response TEXT NOT NULL, PRIMARY KEY(connection_id, method, path, event_id)
);
CREATE TABLE IF NOT EXISTS site_outbox (
 id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, tenant_id INTEGER NOT NULL,
 sku TEXT NOT NULL, channel TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 created_at DOUBLE PRECISION NOT NULL, next_attempt DOUBLE PRECISION NOT NULL,
 lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS site_outbox_due ON site_outbox(state, next_attempt, lease_until);
CREATE TABLE IF NOT EXISTS site_event_jobs (
 connection_id TEXT NOT NULL, event_id TEXT NOT NULL, job_id TEXT NOT NULL,
 PRIMARY KEY(connection_id, event_id, job_id)
);
CREATE TABLE IF NOT EXISTS core_channel_sales (
 tenant_id INTEGER NOT NULL, channel TEXT NOT NULL, order_id TEXT NOT NULL,
 sku TEXT NOT NULL, outcome TEXT NOT NULL,
 PRIMARY KEY(tenant_id, channel, order_id, sku)
);
CREATE TABLE IF NOT EXISTS site_rate_limits (
 connection_id TEXT PRIMARY KEY, bucket INTEGER NOT NULL, requests INTEGER NOT NULL
);
