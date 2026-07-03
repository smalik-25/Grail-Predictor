# Finalized in Phase 9. Targets appear as their phases are built.

.PHONY: test ingest resolve db-up load

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
