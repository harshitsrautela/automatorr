"""Saves and loads the last BatchReport as a JSON file."""

import json
from pathlib import Path

from config import RESULTS_STORE_PATH
from data.models import BatchReport


# Overwrites the results JSON file with the given BatchReport; only the latest is kept.
# Creates the parent folder if needed and returns nothing.
def save_report(report: BatchReport) -> None:
    path = Path(RESULTS_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


# Loads the last saved report from the results JSON file.
# Returns a BatchReport, or None if the file does not exist.
def load_last_report() -> BatchReport | None:
    path = Path(RESULTS_STORE_PATH)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return BatchReport(**data)
