# Finalized in Phase 9. Targets appear as their phases are built.

.PHONY: test ingest resolve db-up load transform label features

test:
	pytest

ingest:
	python -m ingestion.run_ingestion

resolve:
	python -m resolution.catalog

db-up:
	docker compose up -d

load:
	python -m db.load

transform:
	cd dbt_project && dbt build --profiles-dir .

label:
	python -m ml.label --source synth

features:
	python -m features.build_features --source synth
