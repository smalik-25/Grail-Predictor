"""Decision backtest tests: the buy decision is as-of, the sell decision
reads the future on purpose, and the policy comparison is apples-to-apples.

The one that matters most is the leak guard: the watchlist at a cutoff must be
bit-identical whether or not the future exists, because the whole pitch is
that a reseller could have acted on it at the time. The rest pin the flagging
gates, the sell trigger, the refusal to buy what cannot be priced, and the
buy-nothing floor.
"""
from __future__ import annotations

import datetime

import pandas as pd

from ml.backtest import (BacktestConfig, Trade, _pick_sell, simulate_trade,
                        summarize, watchlist)
from ml.pricing import PricingConfig

CUTOFF = datetime.date(2025, 9, 1)
FUTURE = [datetime.date(2025, 10, 1), datetime.date(2025, 11, 1), datetime.date(2025, 12, 1)]


def _candidates(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_watchlist_gates_each_policy_by_its_own_flag() -> None:
    candidates = _candidates([
        {"family_id": "a", "score": 0.9, "search_slope_60d": 0.5},
        {"family_id": "b", "score": 0.6, "search_slope_60d": -0.1},
        {"family_id": "c", "score": 0.3, "search_slope_60d": 0.2},
        {"family_id": "d", "score": 0.1, "search_slope_60d": 0.0},
    ])
    config = BacktestConfig(k=5, min_score=0.5)
    assert watchlist(candidates, "model", config) == ["a", "b"]     # score >= 0.5
    assert watchlist(candidates, "search", config) == ["a", "c"]    # slope > 0, ranked
    assert watchlist(candidates, "none", config) == []


def test_watchlist_is_capped_at_k() -> None:
    candidates = _candidates([
        {"family_id": f"f{i}", "score": 0.9, "search_slope_60d": 1.0} for i in range(10)
    ])
    assert len(watchlist(candidates, "model", BacktestConfig(k=3))) == 3


def test_watchlist_is_as_of_immune_to_the_future() -> None:
    """The leak guard. The cutoff watchlist reads only rows dated at the
    cutoff, so inventing a wild future row cannot change what would have been
    bought. This mirrors run(), which filters candidates to the cutoff first."""
    predictions = pd.DataFrame([
        {"family_id": "a", "cutoff": CUTOFF, "score": 0.9, "search_slope_60d": 0.5},
        {"family_id": "b", "cutoff": CUTOFF, "score": 0.6, "search_slope_60d": 0.1},
    ])
    config = BacktestConfig(k=5)
    honest = watchlist(predictions[predictions["cutoff"] == CUTOFF], "model", config)

    future = pd.concat([predictions, pd.DataFrame([
        {"family_id": "z", "cutoff": datetime.date(2025, 12, 1), "score": 1.0,
         "search_slope_60d": 99.0}])], ignore_index=True)
    leaky = watchlist(future[future["cutoff"] == CUTOFF], "model", config)
    assert honest == leaky == ["a", "b"]


def test_pick_sell_triggers_on_a_faded_signal() -> None:
    lookup = {("f", FUTURE[0]): 0.9, ("f", FUTURE[1]): 0.1}
    sell_date, days, on_fade = _pick_sell("f", CUTOFF, lookup, FUTURE, PricingConfig())
    assert sell_date == FUTURE[1] and days == 61 and on_fade is True


def test_pick_sell_falls_back_to_the_horizon_when_signal_never_fades() -> None:
    lookup = {(("f", m)): 0.9 for m in FUTURE}  # never drops below the floor
    config = PricingConfig(max_hold_days=120)
    sell_date, days, on_fade = _pick_sell("f", CUTOFF, lookup, FUTURE, config)
    assert days == 120 and on_fade is False
    assert sell_date == CUTOFF + datetime.timedelta(days=120)


def test_simulate_trade_refuses_what_it_cannot_price() -> None:
    """No comps at the cutoff means no worth estimate, which means no buy."""
    sales = pd.DataFrame([{"family_id": "other", "sold_date": "2025-08-15",
                           "sold_price_usd": 100.0, "colorway_tier": "core"}])
    trade = simulate_trade("model", "f", CUTOFF, True, sales, {}, FUTURE,
                           BacktestConfig(), datetime.date(2026, 6, 30))
    assert trade is None


def test_summarize_floor_is_zero_for_buy_nothing() -> None:
    assert summarize([]) == {
        "n_trades": 0, "total_net_margin": 0.0, "mean_net_margin": 0.0,
        "mean_return_pct": 0.0, "watchlist_precision": 0.0,
        "mean_days_to_sell": 0.0, "mean_price_realization": 0.0,
        "sold_on_fade_rate": 0.0,
    }


def test_summarize_aggregates_reseller_terms() -> None:
    trades = [
        Trade("model", "2025-09-01", "a", 100.0, 200.0, "2025-11-01", 61, True, True, 76.0, 0.76),
        Trade("model", "2025-09-01", "b", 100.0, 100.0, "2025-10-01", 30, True, False, -12.0, -0.12),
    ]
    out = summarize(trades)
    assert out["n_trades"] == 2
    assert out["watchlist_precision"] == 0.5      # one of two outperformed
    assert out["total_net_margin"] == 64.0
