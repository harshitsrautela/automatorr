"""Verifies decisions a run wrote into a table against the answers a database view says are correct."""

from datetime import datetime, timezone
from typing import Any

from data.models import BatchReport, FieldResult, Record, RecordVerification

_CHUNK = 100

# Keys the verification config must provide for this verifier
REQUIRED_CONFIG = (
    "table",
    "key_column",
    "value_column",
    "expected_view",
    "expected_key_column",
    "expected_value_column",
)


# Lowercases and trims a value for comparison; None becomes "".
# Takes any value and returns the normalized string.
def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


# Reads {key: value} for the given keys from one table or view, in chunks to keep requests small.
# Takes the Supabase client, relation name, key column, value column and the keys; returns a dict keyed by str(key).
def _fetch(client, relation: str, key_column: str, value_column: str, keys: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for i in range(0, len(keys), _CHUNK):
        chunk = keys[i : i + _CHUNK]
        rows = client.table(relation).select(f"{key_column},{value_column}").in_(key_column, chunk).execute().data or []
        for row in rows:
            found[str(row[key_column])] = row[value_column]
    return found


# Compares what the run wrote (table.value_column) with the correct answer (view.expected_value_column), per record.
# Takes the records, a Supabase client, the verification config dict and the field that holds each record's key.
def verify_against_view(records: list[Record], client, config: dict[str, Any], match_key_field: str) -> BatchReport:
    missing = [k for k in REQUIRED_CONFIG if not config.get(k)]
    if missing:
        raise ValueError(f"Verification settings are missing: {', '.join(missing)}")

    keys = [str(r.field_dict().get(match_key_field, "")) for r in records]
    actual = _fetch(client, config["table"], config["key_column"], config["value_column"], keys)
    expected = _fetch(client, config["expected_view"], config["expected_key_column"], config["expected_value_column"], keys)

    results: list[RecordVerification] = []
    correct = 0
    unmatched = 0
    for record, key in zip(records, keys):
        if key not in expected:
            unmatched += 1
            results.append(
                RecordVerification(
                    record_id=record.record_id,
                    matched_in_db=False,
                    match_reason=f"no row in {config['expected_view']} with {config['expected_key_column']}={key!r}",
                    field_results=[],
                )
            )
            continue
        expected_value = expected[key]
        actual_value = actual.get(key)
        passed = _norm(actual_value) == _norm(expected_value) and _norm(actual_value) != ""
        correct += int(passed)
        results.append(
            RecordVerification(
                record_id=record.record_id,
                matched_in_db=True,
                field_results=[
                    FieldResult(
                        field_name=config["value_column"],
                        expected_value=expected_value,
                        actual_value=actual_value,
                        passed=passed,
                        reason=None if passed else ("not_decided" if _norm(actual_value) == "" else "value_mismatch"),
                    )
                ],
            )
        )

    total = len(records) - unmatched
    return BatchReport(
        total_records=len(records),
        total_fields=total,
        correct_fields=correct,
        field_accuracy=(correct / total) if total else 0.0,
        unmatched_records=unmatched,
        per_record_results=results,
        generated_at=datetime.now(timezone.utc),
    )
