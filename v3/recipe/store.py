"""Database access for recipe traces (what the AI did per record) and learned recipes."""

import json
from datetime import datetime, timezone
from typing import Any

import app_db

# How many of the newest traces are kept per workflow and variant; older ones are dropped
_KEEP_TRACES = 6


# Saves what the AI did for one record. Takes the workflow name, variant, record id, the record's fields and the trace list.
def save_trace(workflow_name: str, path_variant: str, record_id: str, fields: dict[str, Any], trace: list[dict[str, Any]]) -> None:
    with app_db.db(write=True) as conn:
        conn.execute(
            "INSERT INTO recipe_traces (workflow_name, path_variant, record_id, fields_json, trace_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (workflow_name, path_variant, record_id, json.dumps(fields, default=str), json.dumps(trace), datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            """
            DELETE FROM recipe_traces WHERE workflow_name = ? AND path_variant = ? AND id NOT IN (
                SELECT id FROM recipe_traces WHERE workflow_name = ? AND path_variant = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (workflow_name, path_variant, workflow_name, path_variant, _KEEP_TRACES),
        )


# Returns the saved traces, newest first, as (record_id, fields, trace) tuples.
def get_traces(workflow_name: str, path_variant: str) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    with app_db.db() as conn:
        rows = conn.execute(
            "SELECT record_id, fields_json, trace_json FROM recipe_traces WHERE workflow_name = ? AND path_variant = ? ORDER BY id DESC",
            (workflow_name, path_variant),
        ).fetchall()
    return [(r["record_id"], json.loads(r["fields_json"]), json.loads(r["trace_json"])) for r in rows]


# Saves (or replaces) the learned recipe for a workflow and variant.
def save_recipe(workflow_name: str, path_variant: str, recipe: dict[str, Any]) -> None:
    with app_db.db(write=True) as conn:
        conn.execute(
            """
            INSERT INTO recipes (workflow_name, path_variant, recipe_json, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (workflow_name, path_variant) DO UPDATE SET recipe_json = excluded.recipe_json, created_at = excluded.created_at
            """,
            (workflow_name, path_variant, json.dumps(recipe), datetime.now(timezone.utc).isoformat()),
        )


# Returns the learned recipe for a workflow and variant, or None.
def get_recipe(workflow_name: str, path_variant: str) -> dict[str, Any] | None:
    with app_db.db() as conn:
        row = conn.execute(
            "SELECT recipe_json FROM recipes WHERE workflow_name = ? AND path_variant = ?", (workflow_name, path_variant)
        ).fetchone()
    return json.loads(row["recipe_json"]) if row else None
