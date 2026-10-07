-- Physical shelf organization & batch reconciliation audits

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='inventory' AND column_name='section'
  ) THEN
    ALTER TABLE inventory ADD COLUMN section text NOT NULL DEFAULT 'General';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='inventory' AND column_name='shelf'
  ) THEN
    ALTER TABLE inventory ADD COLUMN shelf text NOT NULL DEFAULT '';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name='inventory' AND column_name='last_audited_at'
  ) THEN
    ALTER TABLE inventory ADD COLUMN last_audited_at timestamptz;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_inventory_location ON inventory (tenant_id, section, shelf);

CREATE TABLE IF NOT EXISTS shelf_audits (
    id text PRIMARY KEY,
    tenant_id integer NOT NULL DEFAULT 1,
    section text NOT NULL,
    shelf text NOT NULL,
    audited_by text NOT NULL,
    total_expected integer NOT NULL DEFAULT 0,
    total_scanned integer NOT NULL DEFAULT 0,
    verified_count integer NOT NULL DEFAULT 0,
    missing_count integer NOT NULL DEFAULT 0,
    misplaced_count integer NOT NULL DEFAULT 0,
    discrepancies jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shelf_audits_tenant ON shelf_audits (tenant_id, created_at DESC);
