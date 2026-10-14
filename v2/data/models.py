"""Pydantic schemas shared across the app."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# Pydantic model for one row of input data; only record_id is required, other keys are allowed.
# Created by the loader and consumed by the agent prompts, the runner and the evaluator.
class Record(BaseModel):
    # extra="allow" accepts whatever keys the source JSON has
    model_config = ConfigDict(extra="allow")

    record_id: str  # stable identifier for this record, not a DB id

    # Collects the record's form data without the bookkeeping record_id.
    # Takes nothing and returns all data fields as a plain dict.
    def field_dict(self) -> dict[str, Any]:
        # record_id is bookkeeping, not a form field
        data = self.model_dump()
        data.pop("record_id", None)
        return data

# Pydantic model for the pass/fail verdict on one field of one record (expected vs DB value).
# Produced by the evaluator and nested inside RecordVerification.
class FieldResult(BaseModel):

    field_name: str
    expected_value: Any  # value from the source record
    actual_value: Any  # value read from the DB row (None if missing)
    passed: bool
    reason: Optional[str] = None  # e.g. "value_mismatch"


# Pydantic model for the outcome of the browser agent's attempt at one record, not a DB check.
# Produced by the runner and used for logging and quota handling, never for grading.
class RunResult(BaseModel):

    record_id: str
    success: bool  # agent's self-reported outcome, not used for grading
    error_message: Optional[str] = None
    timestamp: datetime
    # True only when the daily Gemini quota is exhausted
    quota_exhausted: bool = False


# Pydantic model for the verification outcome of one record, with its per-field results.
# Produced by the evaluator and collected into BatchReport.
class RecordVerification(BaseModel):

    record_id: str
    matched_in_db: bool  # False = no DB row had a matching MATCH_KEY_FIELD value
    match_reason: Optional[str] = None  # why it was not matched, if so
    field_results: list[FieldResult] = []


# Pydantic model for the final scorecard of a whole batch (accuracy and per-record detail).
# Built by the evaluator, returned by GET /results and saved to storage.
class BatchReport(BaseModel):

    total_records: int
    total_fields: int  # sum of every record's field count
    correct_fields: int
    field_accuracy: float  # correct_fields / total_fields
    unmatched_records: int  # records with no DB row found
    per_record_results: list[RecordVerification]  # detail behind field_accuracy
    generated_at: datetime
