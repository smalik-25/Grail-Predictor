"""Assemble the catalog: canonical items below, style-families above.

Two layers, on purpose:

- Canonical items (union-find over above-threshold matches) group listings
  of the same product. This is the comps layer: pricing reads sold prices
  of the same piece here.
- Style-families group listings at (brand, model_line, era), with colorway
  tier as an attribute. This is the forecasting grain: demand labeling and
  the watchlist operate on families, never on individual items.

Family assignment does not depend on item matching: a listing joins its
family from its own normalized fields, so a listing whose product match
failed still contributes to family price history. That independence is
deliberate, since family coverage should degrade gracefully, not collapse
with pairwise match quality.

The measurement lives here too: item resolution rate (Phase 2a's number)
and family assignment coverage (Phase 2c's number) print side by side,
because together they say where resolution effort goes next.
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
from resolution.family import ERA_UNKNOWN, FamilyAssignment, assign_family, family_id
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
class StyleFamily:
    """One forecasting unit: brand + model-line + era, colorways underneath."""

    family_id: str
    brand: str
    model_line: str
    era: str
    colorway_tiers: tuple[str, ...]  # distinct tiers seen across member listings
    listings: tuple[NormalizedListing, ...]


@dataclass(frozen=True)
class ResolutionReport:
    """The honest numbers for both layers of the catalog."""

    total_listings: int
    unblockable: int
    canonical_items: int
    multi_listing_items: int
    resolved_listings: int
    singletons: int
    borderline_pairs: int
    # family layer
    families: int
    listings_in_families: int
    model_line_unresolved: int
    era_unknown_listings: int

    @property
    def resolution_rate(self) -> float:
        matchable = self.total_listings - self.unblockable
        return self.resolved_listings / matchable if matchable else 0.0

    @property
    def family_coverage(self) -> float:
        return self.listings_in_families / self.total_listings if self.total_listings else 0.0


def build_catalog(
    raw_listings: Iterable[RawListing],
) -> tuple[list[CanonicalItem], list[StyleFamily], ResolutionReport, list[ScoredPair]]:
    normalized = [normalize(listing) for listing in raw_listings]
    blocks = build_blocks(normalized)
    unblockable = sum(
        len(v) for k, v in blocks.items() if k == ("__unblockable__", "__unblockable__")
    )
    scored = list(score_pairs(candidate_pairs(blocks)))
    borderline = [pair for pair in scored if pair.is_borderline]

    items = _build_items(normalized, scored)
    families, assignments = _build_families(normalized, items)

    multi = [item for item in items if len(item.listings) > 1]
    report = ResolutionReport(
        total_listings=len(normalized),
        unblockable=unblockable,
        canonical_items=len(items),
        multi_listing_items=len(multi),
        resolved_listings=sum(len(item.listings) for item in multi),
        singletons=len(items) - len(multi),
        borderline_pairs=len(borderline),
        families=len(families),
        listings_in_families=sum(len(f.listings) for f in families),
        model_line_unresolved=sum(1 for a in assignments.values() if a.family_key is None),
        era_unknown_listings=sum(
            1 for a in assignments.values() if a.family_key and a.family_key[2] == ERA_UNKNOWN
        ),
    )
    return items, families, report, borderline


# ---------------------------------------------------------------------------
# Item layer (unchanged mechanics from Phase 2a)
# ---------------------------------------------------------------------------

def _build_items(
    normalized: list[NormalizedListing], scored: list[ScoredPair]
) -> list[CanonicalItem]:
    parent: dict[int, int] = {id(listing): id(listing) for listing in normalized}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in scored:
        if pair.is_match:
            parent[find(id(pair.left))] = find(id(pair.right))

    components: dict[int, list[NormalizedListing]] = {}
    for listing in normalized:
        components.setdefault(find(id(listing)), []).append(listing)
    items = [_to_item(members) for members in components.values()]
    items.sort(key=lambda item: item.item_id)
    return items


def _to_item(members: Sequence[NormalizedListing]) -> CanonicalItem:
    best = max(
        members,
        key=lambda m: (len(m.title_normalized.split()), len(m.raw.title), m.raw.title),
    )
    anchor = min(m.raw.listing_url or m.raw.title for m in members)
    digest = hashlib.sha1(anchor.encode()).hexdigest()[:12]
    return CanonicalItem(
        item_id=f"item-{digest}",
        canonical_title=best.raw.title,
        brand=next((m.brand_canonical for m in members if m.brand_canonical), None),
        category=next((m.category for m in members if m.category), None),
        season=next((m.season for m in members if m.season), None),
        listings=tuple(members),
    )


# ---------------------------------------------------------------------------
# Family layer (Phase 2c)
# ---------------------------------------------------------------------------

def _build_families(
    normalized: list[NormalizedListing],
    items: list[CanonicalItem],
) -> tuple[list[StyleFamily], dict[int, FamilyAssignment]]:
    assignments = {id(listing): assign_family(listing) for listing in normalized}
    _propagate_through_items(assignments, items)
    grouped: dict[tuple[str, str, str], list[NormalizedListing]] = {}
    for listing in normalized:
        key = assignments[id(listing)].family_key
        if key is not None:
            grouped.setdefault(key, []).append(listing)

    families = [
        StyleFamily(
            family_id=family_id(key),
            brand=key[0],
            model_line=key[1],
            era=key[2],
            colorway_tiers=tuple(
                sorted({assignments[id(member)].colorway_tier for member in members})
            ),
            listings=tuple(members),
        )
        for key, members in grouped.items()
    ]
    families.sort(key=lambda family: family.family_id)
    unresolved = sum(1 for a in assignments.values() if a.family_key is None)
    if unresolved:
        logger.info(
            "families: %d listings have no resolvable model line; they stay in the "
            "item layer and are counted in the report, not dropped silently",
            unresolved,
        )
    return families, assignments


def _propagate_through_items(
    assignments: dict[int, FamilyAssignment], items: list[CanonicalItem]
) -> None:
    """Product identity recovers family metadata that sparse listings lack.

    If the matcher already decided that "RO leather high top black sz 10"
    is the same product as the FW10 Geobasket, then that listing's family
    is the Geobasket family and its era is FW10's era, even though its own
    title says neither. Two passes within each canonical item, brand-scoped:
    model line first (exactly one distinct line among same-brand members
    propagates to members without one), then era (exactly one distinct
    known era propagates to era-unknown members of that line). Conflicts
    propagate nothing and get logged, never guessed away.
    """
    import dataclasses as _dataclasses

    for item in items:
        if len(item.listings) < 2:
            continue
        same_brand = [
            m for m in item.listings if assignments[id(m)].brand == item.brand and item.brand
        ]
        lines = {assignments[id(m)].model_line for m in same_brand} - {None}
        if len(lines) == 1:
            line = next(iter(lines))
            for member in same_brand:
                assignment = assignments[id(member)]
                if assignment.model_line is None:
                    assignments[id(member)] = _dataclasses.replace(
                        assignment,
                        model_line=line,
                        era=assignment.era or ERA_UNKNOWN,
                        colorway_tier=colorway_tier_for(item.brand, line, assignment.colorway),
                    )
        elif len(lines) > 1:
            logger.info("item %s: conflicting model lines %s; propagating nothing", item.item_id, lines)

        by_line: dict[str, list[NormalizedListing]] = {}
        for member in same_brand:
            assignment = assignments[id(member)]
            if assignment.model_line:
                by_line.setdefault(assignment.model_line, []).append(member)
        for line, members in by_line.items():
            known = {assignments[id(m)].era for m in members} - {ERA_UNKNOWN, None}
            if len(known) == 1:
                era = next(iter(known))
                for member in members:
                    assignment = assignments[id(member)]
                    if assignment.era == ERA_UNKNOWN:
                        assignments[id(member)] = _dataclasses.replace(assignment, era=era)
            elif len(known) > 1:
                logger.info("item %s: conflicting eras %s for %s; propagating nothing",
                            item.item_id, known, line)


def colorway_tier_for(brand: str | None, model_line: str | None, colorway: str | None) -> str:
    """Re-tier a colorway once a model line becomes known via propagation."""
    from resolution.family import colorway_tier

    return colorway_tier(brand, model_line, colorway)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_catalog(
    items: list[CanonicalItem],
    families: list[StyleFamily],
    out_dir: Path = PROCESSED_DIR,
) -> tuple[Path, Path, Path]:
    """Items, families, and listings (with both keys) as Parquet."""
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)

    item_by_listing: dict[int, str] = {
        id(member): item.item_id for item in items for member in item.listings
    }
    family_by_listing: dict[int, str] = {
        id(member): family.family_id for family in families for member in family.listings
    }

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
    families_frame = pd.DataFrame(
        [
            {
                "family_id": family.family_id,
                "brand": family.brand,
                "model_line": family.model_line,
                "era": family.era,
                "colorway_tiers": ",".join(family.colorway_tiers),
                "listing_count": len(family.listings),
            }
            for family in families
        ]
    )
    from resolution.family import colorway_tier as _tier

    family_lookup = {family.family_id: family for family in families}

    def _listing_row(item: CanonicalItem, member: NormalizedListing) -> dict:
        assignment = assign_family(member)
        family = family_lookup.get(family_by_listing.get(id(member), ""))
        # the family's line wins over the listing's own sparse title, so a
        # propagated member gets tiered against the line it actually belongs to
        brand = family.brand if family else assignment.brand
        line = family.model_line if family else assignment.model_line
        return {
            "item_id": item_by_listing[id(member)],
            "family_id": family.family_id if family else None,
            **dataclasses.asdict(member.raw),
            "image_urls": list(member.raw.image_urls),
            "brand_canonical": member.brand_canonical,
            "category": member.category,
            "size_normalized": member.size_normalized,
            "condition_ordinal": member.condition_ordinal,
            "season": member.season,
            "colorway": assignment.colorway,
            "colorway_tier": _tier(brand, line, assignment.colorway),
        }

    listings_frame = pd.DataFrame(
        [_listing_row(item, member) for item in items for member in item.listings]
    )
    items_path = out_dir / "catalog_items.parquet"
    families_path = out_dir / "catalog_families.parquet"
    listings_path = out_dir / "catalog_listings.parquet"
    items_frame.to_parquet(items_path, index=False)
    families_frame.to_parquet(families_path, index=False)
    listings_frame.to_parquet(listings_path, index=False)
    logger.info(
        "catalog: wrote %d items, %d families, %d listings",
        len(items_frame), len(families_frame), len(listings_frame),
    )
    return items_path, families_path, listings_path


def main() -> None:
    """Resolve the resale sources end to end and print both measurements."""
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
    items, families, report, _ = build_catalog(listings)
    write_catalog(items, families)
    summary = dataclasses.asdict(report) | {
        "resolution_rate": round(report.resolution_rate, 3),
        "family_coverage": round(report.family_coverage, 3),
    }
    print(json.dumps(summary, indent=2))
    from resolution.family import colorway_tier as _tier

    for family in families:
        print(f"\n{family.family_id}  [tiers: {', '.join(family.colorway_tiers)}]")
        for member in family.listings:
            assignment = assign_family(member)
            tier = _tier(family.brand, family.model_line, assignment.colorway)
            print(f"  - [{member.raw.platform}] {member.raw.title}"
                  f"  ({assignment.colorway or 'no colorway'} -> {tier})")


if __name__ == "__main__":
    main()
