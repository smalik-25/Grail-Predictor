"""Google Trends search interest per piece and per brand.

The plan said pytrends, but pytrends was archived in April 2025 and is
unmaintained, with chronic 429 failures before that. This uses trendspy,
the maintained successor, with the honest caveat that every free Google
Trends client is fragile by nature: Google rate-limits aggressively and
changes the private endpoints without notice. Interest values are Google's
0-100 relative index, not absolute volume.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ingestion.base import live_mode, load_fixture, polite_sleep

logger = logging.getLogger(__name__)

PLATFORM = "trends"

DEFAULT_KEYWORDS: tuple[str, ...] = (
    "rick owens geobasket",
    "margiela gat",
    "number nine kurt cardigan",
    "undercover scab jacket",
)


@dataclass(frozen=True)
class TrendPoint:
    """One weekly observation of Google's relative search interest index."""

    keyword: str
    date: str  # ISO date, start of the observed week
    interest: int  # Google's 0-100 relative index


class TrendsClient:
    """Yields TrendPoint records, fixture-backed by default."""

    def __init__(self, fixture_dir: Path | None = None) -> None:
        self._fixture_dir = fixture_dir

    @property
    def live(self) -> bool:
        return live_mode()

    def interest(self, keywords: tuple[str, ...] = DEFAULT_KEYWORDS) -> Iterator[TrendPoint]:
        if not self.live:
            logger.info("trends: fixture mode (INGEST_LIVE unset)")
            for row in load_fixture(PLATFORM, self._fixture_dir):
                yield TrendPoint(
                    keyword=row["keyword"], date=row["date"], interest=int(row["interest"])
                )
            return
        for keyword in keywords:
            yield from self._live_interest(keyword)
            polite_sleep(5.0)  # Google throttles hard; back way off

    def _live_interest(self, keyword: str) -> Iterator[TrendPoint]:
        from trendspy import Trends

        client = Trends()
        try:
            series = client.interest_over_time(keyword, timeframe="today 12-m")
        except Exception as exc:  # noqa: BLE001 - trendspy raises library-specific types
            logger.warning("trends: fetch failed for %r (%s); skipping keyword", keyword, exc)
            return
        for date_index, value in series[keyword].items():
            yield TrendPoint(
                keyword=keyword,
                date=str(date_index.date() if hasattr(date_index, "date") else date_index),
                interest=int(value),
            )
