"""Feature builder tests at the family grain. The leak canary earns its keep.

One shared local Spark session (JVM startup dominates), tiny hand-built
frames for the canary so the perturbation is surgical and the assertion is
bit-level equality.
"""
from __future__ import annotations

import datetime
import os

import pandas as pd
import pytest

from features.build_features import compute_features, is_archive_era, season_year, validate


@pytest.mark.parametrize(
    ("season", "expected"),
    [("SS03", 2003), ("FW10", 2010), ("FW97", 1997), ("SS24", 2024), (None, None), ("garbage", None)],
)
def test_season_year(season: str | None, expected: int | None) -> None:
    assert season_year(season) == expected


@pytest.mark.parametrize(
    ("era", "year", "expected"),
    [
        ("archive-2003-2009", 2025, True),
        ("recent-2016-plus", 2025, False),
        ("yeezus-era-2013-2014", 2025, True),   # year range in the era name
        ("later-2015-plus", 2018, False),
        ("era-unknown", 2025, False),           # refusing to guess
        (None, 2025, False),
    ],
)
def test_is_archive_era(era: str | None, year: int, expected: bool) -> None:
    assert is_archive_era(era, year) == expected


@pytest.fixture(scope="module")
def spark():
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    os.environ.setdefault("SPARK_LOCAL_HOSTNAME", "localhost")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("grail-features-tests")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    yield session
    session.stop()


MOMENT = datetime.date(2025, 6, 1)


def _frames(spark, sales_rows, attention_rows):
    labels = spark.createDataFrame(
        pd.DataFrame([{"family_id": "fam-1", "prediction_moment": MOMENT, "label": True}])
    )
    sales = spark.createDataFrame(pd.DataFrame(sales_rows))
    attention = spark.createDataFrame(pd.DataFrame(attention_rows))
    families = spark.createDataFrame(
        pd.DataFrame([{"family_id": "fam-1", "brand": "Rick Owens", "category": "footwear",
                       "era": "archive-2010-2015", "retail_price_usd": 500.0,
                       "collab_flag": False}])
    )
    return labels, sales, attention, families


def _sales_rows() -> list[dict]:
    rows = []
    for offset in range(5, 115, 10):
        rows.append({"family_id": "fam-1",
                     "sold_date": (MOMENT - datetime.timedelta(days=offset)).isoformat(),
                     "sold_price_usd": 700.0 + offset,
                     "colorway_tier": "rare" if offset % 30 == 5 else "core"})
    return rows


def _attention_rows() -> list[dict]:
    rows = []
    for offset in range(0, 90, 7):
        rows.append({"family_id": "fam-1",
                     "week_date": (MOMENT - datetime.timedelta(days=offset)).isoformat(),
                     "search_interest": 20.0 + offset / 7, "social_mentions": 3})
    return rows


def test_features_are_invariant_to_post_cutoff_data(spark) -> None:
    """THE leak canary: add wild post-cutoff data; features must not move."""
    base = compute_features(spark, *_frames(spark, _sales_rows(), _attention_rows())).toPandas()

    spiked_sales = _sales_rows() + [
        {"family_id": "fam-1", "sold_date": "2025-07-15", "sold_price_usd": 99999.0,
         "colorway_tier": "rare"},
        {"family_id": "fam-1", "sold_date": "2025-06-02", "sold_price_usd": 88888.0,
         "colorway_tier": "core"},
    ]
    spiked_attention = _attention_rows() + [
        {"family_id": "fam-1", "week_date": "2025-06-08", "search_interest": 100.0,
         "social_mentions": 500},
    ]
    spiked = compute_features(spark, *_frames(spark, spiked_sales, spiked_attention)).toPandas()

    pd.testing.assert_frame_equal(
        base.sort_index(axis=1), spiked.sort_index(axis=1), check_exact=True,
        obj="features changed when only post-cutoff data changed: look-ahead leak",
    )


def test_features_read_expected_windows(spark) -> None:
    frame = compute_features(spark, *_frames(spark, _sales_rows(), _attention_rows())).toPandas()
    row = frame.iloc[0]
    assert row["sold_velocity_30d"] > 0
    assert row["price_momentum_60d"] < 0, "prices fall toward the cutoff in this construction"
    assert row["search_slope_60d"] < 0, "interest falls toward the cutoff in this construction"
    assert row["rare_tier_premium"] is not None
    assert row["archive_flag"], "archive-2010-2015 era is archive"
    assert row["celebrity_event_count_90d"] == 0, "6b stub must land as zero, not null"
    assert pd.to_datetime(row["max_source_date_used"]) <= pd.to_datetime(row["prediction_moment"])
    assert pd.isna(row["search_slope_60d_peer_z"]), "a lone family has no peer z"


def test_full_synth_features_pass_validation(spark) -> None:
    from features.build_features import LABELS_PATH, load_synth_frames

    if not LABELS_PATH.exists():
        pytest.skip("labels.parquet missing; run ml.label first")
    frame = compute_features(spark, *load_synth_frames(spark)).toPandas()
    labels = pd.read_parquet(LABELS_PATH)
    assert len(frame) == len(labels), "every labeled example must get a feature row"
    validate(frame)  # raises on schema, range, or cutoff violations
    dense = frame["search_slope_60d_peer_z"].notna().mean()
    assert dense > 0.9, f"peer z should be dense at category grain, got {dense:.2f}"
