"""Vestiaire Collective listings for resale breadth.

Access: Vestiaire sits behind DataDome and Cloudflare, and free scraping is
not realistic at any useful volume. Live mode routes page fetches through
Bright Data Web Unlocker, which costs money per request, so pulls stay small
and targeted. The live parse reads the __NEXT_DATA__ JSON blob the site embeds
in its pages; that structure is best-effort until the first funded live run,
which is exactly why the fixtures and tests carry this module.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterator

from ingestion.base import (
    RawListing,
    brightdata_fetch,
    has_env,
    live_mode,
    load_fixture,
    parse_money,
    polite_sleep,
)

logger = logging.getLogger(__name__)

PLATFORM = "vestiaire"
BASE_URL = "https://www.vestiairecollective.com"
SEARCH_PATH = "/search/?q={query}"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


class VestiaireClient:
    """Yields RawListing records from Vestiaire, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode() and has_env("BRIGHTDATA_API_KEY", "BRIGHTDATA_ZONE")

    def listings(self, queries: tuple[str, ...] = ("rick owens", "margiela")) -> Iterator[RawListing]:
        if not self.live:
            logger.info("vestiaire: fixture mode (INGEST_LIVE unset or Bright Data creds missing)")
            for item in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(item)
            return
        for query in queries:
            yield from self._live_search(query)
            polite_sleep()

    def _live_search(self, query: str) -> Iterator[RawListing]:
        html = brightdata_fetch(BASE_URL + SEARCH_PATH.format(query=query.replace(" ", "+")))
        match = NEXT_DATA_RE.search(html)
        if not match:
            logger.warning("vestiaire: no __NEXT_DATA__ blob found for query %r", query)
            return
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("vestiaire: __NEXT_DATA__ was not valid JSON (%s)", exc)
            return
        items = (
            payload.get("props", {})
            .get("pageProps", {})
            .get("searchResults", {})
            .get("items", [])
        )
        for item in items:
            try:
                yield self._parse(item)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("vestiaire: skipping item (%s): %r", exc, item.get("id"))

    def _parse(self, item: dict[str, Any]) -> RawListing:
        name = item.get("name") or ""
        if not name:
            raise ValueError("item has no name")
        brand = item.get("brand") or {}
        price = item.get("price") or {}
        size = item.get("size") or {}
        condition = item.get("condition") or {}
        seller = item.get("seller") or {}
        cents = price.get("cents")
        link = item.get("link")
        return RawListing(
            platform=PLATFORM,
            title=name,
            brand=brand.get("name") if isinstance(brand, dict) else None,
            price=cents / 100 if isinstance(cents, (int, float)) else parse_money(price.get("amount")),
            currency=price.get("currency"),
            size=size.get("label") if isinstance(size, dict) else None,
            condition=condition.get("label") if isinstance(condition, dict) else None,
            listing_url=BASE_URL + link if link else None,
            listed_date=item.get("created_at"),
            sold_date=None,
            sold_price=None,
            image_urls=tuple(item.get("pictures") or ()),
            seller=seller.get("nickname") if isinstance(seller, dict) else None,
            collection_tag=None,
        )
