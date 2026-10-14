"""Verifies what landed in Supabase against the expected Records, field by field."""

from datetime import datetime, timezone

from data.models import BatchReport, FieldResult, Record, RecordVerification
from evaluation.normalizers import values_match
from run_context import RunContext


# Reads the whole target table once so every record can be matched in memory.
# Takes a Supabase client and the RunContext and returns the rows as a list of dicts (empty if there are none).
def _fetch_db_rows(supabase_client, ctx: RunContext) -> list[dict]:
    response = supabase_client.table(ctx.supabase_table).select("*").execute()
    return response.data or []


# Finds the DB row whose match-key value equals the record's, ignoring case and whitespace.
# Takes a Record, the DB rows and the RunContext and returns the matching row, or None if there is no match.
def _find_matching_row(record: Record, db_rows: list[dict], ctx: RunContext) -> dict | None:
    fields = record.field_dict()
    key_value = fields.get(ctx.match_key_field)
    if key_value is None:
        return None

    # Case and whitespace insensitive match
    key_value_normalized = str(key_value).strip().lower()

    for row in db_rows:
        row_value = row.get(ctx.match_key_field)
        if row_value is not None and str(row_value).strip().lower() == key_value_normalized:
            return row

    return None


# Compares each field of a record against its DB row, failing every field if no row exists.
# Takes a Record, a DB row (or None) and the RunContext and returns a RecordVerification with per-field results.
def _compare_record(record: Record, db_row: dict | None, ctx: RunContext) -> RecordVerification:
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
            match_reason=f"no row with {ctx.match_key_field}={fields.get(ctx.match_key_field)!r}",
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


# TESTING ONLY: deletes every row from the target table (cannot be undone); to be removed later.
# Takes a Supabase client and the RunContext and returns how many rows were deleted.
def delete_all_records(supabase_client, ctx: RunContext) -> int:
    rows = supabase_client.table(ctx.supabase_table).select("id").execute().data or []
    count = len(rows)
    if count:
        # PostgREST rejects an unfiltered delete; ids are never negative
        supabase_client.table(ctx.supabase_table).delete().neq("id", -1).execute()
    return count


# Grades a whole batch from what is actually in Supabase; never reads RunResult.success.
# Takes the expected Records, a Supabase client and the RunContext and returns a BatchReport with field accuracy.
def verify_batch(records: list[Record], supabase_client, ctx: RunContext) -> BatchReport:
    db_rows = _fetch_db_rows(supabase_client, ctx)

    per_record_results: list[RecordVerification] = []
    total_fields = 0
    correct_fields = 0
    unmatched_records = 0

    for record in records:
        db_row = _find_matching_row(record, db_rows, ctx)
        verification = _compare_record(record, db_row, ctx)
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
