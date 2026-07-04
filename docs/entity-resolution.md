# Entity resolution

The centerpiece of the project. The same Margiela piece shows up on Grailed,
eBay, and Vestiaire with three different titles, three sizing conventions,
and three opinions about condition. Until those resolve to one canonical
item, there is no price history and nothing downstream works.

## The pipeline

Four stages, each in its own module under resolution/:

**normalize.py** adds standardized fields without touching the raw ones.
Brands resolve against a controlled vocabulary in data/reference/brands.json,
with a title-scan fallback for listings that never filled the brand field
("RO leather high top" resolves to Rick Owens through the alias scan).
Footwear sizing lands on EU numbers from US, UK, and Japanese-centimeter
inputs; clothing lands on letter buckets from IT/FR numbers and Japanese 0-5
sizing. Condition prose collapses to an ordinal 1-5, including the Japanese
letter grades 2nd Street uses. Season tags parse from both Western (SS03)
and Japanese (03SS) orderings. Anything that can't be normalized confidently
becomes None. No guessing.

**blocking.py** compares only within (canonical brand, category) blocks.
A Margiela GAT is never the same piece as an Undercover blouson, so scoring
that pair is wasted work. Listings missing a brand block on category alone,
which keeps them matchable at the cost of bigger blocks. Listings with
neither brand nor category are reported as unmatchable rather than silently
dropped.

**match.py** scores pairs as a weighted combination over a list of signals:
title similarity (weight 0.70), season agreement (0.15), price proximity
(0.15). A signal with nothing to say (missing data) returns None and its
weight redistributes over the signals that do. This list structure is the
Phase 2b seam: an image-similarity signal slots in without a rewrite.
Matches need a score of 0.70; pairs from 0.55 to 0.70 are logged as
borderline for review instead of being silently guessed either way.

**catalog.py** takes matches as edges and builds canonical items as
connected components. Item ids are deterministic for the same inputs
(derived from the lexicographically-first member listing URL). Canonical
title is the most informative member title. Output is Parquet.

## Why rapidfuzz and not Splink

The plan leaned toward a proper record-linkage framework like Splink. I
looked at it and went the other way, for reasons I'd defend:

Splink's Fellegi-Sunter model earns its complexity when there are many
comparison fields and enough volume to estimate match and non-match
probabilities per field via EM. This problem has one dominant signal, the
de-branded de-noised title, plus two weak structured ones. At fixture scale
EM would be fitting noise, and even at realistic scale the deterministic
combination is inspectable line by line, which matters for a matcher whose
mistakes compound through union-find. The cost accepted: no learned weights.
The weights and threshold are hand-tuned and documented, and if live-scale
data later shows the hand-tuning failing in ways per-field probability
estimation would fix, Splink slots into match.py the same way any new
signal does.

## The subset failure mode, found and fixed

First run over-merged badly. token_set_ratio scores 1.0 whenever the shorter
title's tokens are a subset of the longer's, so a Vestiaire listing titled
just "Jacket" scored a perfect title match against every Balenciaga jacket
in its block, and union-find welded them into one mega-item. The fix is a
sparsity guard: the title score scales by how much information the sparser
title carries (one informative token can never make a confident match,
three or more can). There is a regression test pinning this down.

## Measurement: what text-only resolution actually did

On the fixture set (30 resale listings across six platforms, built with
known overlaps and known traps):

| Metric | Value |
|---|---|
| Total listings | 30 |
| Canonical items | 15 |
| Multi-listing items | 5 |
| Listings resolved into multi-listing items | 20 (67%) |
| Singletons | 10 |
| Borderline pairs logged for review | 15 |

What resolved: the Geobasket across five platforms and seven listings
(including the sparse "RO leather high top black sz 10"), the GAT across
five, the Kurt cardigan across three, the Scab jacket and FW07 Balenciaga
bomber across two or three each. Ramones and Geobaskets stayed correctly
separate despite both being Rick Owens footwear described in overlapping
vocabulary.

What did not resolve, and why it matters:

- **Sparse generic titles.** Vestiaire's "Wool cardigan", "Cloth trainers",
  and "Jacket" all sat in the borderline log against their true matches.
  This is a platform convention, not bad luck: Vestiaire titles are generic
  by design and the photos carry the identity. Text alone cannot fix this
  class.
- **Synonym gaps.** "SCAB BLOUSON" vs "Scab jacket" scored 0.56-0.57,
  borderline. Japanese resale vocabulary (blouson, trainer) systematically
  misses Western titles.
- **Currency-naive price signal.** The Depop ramones (GBP 420) missed the
  Grailed ramones (USD 640) partly because the price signal compares raw
  numbers across currencies. Known limitation; currency normalization lands
  in the dbt layer and the signal can use it then.

## The Phase 2b call

The fixture numbers say the image path would earn its place for exactly one
class of listing: sparse-generic titles, which on Vestiaire are the rule
rather than the exception. But 30 hand-built fixtures are an existence
proof, not a measurement. The honest call: **defer the build-or-skip
decision until the first real ingestion runs produce residual numbers at
volume.** The matcher's signal list is the seam; nothing needs rewriting
either way. If live Vestiaire data shows the same pattern at scale, image
similarity gets built and measured against these same metrics. Recorded
here so the decision input is explicit.

## The style-family grain (Phase 2c)

The pivot to the reseller framing changed resolution's output unit. Items
(same product, via the matcher) remain as the comps layer, but the
forecasting grain is now the style-family: **Brand -> Model-line ->
Era/Generation, with colorway tier as a sub-attribute**, never part of the
key. Finer grain gives cleaner price signal but thinner data; coarser
gives more data but mushier signal. Brand + model-line + era is where
those balance for archive fashion: hype on a generation lifts the family,
hype on one colorway is caught by the tier without fragmenting the data.

Assignment works off reference data in data/reference/: model_lines.json
(per-brand alias vocabularies plus era tables where release history is
documented; decade buckets as the fallback, era-unknown when there is no
year signal at all) and colorways.json (extraction vocabulary plus
per-line tier maps: core, rare, standard, unknown).

**Identity propagation is the piece that makes the grain usable.** Most
resale listings carry no year and many carry sparse titles. But if the
matcher already decided two listings are the same product, they share a
family: within each canonical item, a lone distinct model line propagates
to same-brand members that lack one, and a lone distinct known era
propagates to era-unknown members. Conflicts propagate nothing and get
logged. On fixtures this took family coverage from 66% to 81%, and it is
why "RO leather high top black sz 10" and Vestiaire's "Leather high
trainers" both land in the Geobasket og-2006-2012 family despite naming
neither the model nor the year.

### The Margiela Future worked example

Two Future listings in the fixtures: "Maison Martin Margiela Future High
Top Black Leather 2013" and "Maison Margiela future high top white leather
43 2018". The text matcher merges them into one canonical item, because on
titles alone they are near-identical, and that is exactly the failure the
era table catches: 2013 buckets to yeezus-era-2013-2014, 2018 to
later-2015-plus, the eras conflict inside the item, propagation refuses to
pick a side, and they land in two families. Value concentrated in the
2013-14 generation is precisely what the old item grain would have blurred
away. Their colorways (black -> core, white -> core) sit as tiers inside
their families, not as family splits.

### Family measurement on fixtures (32 listings)

| Metric | Value |
|---|---|
| Families formed | 9 |
| Listings in families | 26 (81%) |
| Model line unresolved | 6 |
| Era unknown after propagation | 8 |

The unresolved six are honest residual: pieces like the 2nd Street "NUMBER
(N)INE MOHAIR CARDIGAN NAVY 03SS" phrased without any vocabulary alias.
That is where the model-line vocabulary grows with real data, and where
the domain knowledge lives: someone who knows the market adds the aliases
a keyword list can't guess.

## Known open items

- Item ids are deterministic for identical inputs but not durable under
  incremental re-resolution (new listings can change component anchors).
  Durable ids matter once price history accumulates across runs; the fix
  (persist id assignments, match new listings against existing items) is
  scoped for the warehouse phases.
- Cross-listing dedupe (the same physical item listed on two platforms by
  one seller) is not distinguished from two units of the same product. For
  price history at product grain this is acceptable; for scarcity features
  it slightly inflates supply and is noted for Phase 6.
