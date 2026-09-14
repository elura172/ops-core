-- Existing rows lack timestamp provenance. Keep them out of SLA evaluation
-- until reconciled. New inserts explicitly supply the first-event provenance.
ALTER TABLE orders ADD COLUMN created_at_estimated boolean NOT NULL DEFAULT true;
