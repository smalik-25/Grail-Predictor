# Celebrity and editorial signal: detection and explanation only

The celebrity layer detects "named cultural figure co-mentioned with
brand/piece" from text and event data, lands events in
fact_celebrity_events, feeds two features (celebrity_event_count_90d,
celebrity_recency_days), and surfaces plain-language reasons on the
watchlist ("flagged: Carti-associated, search accelerating"). That is the
whole scope. Two hard lines:

**Metadata and text only, never biometrics.** Detection reads post titles,
listing text, and event co-occurrence. It never runs facial recognition or
any biometric inference on images. That is the legal line under
biometric-privacy rules (BIPA and its siblings), and it is also the
cheaper path: the text that moves this market says the names out loud.

**No causal claims.** Estimating the true lift of a co-sign is a causal
inference problem this project explicitly does not attempt in v1. A
detected event is a feature the demand model may weigh and a reason a
human can read. "Carti-associated" on the watchlist means "we detected
this," never "this caused that."

## How detection works

Precision comes from a curated list, not from NER over everything.
data/reference/celebrity_figures.json holds the figures who actually move
this market, seeded from the case-study names and grown by hand, which is
where domain knowledge enters as data. A text becomes an event only when a
listed figure's alias and a brand alias co-occur in it.

Specificity is graded, and the event attaches at the most specific grain
its text supports:

| Text contains | Attaches at | Confidence |
|---|---|---|
| figure + brand + model line + era/year | family_id | 0.9 |
| figure + brand + model line | brand + model line | 0.9 |
| figure + brand | brand-wide | 0.6 |

The feature layer widens brand-level events to every family under that
brand, which mirrors how attention actually spreads: a Carti co-sign on
"Rick Owens" lifts interest across Rick Owens families, while "the 2013
Futures" pins one generation.

## Two sources, kept separate

There are two independent event sources, and they deliberately do not feed
each other.

The **detector** reads real text: Reddit post titles from the watched subs
(already ingested), public listing text, and, later, search-spike
co-occurrence. All free, all public, all text. It proves the mechanic, a
figure alias and a brand alias in one sentence become a row in
fact_celebrity_events, and it lands those rows in the warehouse. Its events
carry real brands (Rick Owens, Maison Margiela) that resolve to real
families the catalog holds.

The **synthetic events** (data/fixtures/synth_celebrity_events.json) are
what the feature pipeline actually reads, because the model trains on synth
families and the detector's real-brand events do not belong to them. They
are generated at the family grain by ml/synth.py with its own seed, planted
to lead grail inflections. So the detector demonstrates detection and the
warehouse load, while the synth events give the features something to weigh.
Both speak the same figure vocabulary; neither pretends to be the other.

The paid alternatives in this space (media monitoring vendors, licensed
editorial-photo feeds) would raise fidelity, and the fidelity gap is stated
rather than papered over: fewer sources means missed events, and a missed
event is a feature at zero, not a wrong label.

## As-of discipline

Events join features under the same contract as every other source: only
events dated at or before the prediction moment are visible to a row, the
latest event date joins the max_source_date_used audit stamp, and the
tests include an event dated after the cutoff plus a wrong-brand event,
both of which must move nothing.

## Synthetic caveat

The synthetic market plants events 5 to 55 days before grail inflections
(a co-sign precedes the move) and sprinkles noise events on flat and drift
families so the model cannot learn "any event means buy". On the current
generation (48 families, seed 11, celebrity seed 1109), positive rows
average 1.23 events in the trailing 90 days against 0.01 for negatives, and
33 of the 863 feature rows carry a nonzero count. That gap is the mechanics
check the model phases lean on, not a market finding. Mechanics, not
markets, as everywhere else.
