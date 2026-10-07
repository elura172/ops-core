CREATE TABLE IF NOT EXISTS corvus_sanctuary (
    id text PRIMARY KEY,
    tenant_id integer NOT NULL DEFAULT 1,
    token_type text NOT NULL,
    operation text NOT NULL,
    review_id text,
    subject text NOT NULL,
    inscription text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS corvus_blessings (
    id text PRIMARY KEY,
    tenant_id integer NOT NULL DEFAULT 1,
    sender text NOT NULL DEFAULT 'operator',
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
