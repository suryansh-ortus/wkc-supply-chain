-- =============================================================================
-- WKC Supply Chain System - Database Schema
-- Target: Supabase Postgres 15+
-- Run once:  psql "$SUPABASE_DB_URL" -f backend/db/schema.sql
-- =============================================================================
-- Design notes:
--   * Replaces all CSV/JSON flat files from the original scripts.
--   * Race conditions are prevented by constraints, not application code:
--       - UNIQUE (request_id, seller_id) on rfq_invitations
--       - UNIQUE (invitation_id)        on quotes
--       - procurement_requests.status advanced by conditional UPDATE
--       - negotiations.version          for optimistic locking
--   * Everything the two analysis engines (STN/LTN) produce lands in one
--     place: analysis_runs -> analysis_results -> (buyer approval) -> RFQ.
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Extensions & clean slate
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- fuzzy seller search

DROP TABLE IF EXISTS
    llm_calls, market_intelligence, invitations, seller_leads,
    purchase_orders, negotiation_messages, negotiations, quotes,
    rfq_invitations, procurement_requests, analysis_results, analysis_runs,
    seller_users, buyer_users, seller_products, seller_contacts, sellers,
    monthly_sales, daily_sales, inventory, products
CASCADE;

DROP TYPE IF EXISTS
    seller_tier, seller_status, analysis_engine, run_status,
    recommendation_status, request_status, request_urgency, invitation_status,
    quote_response, negotiation_status, message_sender, lead_status,
    po_status
CASCADE;

-- ---------------------------------------------------------------------------
-- 1. Enums
-- ---------------------------------------------------------------------------
CREATE TYPE seller_tier           AS ENUM ('premium', 'mid', 'budget');
CREATE TYPE seller_status         AS ENUM ('active', 'inactive', 'pending');
CREATE TYPE analysis_engine       AS ENUM ('stn', 'ltn');
CREATE TYPE run_status            AS ENUM ('queued', 'running', 'completed', 'failed');
CREATE TYPE recommendation_status AS ENUM ('pending_approval', 'approved', 'rejected', 'superseded');
CREATE TYPE request_status        AS ENUM ('draft', 'collecting', 'negotiating', 'awarded', 'cancelled', 'expired');
CREATE TYPE request_urgency       AS ENUM ('low', 'normal', 'high', 'critical');
CREATE TYPE invitation_status     AS ENUM ('sent', 'viewed', 'responded', 'expired');
CREATE TYPE quote_response        AS ENUM ('yes', 'no');
CREATE TYPE negotiation_status    AS ENUM ('active', 'accepted', 'rejected', 'ended', 'expired');
CREATE TYPE message_sender        AS ENUM ('buyer', 'seller', 'system');
CREATE TYPE lead_status           AS ENUM ('discovered', 'invited', 'joined', 'rejected');
CREATE TYPE po_status             AS ENUM ('issued', 'confirmed', 'delivered', 'cancelled');

-- ===========================================================================
-- CATALOG & STOCK
-- ===========================================================================

-- Canonical product master. Values seeded from the original stn.py dataset.
CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY,
    sku              TEXT        NOT NULL UNIQUE,
    name             TEXT        NOT NULL,
    category         TEXT        NOT NULL,
    cost_price       NUMERIC(12,2) NOT NULL CHECK (cost_price    >= 0),
    selling_price    NUMERIC(12,2) NOT NULL CHECK (selling_price >= 0),
    shelf_life_days  INTEGER     NOT NULL CHECK (shelf_life_days > 0),
    lead_time_days   INTEGER     NOT NULL CHECK (lead_time_days  > 0),
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stock split from the catalog so quantities can move without touching prices.
CREATE TABLE inventory (
    product_id     INTEGER PRIMARY KEY REFERENCES products(product_id) ON DELETE CASCADE,
    warehouse_qty  INTEGER     NOT NULL DEFAULT 0 CHECK (warehouse_qty >= 0),
    store_qty      INTEGER     NOT NULL DEFAULT 0 CHECK (store_qty     >= 0),
    capacity_units INTEGER     NOT NULL DEFAULT 1000,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- SALES HISTORY  (was daily_sales_history.csv / monthly_sales_history.csv)
-- ===========================================================================

CREATE TABLE daily_sales (
    sale_date   DATE    NOT NULL,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    units       INTEGER NOT NULL CHECK (units >= 0),
    PRIMARY KEY (sale_date, product_id)
);
CREATE INDEX idx_daily_sales_product_date ON daily_sales (product_id, sale_date DESC);

CREATE TABLE monthly_sales (
    year        INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    month       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    units       INTEGER NOT NULL CHECK (units >= 0),
    PRIMARY KEY (year, month, product_id)
);
CREATE INDEX idx_monthly_sales_product ON monthly_sales (product_id, year, month);

-- ===========================================================================
-- SELLERS  (was sellers_database.csv - now split into 3 proper tables)
-- ===========================================================================

CREATE TABLE sellers (
    seller_id                TEXT PRIMARY KEY,               -- e.g. SELL_5001_01
    company_name             TEXT NOT NULL,
    supplier_type            TEXT NOT NULL,
    tier                     seller_tier   NOT NULL,
    location                 TEXT NOT NULL,
    city                     TEXT NOT NULL DEFAULT 'Mumbai',
    reliability_score        NUMERIC(5,2) NOT NULL CHECK (reliability_score BETWEEN 0 AND 100),
    delivery_reliability_pct NUMERIC(5,2) NOT NULL CHECK (delivery_reliability_pct BETWEEN 0 AND 100),
    price_competitiveness    NUMERIC(6,3) NOT NULL,          -- multiplier vs base cost
    avg_delivery_days        INTEGER NOT NULL CHECK (avg_delivery_days > 0),
    payment_terms            TEXT NOT NULL,
    min_order_qty            INTEGER NOT NULL CHECK (min_order_qty > 0),
    status                   seller_status NOT NULL DEFAULT 'active',
    source                   TEXT NOT NULL DEFAULT 'seed',   -- 'seed' | 'discovered'
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Fuzzy + prefix search for the portal's seller search box.
CREATE INDEX idx_sellers_name_trgm ON sellers USING gin (company_name gin_trgm_ops);
CREATE INDEX idx_sellers_status    ON sellers (status);

-- Contacts split out so a seller can have several, with real email addresses.
CREATE TABLE seller_contacts (
    contact_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id      TEXT NOT NULL REFERENCES sellers(seller_id) ON DELETE CASCADE,
    contact_person TEXT NOT NULL,
    email          TEXT NOT NULL,
    phone          TEXT,
    is_primary     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_seller_email UNIQUE (seller_id, email),
    CONSTRAINT ck_email_shape  CHECK (email ~* '^[^@\s]+@[^@\s]+\.[a-z]{2,}$')
);
CREATE UNIQUE INDEX uq_seller_primary_contact
    ON seller_contacts (seller_id) WHERE is_primary;
CREATE INDEX idx_seller_contacts_email ON seller_contacts (lower(email));

-- Many-to-many: the original CSV locked one seller to exactly one product.
CREATE TABLE seller_products (
    seller_id     TEXT    NOT NULL REFERENCES sellers(seller_id)   ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    is_preferred  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_id, product_id)
);
CREATE INDEX idx_seller_products_product ON seller_products (product_id);

-- ===========================================================================
-- AUTH  (deliberately minimal - hackathon, not production)
-- ===========================================================================

CREATE TABLE seller_users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_id     TEXT NOT NULL UNIQUE REFERENCES sellers(seller_id) ON DELETE CASCADE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE buyer_users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- ANALYSIS  (was recommendations_*.csv and weather_aware_analysis_*.json)
-- ===========================================================================

-- One row per execution of the STN or LTN engine.
CREATE TABLE analysis_runs (
    run_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine        analysis_engine NOT NULL,
    status        run_status      NOT NULL DEFAULT 'queued',
    as_of_date    DATE            NOT NULL,      -- simulated "today" for the run
    triggered_by  TEXT,                          -- buyer_users.email or 'system'
    config        JSONB           NOT NULL DEFAULT '{}'::jsonb,
    progress_pct  INTEGER         NOT NULL DEFAULT 0 CHECK (progress_pct BETWEEN 0 AND 100),
    error         TEXT,
    started_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX idx_analysis_runs_engine ON analysis_runs (engine, started_at DESC);

-- One row per product per run. This is the thing the buyer approves.
CREATE TABLE analysis_results (
    result_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id               UUID    NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    product_id           INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,

    -- deterministic layer
    baseline_quantity    INTEGER NOT NULL CHECK (baseline_quantity >= 0),
    metrics              JSONB   NOT NULL DEFAULT '{}'::jsonb,  -- profit/velocity/seasonality

    -- LLM layer (bounded adjustment of the baseline, never a free-form number)
    adjustment_factor    NUMERIC(4,2) CHECK (adjustment_factor BETWEEN 0.40 AND 1.60),
    recommended_quantity INTEGER NOT NULL CHECK (recommended_quantity >= 0),
    confidence           NUMERIC(3,2) CHECK (confidence BETWEEN 0 AND 1),
    reasoning            TEXT,
    risk_flags           TEXT[]  NOT NULL DEFAULT '{}',
    citations            JSONB   NOT NULL DEFAULT '[]'::jsonb,  -- LTN: Bright Data sources

    -- human-in-the-loop gate
    status               recommendation_status NOT NULL DEFAULT 'pending_approval',
    approved_quantity    INTEGER CHECK (approved_quantity >= 0),  -- buyer may override
    approved_by          TEXT,
    approved_at          TIMESTAMPTZ,
    rejection_note       TEXT,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_result_per_run_product UNIQUE (run_id, product_id),
    CONSTRAINT ck_approved_has_qty CHECK (
        status <> 'approved' OR approved_quantity IS NOT NULL
    )
);
CREATE INDEX idx_analysis_results_status ON analysis_results (status, created_at DESC);

-- ===========================================================================
-- PROCUREMENT  (was seller_requests_*.csv + process_*.json)
-- ===========================================================================

CREATE TABLE procurement_requests (
    request_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id       INTEGER NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    -- Link back to the recommendation this came from. NULL = created manually.
    result_id        UUID REFERENCES analysis_results(result_id) ON DELETE SET NULL,
    quantity_needed  INTEGER NOT NULL CHECK (quantity_needed > 0),
    urgency          request_urgency NOT NULL DEFAULT 'normal',
    status           request_status  NOT NULL DEFAULT 'draft',
    response_deadline TIMESTAMPTZ NOT NULL,
    created_by       TEXT NOT NULL,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_requests_status  ON procurement_requests (status, created_at DESC);
CREATE INDEX idx_requests_product ON procurement_requests (product_id);
-- One live RFQ per recommendation: approving twice cannot fan out twice.
CREATE UNIQUE INDEX uq_request_per_result
    ON procurement_requests (result_id) WHERE result_id IS NOT NULL;

-- One row per seller invited to quote. UNIQUE kills duplicate invites.
CREATE TABLE rfq_invitations (
    invitation_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id     UUID NOT NULL REFERENCES procurement_requests(request_id) ON DELETE CASCADE,
    seller_id      TEXT NOT NULL REFERENCES sellers(seller_id) ON DELETE CASCADE,
    message        TEXT,                    -- LLM-written inquiry
    status         invitation_status NOT NULL DEFAULT 'sent',
    sent_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    viewed_at      TIMESTAMPTZ,
    CONSTRAINT uq_invite_per_request_seller UNIQUE (request_id, seller_id)
);
CREATE INDEX idx_invitations_seller ON rfq_invitations (seller_id, status);

-- The seller's answer. UNIQUE(invitation_id) => a seller can answer exactly once.
CREATE TABLE quotes (
    quote_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invitation_id         UUID NOT NULL UNIQUE REFERENCES rfq_invitations(invitation_id) ON DELETE CASCADE,
    request_id            UUID NOT NULL REFERENCES procurement_requests(request_id) ON DELETE CASCADE,
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id) ON DELETE CASCADE,
    response              quote_response NOT NULL,
    quoted_price          NUMERIC(12,2) CHECK (quoted_price > 0),
    expected_delivery_days INTEGER      CHECK (expected_delivery_days > 0),
    notes                 TEXT,
    score                 NUMERIC(6,2),   -- computed rank: price/delivery/reliability
    submitted_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A "yes" must carry commercial terms; a "no" must not.
    CONSTRAINT ck_quote_terms CHECK (
        (response = 'yes' AND quoted_price IS NOT NULL AND expected_delivery_days IS NOT NULL)
        OR
        (response = 'no'  AND quoted_price IS NULL     AND expected_delivery_days IS NULL)
    )
);
CREATE INDEX idx_quotes_request ON quotes (request_id, response);

-- ===========================================================================
-- NEGOTIATION  (was negotiation_*.json)
-- ===========================================================================

CREATE TABLE negotiations (
    negotiation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id       UUID NOT NULL REFERENCES procurement_requests(request_id) ON DELETE CASCADE,
    quote_id         UUID NOT NULL UNIQUE REFERENCES quotes(quote_id) ON DELETE CASCADE,
    seller_id        TEXT NOT NULL REFERENCES sellers(seller_id) ON DELETE CASCADE,
    status           negotiation_status NOT NULL DEFAULT 'active',
    current_round    INTEGER NOT NULL DEFAULT 1 CHECK (current_round >= 1),
    max_rounds       INTEGER NOT NULL DEFAULT 5  CHECK (max_rounds   >= 1),
    opening_price    NUMERIC(12,2),
    opening_delivery INTEGER,
    final_price      NUMERIC(12,2),
    final_delivery   INTEGER,
    -- Optimistic lock: a stale round write is rejected instead of appended.
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_round_within_cap CHECK (current_round <= max_rounds),
    CONSTRAINT ck_settled_has_terms CHECK (
        status <> 'accepted' OR final_price IS NOT NULL
    )
);
CREATE INDEX idx_negotiations_seller ON negotiations (seller_id, status);
CREATE INDEX idx_negotiations_request ON negotiations (request_id, status);

CREATE TABLE negotiation_messages (
    message_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id    UUID NOT NULL REFERENCES negotiations(negotiation_id) ON DELETE CASCADE,
    round             INTEGER NOT NULL CHECK (round >= 1),
    sender            message_sender NOT NULL,
    body              TEXT NOT NULL,
    proposed_price    NUMERIC(12,2) CHECK (proposed_price > 0),
    proposed_delivery INTEGER       CHECK (proposed_delivery > 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One turn per side per round: double-submit cannot duplicate a round.
    CONSTRAINT uq_turn_per_round UNIQUE (negotiation_id, round, sender)
);
CREATE INDEX idx_messages_negotiation ON negotiation_messages (negotiation_id, created_at);

-- The outcome. The original system had nowhere to record a won deal.
CREATE TABLE purchase_orders (
    po_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number       TEXT NOT NULL UNIQUE,
    request_id      UUID NOT NULL REFERENCES procurement_requests(request_id) ON DELETE RESTRICT,
    negotiation_id  UUID REFERENCES negotiations(negotiation_id) ON DELETE SET NULL,
    seller_id       TEXT NOT NULL REFERENCES sellers(seller_id)  ON DELETE RESTRICT,
    product_id      INTEGER NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price      NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
    total_value     NUMERIC(14,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
    delivery_days   INTEGER NOT NULL CHECK (delivery_days > 0),
    status          po_status NOT NULL DEFAULT 'issued',
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Only one award per RFQ.
CREATE UNIQUE INDEX uq_po_per_request ON purchase_orders (request_id);

-- ===========================================================================
-- SUPPLIER DISCOVERY  (new: find suppliers -> email invite -> they join)
-- ===========================================================================

CREATE TABLE seller_leads (
    lead_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id    INTEGER REFERENCES products(product_id) ON DELETE SET NULL,
    company_name  TEXT NOT NULL,
    email         TEXT,
    phone         TEXT,
    website       TEXT,
    location      TEXT,
    supplier_type TEXT,
    source_url    TEXT,                       -- where Bright Data found them
    raw           JSONB NOT NULL DEFAULT '{}'::jsonb,
    status        lead_status NOT NULL DEFAULT 'discovered',
    seller_id     TEXT REFERENCES sellers(seller_id) ON DELETE SET NULL,  -- set on join
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_lead_email UNIQUE (email)
);
CREATE INDEX idx_leads_status ON seller_leads (status, discovered_at DESC);

-- Signed join links emailed to discovered suppliers.
CREATE TABLE invitations (
    token          TEXT PRIMARY KEY,
    lead_id        UUID REFERENCES seller_leads(lead_id) ON DELETE CASCADE,
    request_id     UUID REFERENCES procurement_requests(request_id) ON DELETE SET NULL,
    email          TEXT NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    email_sent_at  TIMESTAMPTZ,
    email_provider TEXT,                      -- 'console' | 'resend'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_invitations_email ON invitations (lower(email));

-- ===========================================================================
-- INFRASTRUCTURE
-- ===========================================================================

-- Bright Data SERP cache: repeat runs must not burn credits.
CREATE TABLE market_intelligence (
    cache_key   TEXT PRIMARY KEY,             -- sha256(provider|query|locale)
    query       TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT 'brightdata_serp',
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_market_intel_expiry ON market_intelligence (expires_at);

-- Every DeepInfra call, for cost/latency visibility. Replaces print().
CREATE TABLE llm_calls (
    call_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            UUID REFERENCES analysis_runs(run_id) ON DELETE SET NULL,
    purpose           TEXT NOT NULL,          -- 'stn_adjust' | 'ltn_plan' | 'negotiate' ...
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    latency_ms        INTEGER,
    ok                BOOLEAN NOT NULL DEFAULT TRUE,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_llm_calls_created ON llm_calls (created_at DESC);

-- ---------------------------------------------------------------------------
-- Convenience view: current stock + product master in one place
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_product_stock AS
SELECT p.product_id,
       p.sku,
       p.name,
       p.category,
       p.cost_price,
       p.selling_price,
       p.shelf_life_days,
       p.lead_time_days,
       i.warehouse_qty,
       i.store_qty,
       (i.warehouse_qty + i.store_qty) AS total_qty,
       i.capacity_units
FROM products p
JOIN inventory i USING (product_id);

COMMIT;
