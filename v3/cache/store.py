"""Database-backed cache of replayable DOM locator steps and per-run metrics (tables live in app_db.py)."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import app_db


# One cached interaction: the element locator info needed to replay a step.
# Holds the workflow, variant, action type, xpath, attributes and fingerprint of the element.
@dataclass
class CachedStep:
    workflow_name: str
    path_variant: str
    step_index: int
    action_offset: int
    action_type: str
    field_name: Optional[str]
    instruction: str
    x_path: str
    node_name: str
    attributes: dict[str, str] = field(default_factory=dict)
    stable_hash: Optional[str] = None
    fingerprint: str = ""
    updated_at: str = ""


# Reads and writes cached steps and run metrics in the app database.
# Holds no state of its own: every call borrows a pooled connection, so threads can use it safely.
class CacheStore:
    # Takes a CachedStep and saves it, overwriting any row with the same workflow, variant and target.
    # Stamps updated_at with the current UTC time when it is empty; returns nothing.
    def upsert_step(self, step: CachedStep) -> None:
        if not step.updated_at:
            step.updated_at = datetime.now(timezone.utc).isoformat()
        # Keyed by target_key, so only the most recent attempt per field survives
        target_key = step.field_name if step.field_name is not None else f"{step.action_type}::{step.x_path}"
        with app_db.db() as conn:
            conn.execute(
                """
                INSERT INTO cached_steps (
                    workflow_name, path_variant, target_key, step_index, action_offset,
                    action_type, field_name, instruction, x_path, node_name,
                    attributes_json, stable_hash, fingerprint, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workflow_name, path_variant, target_key)
                DO UPDATE SET
                    step_index = excluded.step_index,
                    action_offset = excluded.action_offset,
                    action_type = excluded.action_type,
                    field_name = excluded.field_name,
                    instruction = excluded.instruction,
                    x_path = excluded.x_path,
                    node_name = excluded.node_name,
                    attributes_json = excluded.attributes_json,
                    stable_hash = excluded.stable_hash,
                    fingerprint = excluded.fingerprint,
                    updated_at = excluded.updated_at
                """,
                (
                    step.workflow_name,
                    step.path_variant,
                    target_key,
                    step.step_index,
                    step.action_offset,
                    step.action_type,
                    step.field_name,
                    step.instruction,
                    step.x_path,
                    step.node_name,
                    json.dumps(step.attributes),
                    step.stable_hash,
                    step.fingerprint,
                    step.updated_at,
                ),
            )

    # Forgets the cached click/type steps of one workflow variant (used when they turn out to be unsafe to replay).
    def clear_steps(self, workflow_name: str, path_variant: str) -> int:
        with app_db.db() as conn:
            return conn.execute("DELETE FROM cached_steps WHERE workflow_name = ? AND path_variant = ?", (workflow_name, path_variant)).rowcount

    # Takes a workflow name and path variant and returns all its cached steps as CachedStep objects.
    # Steps come back in original-run order (step_index, then action_offset).
    def get_steps(self, workflow_name: str, path_variant: str) -> list[CachedStep]:
        with app_db.db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cached_steps
                WHERE workflow_name = ? AND path_variant = ?
                ORDER BY step_index ASC, action_offset ASC
                """,
                (workflow_name, path_variant),
            ).fetchall()

        return [
            CachedStep(
                workflow_name=row["workflow_name"],
                path_variant=row["path_variant"],
                step_index=row["step_index"],
                action_offset=row["action_offset"],
                action_type=row["action_type"],
                field_name=row["field_name"],
                instruction=row["instruction"] or "",
                x_path=row["x_path"],
                node_name=row["node_name"] or "",
                attributes=json.loads(row["attributes_json"] or "{}"),
                stable_hash=row["stable_hash"],
                fingerprint=row["fingerprint"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    # Appends one metrics row for a finished record run; returns nothing.
    # Takes workflow, variant, record id, mode, start/end times, hit/miss/LLM-step counts and miss reasons.
    def record_run(
        self,
        *,
        workflow_name: str,
        path_variant: str,
        record_id: str,
        mode: str,
        started_at: datetime,
        finished_at: datetime,
        cache_hits: int,
        cache_misses: int,
        llm_steps: int,
        miss_reasons: list[str],
    ) -> None:
        with app_db.db() as conn:
            conn.execute(
                """
                INSERT INTO run_metrics (
                    workflow_name, path_variant, record_id, mode,
                    started_at, finished_at, cache_hits, cache_misses,
                    llm_steps, miss_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_name,
                    path_variant,
                    record_id,
                    mode,
                    started_at.isoformat(),
                    finished_at.isoformat(),
                    cache_hits,
                    cache_misses,
                    llm_steps,
                    json.dumps(miss_reasons),
                ),
            )

    # Returns one dict per workflow and path variant with run count, hit/miss totals and hit rate.
    # Takes no arguments; hit_rate is None when there were no hits or misses, newest run first.
    def hit_rate_stats(self) -> list[dict[str, Any]]:
        with app_db.db() as conn:
            rows = conn.execute(
                """
                SELECT
                    workflow_name,
                    path_variant,
                    COUNT(*) AS runs,
                    SUM(cache_hits) AS total_hits,
                    SUM(cache_misses) AS total_misses,
                    SUM(llm_steps) AS total_llm_steps,
                    MAX(finished_at) AS last_run_at
                FROM run_metrics
                GROUP BY workflow_name, path_variant
                ORDER BY last_run_at DESC
                """
            ).fetchall()

        stats = []
        for row in rows:
            hits = row["total_hits"] or 0
            misses = row["total_misses"] or 0
            total = hits + misses
            stats.append(
                {
                    "workflow_name": row["workflow_name"],
                    "path_variant": row["path_variant"],
                    "runs": row["runs"],
                    "cache_hits": hits,
                    "cache_misses": misses,
                    "hit_rate": (hits / total) if total else None,
                    "total_llm_steps": row["total_llm_steps"] or 0,
                    "last_run_at": row["last_run_at"],
                }
            )
        return stats
