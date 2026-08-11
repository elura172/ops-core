-- Order state, the processed-event ledger, and the conflict quarantine.
--
-- Same shape as the SQLite repository so the two backends stay behaviorally
-- interchangeable. The processed_events primary key is the duplicate guard:
-- webhook delivery is neither ordered nor exactly-once, and a replayed event
-- must lose on that key, not on anything a worker read earlier.
-- The status CHECKs mirror app.domain.orders.OrderStatus; change them together,
-- in a new migration.
CREATE TABLE orders (
  order_id text PRIMARY KEY,
  created_at timestamptz NOT NULL,
  status text NOT NULL CHECK (
    status IN ('RECEIVED', 'PAID', 'FULFILLMENT_PENDING', 'LABEL_CREATED',
               'SHIPPED', 'IN_TRANSIT', 'DELIVERED', 'CANCELLED')
  ),
  updated_at timestamptz NOT NULL
);

CREATE TABLE processed_events (
  event_id text PRIMARY KEY,
  order_id text NOT NULL,
  status text NOT NULL,
  occurred_at timestamptz NOT NULL,
  outcome text NOT NULL DEFAULT 'applied'
);

CREATE INDEX idx_processed_events_order ON processed_events (order_id);

-- resolved is an integer, not a boolean, so the rows serialize identically to
-- the SQLite backend on the operator API.
CREATE TABLE quarantined_events (
  event_id text PRIMARY KEY,
  order_id text NOT NULL,
  observed_status text NOT NULL,
  local_status text NOT NULL,
  occurred_at timestamptz NOT NULL,
  resolved integer NOT NULL DEFAULT 0
);
