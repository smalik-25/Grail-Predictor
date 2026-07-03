# Grail Predictor

Predicting which luxury and avant-garde fashion pieces are about to become grails, before the resale market prices them in.

## Progress

- [x] Phase 0: Scaffold
- [x] Phase 1: Ingestion (Grailed and eBay first, then the rest)
- [ ] Phase 2a: Entity resolution, text
- [ ] Phase 2b: Entity resolution, image (only if the 2a numbers demand it)
- [ ] Phase 3: Schema and warehouse
- [ ] Phase 4: dbt transformation layer
- [ ] Phase 5: Labeling the target
- [ ] Phase 6: Feature engineering
- [ ] Phase 7: Train and evaluate
- [ ] Phase 8: Dashboard
- [ ] Phase 9: Deploy, containerize, finalize

## What this is

By the time a Raf Simons bomber hits four figures on Grailed, everyone already knows. The interesting problem is earlier: catching a piece while it is quietly moving from listed-and-ignored to watched-and-bid-on. This project tries to flag that inflection for archive and designer clothing, brands like Rick Owens, Maison Margiela, Number (N)ine, Undercover, Enfant Riches Deprimes.

It is two projects wearing one repo.

**Data engineering.** Ingest listings from Grailed, eBay, Depop, Vestiaire, The RealReal, and 2nd Street, plus first-hand retail prices from SSENSE, Dover Street Market, and Saks. Then the hard part: the same Margiela piece shows up on three platforms with three titles, three sizing conventions, and three opinions about what "good condition" means. Entity resolution unifies those into one canonical catalog with real price history. That catalog is the spine of everything downstream.

**Predictive ML.** Label which canonical pieces became grails, engineer features strictly from the window before the move, and train a deliberately plain gradient-boosted model. The craft goes into honest evaluation: time-based splits, every feature computed as-of the prediction moment, and a naive baseline the model has to beat. Look-ahead bias makes this kind of model look brilliant while predicting nothing, so guarding against it is a first-class requirement, not a footnote.

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
