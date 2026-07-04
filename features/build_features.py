"""PySpark feature engineering, every feature strictly as-of the prediction moment.

The contract, inherited from Phase 5: a labeled row carries its
prediction_moment T, and no feature may read data after T. This module
makes the contract auditable instead of aspirational: every feature row
carries max_source_date_used, the latest source date that touched any of
its aggregations, and the Pandera validation fails the whole build if any
row's max_source_date_used exceeds its prediction_moment. The leak canary
test perturbs post-T data and asserts features are bit-identical.

Features (all windows end at T, never after):
- price_momentum_90d:   median price (T-45, T] over median (T-90, T-45], minus 1
- sold_velocity_30d:    sales count in (T-90, T], scaled to a 30-day pace
- spread_at_cutoff:     median price (T-90, T] over the item's retail price
- spread_trend:         spread now vs spread over (T-180, T-90], minus 1
- search_slope_60d:     mean interest (T-30, T] over mean (T-60, T-30], minus 1
- search_accel:         search_slope_60d minus the same slope one window earlier
- social_velocity_60d:  smoothed mention growth over the same windows
- brand, category, collab_flag, archive_flag (season at least 5 years old at T)

Output: Parquet partitioned by prediction year. Year partitioning matches
how everything downstream reads this data: the Phase 7 time split selects
by prediction moment, so pruning on year is the access pattern; item-level
partitioning would mean thousands of tiny files for no read path that wants
them.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
FEATURES_PATH = PROCESSED_DIR / "features"
LABELS_PATH = PROCESSED_DIR / "labels.parquet"

SEASON_RE = re.compile(r"^(SS|FW)(\d{2})$")
ARCHIVE_AGE_YEARS = 5


def season_year(season: str | None) -> int | None:
    """SS03 -> 2003, FW97 -> 1997. Two-digit pivot at 89: below is 20xx."""
    if not season:
        return None
    match = SEASON_RE.match(season)
    if not match:
        return None
    two = int(match.group(2))
    return 2000 + two if two < 90 else 1900 + two


def compute_features(spark, labels_df, sales_df, attention_df, items_df):
    """All aggregations are conditional on source dates being <= T.

    Kept as one function taking DataFrames, not paths, so the leak canary
    test can feed perturbed frames straight in.
    """
    from pyspark.sql import functions as F

    labels = labels_df.select("item_id", "prediction_moment", "label")

    # ---- sales windows: one range join against the widest window, then
    # conditional aggregation per sub-window. percentile_approx ignores
    # nulls, so F.when carves the sub-windows out of the joined rows.
    sales = sales_df.select(
        "item_id", F.col("sold_date").cast("date").alias("sold_date"), "sold_price_usd"
    )
    joined_sales = labels.join(sales, "item_id").where(
        (F.col("sold_date") > F.date_sub(F.col("prediction_moment"), 180))
        & (F.col("sold_date") <= F.col("prediction_moment"))
    )

    def in_window(start_days_ago: int, end_days_ago: int):
        return (
            F.col("sold_date") > F.date_sub(F.col("prediction_moment"), start_days_ago)
        ) & (F.col("sold_date") <= F.date_sub(F.col("prediction_moment"), end_days_ago))

    sales_features = joined_sales.groupBy("item_id", "prediction_moment").agg(
        F.expr("percentile_approx(CASE WHEN sold_date > date_sub(prediction_moment, 45) THEN sold_price_usd END, 0.5)").alias("median_recent"),
        F.expr("percentile_approx(CASE WHEN sold_date <= date_sub(prediction_moment, 45) AND sold_date > date_sub(prediction_moment, 90) THEN sold_price_usd END, 0.5)").alias("median_prior"),
        F.expr("percentile_approx(CASE WHEN sold_date > date_sub(prediction_moment, 90) THEN sold_price_usd END, 0.5)").alias("median_90d"),
        F.expr("percentile_approx(CASE WHEN sold_date <= date_sub(prediction_moment, 90) THEN sold_price_usd END, 0.5)").alias("median_90_180d"),
        F.sum(F.when(in_window(90, 0), 1).otherwise(0)).alias("n_sales_90d"),
        F.max("sold_date").alias("max_sales_date_used"),
    )

    # ---- attention windows, same shape
    attention = attention_df.select(
        "item_id", F.col("week_date").cast("date").alias("week_date"),
        "search_interest", "social_mentions",
    )
    joined_attention = labels.join(attention, "item_id").where(
        (F.col("week_date") > F.date_sub(F.col("prediction_moment"), 90))
        & (F.col("week_date") <= F.col("prediction_moment"))
    )
    attention_features = joined_attention.groupBy("item_id", "prediction_moment").agg(
        F.avg(F.expr("CASE WHEN week_date > date_sub(prediction_moment, 30) THEN search_interest END")).alias("interest_0_30"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 30) AND week_date > date_sub(prediction_moment, 60) THEN search_interest END")).alias("interest_30_60"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 60) THEN search_interest END")).alias("interest_60_90"),
        F.avg(F.expr("CASE WHEN week_date > date_sub(prediction_moment, 30) THEN social_mentions END")).alias("mentions_0_30"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 30) AND week_date > date_sub(prediction_moment, 60) THEN social_mentions END")).alias("mentions_30_60"),
        F.max("week_date").alias("max_attention_date_used"),
    )

    items = items_df.select(
        "item_id", "brand", "category", "retail_price_usd", "season",
        F.col("collab_flag").cast("boolean").alias("collab_flag"),
    )

    season_year_udf = F.udf(season_year, "int")

    features = (
        labels
        .join(sales_features, ["item_id", "prediction_moment"], "left")
        .join(attention_features, ["item_id", "prediction_moment"], "left")
        .join(items, "item_id", "left")
        .withColumn("price_momentum_90d",
                    F.when(F.col("median_prior") > 0, F.col("median_recent") / F.col("median_prior") - 1))
        .withColumn("sold_velocity_30d", F.coalesce(F.col("n_sales_90d"), F.lit(0)) / 3.0)
        .withColumn("spread_at_cutoff",
                    F.when(F.col("retail_price_usd") > 0, F.col("median_90d") / F.col("retail_price_usd")))
        .withColumn("spread_trend",
                    F.when((F.col("median_90_180d") > 0) & F.col("median_90d").isNotNull(),
                           F.col("median_90d") / F.col("median_90_180d") - 1))
        .withColumn("search_slope_60d",
                    F.when(F.col("interest_30_60") > 0, F.col("interest_0_30") / F.col("interest_30_60") - 1))
        .withColumn("search_slope_prior",
                    F.when(F.col("interest_60_90") > 0, F.col("interest_30_60") / F.col("interest_60_90") - 1))
        .withColumn("search_accel", F.col("search_slope_60d") - F.col("search_slope_prior"))
        .withColumn("social_velocity_60d",
                    (F.coalesce(F.col("mentions_0_30"), F.lit(0.0)) + 1)
                    / (F.coalesce(F.col("mentions_30_60"), F.lit(0.0)) + 1) - 1)
        .withColumn("season_year", season_year_udf(F.col("season")))
        .withColumn("archive_flag",
                    F.when(F.col("season_year").isNotNull(),
                           (F.year("prediction_moment") - F.col("season_year")) >= ARCHIVE_AGE_YEARS
                           ).otherwise(F.lit(False)))
        .withColumn("max_source_date_used",
                    F.greatest(F.col("max_sales_date_used"), F.col("max_attention_date_used")))
        .withColumn("prediction_year", F.year("prediction_moment"))
    )

    return features.select(
        "item_id", "prediction_moment", "prediction_year", "label",
        "price_momentum_90d", "sold_velocity_30d", "spread_at_cutoff", "spread_trend",
        "search_slope_60d", "search_accel", "social_velocity_60d",
        "brand", "category", "collab_flag", "archive_flag",
        "max_source_date_used",
    )


def validate(frame) -> None:
    """Pandera gate: schema, ranges, required non-nulls, and the cutoff
    invariant. Fails loudly; a feature set that fails this must not train."""
    import pandas as pd
    import pandera.pandas as pa

    schema = pa.DataFrameSchema(
        {
            "item_id": pa.Column(str),
            "prediction_moment": pa.Column("datetime64[ns]", coerce=True),
            "label": pa.Column(bool),
            "price_momentum_90d": pa.Column(float, pa.Check.in_range(-0.95, 20), nullable=True),
            "sold_velocity_30d": pa.Column(float, pa.Check.ge(0)),
            "spread_at_cutoff": pa.Column(float, pa.Check.in_range(0, 100), nullable=True),
            "spread_trend": pa.Column(float, pa.Check.in_range(-0.95, 20), nullable=True),
            "search_slope_60d": pa.Column(float, pa.Check.in_range(-1, 50), nullable=True),
            "search_accel": pa.Column(float, nullable=True),
            "social_velocity_60d": pa.Column(float, pa.Check.in_range(-1, 100), nullable=True),
            "brand": pa.Column(str),
            "category": pa.Column(str),
            "collab_flag": pa.Column(bool),
            "archive_flag": pa.Column(bool),
        },
        checks=pa.Check(
            lambda df: pd.to_datetime(df["max_source_date_used"]).fillna(pd.Timestamp.min)
            <= pd.to_datetime(df["prediction_moment"]),
            error="look-ahead violation: a feature read data after its prediction moment",
        ),
        strict=False,
    )
    schema.validate(frame, lazy=True)
    logger.info("pandera: %d feature rows validated, no cutoff violations", len(frame))


def load_synth_frames(spark):
    import pandas as pd

    from ml.synth import ATTENTION_FIXTURE, ITEMS_FIXTURE, SALES_FIXTURE

    labels = spark.createDataFrame(pd.read_parquet(LABELS_PATH))
    sales = spark.createDataFrame(pd.DataFrame(json.loads(SALES_FIXTURE.read_text())))
    attention = spark.createDataFrame(pd.DataFrame(json.loads(ATTENTION_FIXTURE.read_text())))
    items = spark.createDataFrame(pd.DataFrame(json.loads(ITEMS_FIXTURE.read_text())))
    return labels, sales, attention, items


def main() -> None:
    parser = argparse.ArgumentParser(description="Build as-of features with PySpark.")
    parser.add_argument("--source", choices=["synth"], default="synth",
                        help="warehouse source lands when real history exists")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    import os

    # local mode should never depend on the machine's hostname resolving
    # (sandboxes and CI runners often can't); harmless on machines that can
    os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")
    os.environ.setdefault("SPARK_LOCAL_HOSTNAME", "localhost")

    from pyspark.sql import SparkSession

    spark = (
        SparkSession.builder.appName("grail-features")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    try:
        frames = load_synth_frames(spark)
        features = compute_features(spark, *frames)
        # One Spark action, not two: collect once, validate, and land the
        # validated frame via pyarrow with the same year partitioning.
        # Writing through Spark as well would re-execute the whole DAG just
        # to serialize a few hundred rows.
        pandas_frame = features.toPandas()
    finally:
        spark.stop()
    validate(pandas_frame)
    pandas_frame.to_parquet(FEATURES_PATH, partition_cols=["prediction_year"], index=False)
    logger.info("features: wrote %d rows to %s (partitioned by prediction_year)",
                len(pandas_frame), FEATURES_PATH)


if __name__ == "__main__":
    main()
