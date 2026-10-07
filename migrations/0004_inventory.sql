CREATE TABLE inventory (
    sku text PRIMARY KEY,
    title text NOT NULL,
    authors jsonb NOT NULL,
    format text NOT NULL,
    condition text NOT NULL,
    in_store_price_cad numeric(10,2) NOT NULL,
    online_price_cad numeric(10,2) NOT NULL,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
