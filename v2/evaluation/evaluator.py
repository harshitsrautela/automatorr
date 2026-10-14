"""Verifies what landed in Supabase against the expected Records, field by field."""

from datetime import datetime, timezone

from config import MATCH_KEY_FIELD, SUPABASE_TABLE
from data.models import BatchReport, FieldResult, Record, RecordVerification
from evaluation.normalizers import values_match


# Reads the whole SUPABASE_TABLE once so every record can be matched in memory.
# Takes a Supabase client and returns the rows as a list of dicts (empty if there are none).
def _fetch_db_rows(supabase_client) -> list[dict]:
    response = supabase_client.table(SUPABASE_TABLE).select("*").execute()
    return response.data or []


# Finds the DB row whose MATCH_KEY_FIELD value equals the record's, ignoring case and whitespace.
# Takes a Record and the DB rows and returns the matching row, or None if there is no match.
def _find_matching_row(record: Record, db_rows: list[dict]) -> dict | None:
    fields = record.field_dict()
    key_value = fields.get(MATCH_KEY_FIELD)
    if key_value is None:
        return None

    # Case and whitespace insensitive match
    key_value_normalized = str(key_value).strip().lower()

    for row in db_rows:
        row_value = row.get(MATCH_KEY_FIELD)
        if row_value is not None and str(row_value).strip().lower() == key_value_normalized:
            return row

    return None


# Compares each field of a record against its DB row, failing every field if no row exists.
# Takes a Record and a DB row (or None) and returns a RecordVerification with per-field results.
def _compare_record(record: Record, db_row: dict | None) -> RecordVerification:
    fields = record.field_dict()

    if db_row is None:
        # No row found: every field fails with an explicit reason
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


# TESTING ONLY: deletes every row from SUPABASE_TABLE (cannot be undone); to be removed later.
# Takes a Supabase client and returns how many rows were deleted.
def delete_all_records(supabase_client) -> int:
    rows = supabase_client.table(SUPABASE_TABLE).select("id").execute().data or []
    count = len(rows)
    if count:
        # PostgREST rejects an unfiltered delete; ids are never negative
        supabase_client.table(SUPABASE_TABLE).delete().neq("id", -1).execute()
    return count


# Grades a whole batch from what is actually in Supabase; never reads RunResult.success.
# Takes the expected Records and a Supabase client and returns a BatchReport with field accuracy.
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

        # Tally per field, not per record
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
