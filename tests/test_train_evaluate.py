"""Train and evaluate tests: the time split and the ranking metrics.

The heavy pieces (LightGBM fit, MLflow logging) are exercised by actually
running the phase, not mocked here. What these pin is the logic that would
quietly lie if it broke: the split must never leak a family's future into
its past, and the metrics must reward a clean, complete watchlist and beat
the naive baseline the model has to justify itself against.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from ml.evaluate import (precision_at_k, recall_at_k, threshold_sensitivity)
from ml.train import TrainConfig, time_split


def _rows(moment: str, labels: list[bool]) -> list[dict]:
    return [{"family_id": f"fam-{i}", "prediction_moment": datetime.date.fromisoformat(moment),
             "label": label} for i, label in enumerate(labels)]


def test_time_split_separates_by_moment() -> None:
    frame = pd.DataFrame(
        _rows("2025-01-01", [True, False]) + _rows("2025-12-01", [True, False])
    )
    train, test = time_split(frame, datetime.date(2025, 6, 1))
    assert (pd.to_datetime(train["prediction_moment"]).dt.date < datetime.date(2025, 6, 1)).all()
    assert (pd.to_datetime(test["prediction_moment"]).dt.date >= datetime.date(2025, 6, 1)).all()
    assert len(train) + len(test) == len(frame)


def test_time_split_rejects_empty_test_side() -> None:
    frame = pd.DataFrame(_rows("2025-01-01", [True, False]))
    with pytest.raises(ValueError):
        time_split(frame, datetime.date(2025, 6, 1))


def test_time_split_rejects_no_train_positives() -> None:
    frame = pd.DataFrame(
        _rows("2025-01-01", [False, False]) + _rows("2025-12-01", [True, False])
    )
    with pytest.raises(ValueError, match="no positive"):
        time_split(frame, datetime.date(2025, 6, 1))


def test_default_split_is_inside_coverage() -> None:
    """The pinned default must sit between the first and last moment or the
    phase cannot run; a cheap guard against a config drift breaking train."""
    assert datetime.date(2024, 9, 1) < TrainConfig().split_date < datetime.date(2026, 5, 1)


def _predictions(scores: list[float], labels: list[bool]) -> pd.DataFrame:
    return pd.DataFrame({"score": scores, "label": labels})


def test_precision_at_k_reads_the_top() -> None:
    # top 2 by score are one hit, one miss -> 0.5
    frame = _predictions([0.9, 0.8, 0.1, 0.05], [True, False, True, False])
    assert precision_at_k(frame, "score", 2) == 0.5
    assert precision_at_k(frame, "score", 4) == 0.5


def test_recall_at_k_measures_completeness() -> None:
    # two positives total; top 2 catches one of them -> 0.5
    frame = _predictions([0.9, 0.8, 0.1, 0.7], [True, False, True, False])
    assert recall_at_k(frame, "score", 2) == 0.5
    assert recall_at_k(frame, "score", 4) == 1.0


def test_recall_at_k_no_positives_is_zero() -> None:
    frame = _predictions([0.9, 0.1], [False, False])
    assert recall_at_k(frame, "score", 1) == 0.0


def test_threshold_sensitivity_is_pure_rethresholding() -> None:
    labels = pd.DataFrame({
        "peer_z": [3.0, 2.2, 1.6, 0.5, None],
        "uplift_edge": [0.30, 0.16, 0.12, 0.40, 0.90],
    })
    counts = threshold_sensitivity(labels)
    # the null-z row is peer-unmeasurable and drops out
    assert counts["peer_measurable_rows"] == 4
    # strict cell: z>=2.5 and edge>=0.25 -> only the first row
    assert counts["z2.5_edge0.25"] == 1
    # loosest cell: z>=1.5 and edge>=0.10 -> first three
    assert counts["z1.5_edge0.1"] == 3
    # tightening z never adds positives
    assert counts["z2.5_edge0.1"] <= counts["z1.5_edge0.1"]
