"""Decide which listings are the same piece, on text alone for now.

Design: the score is a weighted combination over a LIST of signals, even
though that list currently holds only text-adjacent signals. Phase 2b's
image similarity, if the measurement justifies building it, slots in as one
more Signal without touching anything else here.

Library choice: rapidfuzz token_set_ratio over a Splink-style probabilistic
linkage model, deliberately. Splink's Fellegi-Sunter approach earns its
complexity when there are many comparison fields and enough volume to
estimate match/non-match probabilities; this problem is product identity
from short noisy titles, dominated by one strong signal (the de-branded,
de-noised title) plus two weak structured ones (season, price). At fixture
scale Splink's EM estimation would be fitting noise, and even at realistic
scale (tens of thousands of listings) the deterministic signal combination
is inspectable in a way I can defend line by line. The cost: no learned
weights, so the weights and threshold below are hand-tuned and documented.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

from rapidfuzz import fuzz

from resolution.normalize import NormalizedListing

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.70
BORDERLINE_FLOOR = 0.55  # pairs scoring in [floor, threshold) get logged for review


@dataclass(frozen=True)
class Signal:
    """One similarity signal: a name, a weight, and a scorer in [0, 1]."""

    name: str
    weight: float
    scorer: Callable[[NormalizedListing, NormalizedListing], float | None]
    # scorer returns None when the signal has nothing to say (missing data);
    # its weight is then redistributed over the signals that do.


@dataclass(frozen=True)
class ScoredPair:
    left: NormalizedListing
    right: NormalizedListing
    score: float
    breakdown: dict[str, float]

    @property
    def is_match(self) -> bool:
        return self.score >= MATCH_THRESHOLD

    @property
    def is_borderline(self) -> bool:
        return BORDERLINE_FLOOR <= self.score < MATCH_THRESHOLD


def _title_similarity(a: NormalizedListing, b: NormalizedListing) -> float | None:
    """token_set_ratio with a sparsity guard.

    token_set_ratio scores 100 whenever the shorter token set is a subset of
    the longer one, which means a listing titled just "Jacket" would match
    every jacket in its block and union-find would weld them into one
    mega-item. The guard scales the score by how much information the
    sparser title actually carries: one informative token can never make a
    confident match, three or more can. Sparse listings fall to borderline
    or singleton status instead, which is exactly the residual Phase 2b's
    image signal would exist to resolve.
    """
    if not a.title_normalized or not b.title_normalized:
        return None
    raw = fuzz.token_set_ratio(a.title_normalized, b.title_normalized) / 100.0
    sparser = min(len(a.title_normalized.split()), len(b.title_normalized.split()))
    return raw * min(1.0, sparser / 3.0)


def _season_agreement(a: NormalizedListing, b: NormalizedListing) -> float | None:
    if a.season is None or b.season is None:
        return None  # absence of a tag is not evidence either way
    return 1.0 if a.season == b.season else 0.0


def _price_proximity(a: NormalizedListing, b: NormalizedListing) -> float | None:
    """Weak signal: same piece rarely lists at wildly different prices.

    Uses sold price when asking price is absent. Ratio is clipped so this
    can nudge, never decide: cross-platform spreads of 2x are normal for
    exactly the pieces this project cares about.
    """
    price_a = a.raw.price if a.raw.price is not None else a.raw.sold_price
    price_b = b.raw.price if b.raw.price is not None else b.raw.sold_price
    if not price_a or not price_b:
        return None
    ratio = min(price_a, price_b) / max(price_a, price_b)
    return max(0.0, min(1.0, (ratio - 0.2) / 0.6))  # <=0.2 -> 0, >=0.8 -> 1


SIGNALS: tuple[Signal, ...] = (
    Signal("title", 0.70, _title_similarity),
    Signal("season", 0.15, _season_agreement),
    Signal("price", 0.15, _price_proximity),
)


def score_pair(
    a: NormalizedListing, b: NormalizedListing, signals: tuple[Signal, ...] = SIGNALS
) -> ScoredPair:
    """Weighted mean over the signals that had something to say."""
    contributions: dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0
    for signal in signals:
        value = signal.scorer(a, b)
        if value is None:
            continue
        contributions[signal.name] = value
        weighted_sum += signal.weight * value
        weight_total += signal.weight
    score = weighted_sum / weight_total if weight_total > 0 else 0.0
    return ScoredPair(left=a, right=b, score=score, breakdown=contributions)


def score_pairs(
    pairs: Iterable[tuple[NormalizedListing, NormalizedListing]],
    signals: tuple[Signal, ...] = SIGNALS,
) -> Iterator[ScoredPair]:
    """Score candidate pairs, logging borderline cases instead of guessing."""
    for a, b in pairs:
        scored = score_pair(a, b, signals)
        if scored.is_borderline:
            logger.info(
                "borderline (%.2f) %s: %r <-> %r",
                scored.score,
                scored.breakdown,
                a.raw.title,
                b.raw.title,
            )
        yield scored
