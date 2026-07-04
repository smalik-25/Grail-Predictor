# Warehouse schema

Hand-written DDL in db/schema.sql, no ORM. Star schema with one dimension
that matters and four fact tables hanging off it.

```
                      dim_platforms
                            |
        +----------------+--+---+------------------+
        |                |      |                  |
  fact_listings   fact_retail_  fact_search_   fact_social_
        |          prices        interest       mentions
        |                |      |                  |
        +--------+-------+------+---------+--------+
                 |                        |
                 +------ dim_items -------+
                    (the canonical catalog)
```

## The family dimension (added in the pivot)

dim_style_families is the forecasting grain: (brand, model_line, era) with
a natural-key UNIQUE constraint. family_id lives on fact_listings rather
than dim_items, deliberately: the text matcher can merge near-identical
generations of the same model line into one canonical item (the two
Margiela Future generations do exactly this on fixtures), so an item does
not always map to one family, but a listing always does. colorway and
colorway_tier sit on the listing row, with a CHECK constraint pinning the
tier vocabulary, because the tier is a listing-level attribute that
aggregates up to the family, never a key that splits it.

## Why the canonical item is the central dimension

Everything this project predicts is a property of a piece, not of a listing.
"The Geobasket is inflecting" is a statement about one canonical item whose
evidence is scattered across seven listings on five platforms. If listings
were the center, every downstream query would re-derive the identity work
that resolution/ already did, badly. dim_items is the spine because it IS
the entity the project exists to track; fact_listings is deliberately just
evidence attached to it.

## Why retail and resale prices are separate fact tables

Different grain, different meaning. A fact_listings row is a tradeable
object: one listing, with a lifecycle (listed, maybe sold) and attributes
like size and condition that belong to a physical unit. A
fact_retail_prices row is an observation: what did the shelf price say on
this date, was it in stock. One is an event stream per unit, the other is a
time series per product. Forcing them into one table would mean nullable
columns that make sense for only half the rows and a grain that is honest
for neither. The retail-vs-resale spread is a JOIN in the dbt layer, where
it belongs.

## Idempotency

Re-ingestion must never duplicate facts. Every fact table declares a
natural key as a UNIQUE constraint and the loader inserts with ON CONFLICT
DO NOTHING:

| Table | Natural key |
|---|---|
| fact_listings | (platform_id, listing_url) |
| fact_retail_prices | (platform_id, product_url, observed_date) |
| fact_search_interest | (keyword, observed_date) |
| fact_social_mentions | (post_id) |

The decision here is DO NOTHING over upsert. A listing's asking price can
change over time, and an upsert would silently overwrite history with the
latest observation. Keeping the first-landed row is the conservative choice
until there's a real slowly-changing-dimension story; when price-drop
tracking matters (and for grails it eventually will), the fix is a listing
price-observation table, not an upsert.

One accepted quirk: Postgres treats NULLs as distinct in UNIQUE
constraints, so url-less listings never collide on the natural key. Only
fixtures lack urls in practice, and the alternative (synthetic keys for
url-less rows) buys complexity for a case real data doesn't have.

## Nullable item_id on the attention and retail facts

fact_retail_prices, fact_search_interest, and fact_social_mentions carry a
nullable item_id. Linking a retail product page, a search keyword, or a
Reddit post to a canonical resale item is itself entity resolution, and
pretending the loader can do it with string equality would poison the
tables with false links. The rows land with their own identities (product
name and url, keyword, post id) so the linkage can be built properly later
and backfilled with an UPDATE. Honest gap, stated.

## Constraints and indexes, and why each exists

- CHECK (condition_ordinal BETWEEN 1 AND 5) and CHECK (interest_index
  BETWEEN 0 AND 100): these encode the normalize.py and Google Trends
  contracts at the storage boundary, so a broken upstream change fails the
  load instead of corrupting the marts.
- CONSTRAINT sold_fields_consistent: a row with a sold_price but
  is_sold = FALSE is a loader bug by definition; the database refuses it.
- idx_fact_listings_item and the date indexes: the dbt marts filter and
  window by item_id and by date; those are the only access paths that
  matter at this scale.
- idx_fact_listings_sold_date is partial (WHERE sold_date IS NOT NULL)
  because sold listings are the minority and the sold-price time series
  only ever reads that minority.

## Running it locally

```
docker compose up -d
export DATABASE_URL=postgresql://grail:grail@localhost:5432/grail
make ingest resolve load
```

The loader applies schema.sql (all CREATE IF NOT EXISTS), seeds
dim_platforms, and bulk-loads with execute_values. Loading twice changes
nothing, and there's a test asserting exactly that.
