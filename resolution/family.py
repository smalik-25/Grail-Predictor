"""Style-family assignment: the grain the whole reseller tool hangs on.

A style-family is (brand, model_line, era). Forecasting and labeling happen
at this grain. Colorway and material sit UNDERNEATH as an attribute
(colorway_tier), never in the key: a family aggregates its colorways, and a
hot colorway carries its own premium in features without splitting the
family into slices too thin to forecast.

Era assignment: the season tag or a year in the title gives a year; the
model-line's era table (data/reference/model_lines.json) buckets it into a
named generation. Model lines without a documented era table fall back to
decade buckets, which is coarse but honest, and listings with no year
signal at all land in 'era-unknown'. That residual is measured and
reported, not hidden: it is exactly the kind of number that decides where
reference-data effort goes next.

Nothing in normalize, blocking, or match changes. This layer sits above
them, and below catalog assembly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from resolution.normalize import NormalizedListing

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"

ERA_UNKNOWN = "era-unknown"
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")


@dataclass(frozen=True)
class FamilyAssignment:
    """Everything family-shaped we can say about one listing."""

    brand: str | None
    model_line: str | None
    era: str | None  # named generation, decade bucket, or era-unknown
    colorway: str | None
    colorway_tier: str  # core | rare | standard | unknown

    @property
    def family_key(self) -> tuple[str, str, str] | None:
        """(brand, model_line, era) or None when the listing can't join a family."""
        if self.brand and self.model_line:
            return (self.brand, self.model_line, self.era or ERA_UNKNOWN)
        return None


@lru_cache(maxsize=1)
def _model_lines() -> dict:
    with (REFERENCE_DIR / "model_lines.json").open() as handle:
        data = json.load(handle)
    data.pop("_comment", None)
    return data


@lru_cache(maxsize=1)
def _colorways() -> dict:
    with (REFERENCE_DIR / "colorways.json").open() as handle:
        data = json.load(handle)
    data.pop("_comment", None)
    return data


def assign_model_line(brand: str | None, title_normalized: str) -> str | None:
    """Longest-alias-first keyword match within the brand's vocabulary.

    Longest first so 'german army trainer' wins before 'gat' gets a chance
    to misfire inside another word, and so 'future high top' beats 'future'.
    """
    if not brand:
        return None
    lines = _model_lines().get(brand, {})
    padded = f" {title_normalized} "
    candidates: list[tuple[int, str]] = []
    for line_name, config in lines.items():
        for alias in config.get("aliases", []):
            if f" {alias} " in padded:
                candidates.append((len(alias), line_name))
                break
    if not candidates:
        return None
    return max(candidates)[1]


def listing_year(listing: NormalizedListing) -> int | None:
    """Year from the season tag first (FW10 -> 2010), else a bare year in
    the title. Season wins because it is a garment-production statement,
    while a bare year in a title might be anything."""
    if listing.season:
        two = int(listing.season[2:])
        return 2000 + two if two < 90 else 1900 + two
    match = YEAR_RE.search(listing.raw.title)
    return int(match.group(0)) if match else None


def assign_era(brand: str | None, model_line: str | None, year: int | None) -> str:
    """Named generation from the reference table, else decade bucket, else unknown."""
    if year is None:
        return ERA_UNKNOWN
    if brand and model_line:
        eras = _model_lines().get(brand, {}).get(model_line, {}).get("eras", [])
        for era in eras:
            if era["start"] <= year <= era["end"]:
                return str(era["name"])
    return f"{year // 10 * 10}s"


def extract_colorway(title_normalized: str) -> str | None:
    """First recognized color tokens in the title, joined when multiple.

    Joining ('black/milk') keeps two-tone pieces as their own colorway
    instead of collapsing them into whichever color appears first.
    """
    vocabulary = _colorways()["vocabulary"]
    tokens = title_normalized.split()
    found = [token for token in tokens if token in vocabulary]
    if not found:
        return None
    deduped = list(dict.fromkeys(found))
    return "/".join(deduped[:2])


def colorway_tier(brand: str | None, model_line: str | None, colorway: str | None) -> str:
    if colorway is None:
        return "unknown"
    tiers = _colorways().get("tiers", {})
    line_tiers = tiers.get(brand or "", {}).get(model_line or "", {})
    # a two-tone colorway takes the strongest tier among its parts
    parts = colorway.split("/")
    ranked = {"core": 2, "rare": 3, "standard": 1}
    best = "standard"
    for part in parts:
        tier = line_tiers.get(part)
        if tier and ranked.get(tier, 0) > ranked.get(best, 0):
            best = tier
    return best


def assign_family(listing: NormalizedListing) -> FamilyAssignment:
    """The one entry point: normalized listing in, family assignment out."""
    brand = listing.brand_canonical
    model_line = assign_model_line(brand, listing.title_normalized)
    year = listing_year(listing)
    colorway = extract_colorway(listing.title_normalized)
    return FamilyAssignment(
        brand=brand,
        model_line=model_line,
        era=assign_era(brand, model_line, year) if model_line else None,
        colorway=colorway,
        colorway_tier=colorway_tier(brand, model_line, colorway),
    )


def family_id(key: tuple[str, str, str]) -> str:
    """Readable, deterministic id: brand__model-line__era, slugged.

    Readable beats hashed here: 'maison-margiela__future-high-top__yeezus-era-2013-2014'
    is self-documenting in every downstream table and every interview."""
    return "__".join(re.sub(r"[^a-z0-9]+", "-", part.casefold()).strip("-") for part in key)
