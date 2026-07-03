"""Reduce the comparison space before matching.

Comparing every listing to every other listing is quadratic and almost all
of those comparisons are wasted: a Margiela GAT is never the same piece as
an Undercover blouson. Blocking on (canonical brand, category) keeps every
plausibly-matching pair and throws away the rest.

The tradeoff accepted here: a listing whose brand could not be resolved
blocks on category alone, which makes those blocks bigger and slower but
keeps "RO leather high top" comparable to properly-branded Rick Owens
footwear via the title-scan fallback in normalize. A listing with neither
brand nor category is unmatchable on text and lands in a discard block that
the catalog reports rather than silently drops.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Iterator

from resolution.normalize import NormalizedListing

logger = logging.getLogger(__name__)

BlockKey = tuple[str, str]

UNBLOCKABLE = ("__unblockable__", "__unblockable__")


def block_key(listing: NormalizedListing) -> BlockKey:
    if listing.brand_canonical is None and listing.category is None:
        return UNBLOCKABLE
    return (listing.brand_canonical or "__unknown_brand__", listing.category or "__unknown_category__")


def build_blocks(listings: Iterable[NormalizedListing]) -> dict[BlockKey, list[NormalizedListing]]:
    blocks: dict[BlockKey, list[NormalizedListing]] = defaultdict(list)
    for listing in listings:
        blocks[block_key(listing)].append(listing)
    if UNBLOCKABLE in blocks:
        logger.warning(
            "blocking: %d listings had neither brand nor category and cannot be matched on text",
            len(blocks[UNBLOCKABLE]),
        )
    return dict(blocks)


def candidate_pairs(
    blocks: dict[BlockKey, list[NormalizedListing]],
) -> Iterator[tuple[NormalizedListing, NormalizedListing]]:
    """All within-block pairs, skipping the unblockable discard block."""
    for key, members in blocks.items():
        if key == UNBLOCKABLE:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                yield members[i], members[j]
