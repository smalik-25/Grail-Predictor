"""Decision backtest: if a reseller had acted on this watchlist at a
historical cutoff, what would it have done to their money.

This is the headline, not RMSE. It rebuilds the watchlist exactly as it would
have looked at each cutoff, using only the model's as-of scores from the test
split (moments the model never trained on, so every score is a real
out-of-sample call), simulates a stated buying policy, and reports the result
in the terms a reseller feels: gross margin, days to sell, price realization,
and the watchlist's precision as a buying signal.

The buy decision is strictly as-of: the watchlist at cutoff T is the top-k
families by score among the rows dated T, and adding data after T cannot
change it (tests/test_backtest.py pins this). The sell decision and the
realized prices legitimately read the future, because that is the outcome
being measured, the same line the label already draws between what defines
the answer and what the model may see.

Three policies, compared apples-to-apples over the same cutoffs with the same
sell logic, so the only thing that varies is the buy decision:
  - model:  buy the top-k families by model score
  - search: buy the top-k by raw rising search interest (the naive screen)
  - none:   buy nothing, the "did acting beat sitting still" anchor

Every number is synthetic. The synth market plants attention and celebrity
events to lead grail inflections, so this demonstrates the decision framework
and its mechanics, not realized market returns. docs/backtest.md says so on
every figure. The framework is the deliverable.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ml.pricing import PricingConfig, hold_or_move, worth_estimate

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
PREDICTIONS_PATH = PROCESSED_DIR / "test_predictions.parquet"
SALES_FIXTURE = ROOT / "data" / "fixtures" / "synth_family_sales.json"
BACKTEST_PATH = PROCESSED_DIR / "backtest.json"

POLICIES = ("model", "search", "none")
RANK_COLUMN = {"model": "score", "search": "search_slope_60d"}


@dataclass(frozen=True)
class BacktestConfig:
    k: int = 5                       # watchlist size cap per cutoff
    min_score: float = 0.5           # model flags a family only above this bar
    fee_rate: float = 0.12           # platform + payment, taken off the sale price
    hold_cost_per_day: float = 0.0   # storage/carry; zero on synth, a knob for real
    pricing: PricingConfig = field(default_factory=PricingConfig)


@dataclass(frozen=True)
class Trade:
    policy: str
    cutoff: str
    family_id: str
    buy_price: float
    sell_price: float
    sell_date: str
    days_held: int
    sold_on_fade: bool       # sold because demand faded, vs forced at the horizon
    was_positive: bool       # did this family actually outperform peers at buy time
    net_margin: float
    return_pct: float


def _pick_sell(family_id, cutoff, score_lookup, future_moments, pricing) -> tuple[datetime.date, int, bool]:
    """Walk the monthly grid forward from the cutoff, asking hold-or-move at
    each step with the model's score as the demand signal. Return the sell
    date, days held, and whether it sold on a fade (vs forced at the horizon)."""
    for moment in future_moments:
        days = (moment - cutoff).days
        if days <= 0:
            continue
        decision = hold_or_move(score_lookup.get((family_id, moment)), days, pricing)
        if decision.action == "move":
            return moment, days, days < pricing.max_hold_days
    # demand never faded and the grid ran out before the horizon: clear at horizon
    return cutoff + datetime.timedelta(days=pricing.max_hold_days), pricing.max_hold_days, False


def simulate_trade(
    policy, family_id, cutoff, was_positive, sales, score_lookup, future_moments, config, last_sale_date,
) -> Trade | None:
    """One buy-hold-sell round. Returns None when the family cannot be priced
    as-of the cutoff, because a piece you cannot comp is a piece you do not buy."""
    buy = worth_estimate(sales, family_id, cutoff, config.pricing)
    if buy is None:
        return None

    sell_date, days_held, sold_on_fade = _pick_sell(
        family_id, cutoff, score_lookup, future_moments, config.pricing
    )
    sell_date = min(sell_date, last_sale_date)
    sell = worth_estimate(sales, family_id, sell_date, config.pricing)
    sell_price = sell.median if sell is not None else buy.median  # no fresh comps: realize flat

    net_margin = (
        sell_price * (1.0 - config.fee_rate)
        - buy.median
        - config.hold_cost_per_day * days_held
    )
    return Trade(
        policy=policy,
        cutoff=cutoff.isoformat(),
        family_id=family_id,
        buy_price=round(buy.median, 2),
        sell_price=round(sell_price, 2),
        sell_date=sell_date.isoformat(),
        days_held=days_held,
        sold_on_fade=sold_on_fade,
        was_positive=bool(was_positive),
        net_margin=round(net_margin, 2),
        return_pct=round(net_margin / buy.median, 4),
    )


def watchlist(candidates, policy: str, config: BacktestConfig) -> list:
    """The families the policy flags and buys at this cutoff: up to k, but
    only those it actually flags. A watchlist that is forced to name k names
    every month buys junk when nothing is rising; a real one abstains.

    Each policy gates by its own honest "is this flagged" bar. The model flags
    a family whose score clears min_score, more likely than not to outperform.
    The naive screen flags anything whose raw search interest is rising at all
    (slope > 0), which is the literal "buy whatever people are googling more".
    buy-nothing flags nothing, ever.
    """
    if policy == "none":
        return []
    if policy == "model":
        flagged = candidates[candidates["score"] >= config.min_score]
    else:  # search: the naive rising-interest screen
        flagged = candidates[candidates["search_slope_60d"] > 0]
    return list(flagged.nlargest(config.k, RANK_COLUMN[policy])["family_id"])


def run(config: BacktestConfig = BacktestConfig()) -> dict:
    import pandas as pd

    predictions = pd.read_parquet(PREDICTIONS_PATH)
    predictions["cutoff"] = pd.to_datetime(predictions["prediction_moment"]).dt.date
    sales = pd.DataFrame(json.loads(SALES_FIXTURE.read_text()))
    last_sale_date = pd.to_datetime(sales["sold_date"]).dt.date.max()

    moments = sorted(predictions["cutoff"].unique())
    score_lookup = {
        (row.family_id, row.cutoff): float(row.score) for row in predictions.itertuples()
    }
    label_lookup = {
        (row.family_id, row.cutoff): bool(row.label) for row in predictions.itertuples()
    }

    # a cutoff is usable only if the full hold horizon fits inside the sales
    # coverage, else the sell price would be an extrapolation, not a comp
    horizon = datetime.timedelta(days=config.pricing.max_hold_days)
    cutoffs = [m for m in moments if m + horizon <= last_sale_date]
    dropped = [m.isoformat() for m in moments if m + horizon > last_sale_date]
    if dropped:
        logger.info("backtest: %d cutoffs dropped for short forward runway: %s",
                    len(dropped), ", ".join(dropped))

    trades: list[Trade] = []
    for policy in POLICIES:
        for cutoff in cutoffs:
            candidates = predictions[predictions["cutoff"] == cutoff]
            future_moments = [m for m in moments if m > cutoff]
            for family_id in watchlist(candidates, policy, config):
                trade = simulate_trade(
                    policy, family_id, cutoff, label_lookup.get((family_id, cutoff), False),
                    sales, score_lookup, future_moments, config, last_sale_date,
                )
                if trade is not None:
                    trades.append(trade)

    report = {
        "config": dataclasses.asdict(config),
        "data_kind": "synthetic",
        "cutoffs": [c.isoformat() for c in cutoffs],
        "policies": {p: summarize([t for t in trades if t.policy == p]) for p in POLICIES},
        "per_cutoff": {
            p: per_cutoff_breakdown([t for t in trades if t.policy == p], cutoffs)
            for p in ("model", "search")
        },
    }
    BACKTEST_PATH.write_text(json.dumps(report, indent=2))
    logger.info("backtest written to %s", BACKTEST_PATH)
    return report


def summarize(trades: list[Trade]) -> dict:
    """Per-policy aggregates in reseller terms. buy-nothing lands here with
    zero trades and zero margin, the honest floor to beat."""
    if not trades:
        return {"n_trades": 0, "total_net_margin": 0.0, "mean_net_margin": 0.0,
                "mean_return_pct": 0.0, "watchlist_precision": 0.0,
                "mean_days_to_sell": 0.0, "mean_price_realization": 0.0,
                "sold_on_fade_rate": 0.0}
    n = len(trades)
    return {
        "n_trades": n,
        "total_net_margin": round(sum(t.net_margin for t in trades), 2),
        "mean_net_margin": round(sum(t.net_margin for t in trades) / n, 2),
        "mean_return_pct": round(sum(t.return_pct for t in trades) / n, 4),
        "watchlist_precision": round(sum(t.was_positive for t in trades) / n, 4),
        "mean_days_to_sell": round(sum(t.days_held for t in trades) / n, 1),
        "mean_price_realization": round(sum(t.sell_price / t.buy_price for t in trades) / n, 4),
        "sold_on_fade_rate": round(sum(t.sold_on_fade for t in trades) / n, 4),
    }


def per_cutoff_breakdown(trades: list[Trade], cutoffs: list) -> list[dict]:
    """Margin and precision per cutoff, so the grail wave and the quiet
    periods are visible instead of blended into one average."""
    out = []
    for cutoff in cutoffs:
        rows = [t for t in trades if t.cutoff == cutoff.isoformat()]
        if not rows:
            out.append({"cutoff": cutoff.isoformat(), "n_trades": 0,
                        "total_net_margin": 0.0, "watchlist_precision": 0.0})
            continue
        out.append({
            "cutoff": cutoff.isoformat(),
            "n_trades": len(rows),
            "total_net_margin": round(sum(t.net_margin for t in rows), 2),
            "watchlist_precision": round(sum(t.was_positive for t in rows) / len(rows), 4),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Decision backtest over historical cutoffs.")
    parser.add_argument("--k", type=int, default=BacktestConfig.k, help="watchlist size per cutoff")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    report = run(BacktestConfig(k=args.k))
    print(json.dumps({"policies": report["policies"]}, indent=2))


if __name__ == "__main__":
    main()
