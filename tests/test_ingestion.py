"""Fixture-mode tests for every ingestion source.

These run with no credentials and no network, which is the point: the
whole pipeline has to work for anyone who clones the repo. Fixtures are
platform-shaped payloads, so these tests exercise the real parse path.
"""
from __future__ import annotations

import json

import pytest

from ingestion.base import RawListing
from ingestion.depop import DepopClient
from ingestion.ebay import EbayClient
from ingestion.grailed import GrailedClient
from ingestion.retail import RetailClient, RetailPrice
from ingestion.secondstreet import SecondStreetClient
from ingestion.social import RedditClient, SocialMention
from ingestion.therealreal import TheRealRealClient
from ingestion.trends import TrendPoint, TrendsClient
from ingestion.vestiaire import VestiaireClient

RESALE_CLIENTS = [
    GrailedClient,
    EbayClient,
    DepopClient,
    VestiaireClient,
    TheRealRealClient,
    SecondStreetClient,
]


@pytest.fixture(autouse=True)
def no_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never accidentally run live, even on a machine with creds."""
    monkeypatch.delenv("INGEST_LIVE", raising=False)


@pytest.mark.parametrize("client_cls", RESALE_CLIENTS)
def test_resale_fixture_records_have_sane_shape(client_cls: type) -> None:
    records = list(client_cls().listings())
    assert records, f"{client_cls.__name__} fixture yielded nothing"
    for record in records:
        assert isinstance(record, RawListing)
        assert record.platform
        assert record.title.strip()
        if record.price is not None:
            assert record.price > 0
            assert record.currency, "a priced listing must carry a currency"
        if record.sold_price is not None:
            assert record.sold_price > 0
        assert record.listing_url is None or record.listing_url.startswith("http")


@pytest.mark.parametrize("client_cls", RESALE_CLIENTS)
def test_clients_default_to_fixture_mode(client_cls: type) -> None:
    assert client_cls().live is False


def test_ebay_fixture_includes_sold_ground_truth() -> None:
    sold = [r for r in EbayClient().listings() if r.sold_price is not None]
    assert len(sold) >= 3, "eBay fixture must carry sold comps; they are the ground truth"
    for record in sold:
        assert record.sold_date, "a sold price without a sold date is unusable"


def test_grailed_fixture_carries_collection_tags() -> None:
    tags = [r.collection_tag for r in GrailedClient().listings() if r.collection_tag]
    assert "FW10" in tags and "SS03" in tags


def test_retail_fixture_records() -> None:
    records = list(RetailClient().prices())
    assert records
    sources = {r.source for r in records}
    assert {"ssense", "dsm", "saks"} <= sources
    for record in records:
        assert isinstance(record, RetailPrice)
        assert record.product_name
        assert isinstance(record.in_stock, bool)
        if record.price is not None:
            assert record.price > 0
    assert any(not r.in_stock for r in records), "fixture should include a sold-out item"


def test_trends_fixture_records() -> None:
    points = list(TrendsClient().interest())
    assert points
    for point in points:
        assert isinstance(point, TrendPoint)
        assert 0 <= point.interest <= 100
        assert point.keyword and point.date


def test_social_fixture_records() -> None:
    mentions = list(RedditClient().mentions())
    assert mentions
    for mention in mentions:
        assert isinstance(mention, SocialMention)
        assert mention.subreddit and mention.post_id and mention.title
        assert mention.score >= 0 and mention.num_comments >= 0


def test_run_ingestion_lands_timestamped_files(tmp_path) -> None:
    from ingestion.run_ingestion import SOURCES, run

    counts = run(sorted(SOURCES), out_dir=tmp_path)
    assert set(counts) == set(SOURCES)
    assert all(count > 0 for count in counts.values())
    files = list(tmp_path.glob("*.json"))
    assert len(files) == len(SOURCES)
    for path in files:
        payload = json.loads(path.read_text())
        assert isinstance(payload, list) and payload


def test_run_ingestion_rejects_unknown_source(tmp_path) -> None:
    from ingestion.run_ingestion import run

    with pytest.raises(ValueError, match="unknown source"):
        run(["myspace"], out_dir=tmp_path)
