"""The RealReal listings for resale breadth.

Access: The RealReal runs serious anti-bot protection, so like Vestiaire the
live path goes through Bright Data Web Unlocker, costs money per request, and
stays surgical. Live parsing targets the JSON the site embeds in product grid
pages and is best-effort until the first funded run; fixtures carry the module
until then. Condition language here ("Very Good", "Pristine") differs from
every other platform, which is exactly the Phase 2a normalization problem.
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

PLATFORM = "therealreal"
BASE_URL = "https://www.therealreal.com"
SEARCH_PATH = "/shop?keywords={query}"
STATE_RE = re.compile(r'<script[^>]*>window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>', re.DOTALL)


class TheRealRealClient:
    """Yields RawListing records from The RealReal, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode() and has_env("BRIGHTDATA_API_KEY", "BRIGHTDATA_ZONE")

    def listings(self, queries: tuple[str, ...] = ("rick owens", "margiela")) -> Iterator[RawListing]:
        if not self.live:
            logger.info("therealreal: fixture mode (INGEST_LIVE unset or Bright Data creds missing)")
            for product in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(product)
            return
        for query in queries:
            yield from self._live_search(query)
            polite_sleep()

    def _live_search(self, query: str) -> Iterator[RawListing]:
        html = brightdata_fetch(BASE_URL + SEARCH_PATH.format(query=query.replace(" ", "+")))
        match = STATE_RE.search(html)
        if not match:
            logger.warning("therealreal: no embedded state blob found for query %r", query)
            return
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.warning("therealreal: embedded state was not valid JSON (%s)", exc)
            return
        products = payload.get("products", {}).get("items", [])
        for product in products:
            try:
                yield self._parse(product)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("therealreal: skipping product (%s): %r", exc, product.get("sku"))

    def _parse(self, product: dict[str, Any]) -> RawListing:
        name = product.get("name") or ""
        if not name:
            raise ValueError("product has no name")
        url = product.get("url")
        return RawListing(
            platform=PLATFORM,
            title=name,
            brand=product.get("designer"),
            price=parse_money(product.get("price")),
            currency=product.get("currency") or "USD",
            size=product.get("size"),
            condition=product.get("condition"),
            listing_url=url if url and url.startswith("http") else (BASE_URL + url if url else None),
            listed_date=product.get("first_listed"),
            sold_date=product.get("sold_date"),
            sold_price=parse_money(product.get("sold_price")),
            image_urls=tuple(u for u in [product.get("image")] if u),
            seller=None,  # consignment: The RealReal is the seller of record
            collection_tag=None,
        )
