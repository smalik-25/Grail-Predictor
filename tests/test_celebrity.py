"""Celebrity detection and signal tests.

Detection precision comes from the curated figure list, and these tests pin
the contract: figure plus brand co-mention makes an event, either alone
makes nothing, and a model line in the text raises confidence and
specificity. The feature-side tests pin the as-of behavior: a pre-cutoff
event lifts the signal, a post-cutoff or wrong-brand event must not.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ml.celebrity import CONFIDENCE_BRAND, CONFIDENCE_FAMILY, detect, texts_from_social_fixture


def _text(text: str, date: str = "2026-06-30") -> dict:
    return {"text": text, "date": date, "source": "test"}


def test_figure_plus_brand_plus_line_makes_a_family_grade_event() -> None:
    (event,) = detect([_text("Carti spotted in Rick Owens geobaskets again")])
    assert event.figure == "Playboi Carti"
    assert event.brand == "Rick Owens"
    assert event.model_line == "Geobasket"
    assert event.confidence == CONFIDENCE_FAMILY
    assert event.family_id is None, "no era in the text: attach at brand+line, don't guess"


def test_figure_plus_brand_alone_makes_a_brand_grade_event() -> None:
    (event,) = detect([_text("kanye west wearing head to toe balenciaga at the show")])
    assert event.brand == "Balenciaga" and event.model_line is None
    assert event.confidence == CONFIDENCE_BRAND


def test_era_in_text_pins_the_family() -> None:
    (event,) = detect([_text("Frank Ocean in the Margiela future high top, the 2013 pair")])
    assert event.family_id == "maison-margiela__future-high-top__yeezus-era-2013-2014"


def test_figure_without_brand_is_gossip_not_signal() -> None:
    assert detect([_text("playboi carti announced tour dates")]) == []


def test_brand_without_figure_is_nothing() -> None:
    assert detect([_text("rick owens geobasket restock at sssense")]) == []


def test_social_fixture_yields_the_planted_events() -> None:
    events = detect(texts_from_social_fixture())
    figures = {event.figure for event in events}
    assert figures == {"Playboi Carti", "Frank Ocean"}
    assert all(event.event_date for event in events)


def test_loader_rows_shape() -> None:
    from db.load import fact_celebrity_rows

    (row,) = fact_celebrity_rows([{
        "family_id": None, "brand": "Rick Owens", "model_line": "Geobasket",
        "figure": "Playboi Carti", "event_date": "2026-06-30",
        "source": "reddit:rickowens", "confidence": 0.9, "evidence": "Carti spotted...",
    }])
    assert row[0] is None and row[1] == "Rick Owens" and row[3] == "Playboi Carti"
    assert str(row[4]) == "2026-06-30" and row[6] == 0.9


# --- feature-side as-of behavior (Spark) ---

@pytest.fixture(scope="module")
def spark():
    import os

    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    os.environ.setdefault("SPARK_LOCAL_HOSTNAME", "localhost")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("grail-celebrity-tests")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_events_lift_only_when_visible_and_matching(spark) -> None:
    import datetime

    from features.build_features import compute_features

    moment = datetime.date(2025, 6, 1)
    labels = spark.createDataFrame(pd.DataFrame(
        [{"family_id": "fam-1", "prediction_moment": moment, "label": True}]))
    sales = spark.createDataFrame(pd.DataFrame([
        {"family_id": "fam-1",
         "sold_date": (moment - datetime.timedelta(days=offset)).isoformat(),
         "sold_price_usd": 700.0, "colorway_tier": "core"}
        for offset in range(5, 115, 10)
    ]))
    attention = spark.createDataFrame(pd.DataFrame([
        {"family_id": "fam-1",
         "week_date": (moment - datetime.timedelta(days=offset)).isoformat(),
         "search_interest": 20.0, "social_mentions": 2}
        for offset in range(0, 90, 7)
    ]))
    families = spark.createDataFrame(pd.DataFrame(
        [{"family_id": "fam-1", "brand": "Rick Owens", "category": "footwear",
          "era": "archive-2010-2015", "retail_price_usd": 500.0, "collab_flag": False}]))
    events = spark.createDataFrame(pd.DataFrame([
        # visible: brand-wide, 40 days before cutoff
        {"family_id": None, "brand": "Rick Owens", "event_date": "2025-04-22"},
        # invisible: dated after the cutoff
        {"family_id": "fam-1", "brand": "Rick Owens", "event_date": "2025-06-10"},
        # invisible: wrong brand entirely
        {"family_id": None, "brand": "Balenciaga", "event_date": "2025-05-20"},
    ]))

    row = compute_features(spark, labels, sales, attention, families, events).toPandas().iloc[0]
    assert row["celebrity_event_count_90d"] == 1, "only the visible matching event counts"
    assert row["celebrity_recency_days"] == 40.0
    assert pd.to_datetime(row["max_source_date_used"]) <= pd.to_datetime(row["prediction_moment"])


def test_celebrity_feature_ignores_post_cutoff_events(spark) -> None:
    """The celebrity leak canary: adding an event dated after the cutoff must
    move nothing, bit for bit. Parity with the sales/attention canary in
    test_features.py, closing the same look-ahead gap on this source."""
    import datetime

    from features.build_features import compute_features

    moment = datetime.date(2025, 6, 1)
    labels = spark.createDataFrame(pd.DataFrame(
        [{"family_id": "fam-1", "prediction_moment": moment, "label": True}]))
    sales = spark.createDataFrame(pd.DataFrame([
        {"family_id": "fam-1",
         "sold_date": (moment - datetime.timedelta(days=offset)).isoformat(),
         "sold_price_usd": 700.0, "colorway_tier": "core"}
        for offset in range(5, 115, 10)
    ]))
    attention = spark.createDataFrame(pd.DataFrame([
        {"family_id": "fam-1",
         "week_date": (moment - datetime.timedelta(days=offset)).isoformat(),
         "search_interest": 20.0, "social_mentions": 2}
        for offset in range(0, 90, 7)
    ]))
    families = spark.createDataFrame(pd.DataFrame(
        [{"family_id": "fam-1", "brand": "Rick Owens", "category": "footwear",
          "era": "archive-2010-2015", "retail_price_usd": 500.0, "collab_flag": False}]))

    honest = [{"family_id": "fam-1", "brand": "Rick Owens", "event_date": "2025-04-22"}]
    leaky = honest + [{"family_id": "fam-1", "brand": "Rick Owens", "event_date": "2025-06-20"}]
    cols = ["celebrity_event_count_90d", "celebrity_recency_days", "max_source_date_used"]

    base = compute_features(spark, labels, sales, attention, families,
                            spark.createDataFrame(pd.DataFrame(honest))).toPandas()[cols]
    spiked = compute_features(spark, labels, sales, attention, families,
                              spark.createDataFrame(pd.DataFrame(leaky))).toPandas()[cols]

    pd.testing.assert_frame_equal(
        base, spiked, check_exact=True,
        obj="a post-cutoff celebrity event changed the features: look-ahead leak",
    )
