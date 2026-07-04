"""Celebrity and editorial wear detection: a feature and an explainer, not a model.

Detects "named cultural figure co-mentioned with brand/piece" from text and
event data, lands events into fact_celebrity_events, and feeds the
celebrity_signal features. Two hard lines, stated here and in docs/celebrity.md:

- Detection is metadata and text analysis ONLY. Never facial recognition,
  never biometric inference on images. That is both the legal line under
  biometric-privacy rules and the cheaper path.
- Detection and explanation only, no causal lift estimates. "Flagged:
  Carti-associated, search accelerating" is a reason on a watchlist, not a
  claim that the co-sign caused the move.

Precision comes from the curated figure list in data/reference/: detection
runs only against figures who actually move this market, so a short
hand-grown vocabulary does the work NER over everything would do worse.
A detected mention resolves to a family when the text names a model line
and an era; with a model line but no era it attaches at brand + model line;
with neither it attaches at brand, and the feature layer lets a brand-level
event lift every family under that brand, which is how attention actually
spreads.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = ROOT / "data" / "reference"
PROCESSED_DIR = ROOT / "data" / "processed"
EVENTS_PATH = PROCESSED_DIR / "celebrity_events.parquet"

CONFIDENCE_FAMILY = 0.9   # figure + brand + model line in one text
CONFIDENCE_BRAND = 0.6    # figure + brand only


@dataclass(frozen=True)
class CelebrityEvent:
    """One detected wear/co-mention event. family_id stays None unless the
    text pinned both a model line and an era; brand is always known."""

    figure: str
    brand: str
    model_line: str | None
    family_id: str | None
    event_date: str  # ISO date
    source: str      # e.g. reddit:rickowens
    confidence: float
    evidence: str    # the matched text, truncated; the human-readable receipt


@lru_cache(maxsize=1)
def _figures() -> dict[str, list[str]]:
    with (REFERENCE_DIR / "celebrity_figures.json").open() as handle:
        data = json.load(handle)
    data.pop("_comment", None)
    return data


def detect(texts: Iterable[dict[str, Any]]) -> list[CelebrityEvent]:
    """Run detection over text records: {text, date, source}.

    Figure alias and brand alias must co-occur in one text. Model line and
    era resolve through the same reference vocabularies resolution uses,
    so an event that CAN be pinned to a family is, and one that can't
    attaches at the widest honest grain instead of being guessed.
    """
    from resolution.family import ERA_UNKNOWN, assign_era, assign_model_line, family_id
    from resolution.normalize import extract_season, normalize_brand

    events: list[CelebrityEvent] = []
    for record in texts:
        text = record.get("text") or ""
        lowered = f" {re.sub(r'[^a-z0-9()$é ]', ' ', text.casefold())} "
        figure = next(
            (name for name, aliases in _figures().items()
             if any(f" {alias} " in lowered for alias in aliases)),
            None,
        )
        if figure is None:
            continue
        brand = normalize_brand(None, text)
        if brand is None:
            continue  # a figure without a brand is gossip, not a signal
        model_line = assign_model_line(brand, lowered.strip())
        season = extract_season(text, None)
        resolved_family = None
        if model_line:
            year_match = re.search(r"\b(19[89]\d|20[0-3]\d)\b", text)
            year = (
                2000 + int(season[2:]) if season and int(season[2:]) < 90
                else int(year_match.group(0)) if year_match
                else None
            )
            era = assign_era(brand, model_line, year)
            if era != ERA_UNKNOWN:
                resolved_family = family_id((brand, model_line, era))
        events.append(
            CelebrityEvent(
                figure=figure,
                brand=brand,
                model_line=model_line,
                family_id=resolved_family,
                event_date=str(record.get("date") or ""),
                source=str(record.get("source") or "unknown"),
                confidence=CONFIDENCE_FAMILY if model_line else CONFIDENCE_BRAND,
                evidence=text[:200],
            )
        )
    logger.info("celebrity: %d events detected from %s texts",
                len(events), "input")
    return events


def texts_from_social_fixture() -> list[dict[str, Any]]:
    """Adapter: the Reddit fixture posts as detection inputs. Live mode
    feeds the same shape from ingestion.social and listing titles."""
    from ml.synth import FIXTURE_DIR

    posts = json.loads((FIXTURE_DIR / "social_sample.json").read_text())
    return [
        {
            "text": post["title"],
            "date": post["created_date"],
            "source": f"reddit:{post['subreddit']}",
        }
        for post in posts
    ]


def write_events(events: list[CelebrityEvent]) -> Path:
    import pandas as pd

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([dataclasses.asdict(event) for event in events])
    frame.to_parquet(EVENTS_PATH, index=False)
    logger.info("celebrity: wrote %d events to %s", len(frame), EVENTS_PATH)
    return EVENTS_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect celebrity/editorial wear events from text.")
    parser.add_argument("--source", choices=["social-fixture"], default="social-fixture",
                        help="live text sources land with real ingestion volume")
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    events = detect(texts_from_social_fixture())
    write_events(events)
    for event in events:
        target = event.family_id or f"{event.brand} / {event.model_line or 'brand-wide'}"
        print(f"{event.event_date}  {event.figure} -> {target}  ({event.confidence:.1f})  [{event.source}]")


if __name__ == "__main__":
    main()
