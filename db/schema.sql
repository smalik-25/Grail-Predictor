-- Grail Predictor warehouse schema. Hand-written, no ORM.
-- Star schema centered on the canonical catalog: dim_items is the spine,
-- every fact table hangs off it. Re-running ingestion must be idempotent,
-- so every fact table carries a natural-key UNIQUE constraint and the
-- loader inserts with ON CONFLICT DO NOTHING.

-- ---------------------------------------------------------------------------
-- Dimensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_platforms (
    platform_id   SERIAL PRIMARY KEY,
    platform_name TEXT NOT NULL UNIQUE,
    -- resale and retail platforms price pieces; search and social measure attention
    platform_type TEXT NOT NULL CHECK (platform_type IN ('resale', 'retail', 'search', 'social'))
);

CREATE TABLE IF NOT EXISTS dim_items (
    -- item_id comes from resolution/catalog.py, deterministic per input set.
    item_id         TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    brand           TEXT,
    category        TEXT,
    season          TEXT,
    first_seen_date DATE
);

CREATE INDEX IF NOT EXISTS idx_dim_items_brand ON dim_items (brand);

-- The forecasting grain: Brand -> Model-line -> Era. Colorway tier is an
-- attribute on listings, never part of this key. family_id lives on
-- fact_listings rather than dim_items because a canonical item can span
-- eras when the text matcher merges near-identical generations; the
-- listing is the row that knows its family for certain.
CREATE TABLE IF NOT EXISTS dim_style_families (
    family_id       TEXT PRIMARY KEY,
    brand           TEXT NOT NULL,
    model_line      TEXT NOT NULL,
    era             TEXT NOT NULL,
    colorway_tiers  TEXT,  -- comma-joined distinct tiers seen, informational
    first_seen_date DATE,
    CONSTRAINT uq_family_natural_key UNIQUE (brand, model_line, era)
);

CREATE INDEX IF NOT EXISTS idx_dim_style_families_brand ON dim_style_families (brand);

-- ---------------------------------------------------------------------------
-- Facts
-- ---------------------------------------------------------------------------

-- One row per platform listing resolved to a canonical item.
-- Natural key: (platform_id, listing_url). Postgres treats NULLs as distinct
-- in UNIQUE constraints, so url-less listings never collide; accepted, since
-- only fixtures lack urls in practice.
CREATE TABLE IF NOT EXISTS fact_listings (
    listing_id        BIGSERIAL PRIMARY KEY,
    item_id           TEXT NOT NULL REFERENCES dim_items (item_id),
    family_id         TEXT REFERENCES dim_style_families (family_id),
    colorway          TEXT,
    colorway_tier     TEXT CHECK (colorway_tier IN ('core', 'rare', 'standard', 'unknown')),
    platform_id       INTEGER NOT NULL REFERENCES dim_platforms (platform_id),
    listing_url       TEXT,
    listed_date       DATE,
    asking_price      NUMERIC(12, 2) CHECK (asking_price IS NULL OR asking_price > 0),
    currency          CHAR(3),
    size_normalized   TEXT,
    condition_ordinal SMALLINT CHECK (condition_ordinal BETWEEN 1 AND 5),
    is_sold           BOOLEAN NOT NULL DEFAULT FALSE,
    sold_date         DATE,
    sold_price        NUMERIC(12, 2) CHECK (sold_price IS NULL OR sold_price > 0),
    -- a sold row must be marked sold; enforced here rather than trusted to loaders
    CONSTRAINT sold_fields_consistent CHECK (NOT (sold_price IS NOT NULL AND is_sold = FALSE)),
    CONSTRAINT uq_listing_natural_key UNIQUE (platform_id, listing_url)
);

CREATE INDEX IF NOT EXISTS idx_fact_listings_item ON fact_listings (item_id);
CREATE INDEX IF NOT EXISTS idx_fact_listings_family ON fact_listings (family_id);
CREATE INDEX IF NOT EXISTS idx_fact_listings_listed_date ON fact_listings (listed_date);
CREATE INDEX IF NOT EXISTS idx_fact_listings_sold_date ON fact_listings (sold_date) WHERE sold_date IS NOT NULL;

-- First-hand retail price observations over time.
-- Different grain and different meaning from fact_listings: a retail row is
-- an observation of a product's shelf price on a date, not a tradeable
-- listing. item_id is nullable because linking retail products to canonical
-- resale items is its own resolution problem, scoped for a later phase;
-- brand and product_name are kept so that linkage remains possible.
CREATE TABLE IF NOT EXISTS fact_retail_prices (
    retail_price_id BIGSERIAL PRIMARY KEY,
    item_id         TEXT REFERENCES dim_items (item_id),
    platform_id     INTEGER NOT NULL REFERENCES dim_platforms (platform_id),
    brand           TEXT,
    product_name    TEXT NOT NULL,
    product_url     TEXT,
    observed_date   DATE NOT NULL,
    retail_price    NUMERIC(12, 2) CHECK (retail_price IS NULL OR retail_price > 0),
    currency        CHAR(3),
    in_stock        BOOLEAN NOT NULL,
    CONSTRAINT uq_retail_natural_key UNIQUE (platform_id, product_url, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_fact_retail_item_date ON fact_retail_prices (item_id, observed_date);
CREATE INDEX IF NOT EXISTS idx_fact_retail_date ON fact_retail_prices (observed_date);

-- Google Trends relative search interest (0-100 index, weekly grain).
-- Keyed by keyword; item_id nullable for the same linkage reason as retail.
CREATE TABLE IF NOT EXISTS fact_search_interest (
    search_interest_id BIGSERIAL PRIMARY KEY,
    item_id            TEXT REFERENCES dim_items (item_id),
    brand              TEXT,
    keyword            TEXT NOT NULL,
    observed_date      DATE NOT NULL,
    interest_index     SMALLINT NOT NULL CHECK (interest_index BETWEEN 0 AND 100),
    CONSTRAINT uq_search_natural_key UNIQUE (keyword, observed_date)
);

CREATE INDEX IF NOT EXISTS idx_fact_search_item_date ON fact_search_interest (item_id, observed_date);
CREATE INDEX IF NOT EXISTS idx_fact_search_date ON fact_search_interest (observed_date);

-- Reddit posts from watched subs. Grain: one row per post; mention volume
-- per item/brand over time is an aggregation that belongs in the dbt layer,
-- not baked into the fact grain.
CREATE TABLE IF NOT EXISTS fact_social_mentions (
    social_mention_id BIGSERIAL PRIMARY KEY,
    item_id           TEXT REFERENCES dim_items (item_id),
    brand             TEXT,
    subreddit         TEXT NOT NULL,
    post_id           TEXT NOT NULL,
    title             TEXT NOT NULL,
    post_url          TEXT,
    created_date      DATE NOT NULL,
    score             INTEGER NOT NULL DEFAULT 0,
    num_comments      INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_social_natural_key UNIQUE (post_id)
);

CREATE INDEX IF NOT EXISTS idx_fact_social_created ON fact_social_mentions (created_date);
CREATE INDEX IF NOT EXISTS idx_fact_social_subreddit ON fact_social_mentions (subreddit, created_date);
