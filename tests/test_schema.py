"""Schema sanity: the DDL must parse as Postgres and keep its contracts.

sqlglot won't catch everything a live Postgres would, but it catches broken
DDL at test time with zero infrastructure, which is what CI needs.
"""
from __future__ import annotations

from pathlib import Path

import sqlglot

SCHEMA = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()


def test_schema_parses_as_postgres() -> None:
    statements = sqlglot.parse(SCHEMA, read="postgres")
    assert len(statements) >= 6, "expected at least the six tables"


def test_schema_declares_expected_tables() -> None:
    for table in ("dim_platforms", "dim_items", "dim_style_families", "fact_listings",
                  "fact_retail_prices", "fact_search_interest", "fact_social_mentions"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in SCHEMA


def test_schema_keeps_family_grain_contracts() -> None:
    assert "uq_family_natural_key UNIQUE (brand, model_line, era)" in SCHEMA
    assert "colorway_tier IN ('core', 'rare', 'standard', 'unknown')" in SCHEMA


def test_schema_keeps_idempotency_natural_keys() -> None:
    """The loader's ON CONFLICT targets must exist in the DDL by name."""
    for constraint in ("uq_listing_natural_key", "uq_retail_natural_key",
                       "uq_search_natural_key", "uq_social_natural_key"):
        assert constraint in SCHEMA, f"missing natural key constraint {constraint}"


def test_schema_keeps_domain_checks() -> None:
    assert "condition_ordinal BETWEEN 1 AND 5" in SCHEMA
    assert "interest_index BETWEEN 0 AND 100" in SCHEMA
    assert "sold_fields_consistent" in SCHEMA
