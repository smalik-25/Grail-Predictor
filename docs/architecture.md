# Architecture

Filled in as phases land. Finalized in Phase 9 with the full diagram and a
Decisions & Tradeoffs section pulled from the DEVLOG.

Planned flow:

ingestion (per platform, fixture-backed) -> data/raw
-> resolution (normalize, block, match, catalog) -> canonical catalog
-> Postgres star schema -> dbt (staging, intermediate, marts)
-> labeling -> PySpark features (as-of cutoff, Pandera-validated)
-> gradient-boosted model + MLflow -> Streamlit dashboard
