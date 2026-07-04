"""Synthetic market generator at the style-family grain.

Why this exists: the hand-written platform fixtures carry ~32 listings over
three months, enough to prove ingestion and resolution but nowhere near
enough longitudinal history to label, feature-engineer, or train on. The ML
phases need years of per-family history, so this generates it
deterministically (seeded).

The v2 design exists to make the peer-relative target testable. Prices are
composed of layered factors:

    price = base * market_factor * brand_factor * regime_curve * tier * noise

- market_factor: a slow market-wide swell plus gentle secular drift that
  EVERY family shares. Under an absolute appreciation label this alone
  would mint false positives; under a peer-relative label it cancels out,
  which is the entire argument for the new target and there is a test
  pinning it.
- brand_factor: per-brand drift, so a whole brand can rise without any of
  its families being an outperformer.
- regime_curve: flat (never a grail), drift (family-specific slow move),
  or grail (flat until a hidden inflection, then 1.8-3.0x over 90-120 days,
  then plateau).
- tier: each sale carries a colorway_tier; rare-tier sales price above the
  family baseline, feeding the tier-premium feature.

Attention (weekly search interest and social mentions) ramps 30-60 days
BEFORE the price inflection for grail families: attention leads price,
which is the hypothesis the model phases exist to exploit.

Ground truth (regime, inflection date) is written alongside. This is a
mechanics harness, not a market claim, and every doc downstream says so.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import random
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
FAMILIES_FIXTURE = FIXTURE_DIR / "synth_families.json"
SALES_FIXTURE = FIXTURE_DIR / "synth_family_sales.json"
ATTENTION_FIXTURE = FIXTURE_DIR / "synth_family_attention.json"
CELEBRITY_FIXTURE = FIXTURE_DIR / "synth_celebrity_events.json"

BRANDS = (
    "Rick Owens", "Balenciaga", "Maison Margiela",
    "Enfant Riches Deprimes", "Number (N)ine", "Undercover",
)

# Which figures plausibly co-sign which brand. Every name is in
# data/reference/celebrity_figures.json, so the synth events speak the same
# vocabulary the detector does even though the two never feed each other.
BRAND_FIGURES = {
    "Rick Owens": ("Playboi Carti", "Michele Lamy", "A$AP Rocky"),
    "Balenciaga": ("Kanye West", "Bella Hadid"),
    "Maison Margiela": ("Frank Ocean", "Kanye West"),
    "Enfant Riches Deprimes": ("Travis Scott", "A$AP Rocky"),
    "Number (N)ine": ("Frank Ocean", "A$AP Rocky"),
    "Undercover": ("Travis Scott", "Jerry Lorenzo"),
}
CATEGORIES = ("footwear", "outerwear", "knitwear", "tops")
ERAS = ("archive-2003-2009", "archive-2010-2015", "recent-2016-plus")
REGIME_WEIGHTS = (("flat", 0.5), ("drift", 0.3), ("grail", 0.2))
TIER_CHOICES = (("core", 0.6), ("standard", 0.3), ("rare", 0.1))
RARE_TIER_MULTIPLIER = 1.35


@dataclass(frozen=True)
class SynthConfig:
    n_families: int = 48
    start: datetime.date = datetime.date(2024, 7, 1)
    end: datetime.date = datetime.date(2026, 6, 30)
    seed: int = 11
    celebrity_seed: int = 1109  # own stream; must not perturb seed=11
    min_gap_days: int = 4
    max_gap_days: int = 10
    noise: float = 0.08
    market_swell: float = 0.12   # amplitude of the shared market cycle
    market_drift: float = 0.10   # shared secular rise across the whole span


def generate(
    config: SynthConfig = SynthConfig(),
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (families, sales, attention) as record dicts, deterministic."""
    rng = random.Random(config.seed)
    span_days = (config.end - config.start).days
    brand_drift = {brand: rng.uniform(-0.12, 0.12) for brand in BRANDS}

    families: list[dict] = []
    sales: list[dict] = []
    attention: list[dict] = []

    for index in range(config.n_families):
        family_id = f"fam-synth-{index:04d}"
        brand = rng.choice(BRANDS)
        category = rng.choice(CATEGORIES)
        regime = _pick_weighted(rng, REGIME_WEIGHTS)
        base_price = round(math.exp(rng.uniform(math.log(150), math.log(2500))), 2)
        inflection_day = rng.randint(int(span_days * 0.3), int(span_days * 0.7))
        inflection_multiple = rng.uniform(1.8, 3.0)
        inflection_length = rng.randint(90, 120)
        family_drift = rng.uniform(-0.10, 0.10)
        attention_lead_days = rng.randint(30, 60)
        base_interest = rng.uniform(5, 30)
        base_mentions = rng.uniform(0, 6)

        families.append(
            {
                "family_id": family_id,
                "brand": brand,
                "model_line": f"synth-line-{index:02d}",
                "era": rng.choice(ERAS),
                "category": category,
                "regime": regime,
                "base_price_usd": base_price,
                "retail_price_usd": round(base_price * rng.uniform(0.45, 0.85), 2),
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
            tier = _pick_weighted(rng, TIER_CHOICES)
            level = (
                base_price
                * _market_factor(day, span_days, config)
                * (1 + brand_drift[brand] * day / 365)
                * _regime_curve(regime, day, inflection_day, inflection_multiple,
                                inflection_length, family_drift)
                * (RARE_TIER_MULTIPLIER if tier == "rare" else 1.0)
            )
            sales.append(
                {
                    "family_id": family_id,
                    "sold_date": date.isoformat(),
                    "sold_price_usd": round(level * rng.uniform(1 - config.noise, 1 + config.noise), 2),
                    "colorway_tier": tier,
                }
            )
            day += rng.randint(config.min_gap_days, config.max_gap_days)

        for week_day in range(0, span_days + 1, 7):
            date = config.start + datetime.timedelta(days=week_day)
            lift = _attention_lift(regime, week_day, inflection_day,
                                   attention_lead_days, inflection_length)
            attention.append(
                {
                    "family_id": family_id,
                    "week_date": date.isoformat(),
                    "search_interest": round(min(100.0, base_interest * lift * rng.uniform(0.85, 1.15)), 1),
                    "social_mentions": int(base_mentions * lift * rng.uniform(0.7, 1.3)),
                }
            )

    return families, sales, attention


def _pick_weighted(rng: random.Random, choices: tuple[tuple[str, float], ...]) -> str:
    roll = rng.random()
    cumulative = 0.0
    for value, weight in choices:
        cumulative += weight
        if roll < cumulative:
            return value
    return choices[-1][0]


def _market_factor(day: int, span_days: int, config: SynthConfig) -> float:
    """The tide every family floats on: a cycle plus secular drift."""
    cycle = config.market_swell * math.sin(2 * math.pi * day / 540)
    drift = config.market_drift * day / span_days
    return 1.0 + cycle + drift


def _regime_curve(
    regime: str, day: int, inflection_day: int, multiple: float,
    length: int, family_drift: float,
) -> float:
    if regime == "flat":
        return 1.0
    if regime == "drift":
        return 1.0 + family_drift * day / 365
    if day <= inflection_day:
        return 1.0
    progress = min(1.0, (day - inflection_day) / length)
    return 1.0 + (multiple - 1.0) * progress


def _attention_lift(
    regime: str, day: int, inflection_day: int, lead_days: int, length: int
) -> float:
    """Attention multiplier over baseline. Grail families ramp to ~4x
    starting lead_days before the price inflection; everyone else stays flat."""
    if regime != "grail":
        return 1.0
    ramp_start = inflection_day - lead_days
    if day <= ramp_start:
        return 1.0
    progress = min(1.0, (day - ramp_start) / (lead_days + length))
    return 1.0 + 3.0 * progress


def generate_celebrity_events(
    families: list[dict], config: SynthConfig = SynthConfig()
) -> list[dict]:
    """Synthetic celebrity events at the family grain, feeding the celebrity
    features. This uses its OWN rng (celebrity_seed), drawn only here after
    the main generation, so it never touches the seed=11 stream that sales,
    attention, and families are pinned to.

    Grail families get 1 to 3 events dated 5 to 55 days before the price
    inflection: the co-sign leads the move, the same shape attention takes.
    A minority of flat and drift families get a single red-herring event so
    the model cannot learn the lazy rule 'any event means buy'."""
    crng = random.Random(config.celebrity_seed)
    span_days = (config.end - config.start).days
    events: list[dict] = []
    for family in families:
        figures = BRAND_FIGURES.get(family["brand"], ("Bella Hadid",))
        if family["regime"] == "grail" and family["inflection_date"]:
            inflection = datetime.date.fromisoformat(family["inflection_date"])
            for _ in range(crng.randint(1, 3)):
                date = inflection - datetime.timedelta(days=crng.randint(5, 55))
                events.append(_celebrity_event(crng, family, figures, date, 0.9))
        elif crng.random() < 0.15:
            date = config.start + datetime.timedelta(days=crng.randint(0, span_days))
            events.append(_celebrity_event(crng, family, figures, date, 0.6))
    events.sort(key=lambda event: (event["event_date"], event["family_id"]))
    return events


def _celebrity_event(
    crng: random.Random, family: dict, figures: tuple[str, ...],
    date: datetime.date, confidence: float,
) -> dict:
    return {
        "family_id": family["family_id"],
        "brand": family["brand"],
        "model_line": family["model_line"],
        "figure": crng.choice(figures),
        "event_date": date.isoformat(),
        "source": "synth",
        "confidence": confidence,
    }


def write_fixture(config: SynthConfig = SynthConfig()) -> tuple[Path, Path, Path]:
    families, sales, attention = generate(config)
    FAMILIES_FIXTURE.write_text(json.dumps(families, indent=1))
    SALES_FIXTURE.write_text(json.dumps(sales))
    ATTENTION_FIXTURE.write_text(json.dumps(attention))
    # celebrity events land after the three pinned files, from their own rng,
    # so regenerating them leaves families/sales/attention byte-identical.
    events = generate_celebrity_events(families, config)
    CELEBRITY_FIXTURE.write_text(json.dumps(events, indent=1))
    logger.info(
        "synth: wrote %d families, %d sales, %d attention weeks, %d celebrity events",
        len(families), len(sales), len(attention), len(events),
    )
    return FAMILIES_FIXTURE, SALES_FIXTURE, ATTENTION_FIXTURE


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    write_fixture()
