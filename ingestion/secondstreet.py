"""2nd Street listings for resale breadth.

Access: 2nd Street's US site (2ndstreetusa.com) serves plain server-rendered
HTML without aggressive bot defenses, so this uses direct requests at a
polite rate, no Bright Data needed. The quirks are the payoff: Japanese
letter-grade conditions (A/B/C) and centimeter shoe sizing, which the
Phase 2a normalizer has to handle anyway, so having them in fixtures early
keeps that work honest.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from ingestion.base import (
    RawListing,
    live_mode,
    load_fixture,
    parse_money,
    polite_sleep,
)

logger = logging.getLogger(__name__)

PLATFORM = "secondstreet"
BASE_URL = "https://www.2ndstreetusa.com"
SEARCH_PATH = "/search?q={query}"


class SecondStreetClient:
    """Yields RawListing records from 2nd Street, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode()  # no credentials needed, just the explicit opt-in

    def listings(self, queries: tuple[str, ...] = ("rick owens", "number nine", "undercover")) -> Iterator[RawListing]:
        if not self.live:
            logger.info("secondstreet: fixture mode (INGEST_LIVE unset)")
            for card in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(card)
            return
        for query in queries:
            yield from self._live_search(query)
            polite_sleep()

    def _live_search(self, query: str) -> Iterator[RawListing]:
        """Fetch and parse a search page into card dicts, then RawListings."""
        import requests
        from bs4 import BeautifulSoup

        url = BASE_URL + SEARCH_PATH.format(query=query.replace(" ", "+"))
        response = requests.get(url, headers={"User-Agent": "grail-predictor/0.1"}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup.select(".itemCard"):
            title_node = node.select_one(".itemCard_name")
            brand_node = node.select_one(".itemCard_brand")
            price_node = node.select_one(".itemCard_price")
            link_node = node.select_one("a")
            card: dict[str, Any] = {
                "title": title_node.get_text(strip=True) if title_node else None,
                "brand": brand_node.get_text(strip=True) if brand_node else None,
                "price": price_node.get_text(strip=True) if price_node else None,
                "currency": "USD",
                "url": (BASE_URL + link_node["href"]) if link_node and link_node.get("href") else None,
            }
            try:
                yield self._parse(card)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("secondstreet: skipping card (%s)", exc)

    def _parse(self, card: dict[str, Any]) -> RawListing:
        title = card.get("title") or ""
        if not title:
            raise ValueError("card has no title")
        return RawListing(
            platform=PLATFORM,
            title=title,
            brand=card.get("brand"),
            price=parse_money(card.get("price")),
            currency=card.get("currency"),
            size=card.get("size"),
            condition=card.get("condition"),
            listing_url=card.get("url"),
            listed_date=card.get("listed_date"),
            sold_date=None,
            sold_price=None,
            image_urls=tuple(card.get("image_urls") or ()),
            seller=None,  # 2nd Street sells its own stock
            collection_tag=None,
        )
