"""Load the canonical catalog and raw facts into the Postgres warehouse.

Bulk loads with psycopg2 execute_values, never row-by-row. Every insert is
ON CONFLICT DO NOTHING against the natural keys declared in schema.sql, so
re-running ingestion and re-loading is idempotent by construction: the same
listing landed twice becomes one row, not two.

Row building is split from I/O on purpose: the *_rows functions are pure
(records in, tuples out) so they get unit-tested without a database, and
load_all is a thin wrapper that ships those tuples to Postgres. Connection
comes from DATABASE_URL (local default via docker-compose.yml:
postgresql://grail:grail@localhost:5432/grail).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
RAW_DIR = ROOT / "data" / "raw"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Seeded on every load; the loader references platforms by name, never by id.
PLATFORMS: tuple[tuple[str, str], ...] = (
    ("grailed", "resale"),
    ("ebay", "resale"),
    ("depop", "resale"),
    ("vestiaire", "resale"),
    ("therealreal", "resale"),
    ("secondstreet", "resale"),
    ("ssense", "retail"),
    ("dsm", "retail"),
    ("saks", "retail"),
    ("google_trends", "search"),
    ("reddit", "social"),
)


def parse_date(value: Any) -> datetime.date | None:
    """ISO strings, ISO timestamps, and eBay's 'Jun 14, 2026' to date.

    Returns None for anything unparseable rather than guessing, and logs it,
    because a silently wrong date poisons every time window downstream.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(text, "%b %d, %Y").date()
    except ValueError:
        logger.warning("unparseable date %r; storing NULL", value)
        return None


# ---------------------------------------------------------------------------
# Row builders: pure functions, records in, tuples out. Unit-tested dry.
# ---------------------------------------------------------------------------

def dim_items_rows(items: Iterable[dict[str, Any]], listings: Iterable[dict[str, Any]]) -> list[tuple]:
    """(item_id, canonical_title, brand, category, season, first_seen_date).

    first_seen_date is the earliest listed date across member listings,
    which is the closest honest proxy for when the market first saw the
    piece within our observation window.
    """
    first_seen: dict[str, datetime.date] = {}
    for listing in listings:
        date = parse_date(listing.get("listed_date")) or parse_date(listing.get("sold_date"))
        item_id = listing["item_id"]
        if date and (item_id not in first_seen or date < first_seen[item_id]):
            first_seen[item_id] = date
    return [
        (
            item["item_id"],
            item["canonical_title"],
            item.get("brand"),
            item.get("category"),
            item.get("season"),
            first_seen.get(item["item_id"]),
        )
        for item in items
    ]


def fact_listings_rows(listings: Iterable[dict[str, Any]]) -> list[tuple]:
    """(item_id, platform_name, listing_url, listed_date, asking_price,
    currency, size_normalized, condition_ordinal, is_sold, sold_date,
    sold_price). platform_name resolves to platform_id inside the INSERT."""
    rows = []
    for listing in listings:
        sold_price = listing.get("sold_price")
        rows.append(
            (
                listing["item_id"],
                listing["platform"],
                listing.get("listing_url"),
                parse_date(listing.get("listed_date")),
                listing.get("price"),
                listing.get("currency"),
                listing.get("size_normalized"),
                listing.get("condition_ordinal"),
                sold_price is not None or parse_date(listing.get("sold_date")) is not None,
                parse_date(listing.get("sold_date")),
                sold_price,
            )
        )
    return rows


def fact_retail_rows(records: Iterable[dict[str, Any]]) -> list[tuple]:
    """(platform_name, brand, product_name, product_url, observed_date,
    retail_price, currency, in_stock). item_id linkage is a later phase."""
    return [
        (
            record["source"],
            record.get("brand"),
            record["product_name"],
            record.get("url"),
            parse_date(record.get("date")),
            record.get("price"),
            record.get("currency"),
            bool(record.get("in_stock", True)),
        )
        for record in records
    ]


def fact_search_rows(records: Iterable[dict[str, Any]]) -> list[tuple]:
    """(keyword, observed_date, interest_index)."""
    return [
        (record["keyword"], parse_date(record["date"]), int(record["interest"]))
        for record in records
    ]


def fact_social_rows(records: Iterable[dict[str, Any]]) -> list[tuple]:
    """(subreddit, post_id, title, post_url, created_date, score, num_comments)."""
    return [
        (
            record["subreddit"],
            record["post_id"],
            record["title"],
            record.get("url"),
            parse_date(record["created_date"]),
            int(record.get("score", 0)),
            int(record.get("num_comments", 0)),
        )
        for record in records
    ]


# ---------------------------------------------------------------------------
# I/O: reading pipeline outputs, shipping rows to Postgres.
# ---------------------------------------------------------------------------

def latest_raw(source: str) -> list[dict[str, Any]]:
    """Most recent landed file for a source, else empty (and say so)."""
    candidates = sorted(RAW_DIR.glob(f"{source}_*.json"))
    if not candidates:
        logger.warning("no raw files for %r in %s; loading nothing for it", source, RAW_DIR)
        return []
    with candidates[-1].open() as handle:
        return json.load(handle)


def read_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import pandas as pd

    items = pd.read_parquet(PROCESSED_DIR / "catalog_items.parquet").to_dict("records")
    listings = pd.read_parquet(PROCESSED_DIR / "catalog_listings.parquet").to_dict("records")
    return items, listings


def load_all(database_url: str | None = None) -> dict[str, int]:
    """Apply schema, seed platforms, bulk-load everything. Returns row counts."""
    import psycopg2
    from psycopg2.extras import execute_values

    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Start the local warehouse with "
            "'docker compose up -d' and export "
            "DATABASE_URL=postgresql://grail:grail@localhost:5432/grail"
        )
    items, listings = read_catalog()
    counts: dict[str, int] = {}
    with psycopg2.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_PATH.read_text())
            execute_values(
                cursor,
                "INSERT INTO dim_platforms (platform_name, platform_type) VALUES %s "
                "ON CONFLICT (platform_name) DO NOTHING",
                list(PLATFORMS),
            )
            execute_values(
                cursor,
                "INSERT INTO dim_items (item_id, canonical_title, brand, category, season, first_seen_date) "
                "VALUES %s ON CONFLICT (item_id) DO NOTHING",
                dim_items_rows(items, listings),
            )
            execute_values(
                cursor,
                "INSERT INTO fact_listings (item_id, platform_id, listing_url, listed_date, "
                "asking_price, currency, size_normalized, condition_ordinal, is_sold, sold_date, sold_price) "
                "SELECT r.item_id, p.platform_id, r.listing_url, r.listed_date, r.asking_price, "
                "r.currency, r.size_normalized, r.condition_ordinal, r.is_sold, r.sold_date, r.sold_price "
                "FROM (VALUES %s) AS r (item_id, platform_name, listing_url, listed_date, asking_price, "
                "currency, size_normalized, condition_ordinal, is_sold, sold_date, sold_price) "
                "JOIN dim_platforms p ON p.platform_name = r.platform_name "
                "ON CONFLICT ON CONSTRAINT uq_listing_natural_key DO NOTHING",
                fact_listings_rows(listings),
            )
            execute_values(
                cursor,
                "INSERT INTO fact_retail_prices (platform_id, brand, product_name, product_url, "
                "observed_date, retail_price, currency, in_stock) "
                "SELECT p.platform_id, r.brand, r.product_name, r.product_url, r.observed_date, "
                "r.retail_price, r.currency, r.in_stock "
                "FROM (VALUES %s) AS r (platform_name, brand, product_name, product_url, "
                "observed_date, retail_price, currency, in_stock) "
                "JOIN dim_platforms p ON p.platform_name = r.platform_name "
                "ON CONFLICT ON CONSTRAINT uq_retail_natural_key DO NOTHING",
                fact_retail_rows(latest_raw("retail")),
            )
            execute_values(
                cursor,
                "INSERT INTO fact_search_interest (keyword, observed_date, interest_index) VALUES %s "
                "ON CONFLICT ON CONSTRAINT uq_search_natural_key DO NOTHING",
                fact_search_rows(latest_raw("trends")),
            )
            execute_values(
                cursor,
                "INSERT INTO fact_social_mentions (subreddit, post_id, title, post_url, "
                "created_date, score, num_comments) VALUES %s "
                "ON CONFLICT ON CONSTRAINT uq_social_natural_key DO NOTHING",
                fact_social_rows(latest_raw("social")),
            )
            for table in ("dim_platforms", "dim_items", "fact_listings",
                          "fact_retail_prices", "fact_search_interest", "fact_social_mentions"):
                cursor.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table list
                counts[table] = cursor.fetchone()[0]
    for table, count in counts.items():
        logger.info("%s: %d rows", table, count)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_all()


if __name__ == "__main__":
    main()
