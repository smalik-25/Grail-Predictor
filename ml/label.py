"""The prediction target: peer-relative uplift per style-family.

A family is a positive example at prediction moment T if, over the next 60
days, it outperformed its peer set on a blended signal, not if it crossed
an absolute price threshold. Peer-relative is the point: a market-wide
swell or a whole brand rising lifts every family's absolute numbers, and a
reseller gains nothing from a watchlist that flags the tide. The synthetic
market bakes exactly that tide in, and the tests assert a uniform rise
labels nothing.

The blended uplift per family compares the outcome window (T, T+60] to the
baseline window (T-60, T], in log space, over three components:

    0.50 * log(price ratio)     achieved resale price, the money
    0.30 * log(interest ratio)  search attention, the leading signal
    0.20 * log(velocity ratio)  sales-count pace, real but noisy

A component with insufficient data drops out and its weight redistributes
over the rest, the same philosophy as the resolution matcher. The peer set
is same brand + same category + baseline price within [0.4x, 2.5x] of the
family's, at least 3 peers, falling back to brand + category before
declaring the family peer-unmeasurable (excluded and counted, not labeled).

"Statistically meaningful" is a robust z-score against the peer
distribution: (uplift - peer median) / (1.4826 * MAD), positive when
z >= 2.0 AND the raw edge over the peer median is at least 15 points.
The z alone would fire on a degenerate near-zero MAD; the edge floor
alone would fire inside noisy peer sets. Both knobs are config, and both
are documented with their reasoning in docs/labeling.md.

Thin data stays excluded, not negative: a family without 2 sales and 4
interest weeks on each side is unmeasured, and calling it "not rising"
would teach the model that illiquidity means failure.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
LABELS_PATH = PROCESSED_DIR / "labels.parquet"


@dataclass(frozen=True)
class LabelConfig:
    """Every knob pinned and documented, nothing implicit."""

    baseline_window_days: int = 60
    outcome_window_days: int = 60
    weight_price: float = 0.50
    weight_interest: float = 0.30
    weight_velocity: float = 0.20
    peer_price_band: tuple[float, float] = (0.4, 2.5)
    min_peers: int = 3
    z_threshold: float = 2.0
    min_edge: float = 0.15  # raw uplift points over the peer median
    # Floor on peer dispersion (in log-uplift units) when computing z. A
    # perfectly still peer set has MAD 0, and dividing by it would either
    # blow up or, worse, zero out the z and hide a genuine outperformer
    # behind motionless peers. Treating dispersion below 0.05 log points
    # as 0.05 keeps z finite and honest in both directions.
    min_peer_spread: float = 0.05
    min_sales_per_window: int = 2
    min_interest_weeks_per_window: int = 4
    grid_day_of_month: int = 1


def prediction_moments(
    min_date: datetime.date, max_date: datetime.date, config: LabelConfig
) -> list[datetime.date]:
    """Aligned monthly grid, clipped to full windows inside data coverage.
    A moment whose outcome window runs past the data is unknowable, not
    negative, so it is never generated."""
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


@dataclass(frozen=True)
class _FamilyWindow:
    """One family's measurable quantities around one moment."""

    family_id: str
    baseline_price: float | None
    log_uplift: float | None  # blended, None when unmeasurable
    n_baseline_sales: int
    n_outcome_sales: int


def build_labels(
    sales: pd.DataFrame,
    attention: pd.DataFrame,
    families: pd.DataFrame,
    config: LabelConfig = LabelConfig(),
) -> pd.DataFrame:
    """One row per (family, moment) that is measurable and peer-measurable.

    sales: family_id, sold_date, sold_price_usd
    attention: family_id, week_date, search_interest
    families: family_id, brand, category
    """
    sales = sales.copy()
    sales["sold_date"] = pd.to_datetime(sales["sold_date"]).dt.date
    attention = attention.copy()
    attention["week_date"] = pd.to_datetime(attention["week_date"]).dt.date
    if sales.empty:
        raise ValueError("no sales to label; refusing to write an empty label set silently")

    moments = prediction_moments(sales["sold_date"].min(), sales["sold_date"].max(), config)
    if not moments:
        raise ValueError(
            "data coverage too short for even one prediction moment: need "
            f"{config.baseline_window_days}d baseline + {config.outcome_window_days}d outcome"
        )

    meta = families.set_index("family_id")[["brand", "category"]]
    sales_by_family = dict(tuple(sales.groupby("family_id")))
    attention_by_family = dict(tuple(attention.groupby("family_id")))

    rows: list[dict] = []
    skipped_thin = 0
    skipped_peerless = 0
    for moment in moments:
        windows: dict[str, _FamilyWindow] = {}
        for family_id in meta.index:
            window = _measure_family(
                family_id,
                sales_by_family.get(family_id),
                attention_by_family.get(family_id),
                moment,
                config,
            )
            if window is not None:
                windows[family_id] = window
            else:
                skipped_thin += 1

        for family_id, window in windows.items():
            peer_result = _peer_set(family_id, window, windows, meta, config)
            if peer_result is None:
                skipped_peerless += 1
                continue
            peers, peer_basis = peer_result
            peer_uplifts = [windows[p].log_uplift for p in peers]
            peer_median = statistics.median(peer_uplifts)
            mad = statistics.median([abs(u - peer_median) for u in peer_uplifts])
            spread = max(1.4826 * mad, config.min_peer_spread)
            z = (window.log_uplift - peer_median) / spread
            edge = math.exp(window.log_uplift) - math.exp(peer_median)
            rows.append(
                {
                    "family_id": family_id,
                    "prediction_moment": moment,
                    "baseline_price_usd": round(window.baseline_price, 2),
                    "log_uplift": round(window.log_uplift, 4),
                    "peer_median_log_uplift": round(peer_median, 4),
                    "peer_z": round(z, 3),
                    "uplift_edge": round(edge, 4),
                    "n_peers": len(peers),
                    "peer_basis": peer_basis,
                    "n_baseline_sales": window.n_baseline_sales,
                    "n_outcome_sales": window.n_outcome_sales,
                    "label": bool(z >= config.z_threshold and edge >= config.min_edge),
                }
            )

    labeled = pd.DataFrame(rows)
    logger.info(
        "labeling: %d examples across %d families, %.1f%% positive, "
        "%d thin (family, moment) pairs excluded, %d peer-unmeasurable excluded",
        len(labeled),
        labeled["family_id"].nunique() if not labeled.empty else 0,
        100 * labeled["label"].mean() if not labeled.empty else 0.0,
        skipped_thin,
        skipped_peerless,
    )
    return labeled


def _measure_family(
    family_id: str,
    family_sales: pd.DataFrame | None,
    family_attention: pd.DataFrame | None,
    moment: datetime.date,
    config: LabelConfig,
) -> _FamilyWindow | None:
    """Blended log-uplift for one family at one moment, or None if thin."""
    if family_sales is None:
        return None
    baseline_start = moment - datetime.timedelta(days=config.baseline_window_days)
    outcome_end = moment + datetime.timedelta(days=config.outcome_window_days)
    dates = family_sales["sold_date"]
    prices = family_sales["sold_price_usd"]
    base_mask = (dates > baseline_start) & (dates <= moment)
    out_mask = (dates > moment) & (dates <= outcome_end)
    n_base, n_out = int(base_mask.sum()), int(out_mask.sum())
    if n_base < config.min_sales_per_window or n_out < config.min_sales_per_window:
        return None

    baseline_price = float(prices[base_mask].median())
    outcome_price = float(prices[out_mask].median())
    components: list[tuple[float, float]] = []  # (weight, log ratio)
    if baseline_price > 0:
        components.append((config.weight_price, math.log(outcome_price / baseline_price)))
    # velocity: counts over identical window lengths, smoothed for zeros
    components.append((config.weight_velocity, math.log((n_out + 1) / (n_base + 1))))

    if family_attention is not None:
        weeks = family_attention["week_date"]
        interest = family_attention["search_interest"]
        base_interest = interest[(weeks > baseline_start) & (weeks <= moment)]
        out_interest = interest[(weeks > moment) & (weeks <= outcome_end)]
        if (
            len(base_interest) >= config.min_interest_weeks_per_window
            and len(out_interest) >= config.min_interest_weeks_per_window
            and base_interest.mean() > 0
        ):
            components.append(
                (config.weight_interest, math.log(out_interest.mean() / base_interest.mean()))
            )

    total_weight = sum(weight for weight, _ in components)
    log_uplift = sum(weight * value for weight, value in components) / total_weight
    return _FamilyWindow(
        family_id=family_id,
        baseline_price=baseline_price,
        log_uplift=log_uplift,
        n_baseline_sales=n_base,
        n_outcome_sales=n_out,
    )


def _peer_set(
    family_id: str,
    window: _FamilyWindow,
    windows: dict[str, _FamilyWindow],
    meta: pd.DataFrame,
    config: LabelConfig,
) -> tuple[list[str], str] | None:
    """A fallback ladder, widest acceptable peer definition wins last:

    1. same brand + category + price band  (basis 'brand')
    2. same brand + category               (basis 'brand_wide')
    3. same category + price band, any brand (basis 'category')

    The category rung exists because brand-by-category cells are thin in
    any realistic catalog; an archive knit is still a fair peer to archive
    knits across brands for the purpose of cancelling market-wide moves.
    The basis is recorded on every labeled row so nobody has to guess how
    strict the comparison behind a label was.
    """
    brand = meta.at[family_id, "brand"]
    category = meta.at[family_id, "category"]
    low, high = config.peer_price_band

    def candidates(match_brand: bool, use_band: bool) -> list[str]:
        result = []
        for other_id, other in windows.items():
            if other_id == family_id:
                continue
            if meta.at[other_id, "category"] != category:
                continue
            if match_brand and meta.at[other_id, "brand"] != brand:
                continue
            if use_band and not (
                low * window.baseline_price <= other.baseline_price <= high * window.baseline_price
            ):
                continue
            result.append(other_id)
        return result

    for match_brand, use_band, basis in (
        (True, True, "brand"),
        (True, False, "brand_wide"),
        (False, True, "category"),
    ):
        peers = candidates(match_brand, use_band)
        if len(peers) >= config.min_peers:
            return peers, basis
    return None


def load_frames(source: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """'synth' reads the checked-in synthetic fixtures; 'warehouse' reads the
    family marts. Synth is the default: hand fixtures cannot produce a
    single labelable example under a 60d + 60d window design."""
    if source == "synth":
        from ml.synth import ATTENTION_FIXTURE, FAMILIES_FIXTURE, SALES_FIXTURE

        for path in (FAMILIES_FIXTURE, SALES_FIXTURE, ATTENTION_FIXTURE):
            if not path.exists():
                raise FileNotFoundError(f"{path} missing; regenerate with 'python -m ml.synth'")
        return (
            pd.DataFrame(json.loads(SALES_FIXTURE.read_text())),
            pd.DataFrame(json.loads(ATTENTION_FIXTURE.read_text())),
            pd.DataFrame(json.loads(FAMILIES_FIXTURE.read_text())),
        )
    if source == "warehouse":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL not set; warehouse source needs the local Postgres up")
        sales = pd.read_sql(
            "SELECT family_id, sold_date, sold_price_usd FROM analytics.mart_family_price_history", url
        )
        attention = pd.read_sql(
            "SELECT keyword AS family_id, observed_date AS week_date, interest_index AS search_interest "
            "FROM analytics.stg_search_interest", url
        )
        families = pd.read_sql(
            "SELECT family_id, brand, model_line AS category FROM analytics.mart_family_current_state", url
        )
        return sales, attention, families
    raise ValueError(f"unknown source {source!r}; expected 'synth' or 'warehouse'")


def _add_month(date: datetime.date) -> datetime.date:
    year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
    return datetime.date(year, month, min(date.day, 28))


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign peer-relative uplift labels per family.")
    parser.add_argument("--source", choices=["synth", "warehouse"], default="synth")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    labels = build_labels(*load_frames(args.source))
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    labels.to_parquet(LABELS_PATH, index=False)
    logger.info("labels written to %s (config: %s)", LABELS_PATH, dataclasses.asdict(LabelConfig()))


if __name__ == "__main__":
    main()
