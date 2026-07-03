"""Tests for the text-based entity resolution pipeline.

The pair tests are the important ones: known same-piece pairs must match,
known different-piece pairs must not, and the deliberately tricky pairs
(same piece described sparsely; different pieces described similarly) must
land where the design says they land, in the borderline log, not silently
merged or silently dropped.
"""
from __future__ import annotations

import pytest

from ingestion.base import RawListing
from resolution.catalog import build_catalog
from resolution.match import score_pair
from resolution.normalize import (
    extract_season,
    normalize,
    normalize_brand,
    normalize_condition,
    normalize_size,
)


def _listing(title: str, brand: str | None = None, price: float | None = None,
             sold_price: float | None = None, collection_tag: str | None = None) -> RawListing:
    return RawListing(
        platform="test", title=title, brand=brand, price=price, currency="USD",
        size=None, condition=None, listing_url=None, listed_date=None,
        sold_date=None, sold_price=sold_price, collection_tag=collection_tag,
    )


# --- normalize ---

@pytest.mark.parametrize(
    ("brand", "title", "expected"),
    [
        ("Rick Owens", "whatever", "Rick Owens"),
        ("MAISON MARTIN MARGIELA", "whatever", "Maison Margiela"),
        ("NUMBER (N)INE", "whatever", "Number (N)ine"),
        (None, "RO leather high top black sz 10", "Rick Owens"),
        (None, "margiela german army trainers gats", "Maison Margiela"),
        (None, "brandless mystery jacket", None),
    ],
)
def test_normalize_brand(brand: str | None, title: str, expected: str | None) -> None:
    assert normalize_brand(brand, title) == expected


@pytest.mark.parametrize(
    ("size", "category", "expected"),
    [
        ("US 10", "footwear", "43"),
        ("sz 10", "footwear", "43"),
        ("27.5cm", "footwear", "43"),
        ("UK 9", "footwear", "43"),
        ("43", "footwear", "43"),
        ("10", "footwear", "43"),
        ("3", "knitwear", "L"),
        ("48 FR", "outerwear", "M"),
        ("M International", "knitwear", "M"),
        ("68", "outerwear", None),  # refuse to guess at nonsense
        (None, "footwear", None),
    ],
)
def test_normalize_size(size: str | None, category: str, expected: str | None) -> None:
    assert normalize_size(size, category) == expected


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("B", 3), ("A", 4), ("C", 2),  # Japanese letter grades
        ("Pristine", 5), ("never worn", 5),
        ("Very good condition", 4), ("used_excellent", 4), ("worn once", 4),
        ("gently used", 3), ("Pre-owned", 3), ("used_good", 3),
        ("used_fair", 2), ("used, some creasing", 2),
        (None, None), ("???", None),
    ],
)
def test_normalize_condition(condition: str | None, expected: int | None) -> None:
    assert normalize_condition(condition) == expected


@pytest.mark.parametrize(
    ("title", "tag", "expected"),
    [
        ("Rick Owens Geobasket FW10", None, "FW10"),
        ("NUMBER (N)INE MOHAIR CARDIGAN NAVY 03SS", None, "SS03"),  # Japanese order
        ("Undercover AW05 Arts and Crafts", None, "FW05"),  # AW folds into FW
        ("no season here", "SS03", "SS03"),
        ("no season anywhere", None, None),
    ],
)
def test_extract_season(title: str, tag: str | None, expected: str | None) -> None:
    assert extract_season(title, tag) == expected


# --- match: known same, known different, deliberately tricky ---

def test_same_piece_described_differently_matches() -> None:
    a = normalize(_listing("Rick Owens Geobasket High Top Black Leather 43",
                           brand="Rick Owens", price=1150))
    b = normalize(_listing("RICK OWENS geo basket high top sneakers sz US 10 black leather mainline",
                           sold_price=1225))
    assert score_pair(a, b).is_match


def test_different_pieces_described_similarly_do_not_match() -> None:
    ramones = normalize(_listing("rick owens ramones size 44 mainline not drkshdw",
                                 brand="Rick Owens", price=640))
    geobasket = normalize(_listing("Rick Owens Mainline Geobasket Sneakers Black Leather Men's US 10 EU 43",
                                   brand="Rick Owens", price=1295))
    assert not score_pair(ramones, geobasket).is_match


def test_sparse_generic_title_cannot_make_a_confident_match() -> None:
    """A listing titled just 'Jacket' must never merge with anything.

    Without the sparsity guard in the title signal, token_set_ratio scores a
    subset title at 1.0 and union-find welds every jacket in the block into
    one mega-item. This is the regression test for that failure mode.
    """
    sparse = normalize(_listing("Jacket", brand="Balenciaga", price=1450))
    bomber = normalize(_listing("Balenciaga FW07 Nicolas Ghesquiere Runway Bomber 48",
                                brand="Balenciaga", price=1650))
    scored = score_pair(sparse, bomber)
    assert not scored.is_match


def test_synonym_gap_lands_in_borderline_not_silence() -> None:
    """'SCAB BLOUSON' vs 'Scab jacket': same piece, Japanese naming.

    Text alone misses it, which is fine ONLY IF it surfaces for review
    rather than disappearing into the singleton pile unnoticed.
    """
    jp = normalize(_listing("UNDERCOVER 03SS SCAB BLOUSON", brand="UNDERCOVER", price=3980))
    en = normalize(_listing("Undercover SS03 Scab jacket crust punk archive Jun Takahashi",
                            sold_price=4650))
    scored = score_pair(jp, en)
    assert not scored.is_match
    assert scored.is_borderline


# --- catalog end to end on the ingestion fixtures ---

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


def test_catalog_resolves_cross_platform_items(catalog_result) -> None:
    items, report, _ = catalog_result
    geobasket = next(i for i in items if "geobasket" in i.canonical_title.lower()
                     or "geo basket" in i.canonical_title.lower())
    platforms = {member.raw.platform for member in geobasket.listings}
    assert len(platforms) >= 4, f"geobasket should span platforms, got {platforms}"
    assert geobasket.brand == "Rick Owens"
    assert report.total_listings == report.resolved_listings + report.singletons + report.unblockable


def test_catalog_keeps_ramones_and_geobasket_apart(catalog_result) -> None:
    items, _, _ = catalog_result
    for item in items:
        titles = " | ".join(m.raw.title.lower() for m in item.listings)
        assert not ("ramones" in titles and "geobasket" in titles), (
            f"ramones and geobasket merged: {titles}"
        )


def test_catalog_reports_borderline_pairs_for_review(catalog_result) -> None:
    _, report, borderline = catalog_result
    assert report.borderline_pairs == len(borderline) > 0


def test_catalog_ids_are_deterministic(catalog_result) -> None:
    from ingestion.grailed import GrailedClient
    from ingestion.ebay import EbayClient

    listings = [r for c in (GrailedClient(), EbayClient()) for r in c.listings()]
    first, _, _ = build_catalog(listings)
    second, _, _ = build_catalog(listings)
    assert [i.item_id for i in first] == [i.item_id for i in second]
