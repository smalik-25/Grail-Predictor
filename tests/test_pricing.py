"""Pricing layer tests: the worth estimate and the hold-or-move flag.

The worth estimate is a comp median with an as-of rule, so the tests pin the
window, the min-comps refusal, the condition normalization, and the leak: a
sale after the as-of date must not move the number. The flag tests pin the
three ways a piece gets moved, age, faded signal, and no signal, against the
one way it gets held.
"""
from __future__ import annotations

import datetime

import pandas as pd

from ml.pricing import (PricingConfig, condition_factor, hold_or_move,
                        worth_estimate)

AS_OF = datetime.date(2025, 6, 1)


def _sales(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _sale(day_offset: int, price: float, tier: str = "core", condition: int | None = None) -> dict:
    row = {"family_id": "fam-1",
           "sold_date": (AS_OF - datetime.timedelta(days=day_offset)).isoformat(),
           "sold_price_usd": price, "colorway_tier": tier}
    if condition is not None:
        row["condition_ordinal"] = condition
    return row


def test_worth_estimate_is_the_windowed_comp_median() -> None:
    sales = _sales([_sale(10, 100), _sale(20, 200), _sale(30, 300)])
    est = worth_estimate(sales, "fam-1", AS_OF)
    assert est is not None
    assert est.median == 200.0
    assert est.low == 150.0 and est.high == 250.0
    assert est.n_comps == 3


def test_worth_estimate_ignores_sales_after_as_of() -> None:
    """The leak: a spike sold the day after the cutoff must not touch it."""
    honest = _sales([_sale(10, 100), _sale(20, 200), _sale(30, 300)])
    leaky = _sales([_sale(10, 100), _sale(20, 200), _sale(30, 300),
                    _sale(-1, 99999)])  # sold one day AFTER as_of
    assert worth_estimate(honest, "fam-1", AS_OF) == worth_estimate(leaky, "fam-1", AS_OF)


def test_worth_estimate_drops_stale_comps_outside_the_window() -> None:
    config = PricingConfig(comp_window_days=60)
    sales = _sales([_sale(10, 100), _sale(20, 200), _sale(90, 300)])  # 90d is stale
    est = worth_estimate(sales, "fam-1", AS_OF, config)
    assert est is None  # only two comps left inside the window, below min_comps


def test_worth_estimate_refuses_below_min_comps() -> None:
    sales = _sales([_sale(10, 100), _sale(20, 200)])
    assert worth_estimate(sales, "fam-1", AS_OF) is None


def test_worth_estimate_filters_by_tier() -> None:
    sales = _sales([_sale(5, 100, "core"), _sale(6, 110, "core"), _sale(7, 120, "core"),
                    _sale(8, 900, "rare"), _sale(9, 950, "rare"), _sale(10, 1000, "rare")])
    core = worth_estimate(sales, "fam-1", AS_OF, colorway_tier="core")
    rare = worth_estimate(sales, "fam-1", AS_OF, colorway_tier="rare")
    assert core.median == 110.0 and rare.median == 950.0


def test_condition_factor_scales_down_from_reference() -> None:
    config = PricingConfig(condition_reference=5, condition_step_discount=0.08)
    assert condition_factor(5, config) == 1.0
    assert condition_factor(4, config) == 0.92
    assert condition_factor(None, config) == 1.0        # unknown is not guessed
    assert condition_factor(1, config) >= 0.4           # floored, never free


def test_worth_estimate_normalizes_condition_to_reference() -> None:
    # three identical $100 sales, one graded down; normalizing lifts it above 100
    sales = _sales([_sale(5, 100, condition=5), _sale(6, 100, condition=5),
                    _sale(7, 100, condition=3)])
    est = worth_estimate(sales, "fam-1", AS_OF)
    assert est.median == 100.0            # the two grade-5 sales sit at reference
    assert est.high > 100.0               # the grade-3 sale normalizes upward


def test_hold_or_move_rides_a_rising_signal() -> None:
    assert hold_or_move(0.9, days_held=30).action == "hold"


def test_hold_or_move_clears_on_a_faded_signal() -> None:
    assert hold_or_move(0.1, days_held=30).action == "move"


def test_hold_or_move_clears_at_the_horizon_regardless_of_signal() -> None:
    config = PricingConfig(max_hold_days=120)
    assert hold_or_move(0.99, days_held=120, config=config).action == "move"


def test_hold_or_move_clears_with_no_signal() -> None:
    assert hold_or_move(None, days_held=30).action == "move"
