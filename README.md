# Grail Predictor

A decision tool for small and mid-size resellers of archive and avant-garde fashion: what to buy, what it's worth, and whether to hold it or move it.

## Progress

- [x] Phase 0: Scaffold
- [x] Phase 1: Ingestion (Grailed and eBay first, then the rest)
- [x] Phase 2a: Entity resolution, text
- [ ] Phase 2b: Entity resolution, image (only if residual numbers from real volume demand it)
- [x] Phase 2c: Regrain resolution to style-families (with the schema migration)
- [x] Phase 3: Schema and warehouse
- [x] Phase 4: dbt transformation layer
- [x] Phase 4b: Marts regrained to family
- [x] Phase 5: Labeling, peer-relative uplift at family grain
- [x] Phase 6: Feature engineering, peer-set and colorway features
- [x] Phase 6b: Celebrity/editorial signal detection
- [x] Phase 7: Train and evaluate
- [x] Phase 8: Decision backtest
- [x] Phase 8b: Pricing and hold-or-move layer
- [ ] Phase 9: Dashboard, reseller framing
- [ ] Phase 10: Deploy, containerize, finalize

## What this is

A reseller of archive fashion makes or loses money on three calls: what to buy, what to charge, and when to mark down. This tool supports those calls for the world of Rick Owens, Maison Margiela, Balenciaga, Number (N)ine, Undercover, and Enfant Riches Deprimes. It flags which style-families are about to rise before the market prices them in, tells the reseller what a flagged piece is worth from condition-adjusted sold comps, and says hold or move. The proof is a decision backtest: what acting on the watchlist at a historical cutoff would have done to margin and sell-through, not a model score.

The project started as a pure grail predictor and pivoted to this reseller framing partway through; the commit history shows the seam, on purpose.

The unit of analysis is the style-family: Brand, then model-line, then era or generation, with material and colorway tier as a sub-attribute underneath. "Maison Margiela Future high-top, 2013-14 generation" is a family; its black and its odd colorways are tiers inside it. Hype on a generation lifts the family, hype on one colorway is caught by the tier, and the data never gets sliced too thin to forecast.

It is two projects wearing one repo.

**Data engineering.** Ingest listings from Grailed, eBay, Depop, Vestiaire, The RealReal, and 2nd Street, plus first-hand retail prices from SSENSE, Dover Street Market, and Saks. Then the hard part: the same Margiela piece shows up on three platforms with three titles, three sizing conventions, and three opinions about what "good condition" means. Entity resolution clusters those into style-families at the grain above, with real price history per family. That catalog is the spine of everything downstream.

**Predictive ML.** Label style-families by peer-relative uplift over the next 60 days, engineer features strictly from the window before the move (including detected celebrity and editorial wear events, text and metadata only, never facial recognition), train a deliberately plain ranking model, and backtest it as a buying decision. Look-ahead bias makes this kind of model look brilliant while predicting nothing, so leak protection is machine-checked, not trusted.

## Stack

| Layer | Tooling |
|---|---|
| Ingestion | Python, pytrends, PRAW, eBay API, Bright Data where free access is not realistic |
| Entity resolution | Text normalization plus record linkage; image embeddings only if measurement justifies them |
| Warehouse | Postgres, hand-written star schema, no ORM |
| Transformation | dbt (staging, intermediate, marts) |
| Features | PySpark with as-of-cutoff enforcement, Pandera validation |
| Modeling | Gradient-boosted trees, MLflow tracking |
| Dashboard | Streamlit |
| Between stages | Parquet |
| CI | GitHub Actions, fixtures only |

## Data and legality

Public data only, respectful rate limits, no circumventing logins or auth walls. Where a platform's terms make scraping a gray area, the DEVLOG says so plainly instead of pretending it is clean. Real scraped data never enters the repo: it lives in gitignored directories, and the repo ships with small realistic fixtures so the whole pipeline, the tests, and CI run end to end for anyone who clones it, no credentials required.

## Running it

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make test
```

Everything runs on fixtures by default. To land raw data from all built sources:

```
python -m ingestion.run_ingestion
```

Live mode requires `INGEST_LIVE=1` plus per-source credentials in `.env` (see `.env.example`); without both, every source stays on fixtures.
