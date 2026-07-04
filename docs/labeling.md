# Labeling: peer-relative uplift at the style-family grain

The label is where this project is most likely to fool itself. This page
pins the definition and confronts the biases in the open. The target
changed with the reseller pivot: the old absolute-appreciation label and
its reasoning are in the DEVLOG history; this describes what runs now.

## The definition

At a prediction moment T, a style-family is a positive example if its
blended uplift over (T, T+60d] beats its peer set by a statistically
meaningful margin. Concretely:

**Blended uplift**, in log space, outcome window over baseline window
(T-60d, T]:

```
0.50 * log(median sold price ratio)
0.30 * log(mean search interest ratio)
0.20 * log(sales count ratio, +1 smoothed)
```

A component with insufficient data drops out and its weight redistributes
over the rest, the same philosophy as the resolution matcher: absence of
evidence is not evidence, and a family shouldn't be punished because a
platform's data is sparse. Price carries half the weight because it is the
money; interest is the leading signal the generator (and the market
hypothesis) says moves first; velocity is real but the noisiest of the
three.

**Peer set**, a fallback ladder, recorded per row as peer_basis:

1. same brand + category + baseline price within [0.4x, 2.5x] ('brand')
2. same brand + category ('brand_wide')
3. same category + price band, any brand ('category')

At least 3 peers at some rung, or the family is peer-unmeasurable at that
moment: excluded and counted, never labeled. The category rung exists
because brand-by-category cells are thin in any realistic catalog, and an
archive knit is a fair peer to archive knits across brands for the purpose
of cancelling market-wide moves.

**The bar**: robust z against the peer distribution,
(uplift - peer median) / max(1.4826 * MAD, 0.05), positive when z >= 2.0
AND the raw edge over the peer median is at least 15 points. Two knobs on
purpose: the z alone fires on degenerate near-zero dispersion, the edge
floor alone fires inside noisy peer sets. The 0.05 spread floor earned its
place in testing: a perfectly still peer set has MAD 0, and the naive
handling hid a genuine 81% outperformer behind motionless peers.

## Why peer-relative at all

Because the alternative mints false positives from the tide. The synthetic
market bakes in a shared market factor (a cycle plus secular drift) and
per-brand drifts, so every family's absolute numbers rise together at
times. An absolute threshold would flag them all. The negative-control
test constructs exactly that: six families doubling in lockstep, and the
label must stay silent because nobody beat anybody. A reseller's capital
is finite; the watchlist exists to rank what beats the market they're
already in, not to describe the market.

An important consequence, tested: a family that already rose labels
negative afterward. The target is the inflection, not the plateau.

## Look-ahead bias

Unchanged discipline from v1, re-proven against the new math:

- Baseline reads data at or before T only; outcome strictly after T only.
- Every labeled row carries its prediction_moment. Phase 6's contract is
  that every feature computes from data at or before it, machine-checked.
- Invariance tests both ways: multiply post-T prices by ten, baseline
  must not move a cent; rewrite pre-T history, the labels at the probed
  moment must not change.

One subtlety worth stating: the label legitimately reads the future of
peers, because outcomes are ground truth and peer outcomes are part of the
outcome definition. Features may never do this. The line is between what
defines the answer and what the model is allowed to see.

## Survivorship bias

We only see families whose listings got ingested, resolved, and family-
assigned. The Phase 2a and 2c residuals (unmatched listings, unresolved
model lines) are part of this model's fine print, and thin families are
excluded rather than labeled. Claims are about the listed, resolved,
measurable universe.

## Thin data: excluded, not negative

A (family, moment) without 2 sales and, when interest is present, 4
interest weeks per window is unmeasured. Labeling it negative would teach
the model that illiquidity predicts failure, when illiquidity predicts
nothing measurable. The cost, stated: the earliest and most illiquid phase
of a family's rise is invisible to the label, which is exactly the phase a
reseller would most like to catch. Attention signals partially compensate,
and the limitation stands.

## Concept drift

Hype cycles shift. The mechanism that made Number (N)ine explode is not
the mechanism that moves the next thing. The time split in Phase 7
measures forward generalization honestly; it cannot fix drift, only reveal
it. Standing limitation.

## The synthetic-data caveat

All numbers on this page's machinery come from the seeded synthetic market
(ml/synth.py): 48 families, layered market/brand/regime price factors,
attention leading price by 30-60 days for grail families. On it, positives
land exclusively on grail-regime families at a 4% overall rate, and the
market tide mints zero false positives. That proves the mechanics, not the
market. Real conclusions wait for real ingested history, and every
downstream doc says which kind of data its numbers came from.
