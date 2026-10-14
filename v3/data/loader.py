"""Loads a JSON array of records from a file into a list of Records."""

import json
from pathlib import Path

from data.models import Record


# Reads a JSON array file and turns each row into a Record, using the row index if record_id is missing.
# Takes a file path and returns a list of Records; raises if the file is absent or not a JSON array.
def load_records(path: str) -> list[Record]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Records file not found: {path}")

    with file_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)  # raw is plain dicts/lists at this point, not Records yet

    if not isinstance(raw, list):
        raise ValueError(
            f"Expected a JSON array of records in {path}, got {type(raw).__name__}"
        )

    records: list[Record] = []
    for i, row in enumerate(raw):
        if "record_id" not in row:
            # Use the row index as the id
            row = {**row, "record_id": str(i)}
        records.append(Record(**row))

    return records
