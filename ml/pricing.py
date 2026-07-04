"""Pricing and hold-or-move: the thin layer riding on the demand signal.

No elasticity model in v1, on purpose. Two questions for a flagged family:
what is a piece worth right now, and should the reseller hold it or move it.

The worth estimate is comps, not a model. The median of recent sold prices at
the family and colorway-tier grain, condition-adjusted to a reference grade,
returned as a range because a single number would be false precision. The
condition adjustment is a real-data mechanism: the synthetic sales carry no
condition, so on synth it is a no-op, stated here and in docs/backtest.md.

The hold-or-move flag is driven by the demand signal from the trained model,
not by any price forecast. While the predicted peer uplift holds up and the
piece has not aged out, hold. When the signal fades below a stated floor or
the piece ages past a stated horizon, move: mark it toward the comp median to
clear. That is the decision Phase 8's simulated sell calls at every step.

The as-of rule holds here too. A worth estimate dated T reads only sales at or
before T; the backtest leans on that and tests/test_pricing.py pins it.
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PricingConfig:
    comp_window_days: int = 60      # trailing window for the comp median
    min_comps: int = 3              # below this the estimate is None, not a guess
    move_score_threshold: float = 0.5   # demand signal below this reads as faded
    max_hold_days: int = 120        # ride a winner this long, then clear it
    condition_reference: int = 5    # the ordinal (5 best) a worth estimate quotes at
    condition_step_discount: float = 0.08  # each grade below reference costs ~8%


@dataclass(frozen=True)
class WorthEstimate:
    """A range, not a point. median is the number to quote; low and high are
    the 25th and 75th percentiles of the comps, the honest spread."""

    median: float
    low: float
    high: float
    n_comps: int
    as_of: str


@dataclass(frozen=True)
class HoldMove:
    action: str  # "hold" or "move"
    reason: str


def condition_factor(ordinal: int | None, config: PricingConfig) -> float:
    """How much a piece in this condition sells for against a reference-grade
    one. Grade 5 is 1.0, each grade down knocks off condition_step_discount,
    floored so a battered piece is not free. Unknown condition is treated as
    reference (no adjustment) rather than guessed."""
    if ordinal is None:
        return 1.0
    steps = config.condition_reference - int(ordinal)
    return max(0.4, 1.0 - config.condition_step_discount * steps)


def worth_estimate(
    sales,
    family_id: str,
    as_of: datetime.date,
    config: PricingConfig = PricingConfig(),
    colorway_tier: str | None = None,
) -> WorthEstimate | None:
    """Median and 25th/75th of the family's comps in the trailing window
    ending at as_of, condition-adjusted to the reference grade.

    sales is a DataFrame with family_id, sold_date, sold_price_usd,
    colorway_tier, and optionally condition_ordinal. tier None pools every
    tier, which is the stable choice for a transaction price; pass a tier for
    the per-colorway worth question. Returns None below min_comps, because a
    price off one or two sales is a guess wearing a number's clothes.
    """
    import numpy as np
    import pandas as pd

    df = sales[sales["family_id"] == family_id]
    if colorway_tier is not None:
        df = df[df["colorway_tier"] == colorway_tier]
    if df.empty:
        return None

    sold = pd.to_datetime(df["sold_date"]).dt.date
    window_start = as_of - datetime.timedelta(days=config.comp_window_days)
    df = df[(sold > window_start).to_numpy() & (sold <= as_of).to_numpy()]
    if len(df) < config.min_comps:
        return None

    prices = df["sold_price_usd"].astype(float)
    if "condition_ordinal" in df.columns:
        factors = df["condition_ordinal"].map(lambda o: condition_factor(o, config))
        prices = prices / factors.to_numpy()  # normalize each comp to reference grade

    values = prices.to_numpy()
    return WorthEstimate(
        median=float(np.median(values)),
        low=float(np.percentile(values, 25)),
        high=float(np.percentile(values, 75)),
        n_comps=int(len(values)),
        as_of=as_of.isoformat(),
    )


def hold_or_move(
    demand_signal: float | None,
    days_held: int,
    config: PricingConfig = PricingConfig(),
) -> HoldMove:
    """The sell decision, driven by the demand signal (the model's predicted
    peer uplift), never by a price forecast.

    Age wins first: past the horizon, clear it regardless of signal, because
    capital tied up in a piece that has not moved is the reseller's real cost.
    Then the signal: no signal to justify a hold is itself a reason to move,
    and a signal below the fade floor means the run the flag was betting on is
    over. Otherwise hold and let it run.
    """
    if days_held >= config.max_hold_days:
        return HoldMove("move", f"aged past the {config.max_hold_days}-day hold horizon")
    if demand_signal is None:
        return HoldMove("move", "no current demand signal to justify holding")
    if demand_signal < config.move_score_threshold:
        return HoldMove("move", f"demand faded below {config.move_score_threshold:.2f}")
    return HoldMove("hold", "demand still rising, ride it")
