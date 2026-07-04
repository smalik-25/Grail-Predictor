"""Style-family grain tests: the unit of analysis for the whole tool.

The two contract tests from the plan: same piece in different colorways
lands in the same family with distinct colorway tiers, and different eras
of the same model line land in different families. Plus the propagation
behavior that makes the grain usable on real listings: product identity
recovering model line and era for sparse titles.
"""
from __future__ import annotations

import pytest

from ingestion.base import RawListing
from resolution.catalog import build_catalog
from resolution.family import (
    ERA_UNKNOWN,
    assign_family,
    assign_model_line,
    extract_colorway,
    family_id,
)
from resolution.normalize import normalize


def _listing(title: str, brand: str | None = None, price: float | None = None,
             collection_tag: str | None = None) -> RawListing:
    return RawListing(
        platform="test", title=title, brand=brand, price=price, currency="USD",
        size=None, condition=None, listing_url=None, listed_date=None,
        sold_date=None, sold_price=None, collection_tag=collection_tag,
    )


# --- assignment units ---

@pytest.mark.parametrize(
    ("brand", "title", "expected"),
    [
        ("Rick Owens", "geobasket high top black leather", "Geobasket"),
        ("Rick Owens", "ramones lows black", "Ramones"),
        ("Maison Margiela", "german army trainer white grey", "GAT Replica"),
        ("Maison Margiela", "replica suede sneakers", "GAT Replica"),
        ("Maison Margiela", "future high top black leather", "Future High-Top"),
        ("Number (N)ine", "kurt cobain cardigan mohair", "Kurt Cardigan"),
        ("Rick Owens", "leather high top black", None),  # sparse: no line on text alone
        (None, "geobasket black", None),  # no brand, no family
    ],
)
def test_assign_model_line(brand: str | None, title: str, expected: str | None) -> None:
    assert assign_model_line(brand, title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("geobasket black leather", "black"),
        ("gat white grey suede", "white/grey"),
        ("future high top", None),
        ("ramones milk dust pearl", "milk/dust"),  # capped at two tokens
    ],
)
def test_extract_colorway(title: str, expected: str | None) -> None:
    assert extract_colorway(title) == expected


def test_family_id_is_readable_and_deterministic() -> None:
    key = ("Maison Margiela", "Future High-Top", "yeezus-era-2013-2014")
    assert family_id(key) == "maison-margiela__future-high-top__yeezus-era-2013-2014"
    assert family_id(key) == family_id(key)


# --- the two plan contracts ---

def test_same_piece_different_colorways_same_family_distinct_tiers() -> None:
    black = assign_family(normalize(_listing(
        "Rick Owens Geobasket Black Leather FW10", brand="Rick Owens", collection_tag="FW10")))
    dust = assign_family(normalize(_listing(
        "Rick Owens Geobasket Dust Leather FW11", brand="Rick Owens", collection_tag="FW11")))
    assert black.family_key == dust.family_key, "colorway must never split a family"
    assert black.colorway_tier == "core" and dust.colorway_tier == "rare"


def test_different_eras_same_model_line_different_families() -> None:
    yeezus = assign_family(normalize(_listing(
        "Maison Margiela Future High Top Black 2013", brand="Maison Margiela")))
    later = assign_family(normalize(_listing(
        "Maison Margiela Future High Top White 2018", brand="Maison Margiela")))
    assert yeezus.family_key != later.family_key
    assert yeezus.era == "yeezus-era-2013-2014"
    assert later.era == "later-2015-plus"


def test_era_falls_back_to_decade_bucket_then_unknown() -> None:
    no_table = assign_family(normalize(_listing(
        "Enfant Riches Deprimes Assemblage Hoodie 2019", brand="Enfant Riches Deprimes")))
    assert no_table.era == "2010s"  # line exists, no era table: decade bucket
    no_year = assign_family(normalize(_listing(
        "Enfant Riches Deprimes Assemblage Hoodie Black", brand="Enfant Riches Deprimes")))
    assert no_year.era == ERA_UNKNOWN


# --- fixture-level behavior, including propagation ---

@pytest.fixture(scope="module")
def catalog_result():
    from ingestion.depop import DepopClient
    from ingestion.ebay import EbayClient
    from ingestion.grailed import GrailedClient
    from ingestion.secondstreet import SecondStreetClient
    from ingestion.therealreal import TheRealRealClient
    from ingestion.vestiaire import VestiaireClient

    listings = [
        record
        for client in (GrailedClient(), EbayClient(), DepopClient(), VestiaireClient(),
                       TheRealRealClient(), SecondStreetClient())
        for record in client.listings()
    ]
    return build_catalog(listings)


def test_identity_propagation_recovers_sparse_listings(catalog_result) -> None:
    """'RO leather high top black sz 10' says neither Geobasket nor an era.
    Product identity says both, and the family layer must benefit."""
    _, families, _, _ = catalog_result
    geobasket = next(f for f in families if f.model_line == "Geobasket")
    titles = {m.raw.title for m in geobasket.listings}
    assert "RO leather high top black sz 10" in titles
    assert geobasket.era == "og-2006-2012", "era must propagate from the one FW10 member"
    assert len(geobasket.listings) >= 6


def test_era_conflict_within_an_item_propagates_nothing(catalog_result) -> None:
    """The matcher merges the 2013 and 2018 Futures into one product item
    (they read almost identically on text); the family layer must still
    keep the generations apart rather than let one era swallow the other."""
    _, families, _, _ = catalog_result
    future_eras = {f.era for f in families if f.model_line == "Future High-Top"}
    assert future_eras == {"yeezus-era-2013-2014", "later-2015-plus"}


def test_family_report_counts_are_consistent(catalog_result) -> None:
    _, families, report, _ = catalog_result
    assert report.families == len(families)
    assert report.listings_in_families == sum(len(f.listings) for f in families)
    assert 0 < report.family_coverage <= 1
    assert report.model_line_unresolved > 0, (
        "fixtures deliberately include listings the vocabulary can't place; "
        "if this hits zero the residual measurement is measuring nothing"
    )


def test_families_never_mix_brands_or_lines(catalog_result) -> None:
    _, families, _, _ = catalog_result
    for family in families:
        for member in family.listings:
            assignment = assign_family(member)
            if assignment.brand:  # propagation can add lines, never flip brands
                assert assignment.brand == family.brand
