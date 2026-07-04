"""PySpark feature engineering at the style-family grain, as-of or nothing.

The contract, inherited from labeling: a labeled row carries its
prediction_moment T, and no feature may read data after T. Every feature
row carries max_source_date_used, the latest source date that touched any
of its aggregations, and Pandera fails the whole build if any row's value
exceeds its prediction_moment. The leak canary test perturbs post-T data
and asserts features are bit-identical.

Feature groups:
- own-history (windows ending at T, never after): price momentum, sold
  velocity, retail spread and its trend, search slope and acceleration,
  social mention velocity, rare-tier premium (the colorway sub-attribute
  earning its keep: rare-tier median over family median, pre-cutoff)
- peer-relative transforms: each core feature re-expressed as a z-score
  against same-brand-and-category families at the same moment, computed in
  Spark via a groupBy over (moment, brand, category). The peer-relative
  label needs peer-relative inputs to have a fair chance. Feature peer
  groups skip the price band the label uses: features are inputs, not the
  answer, and the coarser group keeps the z-scores dense.
- statics: brand, category, collab_flag, archive_flag (era string starts
  with 'archive', or season-year fallback for real catalog eras)
- celebrity_signal: celebrity_event_count_90d and celebrity_recency_days
  from the events frame, under the same as-of contract as every other
  source. A matching event (same family, or brand-wide on the family's
  brand) dated in the trailing 90 days lifts the count; recency is days
  since the most recent one. No events frame reproduces the old stub
  (count 0, recency null) so the shape never changes when the source is
  absent.

Output: Parquet partitioned by prediction year, matching how the time
split reads it. One Spark action: collect once, validate, land via
pyarrow with the same partitioning; writing through Spark as well would
re-execute the DAG to serialize a few hundred rows.
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

PEER_FEATURES = (
    "price_momentum_60d",
    "sold_velocity_30d",
    "search_slope_60d",
    "social_velocity_60d",
)


def season_year(season: str | None) -> int | None:
    """SS03 -> 2003, FW97 -> 1997. Two-digit pivot at 89: below is 20xx."""
    if not season:
        return None
    match = SEASON_RE.match(season)
    if not match:
        return None
    two = int(match.group(2))
    return 2000 + two if two < 90 else 1900 + two


def is_archive_era(era: str | None, prediction_year: int) -> bool:
    """Synth eras say 'archive-...' outright; catalog eras carry year ranges
    or season-derived years. Era-unknown is not archive; refusing to guess."""
    if not era:
        return False
    if era.startswith("archive"):
        return True
    if era.startswith("recent"):
        return False
    years = re.findall(r"(19[89]\d|20[0-3]\d)", era)
    if years:
        return prediction_year - int(years[0]) >= ARCHIVE_AGE_YEARS
    return False


def _with_celebrity_signal(base, labels, families, events_df, F):
    """Attach celebrity_event_count_90d, celebrity_recency_days, and the
    windowed max_event_date_used the audit stamp folds in.

    events_df None keeps the stub (count 0 long, recency null, event max null)
    so the 5-arg path and the leak canary stay bit-identical. Present, an
    event matches a row when it names the row's family OR is brand-wide on the
    row's brand, and it is dated in (T-90, T]. The window filter and the max
    are taken on ONE frame so a post-cutoff event can never reach the stamp."""
    if events_df is None:
        return (
            base
            .withColumn("celebrity_event_count_90d", F.lit(0).cast("long"))
            .withColumn("celebrity_recency_days", F.lit(None).cast("double"))
            .withColumn("max_event_date_used", F.lit(None).cast("date"))
        )

    # A pandas frame turns a None family_id into the string "NaN" when the
    # column also holds real ids, and that is not null, so the brand-wide
    # branch below would silently miss. Map it back to a true null, the same
    # None-to-NaN hazard scrub_nan handles on the load side.
    events = events_df.select(
        F.when(F.col("family_id").cast("string") == "NaN", F.lit(None))
         .otherwise(F.col("family_id").cast("string")).alias("e_family_id"),
        F.col("brand").alias("e_brand"),
        F.col("event_date").cast("date").alias("e_event_date"),
    )
    label_brand = labels.join(families.select("family_id", "brand"), "family_id", "left")
    matched = label_brand.join(
        events,
        (
            ((F.col("e_family_id") == F.col("family_id"))
             | (F.col("e_family_id").isNull() & (F.col("e_brand") == F.col("brand"))))
            & (F.col("e_event_date") <= F.col("prediction_moment"))
            & (F.col("e_event_date") > F.date_sub(F.col("prediction_moment"), 90))
        ),
        "inner",
    )
    celebrity = matched.groupBy("family_id", "prediction_moment").agg(
        F.count(F.lit(1)).alias("celebrity_event_count_90d"),
        F.max("e_event_date").alias("max_event_date_used"),
    )
    return (
        base.join(celebrity, ["family_id", "prediction_moment"], "left")
        .withColumn("celebrity_event_count_90d",
                    F.coalesce(F.col("celebrity_event_count_90d"), F.lit(0)).cast("long"))
        .withColumn("celebrity_recency_days",
                    F.datediff(F.col("prediction_moment"),
                               F.col("max_event_date_used")).cast("double"))
    )


def compute_features(spark, labels_df, sales_df, attention_df, families_df, events_df=None):
    """All aggregations conditional on source dates <= T; peer z-scores by
    (moment, brand, category) in a second Spark pass. DataFrames in, not
    paths, so the leak canary can feed perturbed frames straight in.

    events_df is optional: absent (None), the celebrity columns land as the
    old stub (count 0, recency null) and the stamp ignores them, so the
    5-arg callers and the leak canary stay bit-identical. Present, a matching
    event within the trailing 90 days lifts the count and recency, and the
    windowed event max folds into max_source_date_used like every other
    source. Only events dated at or before T can match, by construction."""
    from pyspark.sql import functions as F

    labels = labels_df.select("family_id", "prediction_moment", "label")

    sales = sales_df.select(
        "family_id",
        F.col("sold_date").cast("date").alias("sold_date"),
        "sold_price_usd",
        F.col("colorway_tier").alias("tier"),
    )
    joined_sales = labels.join(sales, "family_id").where(
        (F.col("sold_date") > F.date_sub(F.col("prediction_moment"), 120))
        & (F.col("sold_date") <= F.col("prediction_moment"))
    )
    sales_features = joined_sales.groupBy("family_id", "prediction_moment").agg(
        F.expr("percentile_approx(CASE WHEN sold_date > date_sub(prediction_moment, 30) THEN sold_price_usd END, 0.5)").alias("median_recent"),
        F.expr("percentile_approx(CASE WHEN sold_date <= date_sub(prediction_moment, 30) AND sold_date > date_sub(prediction_moment, 90) THEN sold_price_usd END, 0.5)").alias("median_prior"),
        F.expr("percentile_approx(CASE WHEN sold_date > date_sub(prediction_moment, 90) THEN sold_price_usd END, 0.5)").alias("median_90d"),
        F.expr("percentile_approx(CASE WHEN sold_date > date_sub(prediction_moment, 90) AND tier = 'rare' THEN sold_price_usd END, 0.5)").alias("rare_median_90d"),
        F.sum(F.when(F.col("sold_date") > F.date_sub(F.col("prediction_moment"), 90), 1).otherwise(0)).alias("n_sales_90d"),
        F.max("sold_date").alias("max_sales_date_used"),
    )

    attention = attention_df.select(
        "family_id",
        F.col("week_date").cast("date").alias("week_date"),
        "search_interest",
        "social_mentions",
    )
    joined_attention = labels.join(attention, "family_id").where(
        (F.col("week_date") > F.date_sub(F.col("prediction_moment"), 90))
        & (F.col("week_date") <= F.col("prediction_moment"))
    )
    attention_features = joined_attention.groupBy("family_id", "prediction_moment").agg(
        F.avg(F.expr("CASE WHEN week_date > date_sub(prediction_moment, 30) THEN search_interest END")).alias("interest_0_30"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 30) AND week_date > date_sub(prediction_moment, 60) THEN search_interest END")).alias("interest_30_60"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 60) THEN search_interest END")).alias("interest_60_90"),
        F.avg(F.expr("CASE WHEN week_date > date_sub(prediction_moment, 30) THEN social_mentions END")).alias("mentions_0_30"),
        F.avg(F.expr("CASE WHEN week_date <= date_sub(prediction_moment, 30) AND week_date > date_sub(prediction_moment, 60) THEN social_mentions END")).alias("mentions_30_60"),
        F.max("week_date").alias("max_attention_date_used"),
    )

    families = families_df.select(
        "family_id", "brand", "category", "era", "retail_price_usd",
        F.col("collab_flag").cast("boolean").alias("collab_flag"),
    )

    archive_udf = F.udf(is_archive_era, "boolean")

    base = (
        labels
        .join(sales_features, ["family_id", "prediction_moment"], "left")
        .join(attention_features, ["family_id", "prediction_moment"], "left")
        .join(families, "family_id", "left")
        .withColumn("price_momentum_60d",
                    F.when(F.col("median_prior") > 0, F.col("median_recent") / F.col("median_prior") - 1))
        .withColumn("sold_velocity_30d", F.coalesce(F.col("n_sales_90d"), F.lit(0)) / 3.0)
        .withColumn("spread_at_cutoff",
                    F.when(F.col("retail_price_usd") > 0, F.col("median_90d") / F.col("retail_price_usd")))
        .withColumn("rare_tier_premium",
                    F.when((F.col("median_90d") > 0) & F.col("rare_median_90d").isNotNull(),
                           F.col("rare_median_90d") / F.col("median_90d") - 1))
        .withColumn("search_slope_60d",
                    F.when(F.col("interest_30_60") > 0, F.col("interest_0_30") / F.col("interest_30_60") - 1))
        .withColumn("search_slope_prior",
                    F.when(F.col("interest_60_90") > 0, F.col("interest_30_60") / F.col("interest_60_90") - 1))
        .withColumn("search_accel", F.col("search_slope_60d") - F.col("search_slope_prior"))
        .withColumn("social_velocity_60d",
                    (F.coalesce(F.col("mentions_0_30"), F.lit(0.0)) + 1)
                    / (F.coalesce(F.col("mentions_30_60"), F.lit(0.0)) + 1) - 1)
        .withColumn("prediction_year", F.year("prediction_moment"))
        .withColumn("archive_flag",
                    F.coalesce(archive_udf(F.col("era"), F.col("prediction_year")), F.lit(False)))
    )

    # ---- celebrity signal, as-of. A detected event matches a row when it
    # names the same family OR is brand-wide on the row's brand, and it is
    # dated inside the trailing 90 days ending at T. The window filter and
    # the max share ONE frame on purpose: computing the event max only over
    # matched (<= T) events keeps a post-cutoff event from leaking into the
    # audit stamp. No events frame at all reproduces the stub exactly.
    base = _with_celebrity_signal(base, labels, families, events_df, F)

    base = base.withColumn(
        "max_source_date_used",
        F.greatest(F.col("max_sales_date_used"), F.col("max_attention_date_used"),
                   F.col("max_event_date_used")),
    )

    # ---- peer-relative pass: z against same-(moment, category) peers.
    # Category-wide rather than brand-and-category, deliberately: at the
    # brand-and-category grain most groups fall under 3 members and the
    # z-scores come back null, and a dense approximate input beats a
    # precise null one. The LABEL keeps its stricter ladder; features are
    # inputs, not the answer. Peer stats never read anything the base
    # features didn't already read, so the as-of guarantee is inherited.
    peer_stats = base.groupBy("prediction_moment", "category").agg(
        *[F.avg(name).alias(f"{name}_peer_mean") for name in PEER_FEATURES],
        *[F.stddev(name).alias(f"{name}_peer_std") for name in PEER_FEATURES],
        F.count("family_id").alias("peer_group_size"),
    )
    features = base.join(peer_stats, ["prediction_moment", "category"], "left")
    for name in PEER_FEATURES:
        features = features.withColumn(
            f"{name}_peer_z",
            F.when(
                (F.col(f"{name}_peer_std") > 0) & (F.col("peer_group_size") >= 3),
                (F.col(name) - F.col(f"{name}_peer_mean")) / F.col(f"{name}_peer_std"),
            ),
        )

    return features.select(
        "family_id", "prediction_moment", "prediction_year", "label",
        "price_momentum_60d", "sold_velocity_30d", "spread_at_cutoff", "rare_tier_premium",
        "search_slope_60d", "search_accel", "social_velocity_60d",
        *[f"{name}_peer_z" for name in PEER_FEATURES],
        "peer_group_size",
        "celebrity_event_count_90d", "celebrity_recency_days",
        "brand", "category", "collab_flag", "archive_flag",
        "max_source_date_used",
    )


def validate(frame) -> None:
    """Pandera gate: schema, ranges, required non-nulls, and the cutoff
    invariant. A feature set that fails this must not train."""
    import pandas as pd
    import pandera.pandas as pa

    schema = pa.DataFrameSchema(
        {
            "family_id": pa.Column(str),
            "prediction_moment": pa.Column("datetime64[ns]", coerce=True),
            "label": pa.Column(bool),
            "price_momentum_60d": pa.Column(float, pa.Check.in_range(-0.95, 20), nullable=True),
            "sold_velocity_30d": pa.Column(float, pa.Check.ge(0)),
            "spread_at_cutoff": pa.Column(float, pa.Check.in_range(0, 100), nullable=True),
            "rare_tier_premium": pa.Column(float, pa.Check.in_range(-0.95, 10), nullable=True),
            "search_slope_60d": pa.Column(float, pa.Check.in_range(-1, 50), nullable=True),
            "search_accel": pa.Column(float, nullable=True),
            "social_velocity_60d": pa.Column(float, pa.Check.in_range(-1, 100), nullable=True),
            "price_momentum_60d_peer_z": pa.Column(float, pa.Check.in_range(-10, 10), nullable=True),
            "sold_velocity_30d_peer_z": pa.Column(float, pa.Check.in_range(-10, 10), nullable=True),
            "search_slope_60d_peer_z": pa.Column(float, pa.Check.in_range(-10, 10), nullable=True),
            "social_velocity_60d_peer_z": pa.Column(float, pa.Check.in_range(-10, 10), nullable=True),
            "celebrity_event_count_90d": pa.Column(int, pa.Check.ge(0)),
            "celebrity_recency_days": pa.Column(float, pa.Check.ge(0), nullable=True),
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

    from ml.synth import (ATTENTION_FIXTURE, CELEBRITY_FIXTURE, FAMILIES_FIXTURE,
                          SALES_FIXTURE)

    labels = spark.createDataFrame(pd.read_parquet(LABELS_PATH))
    sales = spark.createDataFrame(pd.DataFrame(json.loads(SALES_FIXTURE.read_text())))
    attention = spark.createDataFrame(pd.DataFrame(json.loads(ATTENTION_FIXTURE.read_text())))
    families = spark.createDataFrame(pd.DataFrame(json.loads(FAMILIES_FIXTURE.read_text())))
    events = spark.createDataFrame(pd.DataFrame(json.loads(CELEBRITY_FIXTURE.read_text())))
    return labels, sales, attention, families, events


def main() -> None:
    parser = argparse.ArgumentParser(description="Build as-of family features with PySpark.")
    parser.add_argument("--source", choices=["synth"], default="synth",
                        help="warehouse source lands when real history exists")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    import os

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
        pandas_frame = features.toPandas()
    finally:
        spark.stop()
    validate(pandas_frame)
    # clear stale partitions: pandas appends files per partition dir, and a
    # grain change would otherwise leave old rows mixed into the dataset
    import shutil

    if FEATURES_PATH.exists():
        shutil.rmtree(FEATURES_PATH)
    pandas_frame.to_parquet(FEATURES_PATH, partition_cols=["prediction_year"], index=False)
    logger.info("features: wrote %d rows to %s (partitioned by prediction_year)",
                len(pandas_frame), FEATURES_PATH)


if __name__ == "__main__":
    main()
