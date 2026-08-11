-- Review queue and its audit trail.
--
-- The status CHECK mirrors app.review.queue.ReviewStatus; the two must change
-- together, in a new migration, never by editing this file.
CREATE TABLE reviews (
  review_id text PRIMARY KEY,
  operation text NOT NULL,
  payload jsonb NOT NULL,
  status text NOT NULL CHECK (
    status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTING', 'EXECUTED', 'FAILED')
  ),
  reviewer text,
  reason text,
  created_at timestamptz NOT NULL,
  decided_at timestamptz,
  executed_by text,
  executed_at timestamptz,
  result jsonb,
  failure text
);

CREATE INDEX idx_reviews_status ON reviews (status);

CREATE TABLE audit_events (
  sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action text NOT NULL,
  actor text NOT NULL,
  outcome text NOT NULL,
  resource_id text,
  detail jsonb,
  occurred_at timestamptz NOT NULL
);

CREATE INDEX idx_audit_events_resource ON audit_events (resource_id);
