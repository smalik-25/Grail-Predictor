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
