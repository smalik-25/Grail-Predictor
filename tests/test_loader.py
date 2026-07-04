"""Loader tests that run without a database.

The row builders are pure, so the shape and semantics of every INSERT get
tested dry. The end-to-end load against real Postgres is gated on
DATABASE_URL and skipped otherwise, which keeps CI green without secrets
while letting a developer with docker compose up verify the whole path.
"""
from __future__ import annotations

import datetime
import os

import pytest

from db.load import (
    PLATFORMS,
    dim_items_rows,
    fact_listings_rows,
    fact_retail_rows,
    fact_search_rows,
    fact_social_rows,
    parse_date,
    scrub_nan,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-06-14", datetime.date(2026, 6, 14)),
        ("2026-05-02T14:11:09Z", datetime.date(2026, 5, 2)),
        ("Jun 14, 2026", datetime.date(2026, 6, 14)),
        (datetime.date(2026, 1, 1), datetime.date(2026, 1, 1)),
        (None, None),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_date(value, expected) -> None:
    assert parse_date(value) == expected


def test_nan_from_parquet_round_trip_becomes_none() -> None:
    """Regression for the live-load failure: pandas NaN leaked into a VALUES
    list, Postgres inferred the column as double precision, and the first
    real size string ('M') killed the load. NaN must die at the read boundary."""
    import math

    scrubbed = scrub_nan([{"size_normalized": math.nan, "condition_ordinal": math.nan,
                           "price": 100.0, "title": "x"}])
    assert scrubbed[0]["size_normalized"] is None
    assert scrubbed[0]["condition_ordinal"] is None
    assert scrubbed[0]["price"] == 100.0
    assert parse_date(math.nan) is None  # and no warning spam either


def test_condition_ordinal_lands_as_int_not_float() -> None:
    """pandas widens nullable ints to float; SMALLINT should get real ints."""
    (row,) = fact_listings_rows([
        {"item_id": "i", "platform": "grailed", "condition_ordinal": 4.0,
         "price": 1.0, "currency": "USD"}
    ])
    assert row[10] == 4 and isinstance(row[10], int)


def test_fact_listings_rows_carry_family_and_colorway() -> None:
    (row,) = fact_listings_rows([
        {"item_id": "i", "platform": "grailed", "price": 1.0, "currency": "USD",
         "family_id": "rick-owens__geobasket__og-2006-2012",
         "colorway": "black", "colorway_tier": "core"}
    ])
    assert row[1] == "rick-owens__geobasket__og-2006-2012"
    assert row[2] == "black" and row[3] == "core"
    # a listing outside any family still loads, with tier defaulting sanely
    (loose,) = fact_listings_rows([
        {"item_id": "i2", "platform": "ebay", "price": 2.0, "currency": "USD"}
    ])
    assert loose[1] is None and loose[3] == "unknown"


def test_dim_style_families_rows_derive_first_seen() -> None:
    from db.load import dim_style_families_rows

    families = [{"family_id": "f1", "brand": "Rick Owens", "model_line": "Geobasket",
                 "era": "og-2006-2012", "colorway_tiers": "core,unknown"}]
    listings = [
        {"family_id": "f1", "listed_date": "2026-05-02"},
        {"family_id": "f1", "listed_date": None, "sold_date": "Jun 14, 2026"},
        {"family_id": None, "listed_date": "2020-01-01"},  # familyless: ignored
    ]
    ((fid, brand, line, era, tiers, first_seen),) = dim_style_families_rows(families, listings)
    assert fid == "f1" and line == "Geobasket"
    assert first_seen == datetime.date(2026, 5, 2)


def test_platform_seed_covers_every_ingestion_source() -> None:
    names = {name for name, _ in PLATFORMS}
    assert {"grailed", "ebay", "depop", "vestiaire", "therealreal", "secondstreet",
            "ssense", "dsm", "saks", "google_trends", "reddit"} == names


def test_dim_items_rows_derive_first_seen_from_earliest_listing() -> None:
    items = [{"item_id": "item-1", "canonical_title": "Geobasket", "brand": "Rick Owens",
              "category": "footwear", "season": "FW10"}]
    listings = [
        {"item_id": "item-1", "listed_date": "2026-06-05T10:22:00Z"},
        {"item_id": "item-1", "listed_date": "2026-05-02T14:11:09Z"},
        {"item_id": "item-1", "listed_date": None, "sold_date": "Jun 14, 2026"},
    ]
    ((item_id, title, brand, category, season, first_seen),) = dim_items_rows(items, listings)
    assert item_id == "item-1" and brand == "Rick Owens"
    assert first_seen == datetime.date(2026, 5, 2)


def test_fact_listings_rows_mark_sold_consistently() -> None:
    listings = [
        {"item_id": "item-1", "platform": "grailed", "price": 1150.0, "currency": "USD",
         "listed_date": "2026-05-02", "sold_date": None, "sold_price": None},
        {"item_id": "item-1", "platform": "ebay", "price": None, "currency": "USD",
         "listed_date": None, "sold_date": "Jun 14, 2026", "sold_price": 1225.0},
    ]
    active, sold = fact_listings_rows(listings)
    assert active[11] is False and active[13] is None  # is_sold, sold_price
    assert sold[11] is True and sold[12] == datetime.date(2026, 6, 14) and sold[13] == 1225.0


def test_fact_retail_rows_shape() -> None:
    (row,) = fact_retail_rows([
        {"source": "ssense", "brand": "Rick Owens", "product_name": "Black Geobasket Sneakers",
         "price": 1190.0, "currency": "USD", "url": "https://example", "in_stock": False,
         "date": "2026-06-28"}
    ])
    assert row[0] == "ssense" and row[4] == datetime.date(2026, 6, 28) and row[7] is False


def test_fact_search_and_social_rows_shape() -> None:
    (search_row,) = fact_search_rows([{"keyword": "rick owens geobasket",
                                       "date": "2026-05-03", "interest": 31}])
    assert search_row == ("rick owens geobasket", datetime.date(2026, 5, 3), 31)
    (social_row,) = fact_social_rows([{"subreddit": "rickowens", "post_id": "1kx9ab",
                                       "title": "t", "created_date": "2026-06-21",
                                       "score": 214, "num_comments": 58}])
    assert social_row[1] == "1kx9ab" and social_row[4] == datetime.date(2026, 6, 21)


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs a running Postgres (docker compose up)")
def test_load_all_end_to_end() -> None:
    """Full load against a live warehouse, twice, to prove idempotency."""
    from db.load import load_all

    first = load_all()
    assert first["dim_items"] > 0 and first["fact_listings"] > 0
    second = load_all()
    assert second == first, "re-loading the same data must not change row counts"
