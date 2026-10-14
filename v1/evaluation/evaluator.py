"""
Independent verification pass: reads what actually landed in Supabase and
compares it field-by-field against the expected Records.

Deliberately does NOT look at RunResult.success anywhere in the comparison
logic — the agent's own self-reported outcome is not evidence. A record can
be marked matched_in_db=False and still have had success=True reported by
the agent; that mismatch is itself useful signal, not a bug.
"""

from datetime import datetime, timezone

from config import MATCH_KEY_FIELD, SUPABASE_TABLE
from data.models import BatchReport, FieldResult, Record, RecordVerification
from evaluation.normalizers import values_match


def _fetch_db_rows(supabase_client) -> list[dict]:
    response = supabase_client.table(SUPABASE_TABLE).select("*").execute()
    return response.data or []


def _find_matching_row(record: Record, db_rows: list[dict]) -> dict | None:
    fields = record.field_dict()
    key_value = fields.get(MATCH_KEY_FIELD)
    if key_value is None:
        return None

    key_value_normalized = str(key_value).strip().lower()

    for row in db_rows:
        row_value = row.get(MATCH_KEY_FIELD)
        if row_value is not None and str(row_value).strip().lower() == key_value_normalized:
            return row

    return None


def _compare_record(record: Record, db_row: dict | None) -> RecordVerification:
    fields = record.field_dict()

    if db_row is None:
        # No matching row at all — likely means the match-key field itself
        # (e.g. email) was entered wrong, or the record was never submitted.
        # Every field is reported as failed with an explicit reason so this
        # is distinguishable from "row found, individual field wrong."
        field_results = [
            FieldResult(
                field_name=field_name,
                expected_value=expected_value,
                actual_value=None,
                passed=False,
                reason="record_not_found_in_db",
            )
            for field_name, expected_value in fields.items()
        ]
        return RecordVerification(
            record_id=record.record_id,
            matched_in_db=False,
            match_reason=f"no row with {MATCH_KEY_FIELD}={fields.get(MATCH_KEY_FIELD)!r}",
            field_results=field_results,
        )

    field_results = []
    for field_name, expected_value in fields.items():
        actual_value = db_row.get(field_name)
        passed = values_match(field_name, expected_value, actual_value)
        field_results.append(
            FieldResult(
                field_name=field_name,
                expected_value=expected_value,
                actual_value=actual_value,
                passed=passed,
                reason=None if passed else "value_mismatch",
            )
        )

    return RecordVerification(
        record_id=record.record_id,
        matched_in_db=True,
        match_reason=None,
        field_results=field_results,
    )


def verify_batch(records: list[Record], supabase_client) -> BatchReport:
    db_rows = _fetch_db_rows(supabase_client)

    per_record_results: list[RecordVerification] = []
    total_fields = 0
    correct_fields = 0
    unmatched_records = 0

    for record in records:
        db_row = _find_matching_row(record, db_rows)
        verification = _compare_record(record, db_row)
        per_record_results.append(verification)

        if not verification.matched_in_db:
            unmatched_records += 1

        total_fields += len(verification.field_results)
        correct_fields += sum(1 for fr in verification.field_results if fr.passed)

    field_accuracy = (correct_fields / total_fields) if total_fields else 0.0

    return BatchReport(
        total_records=len(records),
        total_fields=total_fields,
        correct_fields=correct_fields,
        field_accuracy=field_accuracy,
        unmatched_records=unmatched_records,
        per_record_results=per_record_results,
        generated_at=datetime.now(timezone.utc),
    )
