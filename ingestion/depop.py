"""Depop listings for resale breadth.

Access: Depop's site is backed by an unauthenticated JSON search endpoint
(webapi.depop.com). It is unofficial and Depop's ToS prohibits scraping, so
this is a gray area used sparingly: public listing data, low volume, polite
rate. Documented honestly in the DEVLOG. If the endpoint hardens, the fallback
is Bright Data, with the cost flagged before switching.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from ingestion.base import (
    DEFAULT_BRANDS,
    RawListing,
    live_mode,
    load_fixture,
    parse_money,
    polite_sleep,
)

logger = logging.getLogger(__name__)

PLATFORM = "depop"
SEARCH_URL = "https://webapi.depop.com/api/v3/search/products/"
PRODUCT_URL = "https://www.depop.com/products/{slug}/"


class DepopClient:
    """Yields RawListing records from Depop, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode()  # no credentials needed, just the explicit opt-in

    def listings(self, queries: tuple[str, ...] = DEFAULT_BRANDS) -> Iterator[RawListing]:
        if not self.live:
            logger.info("depop: fixture mode (INGEST_LIVE unset)")
            for product in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(product)
            return
        for query in queries:
            yield from self._live_search(query)
            polite_sleep()

    def _live_search(self, query: str) -> Iterator[RawListing]:
        import requests

        response = requests.get(
            SEARCH_URL,
            params={"what": query, "itemsPerPage": 48, "country": "us"},
            headers={"User-Agent": "grail-predictor/0.1"},
            timeout=30,
        )
        response.raise_for_status()
        for product in response.json().get("products", []):
            try:
                yield self._parse(product)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("depop: skipping product (%s): %r", exc, product.get("id"))

    def _parse(self, product: dict[str, Any]) -> RawListing:
        title = product.get("title") or product.get("description") or ""
        if not title:
            raise ValueError("product has no title or description")
        price = product.get("price") or {}
        sizes = product.get("sizes") or []
        size = sizes[0].get("name") if sizes and isinstance(sizes[0], dict) else None
        preview = product.get("preview") or {}
        slug = product.get("slug")
        return RawListing(
            platform=PLATFORM,
            title=title,
            brand=product.get("brand_name"),
            price=parse_money(price.get("total_price")),
            currency=price.get("currency_name"),
            size=size,
            condition=product.get("condition"),
            listing_url=PRODUCT_URL.format(slug=slug) if slug else None,
            listed_date=product.get("date_updated"),
            sold_date=None,
            sold_price=None,
            image_urls=tuple(u for u in [preview.get("640")] if u),
            seller=product.get("seller_username"),
            collection_tag=None,
        )
