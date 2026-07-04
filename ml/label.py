"""Define and assign the prediction target with an explicit as-of cutoff.

The target: at a prediction moment T, an item "became a grail" if its
median realized resale price over the outcome window (T, T+180d] reached
at least 1.5x its median over the baseline window (T-90d, T]. The numbers
and their reasoning live in docs/labeling.md; the short version is that
grail moves are multiples, so 50% in six months separates an inflection
from market drift and noise, and both windows are long enough to median
away single-sale flukes.

The cutoff is structural, not a convention: baseline uses only sales at or
before T, outcome only sales strictly after T, and there is a test proving
the label's baseline cannot move when post-T data changes. Downstream
(Phase 6) every feature must be computed from data at or before this same
T, which is why every labeled row carries its prediction_moment.

Thin data is excluded, not labeled negative: an item with too few sales to
measure is unmeasured, and calling it "not a grail" would teach the model
that illiquidity means failure. The exclusion counts are reported.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
LABELS_PATH = PROCESSED_DIR / "labels.parquet"


@dataclass(frozen=True)
class LabelConfig:
    """The knobs, pinned and documented rather than implicit."""

    baseline_window_days: int = 90
    outcome_window_days: int = 180
    appreciation_threshold: float = 1.5
    min_baseline_sales: int = 2
    min_outcome_sales: int = 2
    grid_day_of_month: int = 1  # prediction moments on an aligned monthly grid


def prediction_moments(
    min_date: datetime.date, max_date: datetime.date, config: LabelConfig
) -> list[datetime.date]:
    """Aligned monthly grid, clipped so every T has a full observable
    baseline behind it and a full outcome window ahead of it inside the
    data coverage. A T whose outcome window runs past the end of the data
    is not 'label 0', it is unknowable, so it is never generated."""
    earliest = min_date + datetime.timedelta(days=config.baseline_window_days)
    latest = max_date - datetime.timedelta(days=config.outcome_window_days)
    moments = []
    cursor = datetime.date(earliest.year, earliest.month, config.grid_day_of_month)
    if cursor < earliest:
        cursor = _add_month(cursor)
    while cursor <= latest:
        moments.append(cursor)
        cursor = _add_month(cursor)
    return moments


def build_labels(sales: pd.DataFrame, config: LabelConfig = LabelConfig()) -> pd.DataFrame:
    """Label every (item, prediction moment) with enough data on both sides.

    sales columns: item_id, sold_date (date-like), sold_price_usd.
    Returns one row per labeled example with the explicit prediction_moment,
    both window medians, the ratio, and the boolean label.
    """
    frame = sales.copy()
    frame["sold_date"] = pd.to_datetime(frame["sold_date"]).dt.date
    if frame.empty:
        raise ValueError("no sales to label; refusing to write an empty label set silently")

    moments = prediction_moments(frame["sold_date"].min(), frame["sold_date"].max(), config)
    if not moments:
        raise ValueError(
            "data coverage too short for even one prediction moment: need "
            f"{config.baseline_window_days}d baseline + {config.outcome_window_days}d outcome"
        )

    rows: list[dict] = []
    skipped_thin = 0
    for item_id, group in frame.groupby("item_id"):
        dates = group["sold_date"]
        prices = group["sold_price_usd"]
        for moment in moments:
            baseline_start = moment - datetime.timedelta(days=config.baseline_window_days)
            outcome_end = moment + datetime.timedelta(days=config.outcome_window_days)
            baseline = prices[(dates > baseline_start) & (dates <= moment)]
            outcome = prices[(dates > moment) & (dates <= outcome_end)]
            if len(baseline) < config.min_baseline_sales or len(outcome) < config.min_outcome_sales:
                skipped_thin += 1
                continue
            baseline_price = float(baseline.median())
            outcome_price = float(outcome.median())
            ratio = outcome_price / baseline_price if baseline_price > 0 else float("nan")
            rows.append(
                {
                    "item_id": item_id,
                    "prediction_moment": moment,
                    "baseline_price_usd": round(baseline_price, 2),
                    "outcome_price_usd": round(outcome_price, 2),
                    "appreciation_ratio": round(ratio, 4),
                    "label": ratio >= config.appreciation_threshold,
                    "n_baseline_sales": int(len(baseline)),
                    "n_outcome_sales": int(len(outcome)),
                }
            )

    labeled = pd.DataFrame(rows)
    logger.info(
        "labeling: %d examples across %d items, %.1f%% positive, %d thin (item, moment) pairs excluded",
        len(labeled),
        labeled["item_id"].nunique() if not labeled.empty else 0,
        100 * labeled["label"].mean() if not labeled.empty else 0.0,
        skipped_thin,
    )
    return labeled


def load_sales(source: str) -> pd.DataFrame:
    """'synth' reads the checked-in synthetic fixture; 'warehouse' reads the
    dbt mart. Synth is the default because the hand-written platform
    fixtures span three months, which cannot produce a single labelable
    example under a 90d + 180d window design, and an empty demo is useless."""
    if source == "synth":
        from ml.synth import SALES_FIXTURE

        if not SALES_FIXTURE.exists():
            raise FileNotFoundError(
                f"{SALES_FIXTURE} missing; regenerate with 'python -m ml.synth'"
            )
        return pd.DataFrame(json.loads(SALES_FIXTURE.read_text()))
    if source == "warehouse":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set; warehouse source needs the local Postgres up")
        return pd.read_sql(
            "SELECT item_id, sold_date, sold_price_usd FROM analytics.mart_item_price_history",
            url,
        )
    raise ValueError(f"unknown source {source!r}; expected 'synth' or 'warehouse'")


def _add_month(date: datetime.date) -> datetime.date:
    year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
    return datetime.date(year, month, min(date.day, 28))


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign grail labels with explicit as-of cutoffs.")
    parser.add_argument("--source", choices=["synth", "warehouse"], default="synth")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    labels = build_labels(load_sales(args.source))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(LABELS_PATH, index=False)
    logger.info("labels written to %s (config: %s)", LABELS_PATH, dataclasses.asdict(LabelConfig()))


if __name__ == "__main__":
    main()
