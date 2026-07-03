"""eBay listings: active via the official Browse API, sold via public search pages.

Access reality as of mid 2026: the Finding API (which had findCompletedItems)
was decommissioned in February 2025, and the Marketplace Insights API that
replaced it is restricted to approved eBay partners. So there is no sanctioned
free path to sold comps. The compromise: active listings come from the Browse
API with proper OAuth (clean, official), and sold prices come from the public
completed-listings search pages at a polite rate (gray area, documented in the
DEVLOG). Sold prices are the ground truth for resale value, so dropping them
was not an option.
"""
from __future__ import annotations

import base64
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

PLATFORM = "ebay"
OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SOLD_SEARCH_URL = "https://www.ebay.com/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1"


class EbayClient:
    """Yields RawListing records from eBay, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir
        self._token: str | None = None

    @property
    def live(self) -> bool:
        return live_mode() and has_env("EBAY_APP_ID", "EBAY_CERT_ID")

    def listings(self, queries: tuple[str, ...] = DEFAULT_BRANDS) -> Iterator[RawListing]:
        if not self.live:
            logger.info("ebay: fixture mode (INGEST_LIVE unset or eBay creds missing)")
            for record in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(record)
            return
        for query in queries:
            yield from self._live_active(query)
            polite_sleep()
            yield from self._live_sold(query)
            polite_sleep()

    def _parse(self, record: dict[str, Any]) -> RawListing:
        """Dispatch on which live path a record came from."""
        source = record.get("source")
        if source == "browse":
            return self._parse_browse(record)
        if source == "sold_page":
            return self._parse_sold(record)
        raise ValueError(f"unknown ebay record source: {source!r}")

    # --- active listings: official Browse API ---

    def _live_active(self, query: str) -> Iterator[RawListing]:
        import requests

        response = requests.get(
            BROWSE_URL,
            params={"q": query, "limit": 50, "filter": "conditions:{USED}"},
            headers={"Authorization": f"Bearer {self._oauth_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        for item in response.json().get("itemSummaries", []):
            item = dict(item, source="browse")
            try:
                yield self._parse_browse(item)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("ebay: skipping browse item (%s): %r", exc, item.get("itemId"))

    def _oauth_token(self) -> str:
        """Client-credentials token for the Browse API."""
        import os

        import requests

        if self._token:
            return self._token
        credentials = f"{os.environ['EBAY_APP_ID']}:{os.environ['EBAY_CERT_ID']}"
        response = requests.post(
            OAUTH_URL,
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            headers={
                "Authorization": f"Basic {base64.b64encode(credentials.encode()).decode()}",
            },
            timeout=30,
        )
        response.raise_for_status()
        self._token = response.json()["access_token"]
        return self._token

    def _parse_browse(self, item: dict[str, Any]) -> RawListing:
        title = item.get("title") or ""
        if not title:
            raise ValueError("browse item has no title")
        price = item.get("price") or {}
        image = item.get("image") or {}
        seller = item.get("seller") or {}
        return RawListing(
            platform=PLATFORM,
            title=title,
            brand=item.get("brand"),
            price=parse_money(price.get("value")),
            currency=price.get("currency"),
            size=item.get("size"),
            condition=item.get("condition"),
            listing_url=item.get("itemWebUrl"),
            listed_date=item.get("itemCreationDate"),
            sold_date=None,
            sold_price=None,
            image_urls=tuple(u for u in [image.get("imageUrl")] if u),
            seller=seller.get("username") if isinstance(seller, dict) else None,
            collection_tag=None,
        )

    # --- sold listings: public completed-search pages ---

    def _live_sold(self, query: str) -> Iterator[RawListing]:
        """Best-effort parse of the public sold-listings search page.

        eBay's result markup shifts; every extraction is defensive and a bad
        card is skipped with a warning, never a crash.
        """
        import requests
        from bs4 import BeautifulSoup

        url = SOLD_SEARCH_URL.format(query=query.replace(" ", "+"))
        response = requests.get(url, headers={"User-Agent": "grail-predictor/0.1"}, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for card in soup.select(".s-item"):
            record = {
                "source": "sold_page",
                "title": _text(card, ".s-item__title"),
                "sold_price": _text(card, ".s-item__price"),
                "sold_date": _text(card, ".s-item__caption"),
                "listing_url": _href(card, ".s-item__link"),
            }
            try:
                yield self._parse_sold(record)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("ebay: skipping sold card (%s)", exc)

    def _parse_sold(self, record: dict[str, Any]) -> RawListing:
        title = record.get("title") or ""
        if not title or title.lower().startswith("shop on ebay"):
            raise ValueError("not a real sold listing card")
        return RawListing(
            platform=PLATFORM,
            title=title,
            brand=record.get("brand"),
            price=None,
            currency=record.get("currency") or "USD",
            size=record.get("size"),
            condition=record.get("condition"),
            listing_url=record.get("listing_url"),
            listed_date=None,
            sold_date=_clean_sold_date(record.get("sold_date")),
            sold_price=parse_money(record.get("sold_price")),
            image_urls=tuple(record.get("image_urls") or ()),
            seller=record.get("seller"),
            collection_tag=None,
        )


def _text(card: Any, selector: str) -> str | None:
    node = card.select_one(selector)
    return node.get_text(strip=True) if node else None


def _href(card: Any, selector: str) -> str | None:
    node = card.select_one(selector)
    return node.get("href") if node else None


def _clean_sold_date(value: str | None) -> str | None:
    """'Sold Jun 14, 2026' -> 'Jun 14, 2026'. Kept as text; parsing is Phase 2a."""
    if not value:
        return None
    return value.removeprefix("Sold").strip() or None
