# The decision backtest: what acting on the watchlist would have done

This is the headline of the whole project, and it is a decision, not a score.
The question is the one that costs a reseller money: if I had acted on this
watchlist at a historical cutoff, bought the flagged families, held them, and
moved them when the signal said to, what would it have done to my margin and
my sell-through. Every number here is synthetic and demonstrates the
framework and its mechanics, not a realized market return. The synth market
plants attention and celebrity events to lead grail inflections, so it can
prove the machinery end to end; it cannot tell you what a real Rick Owens
position returns.

## How it is set up

The backtest rebuilds the watchlist as it would have looked at each of seven
monthly cutoffs from 2025-09-01 to 2026-03-01. Those are the out-of-sample
cutoffs: the model trained on moments before 2025-09-01, so at every cutoff
here its scores are real predictions, not memory. Two later cutoffs are
dropped because the 120-day hold horizon would run past the end of the sales
data, and a sell price extrapolated past the comps is not a comp.

The buy decision is strictly as-of. The watchlist at a cutoff is drawn only
from rows dated that cutoff, and a test perturbs the future to prove that
inventing later data cannot change what would have been bought. The sell
decision and the realized prices do read the future, on purpose, because that
is the outcome being measured, the same line the label already draws between
what defines the answer and what the model may see.

Three policies run over the same cutoffs with the same sell logic, so the
only thing that varies is the buy:

- **model**: buy the families whose model score clears 0.5, more likely than
  not to outperform their peers, up to five per cutoff.
- **search**: the naive screen, buy anything whose raw search interest is
  rising (slope > 0), up to five, ranked by that slope. This is the "flag
  whatever people are googling more" baseline the model has to justify itself
  against.
- **none**: buy nothing, the floor that answers "did acting beat sitting still".

Stated assumptions, all knobs in BacktestConfig and PricingConfig: buy at the
comp median (at-market, no assumed sourcing discount, which is conservative),
sell at the trailing-60-day comp median on the sell date (recent market, not
the theoretical peak), a 12% fee off the sale for platform and payment, zero
hold cost on synth (a real knob for storage and capital), and a sell
triggered when the model score fades below 0.5 or the piece ages past 120
days. The worth estimate condition-adjusts comps to a reference grade; the
synthetic sales carry no condition, so that adjustment is a no-op here and
exists for real data.

## The result

| policy | trades | total margin | return/trade | precision | days to sell |
|---|---|---|---|---|---|
| model | 14 | $2,260 | 38.6% | 1.00 | 54 |
| search (naive) | 35 | $1,367 | 13.8% | 0.49 | 41 |
| buy nothing | 0 | $0 | - | - | - |

Both active policies beat sitting still, and the model roughly doubles the
naive screen's realized margin on less than half the trades. But the
mechanism is worth being precise about, because it is not what the headline
number alone would suggest.

## Where the difference actually comes from

Per cutoff, the two policies are identical at the top of the grail wave:

| cutoff | model n / margin | search n / margin |
|---|---|---|
| 2025-09 | 5 / $1,094 | 5 / $1,094 |
| 2025-10 | 5 / $834 | 5 / $834 |
| 2025-11 | 3 / $311 | 5 / $415 |
| 2025-12 | 1 / $21 | 5 / $503 |
| 2026-01 | 0 / $0 | 5 / -$579 |
| 2026-02 | 0 / $0 | 5 / -$458 |
| 2026-03 | 0 / $0 | 5 / -$442 |

In September and October the two buy the same five families for the same
dollars: when the wave is obvious, a calibrated model and a naive
rising-search screen agree, which is exactly the tie the Phase 7 ranking
metrics already showed. The naive screen even makes more gross inside the wave,
$2,846 across the four wave cutoffs against the model's $2,260, because it
keeps buying the marginal-but-still-rising families in November and December
that the model's 0.5 bar filters out, and on synthetic data those still paid.

The model wins on what it does next. From 2026-01 on there are no real
outperformers left, and the model's scores collapse below the bar, so it buys
nothing and loses nothing. The naive screen has no bar. It keeps flagging five
families a month because something is always googling-up in the noise, and it
gives back $1,479 in the quiet quarter, more than erasing its wider wave
haul. The model's edge is discipline in two forms: higher conviction per trade,
every one of its fourteen buys outperformed, and the willingness to stop when
nothing is worth buying.

That is the case for why the decision backtest is the headline and precision@k
was not. At a fixed k the two policies tie, because precision@k forces exactly
k picks and cannot express "buy fewer" or "buy none this month". The money
question can, and it is where the model's calibration shows up as dollars.

## What this does and does not establish

It establishes the framework: an as-of watchlist rebuilt without leakage, a
stated buying and selling policy, realized in margin and sell-through against
a real baseline and against doing nothing, with the pieces the reseller feels,
conviction, precision, days to sell, and price realization, all falling out of
it.

It does not establish a return. The clean abstention leans on scores that go
nearly binary on synthetic data, near 1 while a family is running and near 0
once it is done; real scores would be graded and the 0.5 gate would be fuzzier,
so the quiet-period discipline would be messier than it looks here. The two
policies gate on different bars, a probability against a slope, which is fair
as each is the honest flag for its own signal but is not a controlled
comparison of one threshold. Seven cutoffs is a small backtest. And the whole
thing rides on a synthetic market built to make the mechanics legible. The
number to trust is the shape of the decision, not the 38.6%.
