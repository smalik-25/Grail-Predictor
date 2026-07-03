"""Orchestrates whichever sources are built and lands raw output on disk.

Each run writes one JSON file per source to data/raw/ with a timestamped
filename. Raw landed data is deliberately decoupled from anything the
database touches: this stage only captures, it never transforms. Fixture
mode is the default; live mode requires INGEST_LIVE=1 plus per-source
credentials, checked by each client itself.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
from pathlib import Path
from typing import Any, Callable, Iterator

from ingestion.ebay import EbayClient
from ingestion.depop import DepopClient
from ingestion.grailed import GrailedClient
from ingestion.retail import RetailClient
from ingestion.secondstreet import SecondStreetClient
from ingestion.social import RedditClient
from ingestion.therealreal import TheRealRealClient
from ingestion.trends import TrendsClient
from ingestion.vestiaire import VestiaireClient

logger = logging.getLogger(__name__)

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

SOURCES: dict[str, Callable[[], Iterator[Any]]] = {
    "grailed": lambda: GrailedClient().listings(),
    "ebay": lambda: EbayClient().listings(),
    "depop": lambda: DepopClient().listings(),
    "vestiaire": lambda: VestiaireClient().listings(),
    "therealreal": lambda: TheRealRealClient().listings(),
    "secondstreet": lambda: SecondStreetClient().listings(),
    "retail": lambda: RetailClient().prices(),
    "trends": lambda: TrendsClient().interest(),
    "social": lambda: RedditClient().mentions(),
}


def run(sources: list[str], out_dir: Path = RAW_DIR) -> dict[str, int]:
    """Run the named sources and write one timestamped JSON file each.

    Returns a source -> record count map so callers (and tests) can see
    exactly what landed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    counts: dict[str, int] = {}
    for name in sources:
        if name not in SOURCES:
            raise ValueError(f"unknown source {name!r}; known: {sorted(SOURCES)}")
        records = [dataclasses.asdict(record) for record in SOURCES[name]()]
        path = out_dir / f"{name}_{stamp}.json"
        with path.open("w") as handle:
            json.dump(records, handle, indent=2)
        counts[name] = len(records)
        logger.info("%s: wrote %d records to %s", name, len(records), path)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Land raw data from ingestion sources.")
    parser.add_argument(
        "--sources",
        nargs="+",
        default=sorted(SOURCES),
        help="Which sources to run (default: all built sources).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=RAW_DIR,
        help="Directory for raw landed files (default: data/raw/).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    counts = run(args.sources, args.out_dir)
    total = sum(counts.values())
    logger.info("done: %d records across %d sources", total, len(counts))


if __name__ == "__main__":
    main()
