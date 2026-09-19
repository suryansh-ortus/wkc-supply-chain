-- Hold system: when stock is sufficient, a product is parked for N days and is
-- skipped entirely by STN and LTN (never sent to the LLM) until the hold expires.
--
--   psql / python db/apply_migration.py db/migration_02_holds.sql

BEGIN;

CREATE TABLE IF NOT EXISTS product_holds (
    hold_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    hold_days   INTEGER NOT NULL CHECK (hold_days BETWEEN 1 AND 365),
    hold_until  DATE    NOT NULL,
    reason      TEXT,
    created_by  TEXT    NOT NULL DEFAULT 'stn',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

-- One active hold per product.
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_hold
    ON product_holds (product_id) WHERE released_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_holds_until ON product_holds (hold_until);

-- The LLM's hold decision, stored alongside the recommendation.
ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS hold_days INTEGER;

COMMIT;
