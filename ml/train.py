"""Train the deliberately boring model on the validated feature set.

The model is LightGBM with near-default parameters, and that is the point:
the story of this project is the clean canonical catalog, the honest label,
and the leak-proof features. A clever architecture on top of those would
add variance, not credibility.

The split is by TIME, never random. Train on earlier prediction moments,
test on later ones. A random split would put a January observation of an
item in train and its March observation in test: the model would be graded
on items whose future it partially saw, and the score would be a pleasant
lie. The split boundary is logged to MLflow along with everything else.

Tracking: MLflow via MLFLOW_TRACKING_URI. The real setup is the local
Postgres (postgresql+psycopg2://grail:grail@localhost:5432/grail), the same
disposable warehouse docker-compose already runs, which is a proper backend
rather than sqlite scratch, and it is where models get registered. Without
the env var it falls back to a local sqlite file purely so credential-free
fixture runs work anywhere (MLflow 3.x retired the old file store);
registration is skipped loudly on the fallback.
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
FEATURES_PATH = PROCESSED_DIR / "features"
PREDICTIONS_PATH = PROCESSED_DIR / "test_predictions.parquet"
MODEL_INFO_PATH = PROCESSED_DIR / "model_info.json"

CATEGORICAL_FEATURES = ("brand", "category")
NUMERIC_FEATURES = (
    "price_momentum_90d",
    "sold_velocity_30d",
    "spread_at_cutoff",
    "spread_trend",
    "search_slope_60d",
    "search_accel",
    "social_velocity_60d",
    "collab_flag",
    "archive_flag",
)
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class TrainConfig:
    split_date: datetime.date = datetime.date(2025, 7, 1)
    n_estimators: int = 300
    learning_rate: float = 0.05
    num_leaves: int = 15  # small trees: few features, few hundred rows
    seed: int = 7


def time_split(frame: pd.DataFrame, split_date: datetime.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Earlier moments train, later moments test. No overlap, by definition."""
    moments = pd.to_datetime(frame["prediction_moment"]).dt.date
    train = frame[moments < split_date]
    test = frame[moments >= split_date]
    if train.empty or test.empty:
        raise ValueError(
            f"split at {split_date} leaves train={len(train)} test={len(test)}; "
            "pick a boundary inside the data coverage"
        )
    if not train["label"].any():
        raise ValueError("no positive examples before the split; the model would learn nothing")
    return train, test


def prepare_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    matrix = frame[list(FEATURE_COLUMNS)].copy()
    for column in CATEGORICAL_FEATURES:
        matrix[column] = matrix[column].astype("category")
    for column in ("collab_flag", "archive_flag"):
        matrix[column] = matrix[column].astype(int)
    return matrix


def train(config: TrainConfig = TrainConfig()) -> dict:
    import lightgbm as lgb
    import mlflow

    features = pd.read_parquet(FEATURES_PATH)
    train_frame, test_frame = time_split(features, config.split_date)
    logger.info(
        "time split at %s: train %d rows (%d pos), test %d rows (%d pos)",
        config.split_date, len(train_frame), int(train_frame["label"].sum()),
        len(test_frame), int(test_frame["label"].sum()),
    )

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{ROOT / 'mlruns.db'}")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("grail-predictor")

    model = lgb.LGBMClassifier(
        n_estimators=config.n_estimators,
        learning_rate=config.learning_rate,
        num_leaves=config.num_leaves,
        is_unbalance=True,  # positives are ~5% of examples; see docs/labeling.md
        random_state=config.seed,
        verbose=-1,
    )

    with mlflow.start_run() as run:
        mlflow.log_params(dataclasses.asdict(config) | {
            "model": "LGBMClassifier",
            "features": ",".join(FEATURE_COLUMNS),
            "train_rows": len(train_frame),
            "test_rows": len(test_frame),
            "train_positives": int(train_frame["label"].sum()),
            "test_positives": int(test_frame["label"].sum()),
        })
        model.fit(prepare_matrix(train_frame), train_frame["label"])

        scores = model.predict_proba(prepare_matrix(test_frame))[:, 1]
        predictions = test_frame[["item_id", "prediction_moment", "label",
                                  "search_slope_60d", "price_momentum_90d"]].copy()
        predictions["score"] = scores
        predictions.to_parquet(PREDICTIONS_PATH, index=False)

        from ml.evaluate import compute_metrics

        metrics = compute_metrics(predictions)
        mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})
        mlflow.lightgbm.log_model(model, name="model")

        registered = False
        if tracking_uri.startswith("postgresql"):
            mlflow.register_model(f"runs:/{run.info.run_id}/model", "grail-predictor")
            registered = True
        else:
            logger.warning(
                "fallback tracking (%s): registration happens on the Postgres backend, skipping here",
                tracking_uri,
            )

        info = {
            "run_id": run.info.run_id,
            "tracking_uri": tracking_uri,
            "registered": registered,
            "split_date": config.split_date.isoformat(),
            "feature_importance": dict(
                sorted(
                    zip(FEATURE_COLUMNS, model.feature_importances_.tolist()),
                    key=lambda kv: -kv[1],
                )
            ),
            "metrics": metrics,
        }
        MODEL_INFO_PATH.write_text(json.dumps(info, indent=2))
        logger.info("run %s logged to %s", run.info.run_id, tracking_uri)
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the grail model with a time split.")
    parser.add_argument("--split-date", type=datetime.date.fromisoformat,
                        default=TrainConfig.split_date)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    info = train(TrainConfig(split_date=args.split_date))
    print(json.dumps(info["metrics"], indent=2))


if __name__ == "__main__":
    main()
