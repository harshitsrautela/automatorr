"""
Persists the last BatchReport to a flat JSON file. Nothing fancy — this is
just so a crash mid-batch (or a server restart) doesn't lose your results.
"""

import json
from pathlib import Path

from config import RESULTS_STORE_PATH
from data.models import BatchReport


def save_report(report: BatchReport) -> None:
    path = Path(RESULTS_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def load_last_report() -> BatchReport | None:
    path = Path(RESULTS_STORE_PATH)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return BatchReport(**data)
