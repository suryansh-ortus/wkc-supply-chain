-- UPI payment against a purchase order.
--   python db/apply_migration.py db/migration_03_payments.sql

BEGIN;

CREATE TABLE IF NOT EXISTS payments (
    payment_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id       UUID NOT NULL REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
    vpa         TEXT NOT NULL,
    payee_name  TEXT NOT NULL,
    amount      NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    upi_link    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | paid
    paid_at     TIMESTAMPTZ,
    paid_by     TEXT,
    utr         TEXT,                              -- UPI reference, typed in by hand
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- one payment per purchase order
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_per_po ON payments (po_id);

COMMIT;
