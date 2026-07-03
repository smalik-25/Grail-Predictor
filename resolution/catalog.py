"""Assemble the canonical catalog from scored match pairs.

Matches above threshold define edges; connected components over those edges
become canonical items (a piece listed on Grailed, eBay, and Vestiaire is
one item with three listings). Each item gets a deterministic id derived
from its most stable member listing, a canonical title chosen as the most
informative member title, and the full set of resolved listings that price
history hangs off downstream.

Also the measurement lives here: how much did text-only resolution actually
resolve? That number is the explicit decision input for Phase 2b.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from resolution.blocking import build_blocks, candidate_pairs
from resolution.match import ScoredPair, score_pairs
from resolution.normalize import NormalizedListing, normalize

from ingestion.base import RawListing

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"


@dataclass(frozen=True)
class CanonicalItem:
    item_id: str
    canonical_title: str
    brand: str | None
    category: str | None
    season: str | None
    listings: tuple[NormalizedListing, ...]


@dataclass(frozen=True)
class ResolutionReport:
    """The honest numbers: what text-only matching did and did not resolve."""

    total_listings: int
    unblockable: int
    canonical_items: int
    multi_listing_items: int
    resolved_listings: int  # listings living in a multi-listing item
    singletons: int
    borderline_pairs: int

    @property
    def resolution_rate(self) -> float:
        matchable = self.total_listings - self.unblockable
        return self.resolved_listings / matchable if matchable else 0.0


def build_catalog(
    raw_listings: Iterable[RawListing],
) -> tuple[list[CanonicalItem], ResolutionReport, list[ScoredPair]]:
    normalized = [normalize(listing) for listing in raw_listings]
    blocks = build_blocks(normalized)
    unblockable = sum(
        len(v) for k, v in blocks.items() if k == ("__unblockable__", "__unblockable__")
    )
    scored = list(score_pairs(candidate_pairs(blocks)))
    borderline = [pair for pair in scored if pair.is_borderline]

    parent: dict[int, int] = {id(listing): id(listing) for listing in normalized}
    by_id = {id(listing): listing for listing in normalized}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for pair in scored:
        if pair.is_match:
            union(id(pair.left), id(pair.right))

    components: dict[int, list[NormalizedListing]] = {}
    for listing in normalized:
        components.setdefault(find(id(listing)), []).append(listing)

    items = [_to_item(members) for members in components.values()]
    items.sort(key=lambda item: item.item_id)

    multi = [item for item in items if len(item.listings) > 1]
    report = ResolutionReport(
        total_listings=len(normalized),
        unblockable=unblockable,
        canonical_items=len(items),
        multi_listing_items=len(multi),
        resolved_listings=sum(len(item.listings) for item in multi),
        singletons=len(items) - len(multi),
        borderline_pairs=len(borderline),
    )
    return items, report, borderline


def _to_item(members: Sequence[NormalizedListing]) -> CanonicalItem:
    # Canonical title: the most informative member title (most tokens after
    # normalization; ties broken by raw length, then lexicographically for
    # determinism).
    best = max(
        members,
        key=lambda m: (len(m.title_normalized.split()), len(m.raw.title), m.raw.title),
    )
    # Item id: stable for the same member listings. Derived from the
    # lexicographically-first listing URL so re-running on the same data
    # yields the same ids. Durable ids under incremental re-resolution are
    # future work and recorded as such in docs/entity-resolution.md.
    anchor = min(m.raw.listing_url or m.raw.title for m in members)
    digest = hashlib.sha1(anchor.encode()).hexdigest()[:12]
    brand = next((m.brand_canonical for m in members if m.brand_canonical), None)
    season = next((m.season for m in members if m.season), None)
    category = next((m.category for m in members if m.category), None)
    return CanonicalItem(
        item_id=f"item-{digest}",
        canonical_title=best.raw.title,
        brand=brand,
        category=category,
        season=season,
        listings=tuple(members),
    )


def write_catalog(items: list[CanonicalItem], out_dir: Path = PROCESSED_DIR) -> tuple[Path, Path]:
    """Write items and resolved listings as Parquet, the inter-stage format."""
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    items_frame = pd.DataFrame(
        [
            {
                "item_id": item.item_id,
                "canonical_title": item.canonical_title,
                "brand": item.brand,
                "category": item.category,
                "season": item.season,
                "listing_count": len(item.listings),
            }
            for item in items
        ]
    )
    listings_frame = pd.DataFrame(
        [
            {
                "item_id": item.item_id,
                **dataclasses.asdict(member.raw),
                "image_urls": list(member.raw.image_urls),
                "brand_canonical": member.brand_canonical,
                "category": member.category,
                "size_normalized": member.size_normalized,
                "condition_ordinal": member.condition_ordinal,
                "season": member.season,
            }
            for item in items
            for member in item.listings
        ]
    )
    items_path = out_dir / "catalog_items.parquet"
    listings_path = out_dir / "catalog_listings.parquet"
    items_frame.to_parquet(items_path, index=False)
    listings_frame.to_parquet(listings_path, index=False)
    logger.info("catalog: wrote %d items, %d listings", len(items_frame), len(listings_frame))
    return items_path, listings_path


def main() -> None:
    """Resolve the resale sources end to end and print the measurement."""
    import logging as _logging

    from ingestion.depop import DepopClient
    from ingestion.ebay import EbayClient
    from ingestion.grailed import GrailedClient
    from ingestion.secondstreet import SecondStreetClient
    from ingestion.therealreal import TheRealRealClient
    from ingestion.vestiaire import VestiaireClient

    _logging.basicConfig(level=_logging.INFO, format="%(name)s %(levelname)s %(message)s")
    listings = [
        record
        for client in (
            GrailedClient(),
            EbayClient(),
            DepopClient(),
            VestiaireClient(),
            TheRealRealClient(),
            SecondStreetClient(),
        )
        for record in client.listings()
    ]
    items, report, borderline = build_catalog(listings)
    write_catalog(items)
    print(json.dumps(dataclasses.asdict(report) | {"resolution_rate": round(report.resolution_rate, 3)}, indent=2))
    for item in items:
        if len(item.listings) > 1:
            print(f"\n{item.item_id}  {item.canonical_title}")
            for member in item.listings:
                print(f"  - [{member.raw.platform}] {member.raw.title}")


if __name__ == "__main__":
    main()
