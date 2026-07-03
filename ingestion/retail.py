"""First-hand retail prices from SSENSE, Dover Street Market, and Saks.

These feed the retail-vs-resale spread: a piece trading above retail while
still buyable new is a different signal from one that is long sold out.

Access, per source:
- Dover Street Market's regional e-shops run on Shopify, which exposes the
  public /products.json endpoint. Free and clean.
- SSENSE serves JSON from its own category endpoints; direct requests at a
  polite rate, gray-ish but public catalog data.
- Saks sits behind Akamai; live pulls route through Bright Data and are the
  lowest priority of the three because SSENSE and DSM cover most of the
  watchlist brands.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ingestion.base import (
    brightdata_fetch,
    has_env,
    live_mode,
    load_fixture,
    parse_money,
    polite_sleep,
)

logger = logging.getLogger(__name__)

PLATFORM = "retail"
DSM_PRODUCTS_URL = "https://shop-us.doverstreetmarket.com/products.json?limit=250"
SSENSE_MEN_URL = "https://www.ssense.com/en-us/men/designers/{designer_slug}.json"
SAKS_SEARCH_URL = "https://www.saksfifthavenue.com/search?q={query}"


@dataclass(frozen=True)
class RetailPrice:
    """One first-hand retail observation for a product on a given date."""

    source: str  # ssense | dsm | saks
    brand: str | None
    product_name: str
    price: float | None
    currency: str | None
    url: str | None
    in_stock: bool
    date: str  # ISO date of observation


class RetailClient:
    """Yields RetailPrice records across retail sources, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode()

    def prices(self) -> Iterator[RetailPrice]:
        if not self.live:
            logger.info("retail: fixture mode (INGEST_LIVE unset)")
            for row in load_fixture(PLATFORM, self._fixture_dir):
                yield self._parse(row)
            return
        yield from self._live_dsm()
        polite_sleep()
        yield from self._live_ssense("rick-owens")
        polite_sleep()
        if has_env("BRIGHTDATA_API_KEY", "BRIGHTDATA_ZONE"):
            yield from self._live_saks("rick owens")

    def _parse(self, row: dict[str, Any]) -> RetailPrice:
        name = row.get("product_name") or ""
        if not name:
            raise ValueError("retail row has no product_name")
        return RetailPrice(
            source=row.get("source") or "unknown",
            brand=row.get("brand"),
            product_name=name,
            price=parse_money(row.get("price")),
            currency=row.get("currency"),
            url=row.get("url"),
            in_stock=bool(row.get("in_stock", True)),
            date=row.get("date") or "",
        )

    def _live_dsm(self) -> Iterator[RetailPrice]:
        """DSM US e-shop via Shopify's public products.json."""
        import datetime

        import requests

        response = requests.get(
            DSM_PRODUCTS_URL, headers={"User-Agent": "grail-predictor/0.1"}, timeout=30
        )
        response.raise_for_status()
        today = datetime.date.today().isoformat()
        for product in response.json().get("products", []):
            variants = product.get("variants") or []
            first = variants[0] if variants else {}
            row = {
                "source": "dsm",
                "brand": product.get("vendor"),
                "product_name": product.get("title"),
                "price": first.get("price"),
                "currency": "USD",
                "url": f"https://shop-us.doverstreetmarket.com/products/{product.get('handle')}",
                "in_stock": any(v.get("available") for v in variants),
                "date": today,
            }
            try:
                yield self._parse(row)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("retail/dsm: skipping product (%s): %r", exc, product.get("id"))

    def _live_ssense(self, designer_slug: str) -> Iterator[RetailPrice]:
        """SSENSE designer page JSON. Shape drifts; parse defensively."""
        import datetime

        import requests

        response = requests.get(
            SSENSE_MEN_URL.format(designer_slug=designer_slug),
            headers={"User-Agent": "grail-predictor/0.1", "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        today = datetime.date.today().isoformat()
        for product in response.json().get("products", []):
            price_block = product.get("priceByCountry") or product.get("price") or {}
            row = {
                "source": "ssense",
                "brand": (product.get("brand") or {}).get("name"),
                "product_name": product.get("name"),
                "price": price_block.get("regular") if isinstance(price_block, dict) else price_block,
                "currency": "USD",
                "url": "https://www.ssense.com" + (product.get("url") or ""),
                "in_stock": bool(product.get("inStock", True)),
                "date": today,
            }
            try:
                yield self._parse(row)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("retail/ssense: skipping product (%s): %r", exc, product.get("id"))

    def _live_saks(self, query: str) -> Iterator[RetailPrice]:
        """Saks via Bright Data. Lowest priority; best-effort until funded run."""
        html = brightdata_fetch(SAKS_SEARCH_URL.format(query=query.replace(" ", "+")))
        logger.info("retail/saks: fetched %d bytes; parser lands with first funded run", len(html))
        return
        yield  # pragma: no cover - makes this a generator
