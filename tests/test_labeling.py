"""Labeling tests. The leak tests are the ones that matter.

A labeling bug that peeks past the prediction moment produces a model that
looks brilliant and predicts nothing, so the future-invariance tests here
are the phase's real deliverable: baseline must be blind to the future,
outcome must be blind to the past.
"""
from __future__ import annotations

import datetime

import pandas as pd
import pytest

from ml.label import LabelConfig, build_labels, prediction_moments
from ml.synth import SynthConfig, generate

CONFIG = LabelConfig()


def _sales(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["item_id", "sold_date", "sold_price_usd"])


def _monthly_series(item_id: str, start: str, months: int, price_fn) -> list[tuple[str, str, float]]:
    """Two sales a month so min_baseline_sales/min_outcome_sales are satisfied."""
    first = datetime.date.fromisoformat(start)
    rows = []
    for month in range(months):
        for day in (5, 20):
            date = _shift_months(first, month).replace(day=day)
            rows.append((item_id, date.isoformat(), float(price_fn(month))))
    return rows


def _shift_months(date: datetime.date, months: int) -> datetime.date:
    year = date.year + (date.month - 1 + months) // 12
    month = (date.month - 1 + months) % 12 + 1
    return date.replace(year=year, month=month)


def test_flat_item_never_labels_positive() -> None:
    labels = build_labels(_sales(_monthly_series("flat-1", "2024-01-01", 24, lambda m: 500.0)))
    assert not labels.empty
    assert not labels["label"].any()


def test_step_change_labels_positive_at_the_right_moments() -> None:
    # 500 for a year, then 1100 for a year: a clean 2.2x step at month 12.
    labels = build_labels(
        _sales(_monthly_series("step-1", "2024-01-01", 24, lambda m: 500.0 if m < 12 else 1100.0))
    )
    positives = labels[labels["label"]]
    assert not positives.empty
    step_date = datetime.date(2025, 1, 5)
    for moment in positives["prediction_moment"]:
        # a positive moment must be able to SEE the step in its outcome window
        assert moment < step_date + datetime.timedelta(days=CONFIG.outcome_window_days)
    # moments long after the step see 1100 -> 1100: appreciation over, label 0
    late = labels[labels["prediction_moment"] >= datetime.date(2025, 6, 1)]
    assert not late["label"].any(), "an already-inflated item is not an up-and-comer"


def test_thin_items_are_excluded_not_labeled_negative() -> None:
    thin = [("thin-1", "2024-06-05", 400.0), ("thin-1", "2025-06-05", 900.0)]
    rich = _monthly_series("rich-1", "2024-01-01", 24, lambda m: 500.0)
    labels = build_labels(_sales(thin + rich))
    assert "thin-1" not in set(labels["item_id"]), "one sale per window is unmeasurable, not negative"


def test_baseline_is_invariant_to_the_future() -> None:
    """THE leak test: changing post-T data must not move any pre-T quantity."""
    base_rows = _monthly_series("leak-1", "2024-01-01", 24, lambda m: 500.0)
    moment = datetime.date(2025, 1, 1)
    spiked = [
        (item, date, price * 10 if datetime.date.fromisoformat(date) > moment else price)
        for item, date, price in base_rows
    ]
    before = build_labels(_sales(base_rows)).set_index(["item_id", "prediction_moment"])
    after = build_labels(_sales(spiked)).set_index(["item_id", "prediction_moment"])
    shared = before.index.intersection(after.index)
    at_moment = [ix for ix in shared if ix[1] == moment]
    assert at_moment, "the probed moment must exist in both label sets"
    for ix in at_moment:
        assert before.loc[ix, "baseline_price_usd"] == after.loc[ix, "baseline_price_usd"], (
            "baseline moved when only post-cutoff data changed: look-ahead leak"
        )
    # and the outcome DID change, proving the probe actually probed
    assert (after.loc[at_moment, "outcome_price_usd"].values
            != before.loc[at_moment, "outcome_price_usd"].values).all()


def test_outcome_is_invariant_to_the_past() -> None:
    base_rows = _monthly_series("leak-2", "2024-01-01", 24, lambda m: 500.0)
    moment = datetime.date(2025, 1, 1)
    rewritten = [
        (item, date, price * 3 if datetime.date.fromisoformat(date) <= moment else price)
        for item, date, price in base_rows
    ]
    before = build_labels(_sales(base_rows)).set_index(["item_id", "prediction_moment"])
    after = build_labels(_sales(rewritten)).set_index(["item_id", "prediction_moment"])
    at_moment = [ix for ix in before.index.intersection(after.index) if ix[1] == moment]
    assert at_moment
    for ix in at_moment:
        assert before.loc[ix, "outcome_price_usd"] == after.loc[ix, "outcome_price_usd"]


def test_every_moment_has_full_windows_inside_coverage() -> None:
    moments = prediction_moments(datetime.date(2024, 1, 1), datetime.date(2026, 1, 1), CONFIG)
    assert moments
    for moment in moments:
        assert moment - datetime.timedelta(days=CONFIG.baseline_window_days) >= datetime.date(2024, 1, 1)
        assert moment + datetime.timedelta(days=CONFIG.outcome_window_days) <= datetime.date(2026, 1, 1)


def test_coverage_too_short_fails_loudly() -> None:
    with pytest.raises(ValueError, match="coverage too short"):
        build_labels(_sales([("x", "2026-01-01", 100.0), ("x", "2026-02-01", 100.0)]))


# --- synthetic generator ---

def test_synth_is_deterministic() -> None:
    assert generate(SynthConfig()) == generate(SynthConfig())


def test_synth_attention_leads_price_for_grail_items() -> None:
    """The generator must encode the core hypothesis: for grail items,
    attention is already elevated in the window before the price inflection."""
    import datetime as dt
    import statistics

    items, _, attention = generate(SynthConfig())
    by_item: dict[str, list[dict]] = {}
    for row in attention:
        by_item.setdefault(row["item_id"], []).append(row)
    grail_items = [i for i in items if i["regime"] == "grail"]
    assert grail_items
    for item in grail_items:
        inflection = dt.date.fromisoformat(item["inflection_date"])
        series = by_item[item["item_id"]]
        pre_window = [r["search_interest"] for r in series
                      if inflection - dt.timedelta(days=30) <= dt.date.fromisoformat(r["week_date"]) < inflection]
        long_before = [r["search_interest"] for r in series
                       if dt.date.fromisoformat(r["week_date"]) < inflection - dt.timedelta(days=120)]
        if pre_window and long_before:
            assert statistics.mean(pre_window) > statistics.mean(long_before), (
                f"{item['item_id']}: attention did not lead the inflection"
            )


def test_synth_labels_land_only_on_grail_regime_items() -> None:
    items, sales, _ = generate(SynthConfig())
    regimes = {item["item_id"]: item["regime"] for item in items}
    labels = build_labels(pd.DataFrame(sales))
    labels["regime"] = labels["item_id"].map(regimes)
    positives = labels[labels["label"]]
    assert not positives.empty
    assert set(positives["regime"]) == {"grail"}, (
        "a flat or drift item labeled positive means the threshold is inside noise range"
    )
    rate = labels["label"].mean()
    assert 0.01 <= rate <= 0.20, f"positive rate {rate:.3f} should look like a rare-event problem"
