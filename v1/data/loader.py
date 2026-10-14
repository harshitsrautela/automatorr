"""
Reads a JSON file of records into a list[Record].

Expected input shape: a JSON array of objects, e.g.

[
  {"record_id": "1", "name": "John Smith", "email": "john@x.com", ...},
  ...
]

If your source records don't already have a `record_id` field, we synthesize
one from the row index so every Record has a stable identifier.
"""

import json
from pathlib import Path

from data.models import Record


def load_records(path: str) -> list[Record]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Records file not found: {path}")

    with file_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(
            f"Expected a JSON array of records in {path}, got {type(raw).__name__}"
        )

    records: list[Record] = []
    for i, row in enumerate(raw):
        if "record_id" not in row:
            row = {**row, "record_id": str(i)}
        records.append(Record(**row))

    return records
