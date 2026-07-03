"""Grailed listings, the primary resale source.

Access: Grailed has no public API. Site search is served by Algolia, and the
search credentials Grailed embeds in its own frontend can query the same
index directly. Those values rotate, so they come from env vars rather than
being hardcoded here. Grailed's ToS does not welcome scraping, so this stays
on public listing data at a polite rate; the gray area is recorded in the
DEVLOG rather than pretended away.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from ingestion.base import (
    DEFAULT_BRANDS,
    RawListing,
    has_env,
    live_mode,
    load_fixture,
    parse_money,
    polite_sleep,
)

logger = logging.getLogger(__name__)

PLATFORM = "grailed"
LISTING_URL = "https://www.grailed.com/listings/{listing_id}"
DEFAULT_INDEX = "Listing_production"
HITS_PER_PAGE = 50


class GrailedClient:
    """Yields RawListing records from Grailed, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode() and has_env("GRAILED_ALGOLIA_APP_ID", "GRAILED_ALGOLIA_API_KEY")

    def listings(self, queries: tuple[str, ...] = DEFAULT_BRANDS) -> Iterator[RawListing]:
        if not self.live:
            logger.info("grailed: fixture mode (INGEST_LIVE unset or Algolia creds missing)")
            for hit in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(hit)
            return
        for query in queries:
            yield from self._live_search(query)
            polite_sleep()

    def _live_search(self, query: str) -> Iterator[RawListing]:
        """Query Grailed's Algolia index the way its own frontend does."""
        import os

        import requests

        app_id = os.environ["GRAILED_ALGOLIA_APP_ID"]
        api_key = os.environ["GRAILED_ALGOLIA_API_KEY"]
        index = os.environ.get("GRAILED_ALGOLIA_INDEX", DEFAULT_INDEX)
        url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{index}/query"
        response = requests.post(
            url,
            json={"query": query, "hitsPerPage": HITS_PER_PAGE},
            headers={
                "x-algolia-application-id": app_id,
                "x-algolia-api-key": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        for hit in response.json().get("hits", []):
            try:
                yield self._parse(hit)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("grailed: skipping unparseable hit (%s): %r", exc, hit.get("id"))

    def _parse(self, hit: dict[str, Any]) -> RawListing:
        """Map one Algolia hit to a RawListing. Defensive: hit shapes drift."""
        designer = hit.get("designer") or {}
        brand = designer.get("name") if isinstance(designer, dict) else None
        listing_id = hit.get("id")
        cover = hit.get("cover_photo") or {}
        user = hit.get("user") or {}
        title = hit.get("title") or ""
        if not title:
            raise ValueError("listing has no title")
        return RawListing(
            platform=PLATFORM,
            title=title,
            brand=brand,
            price=parse_money(hit.get("price")),
            currency=hit.get("currency") or "USD",
            size=hit.get("size"),
            condition=hit.get("condition"),
            listing_url=LISTING_URL.format(listing_id=listing_id) if listing_id else None,
            listed_date=hit.get("created_at"),
            sold_date=hit.get("sold_at"),
            sold_price=parse_money(hit.get("sold_price")),
            image_urls=tuple(u for u in [cover.get("url")] if u),
            seller=user.get("username") if isinstance(user, dict) else None,
            collection_tag=hit.get("season"),
        )
