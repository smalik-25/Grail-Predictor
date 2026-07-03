"""Shared ingestion machinery: the raw record shape and the fixture fallback.

Every platform client follows the same contract: a generator of records,
live network access only when INGEST_LIVE=1 and the source's credentials are
present, fixture mode otherwise. Fixtures are platform-shaped payloads run
through the same parser as live data, so the tests exercise the real parse
path, not a shortcut around it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# The watchlist live clients query. Will move to data/reference/ once the
# brand vocabulary exists in Phase 2a.
DEFAULT_BRANDS: tuple[str, ...] = (
    "Rick Owens",
    "Balenciaga",
    "Maison Margiela",
    "Enfant Riches Deprimes",
    "Number (N)ine",
    "Undercover",
)

BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"


@dataclass(frozen=True)
class RawListing:
    """One resale listing as it came off a platform, minimally touched.

    Normalization (sizing, brand vocabulary, ordinal condition) happens in
    resolution/, not here. Ingestion's job is faithful capture, which is why
    size and condition stay as the raw strings the platform used.
    """

    platform: str
    title: str
    brand: str | None
    price: float | None
    currency: str | None
    size: str | None
    condition: str | None
    listing_url: str | None
    listed_date: str | None  # ISO-ish date string as the platform reported it
    sold_date: str | None
    sold_price: float | None
    image_urls: tuple[str, ...] = ()
    seller: str | None = None
    collection_tag: str | None = None  # e.g. FW10, SS03, when the listing says


def live_mode() -> bool:
    """Live network access is opt-in, never the default."""
    return os.environ.get("INGEST_LIVE") == "1"


def has_env(*names: str) -> bool:
    """True if every named env var is present and non-empty."""
    return all(os.environ.get(n) for n in names)


def load_fixture(platform: str, fixture_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load the platform-shaped sample payload for fixture mode.

    Fails loudly if the fixture is missing: the pipeline must run without
    credentials, so a missing fixture is a bug, not a condition to paper over.
    """
    path = (fixture_dir or FIXTURE_DIR) / f"{platform}_sample.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No fixture for {platform!r} at {path}. Fixtures are required; "
            "the whole pipeline runs on them by default."
        )
    with path.open() as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"Fixture {path} must be a JSON list of raw records.")
    return payload


def parse_money(value: Any) -> float | None:
    """Turn '$1,225.00', '1225', 1225.0, or None into a float or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "").lstrip("$€£¥")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        logger.warning("Could not parse money value %r", value)
        return None


def polite_sleep(seconds: float = 2.0) -> None:
    """Rate limiting between live requests. Non-negotiable in live mode."""
    time.sleep(seconds)


def brightdata_fetch(url: str, timeout: int = 60) -> str:
    """Fetch a protected page through Bright Data Web Unlocker.

    Used only where free access is not realistic (DataDome or Cloudflare
    fronted platforms). Each call costs money, so callers stay surgical:
    small pulls, no crawling for the sake of it.
    """
    import requests

    api_key = os.environ.get("BRIGHTDATA_API_KEY")
    zone = os.environ.get("BRIGHTDATA_ZONE")
    if not api_key or not zone:
        raise RuntimeError(
            "Bright Data credentials missing (BRIGHTDATA_API_KEY, BRIGHTDATA_ZONE). "
            "Refusing to guess; run in fixture mode instead."
        )
    response = requests.post(
        BRIGHTDATA_ENDPOINT,
        json={"zone": zone, "url": url, "format": "raw"},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text
