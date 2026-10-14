"""
Pydantic schemas shared across the app.

Record is intentionally flexible (extra="allow") — the exact fields depend on
whatever the source JSON file contains, so we don't hardcode a fixed schema.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    """One row of input data. Field set is inferred from the source JSON —
    this model just guarantees an `id`-able record and allows any other keys."""

    model_config = ConfigDict(extra="allow")

    record_id: str  # your own stable identifier for this record, not a DB id

    def field_dict(self) -> dict[str, Any]:
        """All data fields except our internal record_id, as a plain dict."""
        data = self.model_dump()
        data.pop("record_id", None)
        return data

class FieldResult(BaseModel):
    field_name: str
    expected_value: Any
    actual_value: Any
    passed: bool
    reason: Optional[str] = None  # e.g. "not_found_in_db", "type_mismatch", etc.


class RunResult(BaseModel):
    """Outcome of the browser agent attempting ONE record. This is about the
    agent run itself (did it crash, did it claim success) — NOT verification.
    Verification against the real DB happens later, in evaluator.py."""

    record_id: str
    success: bool  # agent's own self-reported outcome — not to be trusted for grading
    error_message: Optional[str] = None
    timestamp: datetime


class RecordVerification(BaseModel):
    """Verification outcome for one record, produced by the evaluator."""

    record_id: str
    matched_in_db: bool
    match_reason: Optional[str] = None  # why it wasn't matched, if it wasn't
    field_results: list[FieldResult] = []


class BatchReport(BaseModel):
    total_records: int
    total_fields: int
    correct_fields: int
    field_accuracy: float
    unmatched_records: int  # records with no corresponding DB row found
    per_record_results: list[RecordVerification]
    generated_at: datetime
