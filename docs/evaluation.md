# Evaluation: an honest read on the watchlist model

Every number here comes from synthetic data. The synth market is built to
make the peer-relative target testable, with attention planted to lead grail
inflections by 30 to 60 days and celebrity events planted to lead them by 5
to 55, so these results prove that the mechanics work end to end. They are
not a claim about real resale markets. The number that will matter is the
Phase 8 decision backtest in margin and sell-through, not anything on this
page.

## The problem and why accuracy is the wrong metric

Positives are rare: 35 of 863 labeled rows, about 4%. A model that predicts
"never a grail" scores 96% accurate and is worthless. So the metrics are the
ones a reseller actually feels: precision@k (of the top k families I flag,
how many really outperformed their peers) and recall@k (of the families that
outperformed, how many the top k caught). Precision asks if the watchlist is
clean, recall asks if it is complete.

## The split

Time, never random. The model trains on prediction moments before
2025-09-01 and is tested on moments at or after it. A random split would let
a family's March observation grade a model that trained on its January one,
and the score would be a pleasant lie. The boundary is a real constraint,
not a convenience: the positives cluster in the 2025 grail wave, and this
date is the most balanced cut the coverage allows, 17 positive rows in train
against 18 in test. It is logged to MLflow with everything else.

## Headline: the model does not beat the naive baseline here, and that is honest

The baseline is the "flag whatever people are googling more" screen: rank
families by raw rising search interest and take the top k. A model that
cannot beat that has no reason to exist. On this synthetic data it does not
beat it.

| metric | model | search baseline |
|---|---|---|
| precision@5 | 1.00 | 1.00 |
| precision@10 | 1.00 | 1.00 |
| precision@20 | 0.75 | 0.85 |
| recall@20 | 0.83 | 0.94 |
| PR-AUC | 0.94 | 0.96 |

At the top of the watchlist, where a capital-constrained reseller actually
buys, the model and the baseline are both perfect: the top 10 flagged
families are all real outperformers either way. Deeper down, at k=20 and on
PR-AUC across the whole test set, the baseline edges ahead.

The reason is in the generator, and it is worth saying plainly rather than
hiding. The synth market makes search interest lead the price inflection
almost deterministically, so raw search slope sits very close to the
data-generating mechanism. Beating it with a model that also has to learn
from 17 positive examples is a tall order, and the model ties it at the top
and trails it slightly at the tail. On a real market, where search is noisy
and leads inconsistently, the peer-relative and celebrity features have more
room to add signal over the raw screen. That is a claim the synthetic data
cannot test, and I am not going to pretend it did.

Keeping the model simple was deliberate. The craft this project is betting
on is the clean catalog, the peer-relative label, and the leak controls, not
the architecture. A fancier model tuned until it edged past the baseline on
synthetic data would be a worse result, not a better one: it would be
fitting the generator, and it would read as a win it did not earn.

## Feature importance: a sanity read, and the celebrity mechanics check

| feature | share | rank |
|---|---|---|
| search_slope_60d | 27% | 1 |
| search_slope_60d_peer_z | 11% | 2 |
| celebrity_recency_days | 9% | 3 |
| price_momentum_60d | 9% | 4 |
| rare_tier_premium | 8% | 5 |
| ... | | |
| celebrity_event_count_90d | 4% | 10 |
| brand | 0% | 17 |
| category | 0% | 18 |

This profile is what it should be. Raw and peer-relative search lead,
because attention leads price in the generator. Price momentum, the rare-tier
premium, and the retail spread all contribute at sensible weights. Brand and
category carry zero importance, which is the right answer for a
time-sensitive target: if a static attribute were the top signal it would
mean the label was imbalanced by group and the model was memorizing which
brands get labeled, so their sitting at the bottom is a leak check passing,
not a feature wasted. The automated leakage smell test raised nothing.

The celebrity features get a specific look because Phase 6b planted the
events to lead grail inflections, so high importance there is expected and is
a mechanics check, not a market finding. celebrity_recency_days lands at rank
3 with 9% of total importance and celebrity_event_count_90d at rank 10. That
is the planted signal showing up exactly where it was planted. It says the
6b wiring feeds the model as intended. It says nothing about whether a real
Carti co-sign moves the Rick Owens market, and the docs have been careful
never to claim otherwise.

## Threshold sensitivity

The label calls a family positive at peer_z >= 2.0 and uplift_edge >= 0.15.
Both are judgment calls. Because the label stores the raw z and edge per row,
moving them is pure re-thresholding, not a re-run, across all 863
peer-measurable rows.

| | edge 0.10 | edge 0.15 | edge 0.25 |
|---|---|---|---|
| z 1.5 | 46 | 35 | 27 |
| z 2.0 | 46 | 35 | 27 |
| z 2.5 | 38 | 35 | 27 |

Two things fall out. The default cell reproduces the 35 labeled positives
exactly, which is a quiet confirmation that the stored z and edge and the
labeler agree. And the count barely moves with z: pulling the z floor from
1.5 up to 2.5 at the default edge leaves it at 35 the whole way, because the
real positives sit comfortably above z=2.5. The binding knob is the edge, not
the z. That is useful to know before Phase 8 tunes the watchlist size: the
watchlist grows and shrinks with the edge floor, and the z threshold has
slack in it.

## What this establishes, and what it does not

The pipeline trains a leak-controlled model on the peer-relative target,
ranks a watchlist, scores it against a real baseline, and the feature
importances make domain sense with the planted celebrity signal landing where
it should. That is the mechanics working. What it does not establish is that
the model beats a naive screen on a real market, or that any of these
precision numbers survive contact with noisy data. The decision backtest is
the next phase and the real test, and it will carry the same synthetic caveat
on every number it reports.
