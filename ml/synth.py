"""Synthetic sales-history generator for fixture-mode demos of the ML phases.

Why this exists: the hand-written platform fixtures carry ~30 listings over
three months, which is enough to prove ingestion and resolution but nowhere
near enough longitudinal history to label, feature-engineer, or train on.
The ML phases need years of per-item sale events. This generator produces
them deterministically (seeded), with three named price regimes:

- flat: price wanders around a level; never a grail
- drift: slow secular movement up or down; not a grail
- grail: flat-ish until a hidden inflection date, then price multiplies
  1.8-3.0x over 90-120 days and plateaus at the new level

Alongside sales, the generator emits weekly attention series (search
interest and social mentions). For grail items, attention starts climbing
30-60 days BEFORE the price inflection, which encodes the project's core
hypothesis (attention leads price) so the feature and model phases have a
learnable early signal to demonstrate against. Items also carry a static
synthetic retail price, a season tag, and a collab flag.

The regime of every item is written alongside the sales as ground truth.
This is a mechanics harness, not a claim about markets: it exists so the
labeling logic, the leak tests, and the training loop can be demonstrated
end to end by anyone who clones the repo. The honest evaluation story
(docs/evaluation.md, Phase 7) is explicit that real conclusions wait for
real ingested history.
"""
from __future__ import annotations

import datetime
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
SALES_FIXTURE = FIXTURE_DIR / "sales_history_sample.json"
ITEMS_FIXTURE = FIXTURE_DIR / "sales_history_items.json"
ATTENTION_FIXTURE = FIXTURE_DIR / "attention_history_sample.json"

BRANDS = (
    "Rick Owens", "Balenciaga", "Maison Margiela",
    "Enfant Riches Deprimes", "Number (N)ine", "Undercover",
)
CATEGORIES = ("footwear", "outerwear", "knitwear", "tops", "bottoms")
REGIME_WEIGHTS = (("flat", 0.5), ("drift", 0.3), ("grail", 0.2))


@dataclass(frozen=True)
class SynthConfig:
    n_items: int = 40
    start: datetime.date = datetime.date(2024, 7, 1)
    end: datetime.date = datetime.date(2026, 6, 30)
    seed: int = 7
    min_gap_days: int = 5
    max_gap_days: int = 35
    noise: float = 0.08  # multiplicative sale-to-sale noise


def generate(
    config: SynthConfig = SynthConfig(),
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (items, sales, attention) as record dicts, fully deterministic.

    attention rows are weekly: item_id, week_date, search_interest (0-100
    style index) and social_mentions (a count). For grail items both series
    ramp starting 30-60 days before the price inflection: attention leads
    price, which is the hypothesis the model phases exist to exploit.
    """
    rng = random.Random(config.seed)
    items: list[dict] = []
    sales: list[dict] = []
    attention: list[dict] = []
    span_days = (config.end - config.start).days
    seasons = tuple(
        f"{half}{year:02d}" for year in list(range(3, 12)) + [22, 23, 24] for half in ("SS", "FW")
    )

    for index in range(config.n_items):
        item_id = f"item-synth-{index:04d}"
        regime = _pick_regime(rng)
        base_price = round(rng.uniform(150, 2500), 2)
        inflection_day = rng.randint(int(span_days * 0.3), int(span_days * 0.7))
        inflection_multiple = rng.uniform(1.8, 3.0)
        inflection_length = rng.randint(90, 120)
        drift_annual = rng.uniform(-0.10, 0.10)
        attention_lead_days = rng.randint(30, 60)
        base_interest = rng.uniform(5, 30)
        base_mentions = rng.uniform(0, 6)

        items.append(
            {
                "item_id": item_id,
                "brand": rng.choice(BRANDS),
                "category": rng.choice(CATEGORIES),
                "regime": regime,
                "base_price_usd": base_price,
                "retail_price_usd": round(base_price * rng.uniform(0.45, 0.85), 2),
                "season": rng.choice(seasons),
                "collab_flag": rng.random() < 0.12,
                "inflection_date": (
                    (config.start + datetime.timedelta(days=inflection_day)).isoformat()
                    if regime == "grail" else None
                ),
            }
        )

        day = rng.randint(0, config.max_gap_days)
        while day <= span_days:
            date = config.start + datetime.timedelta(days=day)
            level = _price_level(
                regime, base_price, day, span_days,
                inflection_day, inflection_multiple, inflection_length, drift_annual,
            )
            price = round(level * rng.uniform(1 - config.noise, 1 + config.noise), 2)
            sales.append(
                {"item_id": item_id, "sold_date": date.isoformat(), "sold_price_usd": price}
            )
            day += rng.randint(config.min_gap_days, config.max_gap_days)

        for week_day in range(0, span_days + 1, 7):
            date = config.start + datetime.timedelta(days=week_day)
            lift = _attention_lift(
                regime, week_day, inflection_day, attention_lead_days, inflection_length
            )
            interest = min(100, base_interest * lift * rng.uniform(0.85, 1.15))
            mentions = base_mentions * lift * rng.uniform(0.7, 1.3)
            attention.append(
                {
                    "item_id": item_id,
                    "week_date": date.isoformat(),
                    "search_interest": round(interest, 1),
                    "social_mentions": int(mentions),
                }
            )

    return items, sales, attention


def _attention_lift(
    regime: str, day: int, inflection_day: int, lead_days: int, length: int
) -> float:
    """Attention multiplier over baseline. Grail items ramp to ~4x starting
    lead_days before the price inflection; everyone else stays at 1x."""
    if regime != "grail":
        return 1.0
    ramp_start = inflection_day - lead_days
    if day <= ramp_start:
        return 1.0
    progress = min(1.0, (day - ramp_start) / (lead_days + length))
    return 1.0 + 3.0 * progress


def _pick_regime(rng: random.Random) -> str:
    roll = rng.random()
    cumulative = 0.0
    for regime, weight in REGIME_WEIGHTS:
        cumulative += weight
        if roll < cumulative:
            return regime
    return REGIME_WEIGHTS[-1][0]


def _price_level(
    regime: str,
    base: float,
    day: int,
    span_days: int,
    inflection_day: int,
    multiple: float,
    length: int,
    drift_annual: float,
) -> float:
    if regime == "flat":
        return base
    if regime == "drift":
        return base * (1 + drift_annual * day / 365)
    # grail: flat, then a ramp to base*multiple over `length` days, then plateau
    if day <= inflection_day:
        return base
    progress = min(1.0, (day - inflection_day) / length)
    return base * (1 + (multiple - 1) * progress)


def write_fixture(config: SynthConfig = SynthConfig()) -> tuple[Path, Path, Path]:
    items, sales, attention = generate(config)
    ITEMS_FIXTURE.write_text(json.dumps(items, indent=1))
    SALES_FIXTURE.write_text(json.dumps(sales))
    ATTENTION_FIXTURE.write_text(json.dumps(attention))
    logger.info(
        "synth: wrote %d items, %d sales, %d attention weeks",
        len(items), len(sales), len(attention),
    )
    return ITEMS_FIXTURE, SALES_FIXTURE, ATTENTION_FIXTURE


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_fixture()
