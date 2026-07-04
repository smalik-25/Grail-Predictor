# DEVLOG

Append-only, one entry per work session, newest at the bottom. Each entry covers: what I built, why I made these decisions (with the tradeoffs named), what I learned or got stuck on, what's next.

---

## Scaffold

**What I built**

The full repo skeleton before any real data: ingestion/ with a stub per source (Grailed, eBay, Depop, Vestiaire, The RealReal, 2nd Street, retail, trends, social, plus the orchestrator), resolution/ for the entity resolution pipeline, db/ for the hand-written schema, empty dbt_project/, features/, ml/, dashboard/, data/ split into raw (gitignored), fixtures, and reference, docs/, tests/, and the CI directory. Plus the machinery: README with the phase checklist at the top, this DEVLOG, .env.example covering Bright Data, Reddit, and eBay credentials, .gitignore, and thin Makefile, Dockerfile, requirements.txt, and pyproject.toml. Every Python module is a docstring stub saying what it will do and which phase builds it, so the tree reads as an architecture diagram from the first commit.

**Why these decisions**

requirements.txt starts with pytest and nothing else. Dependencies get added in the phase that first imports them. The alternative was pinning the whole imagined stack now, Splink, PySpark, LightGBM, the lot, and that turns into fiction the first time a library choice changes under measurement, which Phase 2 is explicitly designed to allow. The cost is that early commits do not install the eventual stack. I can live with that.

data/raw/ is excluded with a wildcard plus a kept .gitkeep, and fixtures live in their own tracked directory instead of inside raw with an exception rule. "Raw never enters git" is a rule I can state in one sentence and audit at a glance. "Raw is in git except when" is not.

Stub modules over empty files: a few docstrings will be wrong by the time their phase arrives, and I would rather correct a docstring than stare at a bare tree for eight phases.

Makefile and Dockerfile are deliberately thin. They get finalized in Phase 9 when there are actual stages to wire up. Writing them now means writing them twice.

**What I learned or got stuck on**

Nothing hard yet. One environment note worth recording: part of my workflow runs in a Linux dev container, so the virtual environment gets created on my machine, not in the session. .venv is gitignored and the setup commands live in the README.

**What's next**

Grailed ingestion. Before building anything, confirm the current realistic access path and write up the choice, including whatever ToS gray areas it touches.

---

## Ingestion, all nine sources

**What I built**

The full ingestion layer. A shared base module with the RawListing record, the fixture machinery, and a single Bright Data seam. Nine clients: Grailed, eBay, Depop, Vestiaire, The RealReal, 2nd Street, retail (SSENSE, DSM, Saks), Google Trends, and Reddit. An orchestrator that lands one timestamped JSON file per source in data/raw/. Nineteen tests that run with no credentials and no network. A full fixture run lands 57 records across all nine sources. I built Grailed and eBay first and verified them end to end before touching the rest, per the plan, but the whole phase ships as one commit since that's the rhythm now.

**Why these decisions**

Fixtures are platform-shaped payloads, not pre-parsed records. A Grailed fixture looks like an Algolia hit, a Depop fixture looks like the webapi response, and both go through the exact parser live data would. The alternative, fixtures in my own clean schema, would have been faster to write and would have tested nothing but the dataclass constructor. Cost: fixture authoring took real time, and when a live payload shape drifts the fixture has to drift with it.

Live mode is double-gated: INGEST_LIVE=1 plus per-source credentials. Without both, every client stays on fixtures. This means the default behavior is deterministic on any machine, including CI, and nobody accidentally hammers a platform because they happened to have creds in their env.

Per platform, the access calls and what they cost me:

Grailed has no API, but its search runs on Algolia and the search credentials its own frontend uses can query the index directly. Those values rotate, so they're env vars, not code. Grailed's ToS doesn't welcome scraping; this is public listing data at a polite rate, and I'm calling it what it is, a gray area.

eBay was the biggest surprise. The Finding API, which had findCompletedItems, was decommissioned in February 2025, and the Marketplace Insights API that replaced it is restricted to approved partners. There is no sanctioned free path to sold comps anymore. So actives come from the Browse API with proper OAuth, clean and official, and sold prices come from the public completed-listings search pages, parsed defensively. Gray area, documented, and not optional: sold prices are the ground truth the whole label depends on.

Depop has an unauthenticated JSON search endpoint its own site uses. ToS prohibits scraping, so it's low-volume and polite, and it's in the same gray bucket as Grailed.

Vestiaire and The RealReal sit behind DataDome and Cloudflare, and free scraping there isn't realistic at any useful volume. Both route through Bright Data Web Unlocker. That's the one part of the stack that costs money, roughly a dollar and a half per thousand requests, so a weekly pull of a few hundred pages stays under a dollar a month. One honest caveat: the live parsers for both target embedded page JSON and are best-effort until the first funded run. The fixtures and tests carry those modules until then.

2nd Street's US site is plain server-rendered HTML, so it's direct requests, no middleman. It also brings the best normalization problems in early: Japanese letter-grade conditions and centimeter shoe sizing, which Phase 2a has to handle anyway.

Retail: DSM's e-shop is Shopify, which exposes the public products.json endpoint, free and clean. SSENSE serves JSON from its own endpoints. Saks is behind Akamai, routes through Bright Data, and is the lowest priority of the three since SSENSE and DSM cover most of the watchlist.

Trends: the plan said pytrends, but pytrends was archived in April 2025 after years of 429 misery, so I went with trendspy, the successor its own maintainer points to. Every free Google Trends client is fragile by nature; if the trends signal earns its way into the model, this is the dependency most likely to break first.

Reddit via PRAW is the only fully clean source in the layer. Official API, free tier, no asterisks.

**What I learned or got stuck on**

The access landscape moved under the plan between writing it and building it: pytrends dead, eBay's Finding API gone. Researching each source before building it, instead of trusting the plan's assumptions, earned its keep on day one of real work.

**What's next**

Phase 2a, text-based entity resolution. The fixtures already carry the problem on purpose: the same Geobasket listed as "43", "US 10", "sz 10", and "27.5cm", conditions ranging from "B" to "Very good condition" to "used, some creasing". Normalize, block, match, measure.

---

## Entity resolution, text first

**What I built**

The four-stage resolution pipeline: normalize (brand vocabulary with title-scan fallback, footwear-to-EU and clothing-to-letter sizing, ordinal conditions including Japanese letter grades, season tags in both SS03 and 03SS orderings), blocking on brand plus category, a multi-signal matcher, and catalog assembly via union-find over above-threshold matches. Reference data in data/reference/ (brands.json, sizing.json). Parquet output. Forty-five new tests, 64 total, all green. Full reasoning and measurement in docs/entity-resolution.md.

**Why these decisions**

The matcher combines a list of signals (title 0.70, season 0.15, price 0.15) even though the list is text-only today. That structure is the whole point: if image similarity earns its way in later, it's one more entry in a tuple, not surgery. Signals with missing data return None and their weight redistributes, so a pair without prices isn't punished for the platform's sparseness.

I went with rapidfuzz over Splink, against the plan's lean, and the reasoning is in docs/entity-resolution.md. Short version: Fellegi-Sunter pays off with many comparison fields and volume enough to estimate per-field probabilities; this problem is one dominant title signal plus two weak helpers, and a deterministic combination I can inspect line by line beats a probabilistic model I'd be fitting on noise. The cost is hand-tuned weights, stated plainly.

Threshold 0.70, with a borderline band from 0.55 logged for review instead of silently guessed. The borderline log turned out to be the most informative artifact of the phase: it's where every trap I planted landed.

**What I learned or got stuck on**

The first full run over-merged and taught me the subset failure mode: token_set_ratio scores a perfect 1.0 when a short title's tokens are a subset of a longer one's, so Vestiaire's "Jacket" matched every Balenciaga jacket in its block and union-find welded them into one item. The fix is a sparsity guard scaling the title score by the informative token count of the sparser title, with a regression test. This one would have been ugly to discover at volume.

Measurement on fixtures: 30 listings, 15 canonical items, 20 listings (67%) resolved into cross-platform items, 10 singletons, 15 borderline pairs. The residual is not random: it's Vestiaire-style sparse-generic titles (where the photo carries the identity), Japanese synonym gaps (blouson vs jacket), and a currency-naive price signal that penalized the GBP Depop ramones against its USD Grailed twin. The 2b decision is recorded as deferred: fixture numbers are an existence proof, not a measurement, so build-or-skip waits for residual numbers from real ingestion volume. The seam is ready either way.

**What's next**

Phase 3, the star schema and loader. The canonical item becomes dim_items and everything else hangs off it.

---

## Schema and warehouse

**What I built**

db/schema.sql by hand: dim_items and dim_platforms, then fact_listings, fact_retail_prices, fact_search_interest, and fact_social_mentions, each with a natural-key UNIQUE constraint, domain CHECKs, and the indexes the marts will actually use. A loader in db/load.py that applies the schema, seeds platforms, and bulk-loads with execute_values, all inserts ON CONFLICT DO NOTHING. docker-compose.yml for a local Postgres 16. docs/erd.md walks every design call. Thirteen new tests: the row builders are pure functions tested dry, the DDL is parsed as Postgres by sqlglot at test time, and the end-to-end load (including a load-twice idempotency assertion) is gated on DATABASE_URL so CI stays green without a database.

**Why these decisions**

DO NOTHING over upsert, deliberately. Asking prices change, and an upsert would silently overwrite history with the latest observation. Keeping the first-landed row is conservative and honest; when price-drop tracking matters, the answer is a price-observation table, not an upsert that eats history.

item_id is nullable on the retail, search, and social facts. Linking a retail product or a Reddit post to a canonical resale item is entity resolution, and doing it in the loader with string equality would fill the warehouse with confident-looking false links. The rows keep their own identities so the linkage can be built properly and backfilled. The spread signal needs this linkage eventually, so it's a named gap, not a forgotten one.

The sold_fields_consistent CHECK exists because a sold_price on an unsold row is a loader bug by definition, and I'd rather the database refuse it than discover it in a mart.

One tradeoff accepted: Postgres treats NULLs as distinct in UNIQUE constraints, so url-less listings bypass the listings natural key. Only fixtures lack urls; synthetic keys for that case would be complexity without a customer.

**What I learned or got stuck on**

The dev container has no Docker and no root, so the live end-to-end load couldn't be verified there. The compromise shaped the design for the better: everything testable without a connection got factored into pure functions, and the one test that genuinely needs Postgres skips itself when DATABASE_URL is absent. The live verification runs on my machine with docker compose up.

**What's next**

Phase 4, the dbt layer: staging, the spread and velocity intermediates, and the two marts the dashboard and model read.

---

## dbt transformation layer

**What I built**

Fourteen models in three layers, thirty schema tests, one seed. Staging: one typed view per warehouse table, with prices normalized to USD through an fx_rates seed. Intermediate: sale events, a per-item daily resale series, the brand retail baseline, the retail-vs-resale spread, price velocity normalized to a 30-day pace, and listing liquidity (counts plus sold-through). Marts: mart_item_price_history (the per-item time series with 3-sale rolling average and deltas, via window functions) and mart_item_current_state (one row per item; the dashboard and the model's inference path read this and nothing else). Every model opens with a one-line purpose comment. Tests: unique and not_null on keys, relationships from listings and sales back to stg_items, accepted_values on platform_type and spread_basis.

**Why these decisions**

The spread signal has a linkage problem I refused to paper over. Retail rows aren't resolved to canonical items yet (that's its own entity resolution, recorded in the Phase 3 notes), so an item-level retail-vs-resale spread isn't computable honestly. Instead the spread uses a brand-level retail median as the reference, and every spread row carries spread_basis = 'brand_proxy' saying so. When item-level linkage lands, those rows flip to 'item' and no consumer changes. The alternative was silently joining retail to items on brand and pretending it was item-level. The column exists so nobody, including future me, mistakes the proxy for the real thing.

FX rates are a static seed (four currencies, one rate each), not a dated rate table. At fixture scale, dated FX would be precision theater: the fixture prices themselves are hand-written. The seed's job is making the currency normalization path real so the models and tests exercise it; swapping the seed for a dated reference table later changes one ref and zero model logic.

The daily resale series (int_item_daily_resale) exists so every window function downstream runs on a unique (item, day) grain. Without it, two same-day sales on different platforms would double rows through every join and the mart's uniqueness test would be a lie waiting to fail.

profiles.yml is checked in, which is normally wrong. Here the warehouse is a disposable local Postgres holding fixture data, credentials grail/grail from docker-compose. Nothing to protect, and every cloner gets dbt build working with zero setup.

**What I learned or got stuck on**

The sandbox has no Postgres, so verification split again: dbt parse (structure, refs, jinja) ran clean in-session, and dbt build with its 30 tests runs on my machine against the docker warehouse. Also dbt 1.12 deprecation-warns the yml test shorthand; noted, ignoring until the syntax actually breaks.

**What's next**

Phase 5, labeling. Short on code, long on thinking: what "became a grail" means as a number, and the as-of cutoff that keeps every feature honest.

---

## Labeling the target

**What I built**

ml/label.py: an item labels positive at prediction moment T if its median sold price over (T, T+180d] is at least 1.5x its median over (T-90d, T], minimum two sales per window, moments on an aligned monthly grid clipped to full data coverage. Every labeled row carries its prediction_moment explicitly. Also ml/synth.py, a seeded synthetic market generator (40 items, ~1,500 sales over two years, three named regimes: flat, drift, grail inflection), because the hand-written fixtures span three months and cannot produce one labelable example under a 90d+180d design. Nine new tests. docs/labeling.md confronts the biases at length.

**Why these decisions**

The two decisions I'd defend hardest:

Thin data is excluded, not labeled negative. An (item, moment) without two sales on each side is unmeasurable, and calling it "not a grail" teaches the model that illiquidity means failure. The cost is honest and stated: the earliest, most illiquid phase of a grail's life is invisible to a price-based label.

The leak tests are invariance proofs, not spot checks. Multiply every post-T price by ten and the baseline must not move a cent; rewrite pre-T history and the outcome must not move. Both directions are tested, and the future-spike test also asserts the outcome DID change, so the probe can't silently probe nothing.

A subtle one the step-change test caught by design: an item that already inflated labels negative afterward, because 1100 over 1100 is 1.0. The target is the inflection, not the plateau. That's the difference between predicting up-and-comers and re-discovering known grails.

On the synthetic data: it's a mechanics harness with ground-truth regimes, not a market claim. On it, positives land exclusively on grail-regime items (29% of their moments, the ones whose outcome window catches the ramp) and never on flat or drift items, at a 4.4% overall positive rate. That proves the machinery, and docs/labeling.md is explicit that evidence about real grails waits for real ingested history.

**What I learned or got stuck on**

Nothing stuck, but writing the doc surfaced that the 1.5x threshold needs a sensitivity check in Phase 7's evaluation before any reported number gets trusted. Noted there so it doesn't get lost.

**What's next**

Phase 6, feature engineering in PySpark, every feature as-of the prediction moment, with a leak canary in the tests.

---

## Feature engineering, as-of or nothing

**What I built**

features/build_features.py in PySpark: price momentum, sold velocity, retail spread at cutoff and its trend, search interest slope and acceleration, social mention velocity, plus brand, category, collab and archive flags. Every window ends at the prediction moment, never after. The synthetic generator grew weekly attention series where, for grail items, search and social interest start climbing 30-60 days before the price inflection, which encodes the project's core hypothesis (attention leads price) as a learnable signal. Output is Parquet partitioned by prediction year, gated by Pandera. Nine new tests, 100 total.

**Why these decisions**

Every feature row carries max_source_date_used, the latest source date that touched any of its aggregations, and Pandera fails the entire build if any row's value exceeds its prediction moment. That turns "no look-ahead" from a code-review promise into a machine-checked invariant that travels with the data. The leak canary test goes further: add absurd post-cutoff rows (a 99,999 dollar sale the day after cutoff) and assert the features are bit-identical, via assert_frame_equal with check_exact.

Year partitioning, because the Phase 7 time split selects by prediction moment, so year pruning is the actual read pattern. Item-level partitioning would mean thousands of tiny files for a read path nothing uses.

One Spark action, not two: the job collects once, validates the pandas frame, and lands it via pyarrow with the same partitioning. Writing through Spark as well would re-execute the DAG to serialize a few hundred rows. At real scale the write flips back to Spark; the comment in the code says exactly that.

Nulls are policy, not accident: sold_velocity, brand and category are required non-null; momentum, spread and attention features are nullable because a window with insufficient data has nothing honest to say, and the model phase gets to decide how to treat missingness (gradient-boosted trees handle it natively, which is part of why Phase 7 uses them).

Sanity read on the output: positives average a search slope of 0.30 against 0.007 for negatives, exactly the lead the generator planted. On synthetic data that's a mechanics check, not a discovery, and the evaluation doc will keep saying so.

**What I learned or got stuck on**

Two environment fights worth recording. PySpark in a sandbox without a resolvable hostname dies at JVM launch with an opaque gateway error; the fix is pinning SPARK_LOCAL_IP and the driver bind address to loopback, which is harmless everywhere and now lives in both the builder and the test fixture. And installing a 318MB sdist under a 45-second process cap taught me more about pip's cache layout than I wanted to know.

**What's next**

Phase 7: train the deliberately boring model, split by time, and evaluate it against a naive baseline with precision at k. The 1.5x label threshold sensitivity check lands there too.

---

## The pivot: from grail predictor to reseller decision tool

**What changed and why**

The project reframed mid-build. The old question was "which pieces are about to become grails," a prediction for its own sake. The new question is the one that actually costs a reseller money: what should I buy, what is it worth, and should I hold it or move it. Predicting popularity is only useful if it drives those three calls, so the north star is now operational. Three problems, stacked: demand forecasting per style-family is the spine (peer-relative uplift over the next 60 days), pricing and markdown ride on top as a thin layer (condition-adjusted comps plus a hold-or-move flag, no elasticity model because I can't identify elasticity honestly without experimental price variation), and celebrity/editorial impact becomes a detected feature and a watchlist explainer, never a causal model.

The unit of analysis changes with it. The old build resolved listings toward canonical items. The new grain is Brand, then model-line, then era or generation, with material/colorway tier as a sub-attribute. This came out of the Margiela Future case study: value concentrated in specific generations and specific colorway tiers, not uniformly across every Future ever made. Hype on a generation lifts the family; hype on one colorway is caught by the tier without splintering the data too thin to forecast.

**What survives and what gets reworked**

Phases 0 through 4 survive whole: ingestion doesn't care what the listings mean downstream, and the warehouse and dbt mechanics are grain-agnostic. The resolution pipeline survives below the catalog layer (normalize, blocking, the signal-combining matcher); catalog assembly regains a family layer on top. Labeling keeps its harness (leak invariance tests, thin-data exclusion, the synthetic generator) and swaps the target from an absolute 1.5x threshold to peer-relative uplift on a blended signal. Features keep every as-of control and regrain. The old item-grain labeling and features are being committed as built, alongside this pivot, because the history should show the reframe rather than pretend the project was always this.

The evaluation story changes the most: the headline is no longer precision at k, it's a decision backtest. If a reseller had bought the top flagged families at historical cutoffs and sold when the hold-or-move flag said move, what happens to margin and sell-through against a naive baseline. That's the difference between a model and a tool.

**What's next**

Phase 2c: regrain resolution to style-families, with the model-line and era reference data, colorway tiers, and the schema migration riding along.

---

## Style-families: the regrain

**What I built**

The family layer on top of the existing resolution pipeline, none of which changed. New reference data: model_lines.json (per-brand alias vocabularies with era tables where I can document release history, seeded with the Margiela Future generations from the case study) and colorways.json (extraction vocabulary plus per-line tier maps). resolution/family.py assigns (brand, model_line, era) and a colorway tier per listing; catalog.py now assembles both layers, items below for comps, families above for forecasting. Readable family ids like maison-margiela__future-high-top__yeezus-era-2013-2014, because a self-documenting key beats a hash in every downstream table. Schema gains dim_style_families plus family_id, colorway, colorway_tier on fact_listings; the loader loads them; the dbt marts regrained to mart_family_price_history and mart_family_current_state, the latter now carrying a rare-tier premium ratio. Twelve new tests, 124 total.

**Why these decisions**

family_id lives on the listing, not the item. The first full run showed why: the text matcher merged the 2013 and 2018 Futures into one canonical item, because on titles alone they are near-identical. The era table caught it, the conflict logged, and the two generations landed in separate families. An item can span eras when text blurs generations; a listing always knows its family. The grain survives the matcher's mistakes, which is the whole point of layering them.

Identity propagation is the piece that makes the grain usable on real listings. Most listings carry no year and many are titled by people who can't spell the model line. But when the matcher has already decided "RO leather high top black sz 10" is the same product as the FW10 Geobasket, that listing inherits the model line and the era through the item, with conflicts refusing to propagate. Family coverage on fixtures went from 66% to 81% on this one mechanism, and the sparse Vestiaire titles that motivated the Phase 2b discussion get family membership for free.

Colorway tier stays out of the family key without exception. The Geobasket family holds core black and rare dust as tiers inside it, and the current-state mart exposes the rare-tier premium as a ratio over the family median. Splitting on colorway would have given cleaner per-colorway price signal and data too thin to forecast; the case study said generation-level moves and colorway-level moves both happen, so the grain has to carry both without fragmenting.

The residual is honest and useful: six fixture listings have no resolvable model line, including the 2nd Street NAVY cardigan phrased without any alias the vocabulary knows. That's where domain knowledge enters the project as data: the vocabulary file grows with real volume, and knowing that "mohair cardigan navy 03SS" from a Japanese seller IS the Kurt is exactly the edge a keyword list can't fake.

**What I learned or got stuck on**

The era fragmentation problem showed up in the first run: one FW10-tagged Geobasket in the right era, four siblings stranded in era-unknown. The fix wasn't better parsing, it was realizing the item layer already held the answer. Resolution layers should feed each other.

**What's next**

Labeling v2: peer-relative uplift on a blended signal at the family grain, with the synth generator regrown to families and peer structure.

---

## Labeling v2 and features v2: the peer-relative target

**What I built**

The synthetic market regrown at family grain with layered price factors: a shared market swell plus secular drift that every family floats on, per-brand drifts, and the per-family regime curve on top, with rare-tier sales priced above baseline and attention still leading grail inflections by 30 to 60 days. The label rebuilt: blended log-uplift (price 0.50, search interest 0.30, sales velocity 0.20, missing components redistributing their weight) over 60-day windows, measured against a peer set with a recorded fallback ladder (brand+category+price band, then brand+category, then category+band), positive at robust z >= 2 with a 15-point edge floor. Features regrained to family with peer z-scores computed in Spark at (moment, category), a rare-tier premium feature, and celebrity_signal columns wired as zeros until 6b fills them. Every as-of control survived: max_source_date_used, the Pandera cutoff gate, bit-equality leak canaries, both-direction label invariance. 131 tests.

**Why these decisions**

The market factor in the generator is the argument for the target, executable. Six families doubling in lockstep must label nothing, and that negative control is a test now. An absolute threshold would have flagged all six; a reseller's capital is finite and the watchlist ranks what beats the market, it doesn't describe the market.

Two design decisions came out of failures during the build, which is where they should come from. First, the degenerate-MAD bug: with perfectly flat peers the spread is zero, and my first cut zeroed the z, hiding an 81% outperformer behind motionless peers, the exact case the label exists to catch. The fix is a 0.05 log-point spread floor, and the reasoning lives next to the config knob. Second, peer sparsity: brand-by-category cells are thin (two thirds of pairs came back peer-unmeasurable on the first run), so the label got its fallback ladder with the basis recorded per row, and the feature z-scores went category-wide outright, because a dense approximate input beats a precise null one. The label stays strict; the features don't have to be.

One line worth keeping from the docs: the label legitimately reads the future of peers, because peer outcomes are part of the outcome definition. Features never may. The line is between what defines the answer and what the model is allowed to see.

**What I learned or got stuck on**

Stale Parquet partitions bit once: pandas appends files per partition directory, so a grain change left old item-grain rows mixed into the features dataset until the writer learned to clear the directory first. Cheap lesson, now structural.

**What's next**

Phase 6b, the celebrity/editorial detector: fact_celebrity_events, the curated figure list, and the features flipping from stubs to signal. Then the retargeted train and evaluate.

---

## Celebrity and editorial signal, wired end to end

**What I built**

I came in to start the training phase and found the celebrity phase was not actually finished, only half-built and never committed. The detector module and the figure list were there and the unit tests on explicit text passed, but nothing connected: there was no fact_celebrity_events table in the schema, no loader path for it, the social fixture the detector reads named no figures so it found nothing, and the feature builder still emitted celebrity zeros while a stale features Parquet on disk carried real celebrity values that no committed code could reproduce. So I finished it. New schema table, loader row builder and insert, two planted Reddit posts, the as-of event feature in Spark, a seeded synthetic event generator, a Makefile target, the doc, and a leak canary. 141 tests pass with the database up, one still skips without it.

The shape that matters: two event sources that stay separate on purpose. The detector reads real text, real brands, and lands events in fact_celebrity_events, which is the mechanic and the warehouse story. The synthetic events (synth_celebrity_events.json, generated at the family grain) are what the feature pipeline actually reads, because the model trains on synth families and the detector's real-brand events do not belong to them. On the fixture the detector finds two events, Carti in Rick Owens Geobaskets and Frank Ocean in the 2013 Margiela Futures, and the load holds them at two rows across repeated reloads. On the feature side, positive rows average 1.23 events in the trailing 90 days against 0.01 for negatives, 33 of 863 rows carry a nonzero count, and every row's audit stamp stays at or before its prediction moment.

**Why these decisions**

The two sources do not feed each other, and that is the honest call, not a shortcut. Bridging them would mean mapping a real Rick Owens co-sign onto some fam-synth-0003, which is a fabricated join dressed up as signal. The detector proves detection and the load; the synthetic events give the model something to weigh. Each does one job it can actually do.

family_id on the celebrity table is FK-ish and deliberately not a foreign key. The detector can name a family the catalog has not landed yet, and a hard constraint would drop that row and the signal with it. The cost is no referential integrity on that one column, which I take, because the whole reason the layer exists is to catch attention before the catalog is complete.

compute_features grew an optional events argument rather than a required one. With no events frame it reproduces the old stub exactly, count zero and recency null, so the callers that pass four frames and the existing leak canary stay bit-identical. A branch in the function is a small price for not changing the contract of everything that already reads features.

The leak stamp is the subtle part. The event date that folds into max_source_date_used is the max over the same windowed, at-or-before-cutoff frame the count comes from, so a post-cutoff event can never reach the stamp even as it is correctly excluded from the count. I did not want to trust that by eye, so there is now a celebrity leak canary that adds a post-cutoff event and asserts the columns and the stamp are bit-for-bit unchanged, the same guarantee the sales and attention canary already carries.

Synthetic event generation draws from its own seed, after the main generation, touching nothing in the pinned seed=11 stream. The proof is mechanical: regenerate, and git diff on families, sales, and attention is empty. That let me add a whole new fixture without invalidating a single downstream number the other docs and entries already cite.

**What I learned or got stuck on**

The status table said this phase was done and 140 tests passed. Three tests were red, there was no celebrity commit in the log, and the table's own schema table did not exist. The tests and the git log were right and the summary was wrong, which is the correct order of trust.

The bug that actually cost time: a brand-wide event carries a null family_id, and a null in a pandas column that also holds real ids comes across as the string "NaN", not a null. So isNull was false, the brand-wide branch missed, and the count came back zero. It is the same None-to-NaN hazard scrub_nan exists to catch on the load side, and the full synthetic run hid it completely because every synthetic event carries a real family_id. The one test that fed a null-family, brand-wide event is what surfaced it. The fix maps "NaN" back to a real null in the event select, and it is a reminder that the leak controls and the edge-case tests earn their keep on exactly the paths the happy-path data never exercises.

**What's next**

Phase 7 for real: retarget train and evaluate to the peer-relative target on the family-grain features, now that the celebrity columns are real and reproducible from committed code, with the celebrity importance read as a mechanics check on the planted signal.

---

## Training and evaluating the watchlist, and a baseline I could not beat

**What I built**

The retargeted train and evaluate. train.py and evaluate.py were still the old item-grain stubs, wrong feature names, keyed on item_id, one feature that no longer exists. Now train.py reads the family-grain feature set (own-history, the four peer z-scores, the rare-tier premium, the celebrity count and recency), splits by time at 2025-09-01, fits a near-default LightGBM classifier on the peer-relative label, and logs and registers the run to the MLflow Postgres backend. evaluate.py scores the test watchlist with precision@k and recall@k against the naive rising-search baseline, reads feature importance with a leak smell test and a specific look at the celebrity features, and runs a z-by-edge threshold sensitivity that re-thresholds the stored label z and edge rather than recomputing anything. New deps in requirements (scikit-learn, lightgbm, mlflow), two new Makefile targets, docs/evaluation.md, and eight tests on the split and the metrics. 149 pass with the database up.

**Why these decisions**

The split date is a data fact, not a preference. The positives are not spread evenly, they cluster in the 2025 grail wave, so a split has to land inside that wave or one side comes up with zero positives and the run refuses to start. 2025-09-01 gives 17 positive rows in train against 18 in test, the most balanced cut the coverage allows. Seventeen training positives is thin, and I would rather name that as a ceiling on what the model can learn than pretend the sample is bigger than it is.

The model stays boring on purpose, a LightGBM classifier at near-default settings, 200 small trees. The whole bet of this project is that the clean catalog, the peer-relative label, and the leak controls are the work, not the architecture. Which is why I did not tune the model until it edged past the baseline. That would have been fitting the generator and calling it a win.

Because the honest result is that the model does not beat the naive baseline here. The baseline ranks families by raw rising search interest and takes the top k. At the top of the watchlist, where a reseller with finite capital actually buys, the two are tied and both perfect: precision@5 and precision@10 are 1.00 for the model and 1.00 for the baseline. Deeper down the baseline pulls ahead, precision@20 0.75 against 0.85, PR-AUC 0.94 against 0.96. The reason is in the synth generator: it makes search lead the price inflection almost deterministically, so raw search slope sits close to the data-generating mechanism, and beating a near-oracle feature from 17 positives is a tall order. On a real market where search is noisy and leads inconsistently the peer-relative and celebrity features have room to add over the raw screen, but the synthetic data cannot test that claim and I did not dress it up as though it had. The plan said if the signal is weak, say so, and the pitch was always the decision backtest, not the ranking score.

Two things in the evaluation are worth keeping. The feature importance reads correctly: search and its peer z-score lead, celebrity_recency_days lands at rank 3 and celebrity_event_count at rank 10, which is the Phase 6b signal showing up exactly where it was planted, a mechanics check and nothing more, and brand and category sit at zero importance, which is the leak check passing, because a static attribute topping a time-sensitive target would mean the model had memorized which brands get labeled. And the threshold sensitivity, free because the label stores its raw z and edge, shows the default cell reproducing the 35 labeled positives exactly, and the count moving with the edge floor but not with z, since the real positives sit comfortably above z=2.5. The binding knob is the edge, which is worth knowing before Phase 8 decides how long the watchlist should be.

**What I learned or got stuck on**

Installing mlflow quietly pulled pandas from 3.0 back to 2.3. That is the more standard version for this pyspark stack, the full suite stayed green across it, so I took the downgrade rather than fight it. The more interesting lesson was the baseline. It is easy to build an evaluation that flatters the model by omitting the obvious screen it should be measured against, and the moment the naive search baseline went in, the comfortable precision numbers had company that beat them. That is the evaluation doing its job. A watchlist model that ties the "flag whatever people are googling" screen at the top and trails it at the tail is a real finding to carry into the backtest, not something to bury.

**What's next**

Phase 8, the decision backtest, the headline deliverable. Reuse the as-of machinery to rebuild the watchlist as it would have looked at historical cutoffs, simulate a stated buying policy in margin and sell-through terms, and compare against buy-nothing and buy-the-search-screen baselines. Build 8b, the pricing and hold-or-move layer, alongside it, since the simulated sell decision calls it.

---

## The pricing layer: worth as a range, hold-or-move as a rule

**What I built**

ml/pricing.py, the thin layer Phase 8 leans on. Two questions. Worth: the median of a family's sold comps in a trailing 60-day window ending at the as-of date, condition-adjusted to a reference grade, returned as a range (the 25th and 75th alongside the median) rather than a single number. Hold-or-move: a rule, not a forecast. Past the hold horizon, move regardless. No demand signal to justify a hold, move. Signal below the fade floor, move. Otherwise hold and let it run. Eleven tests on the window, the min-comps refusal, the tier filter, the condition normalization, the as-of leak, and each branch of the flag.

**Why these decisions**

A range, not a point, because a point is false precision on a market this thin. A worth estimate off three comps that reads "$1,240" is lying about what it knows; "$1,050 to $1,400, median $1,240, three comps" is the truth, and the range is what a reseller actually negotiates against.

No elasticity model, by the plan, and no price forecast in the flag. The hold-or-move decision is driven by the demand signal the model already produces, not by a second model predicting price. That keeps the layer honest about what it is: a rule that says ride the piece while the watchlist still believes in it and clear it when the belief fades or the capital has sat too long. The thresholds are stated, not tuned into a black box.

The condition adjustment is built and tested but is a no-op on synth, because the synthetic sales carry no condition grade. I built the mechanism anyway, normalizing each comp to a reference grade before taking the median, because it is real on real data and I would rather ship the seam than pretend condition does not move price. The docstring and the backtest doc both say plainly that it does nothing on the fixtures.

The as-of rule is the same one the rest of the pipeline lives by. A worth estimate dated T reads only sales at or before T, and there is a leak test that adds a wild sale one day after the cutoff and asserts the number does not move. The backtest could not be trusted without it.

**What I learned or got stuck on**

Nothing dramatic. The one judgment call was the window length: a short window tracks the current price but goes empty on thin families, a long one is stable but lags a fast move. Sixty days matches the label's baseline window and kept enough comps on the synth families to price nearly every buy. It is a config knob, not a law.

**What's next**

Phase 8 proper, the backtest that calls this.

---

## The decision backtest, and the model earning its keep by knowing when not to buy

**What I built**

ml/backtest.py, the headline. It rebuilds the watchlist at seven out-of-sample monthly cutoffs, 2025-09 through 2026-03, simulates a stated buying policy, and reports it in reseller terms: gross margin, return per trade, days to sell, watchlist precision, and price realization. Three policies over the same cutoffs with the same sell logic so only the buy decision varies: the model (buy families scoring above 0.5), the naive search screen (buy anything with rising search interest), and buy-nothing. Eight tests, including the leak guard that perturbs the future and asserts the cutoff watchlist does not move. docs/backtest.md, written honestly, synthetic caveat on every figure.

The result: the model realizes about $2,260 across fourteen trades at 38.6% per trade with perfect precision, against the naive screen's $1,367 across thirty-five trades at 13.8% and 49% precision, both beating the buy-nothing floor of zero.

**Why these decisions**

The comparison had to be apples-to-apples, so all three policies use the same model-driven sell logic and the same prices, and differ only in what they buy. That isolates the buy decision, which is the thing being evaluated. The baseline is the same naive rising-search screen Phase 7 measured against, so the two phases tell one continuous story.

The decision that made the result honest was gating the watchlist instead of forcing a fixed top-k. My first cut bought the top five families every cutoff no matter what, and the two policies came out nearly tied, about $1,330 each, because in a forced top-five they buy almost the same basket. That tie is real but it hides the point. A watchlist that must name five families every month buys junk in a quiet market, and the per-cutoff breakdown showed exactly that: both policies lost $400 to $580 a month across the 2026 quiet quarter when there were no real outperformers to buy. So I gated each policy by its own honest flag, the model by its 0.5 probability bar, the search screen by a positive slope, which is the literal "rising interest" screen. That one change is what let the model's calibration show up as money.

Because the finding is not that the model picks better in the wave. It does not. In September and October the two buy the identical five families for the identical dollars, the same tie Phase 7's precision@k already reported. The naive screen actually makes more gross inside the wave, because it keeps buying the marginal families in November and December that the model's bar filters out, and on synth those still paid. The model wins on discipline: from January on its scores collapse below the bar and it buys nothing and loses nothing, while the screen has no bar, keeps flagging five families a month out of the noise, and gives back more in the quiet quarter than its wider wave haul was worth. The edge is higher conviction per trade and the willingness to stop.

That is the argument for why the backtest is the headline and the ranking score was not. Precision@k forces exactly k picks; it cannot express "buy fewer this month" or "buy none". The margin question can, and it is the only place the model's calibration turns into dollars.

**What I learned or got stuck on**

The forced top-k tie was the lesson, and I left the story of it in the DEVLOG and the doc rather than quietly shipping the gated version as if it were obvious. The gate is also where the honesty has to be loudest: the model's clean abstention leans on synthetic scores that go nearly binary, near 1 while a family runs and near 0 once it is done, so the quiet-period discipline is crisper than graded real-world scores would ever be. The doc says that in as many words. The framework is the deliverable; the 38.6% is a demonstration of the framework, not a number to believe.

**What's next**

Phase 9, the Streamlit dashboard, framed as the reseller's decisions: the watchlist with plain-language reasons, a family explorer with the worth estimate and the hold-or-move flag, the backtest as proof, and the model report with its limits visible. Thin Python over the marts and the model output.
