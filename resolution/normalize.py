"""Clean and standardize raw listings ahead of matching.

Four jobs, each fed by a reference file or a controlled mapping:
- brand names resolved against a controlled vocabulary (with a title scan
  fallback for listings that never filled the brand field)
- sizing standardized across conventions: footwear to EU numbers, clothing
  to letter buckets, handling US/UK/JP-cm/IT/JP-numeric inputs
- season tags (FW10, SS03, and the Japanese 03SS ordering) extracted
- condition prose collapsed to an ordinal 1-5 scale

Everything here is deterministic and reversible in spirit: the raw strings
stay on the record, normalization only adds fields. If a value can't be
normalized confidently it becomes None, never a guess.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ingestion.base import RawListing

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"

SEASON_RE = re.compile(r"\b(fw|ss|aw)\s*(\d{2})\b", re.IGNORECASE)
SEASON_JP_RE = re.compile(r"\b(\d{2})\s*(ss|fw|aw)\b", re.IGNORECASE)

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("footwear", ("sneaker", "sneakers", "trainer", "trainers", "shoe", "shoes",
                  "boot", "boots", "high top", "geobasket", "geobaskets",
                  "geo basket", "ramones", "gat", "gats", "german army", "dunk")),
    ("outerwear", ("jacket", "bomber", "blouson", "coat", "parka", "anorak")),
    ("knitwear", ("cardigan", "knit", "sweater", "jumper", "mohair")),
    ("tops", ("hoodie", "tee", "t-shirt", "tshirt", "shirt", "sweatshirt", "top", "vest")),
    ("bottoms", ("pants", "trousers", "jeans", "shorts", "cargos", "denim", "skirt")),
)

# Ordered: more specific phrases first so "very good" never falls through to "good".
CONDITION_SCALE: tuple[tuple[int, tuple[str, ...]], ...] = (
    (5, ("new with tags", "never worn", "deadstock", "pristine", "new")),
    (4, ("worn once", "used_excellent", "excellent", "very good")),
    (2, ("used_fair", "fair", "creasing", "distressed by wear", "worn in")),
    (1, ("poor", "damaged", "thrashed", "for repair")),
    (3, ("gently used", "used_good", "good", "pre-owned", "used")),
)

_CONDITION_LETTER = {"s": 5, "a": 4, "b": 3, "c": 2, "d": 1}


@dataclass(frozen=True)
class NormalizedListing:
    """A raw listing plus the standardized fields matching depends on."""

    raw: RawListing
    brand_canonical: str | None
    category: str | None
    size_normalized: str | None  # EU number for footwear, letter for clothing
    condition_ordinal: int | None  # 5 best, 1 worst, None unknown
    season: str | None  # e.g. "SS03", AW folded into FW
    title_normalized: str  # lowercased, de-branded, de-noised token string


@lru_cache(maxsize=1)
def _brand_aliases() -> dict[str, str]:
    """alias -> canonical, longest aliases first so scans prefer specificity."""
    with (REFERENCE_DIR / "brands.json").open() as handle:
        vocabulary: dict[str, list[str]] = json.load(handle)
    aliases = {alias: canonical for canonical, alist in vocabulary.items() for alias in alist}
    return dict(sorted(aliases.items(), key=lambda kv: -len(kv[0])))


@lru_cache(maxsize=1)
def _sizing() -> dict[str, dict[str, object]]:
    with (REFERENCE_DIR / "sizing.json").open() as handle:
        return json.load(handle)


def normalize_brand(brand: str | None, title: str) -> str | None:
    """Resolve against the vocabulary; fall back to scanning the title."""
    aliases = _brand_aliases()
    if brand:
        key = brand.strip().casefold()
        if key in aliases:
            return aliases[key]
    lowered = f" {re.sub(r'[^a-z0-9()é ]', ' ', title.casefold())} "
    for alias, canonical in aliases.items():
        if f" {alias} " in lowered:
            return canonical
    return None


def infer_category(title: str) -> str | None:
    lowered = title.casefold()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None


def normalize_size(size: str | None, category: str | None) -> str | None:
    """Standardize sizing; returns None rather than guessing.

    Footwear lands on EU numbers ('43', '43.5'). Clothing lands on letter
    buckets (XS-XXL). The conventions differ by region and platform: US and
    UK numbers, Japanese centimeters, IT/FR clothing numbers, Japanese 0-5
    clothing numbers, and bare letters all show up in the fixtures alone.
    """
    if not size:
        return None
    text = size.strip().casefold().replace("size", "").strip()
    tables = _sizing()

    if category == "footwear":
        cm_match = re.search(r"(\d{2}(?:\.\d)?)\s*cm", text)
        if cm_match:
            eu = tables["footwear_cm_to_eu"].get(cm_match.group(1).rstrip("0").rstrip("."))
            return _fmt(eu)
        uk_match = re.search(r"uk\s*(\d{1,2}(?:\.\d)?)", text)
        if uk_match:
            return _fmt(tables["footwear_uk_to_eu"].get(uk_match.group(1)))
        us_match = re.search(r"(?:us|sz)\s*(\d{1,2}(?:\.\d)?)", text)
        if us_match:
            return _fmt(tables["footwear_us_to_eu"].get(us_match.group(1)))
        bare = re.fullmatch(r"(\d{1,2}(?:\.\d)?)(?:\s*(?:eu|it))?", text) or re.fullmatch(
            r"(?:eu|it)\s*(\d{1,2}(?:\.\d)?)", text
        )
        if bare:
            value = float(bare.group(1))
            if 39 <= value <= 48:  # already EU
                return _fmt(value)
            if 6 <= value <= 14:  # bare number in US range
                return _fmt(tables["footwear_us_to_eu"].get(bare.group(1)))
        return None

    # Clothing paths.
    letter = re.fullmatch(r"(xxs|xs|s|m|l|xl|xxl)(?:\s*international)?", text)
    if letter:
        return letter.group(1).upper()
    eu_match = re.search(r"(\d{2})\s*(?:fr|it|eu)?", text)
    if eu_match and eu_match.group(1) in tables["clothing_eu_to_letter"]:
        return str(tables["clothing_eu_to_letter"][eu_match.group(1)])
    jp_match = re.fullmatch(r"(?:jp\s*)?([0-5])", text)
    if jp_match:
        return str(tables["clothing_jp_to_letter"][jp_match.group(1)])
    return None


def normalize_condition(condition: str | None) -> int | None:
    if not condition:
        return None
    text = condition.strip().casefold()
    if text in _CONDITION_LETTER:  # Japanese resale letter grades
        return _CONDITION_LETTER[text]
    for score, phrases in CONDITION_SCALE:
        if any(phrase in text for phrase in phrases):
            return score
    return None


def extract_season(title: str, collection_tag: str | None) -> str | None:
    """FW10 / SS03 / 03SS -> canonical '<FW|SS><YY>'. AW folds into FW."""
    for source in (collection_tag or "", title):
        match = SEASON_RE.search(source)
        if match:
            half, year = match.group(1).upper(), match.group(2)
            return f"{'FW' if half == 'AW' else half}{year}"
        match = SEASON_JP_RE.search(source)
        if match:
            year, half = match.group(1), match.group(2).upper()
            return f"{'FW' if half == 'AW' else half}{year}"
    return None


_NOISE_TOKENS = frozenset(
    "size sz mens men s us uk eu it jp fr cm authentic rare grail og new used".split()
)


def normalize_title(title: str, brand_canonical: str | None) -> str:
    """Lowercase, strip punctuation and brand aliases, drop noise tokens.

    What survives is the product language (model names, materials, colors),
    which is what the matcher should compare.
    """
    lowered = re.sub(r"[^a-z0-9()é' ]", " ", title.casefold())
    for alias, canonical in _brand_aliases().items():
        if brand_canonical is None or canonical == brand_canonical:
            lowered = lowered.replace(alias, " ")
    tokens = [
        token
        for token in lowered.split()
        if token not in _NOISE_TOKENS and not re.fullmatch(r"\d{1,2}(?:\.\d)?", token)
    ]
    return " ".join(tokens)


def normalize(listing: RawListing) -> NormalizedListing:
    """The one entry point: raw listing in, standardized listing out."""
    brand = normalize_brand(listing.brand, listing.title)
    category = infer_category(listing.title)
    return NormalizedListing(
        raw=listing,
        brand_canonical=brand,
        category=category,
        size_normalized=normalize_size(listing.size, category),
        condition_ordinal=normalize_condition(listing.condition),
        season=extract_season(listing.title, listing.collection_tag),
        title_normalized=normalize_title(listing.title, brand),
    )


def _fmt(value: object) -> str | None:
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return str(int(number)) if number == int(number) else str(number)
