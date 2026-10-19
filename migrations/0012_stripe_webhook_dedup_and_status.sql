-- Durable Stripe webhook deduplication and raw subscription lifecycle status.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS stripe_subscription_status TEXT;

CREATE TABLE IF NOT EXISTS stripe_webhook_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
