"""Honest evaluation for a rare-positive early-detection problem.

Accuracy is meaningless at a 5% positive rate, so the metrics that matter:
precision and recall at the default threshold, PR-AUC, and precision@k,
which is the product question ("if I flag the top k up-and-comers, how many
actually popped"). Every model number sits next to a naive baseline that
just ranks by rising search interest, because a model that can't beat
"flag whatever people are googling more" has no reason to exist.

Also here: the label-threshold sensitivity check (labels carry their raw
peer_z and uplift_edge, so re-thresholding is free) and a feature-importance
sanity read, looking for anything that smells like leakage, with a specific
look at the celebrity features whose high importance on synth is expected.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
PREDICTIONS_PATH = PROCESSED_DIR / "test_predictions.parquet"
LABELS_PATH = PROCESSED_DIR / "labels.parquet"
MODEL_INFO_PATH = PROCESSED_DIR / "model_info.json"
EVALUATION_PATH = PROCESSED_DIR / "evaluation.json"

K_VALUES = (5, 10, 20)


def precision_at_k(frame: pd.DataFrame, score_column: str, k: int) -> float:
    """Of the top-k flagged families, the fraction that actually outperformed
    their peers. The product question: flag k up-and-comers, how many popped."""
    top = frame.nlargest(k, score_column)
    return float(top["label"].mean()) if len(top) else 0.0


def recall_at_k(frame: pd.DataFrame, score_column: str, k: int) -> float:
    """Of all the families that outperformed, the fraction the top-k caught.
    Precision asks if the watchlist is clean; recall asks if it is complete."""
    total_positives = int(frame["label"].sum())
    if total_positives == 0:
        return 0.0
    top = frame.nlargest(k, score_column)
    return float(top["label"].sum() / total_positives)


def compute_metrics(predictions: pd.DataFrame) -> dict:
    """Model metrics side by side with the naive search-interest baseline.

    The baseline ranks by raw rising search interest (search_slope_60d), the
    "flag whatever people are googling more" screen. A model that cannot beat
    it on precision@k has no reason to exist, so every k reports both.
    """
    from sklearn.metrics import average_precision_score, precision_score, recall_score

    labels = predictions["label"].astype(int)
    binary = (predictions["score"] >= 0.5).astype(int)
    baseline = predictions.copy()
    baseline["baseline_score"] = baseline["search_slope_60d"].fillna(-999)

    metrics: dict = {
        "test_rows": int(len(predictions)),
        "test_positives": int(labels.sum()),
        "positive_rate": float(labels.mean()),
        "precision_at_0.5": float(precision_score(labels, binary, zero_division=0)),
        "recall_at_0.5": float(recall_score(labels, binary, zero_division=0)),
        "pr_auc": float(average_precision_score(labels, predictions["score"])),
        "pr_auc_baseline": float(average_precision_score(labels, baseline["baseline_score"])),
    }
    for k in K_VALUES:
        metrics[f"precision_at_{k}"] = precision_at_k(predictions, "score", k)
        metrics[f"precision_at_{k}_baseline"] = precision_at_k(baseline, "baseline_score", k)
        metrics[f"recall_at_{k}"] = recall_at_k(predictions, "score", k)
        metrics[f"recall_at_{k}_baseline"] = recall_at_k(baseline, "baseline_score", k)
    return metrics


Z_THRESHOLDS = (1.5, 2.0, 2.5)
EDGE_THRESHOLDS = (0.10, 0.15, 0.25)


def threshold_sensitivity(labels: pd.DataFrame) -> dict[str, int]:
    """Positive counts under alternative label thresholds.

    The label calls a family positive at peer_z >= 2.0 and uplift_edge >=
    0.15. Both knobs are judgment calls. Because the label stores the raw z
    and edge per row, this is pure re-thresholding, not recomputation: for
    each (z, edge) pair, count the peer-measurable rows that clear both. The
    default cell (z2.0, edge0.15) can sit slightly above the labeled count,
    because re-thresholding does not re-apply the sales-count and spread
    floors the labeler also enforces; that gap is stated, not hidden.
    Counts that swing wildly would mean the watchlist sits on a knife edge.
    """
    measurable = labels.dropna(subset=["peer_z"])
    counts: dict[str, int] = {"peer_measurable_rows": int(len(measurable))}
    for z in Z_THRESHOLDS:
        for edge in EDGE_THRESHOLDS:
            hit = (measurable["peer_z"] >= z) & (measurable["uplift_edge"] >= edge)
            counts[f"z{z}_edge{edge}"] = int(hit.sum())
    return counts


CELEBRITY_FEATURES = ("celebrity_event_count_90d", "celebrity_recency_days")


def celebrity_importance_read(feature_importance: dict[str, float]) -> dict:
    """The celebrity features get a specific look, because on synth the
    events are planted to lead grail inflections, so high importance is
    EXPECTED and is a mechanics check, not a market finding. This surfaces
    their rank and share so the write-up can say so plainly."""
    ranked = sorted(feature_importance.items(), key=lambda kv: -kv[1])
    total = sum(feature_importance.values()) or 1
    order = {name: rank for rank, (name, _) in enumerate(ranked, start=1)}
    return {
        name: {
            "importance": float(feature_importance.get(name, 0.0)),
            "share": float(feature_importance.get(name, 0.0) / total),
            "rank": order.get(name),
        }
        for name in CELEBRITY_FEATURES
    }


def leakage_smell_test(feature_importance: dict[str, float]) -> list[str]:
    """Domain sanity: warnings, not verdicts, for a human to read."""
    warnings = []
    ranked = sorted(feature_importance.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] == 0:
        warnings.append("model learned nothing: all importances zero")
        return warnings
    top_name, top_value = ranked[0]
    total = sum(v for _, v in ranked) or 1
    if top_value / total > 0.8:
        warnings.append(
            f"'{top_name}' carries {top_value / total:.0%} of total importance; "
            "a single dominant feature this strong deserves a leak audit"
        )
    for static in ("brand", "category", "collab_flag", "archive_flag"):
        if ranked[0][0] == static:
            warnings.append(
                f"a static attribute ('{static}') should not be the top signal for a "
                "time-sensitive target; check for label imbalance by group"
            )
    return warnings


def evaluate() -> dict:
    predictions = pd.read_parquet(PREDICTIONS_PATH)
    labels = pd.read_parquet(LABELS_PATH)
    model_info = json.loads(MODEL_INFO_PATH.read_text())

    report = {
        "metrics": compute_metrics(predictions),
        "threshold_sensitivity": threshold_sensitivity(labels),
        "feature_importance": model_info["feature_importance"],
        "celebrity_importance": celebrity_importance_read(model_info["feature_importance"]),
        "leakage_warnings": leakage_smell_test(model_info["feature_importance"]),
        "split_date": model_info["split_date"],
        "data_kind": "synthetic",  # flips to 'live' when real history trains the model
    }
    EVALUATION_PATH.write_text(json.dumps(report, indent=2))
    logger.info("evaluation written to %s", EVALUATION_PATH)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    report = evaluate()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
