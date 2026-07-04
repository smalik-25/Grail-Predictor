# Labeling: what "became a grail" means as a number

The label sounds simple and is where this project is most likely to fool
itself. This page pins the definition and confronts the biases in the open.

## The definition

At a prediction moment T, an item is labeled positive if:

```
median(sold_price_usd over (T, T+180d])
--------------------------------------  >= 1.5
median(sold_price_usd over (T-90d, T])
```

with at least 2 sales in each window. Prediction moments sit on an aligned
monthly grid, generated only where the data coverage allows a full baseline
behind T and a full outcome window ahead of it.

Why these numbers:

- **1.5x**: grail moves are multiples, not percentages. Secular luxury-resale
  drift and platform-level noise live in the ±20% band; a piece that adds
  half its value in six months has inflected. The threshold is a config
  field, not a constant, and the Phase 7 evaluation should include
  sensitivity to it before any conclusion gets trusted.
- **180-day outcome**: hype inflections in this market play out over a few
  months (the synthetic generator's 90-120 day ramps mirror what archive
  pieces do when a co-sign lands). Much shorter and the window misses slow
  builds; much longer and the label starts rewarding secular drift.
- **90-day baseline**: long enough to median away single-sale flukes, short
  enough that the baseline is "the price before the move" rather than
  ancient history.
- **Median, both sides**: a single desperate seller or a single overpay
  should not define either price. With a 2-sale minimum the median is the
  honest small-sample choice.

An important consequence of the definition: an item that already inflated
labels negative afterward (1100 to 1100 is a ratio of 1.0). The target is
up-and-comers, not pieces that are already hot, and there is a test pinning
exactly that behavior.

## Look-ahead bias

The label is defined over a future window, so the machinery has to make
peeking structurally hard, not just discouraged:

- Baseline reads sales at or before T only; outcome reads strictly after T
  only. The windows cannot overlap by construction.
- Every labeled row carries its prediction_moment explicitly. Phase 6's
  contract is that every feature is computed from data at or before that
  moment, and its tests must include a leak canary.
- The leak tests in tests/test_labeling.py prove invariance both ways:
  multiply every post-T price by 10 and the baseline must not move a cent;
  rewrite the pre-T history and the outcome must not move either. If either
  test ever fails, nothing downstream is trustworthy.

## Survivorship bias

We only see pieces that got listed, on platforms we ingest, in listings our
entity resolution managed to unify. That excludes: pieces so hyped they
sell privately before listing, pieces on platforms outside the six we
cover, and pieces whose listings were too sparse to resolve (the Phase 2a
residual). Every claim this model makes is therefore a claim about the
*listed, resolved* universe, not about fashion at large. The resolution
rate from Phase 2a is part of the model's fine print.

## Thin data: excluded, not negative

An (item, moment) pair without 2 sales on each side is dropped and counted,
not labeled 0. Labeling illiquid items negative would teach the model that
illiquidity predicts failure, when illiquidity actually predicts *nothing
measurable*. The cost, stated: the model can never flag a piece before its
market has at least minimal liquidity, so the earliest phase of any grail's
life is invisible to it. That is a real limitation of a price-based label,
and attention signals (search, social) can only partially compensate.

## Concept drift

Hype cycles shift. A model trained on 2024-2025 grails learns what
2024-2025 hype looked like; the mechanism that made Number (N)ine explode
is not the mechanism that will move the next thing. The time-based split in
Phase 7 measures generalization forward honestly, but it cannot fix drift,
only reveal it. Stated as a standing limitation.

## The synthetic-data caveat

The labeling machinery is demonstrated end to end on a seeded synthetic
market (ml/synth.py) because the hand-written platform fixtures span three
months and cannot produce a single labelable example under a 90d+180d
design. On the synthetic set, positives land exclusively on
inflection-regime items at the moments whose outcome window catches the
ramp, which is the mechanics working as designed. It is not evidence the
label finds real grails. That evidence waits for real ingested history, and
docs/evaluation.md must say which kind of data any reported number came from.
