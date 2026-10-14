"""Starts a saved workflow as a background run: builds its RunContext, runs the batch, verifies, and logs events."""

import json
import logging
import os
import threading
from typing import Any

from supabase import create_client

import app_db
import secrets_vault
from config import FORCE_HEADLESS
from data.models import BatchReport, Record
from evaluation.evaluator import verify_batch
from evaluation.view_verifier import verify_against_view
from orchestrator import halt_reason, run_batch_cached, run_reconciliation_pass_sync
from run_context import RunContext, extra_keys_from_env

logger = logging.getLogger(__name__)

# Only one run at a time: the engine still keeps some process-wide state (quota flag, seed locks).
_run_lock = threading.Lock()
_current: dict[str, Any] = {"run_id": None, "abort": threading.Event()}


# Raised when a run is requested while another one is still going.
class RunBusy(Exception):
    pass


# Raised when a workflow is not ready to run; the message says what to fix.
class WorkflowNotReady(Exception):
    pass


# Decrypts a stored secret into its dict of values.
# Takes the secret id and the kind it must have; raises WorkflowNotReady if it is missing or the wrong kind.
def _load_secret(secret_id: int | None, kind: str, what: str) -> dict[str, Any]:
    if not secret_id:
        raise WorkflowNotReady(f"Choose a {what} in the workflow settings.")
    with app_db.db() as conn:
        row = conn.execute("SELECT kind, ciphertext FROM secrets WHERE id=?", (secret_id,)).fetchone()
    if row is None:
        raise WorkflowNotReady(f"The {what} this workflow uses no longer exists. Pick another in Settings.")
    if row["kind"] != kind:
        raise WorkflowNotReady(f"The selected {what} is the wrong kind of secret.")
    return secrets_vault.decrypt(row["ciphertext"])


# The main Gemini key for a workflow: a saved secret when one is chosen, otherwise the server's GEMINI_API_KEY (.env).
# Takes the saved secret id (or None); raises WorkflowNotReady when no key is available.
def gemini_primary_key(secret_id: int | None) -> str:
    if secret_id:
        return _load_secret(secret_id, "llm_key", "Gemini API key")["value"]
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise WorkflowNotReady("No Gemini key is set on the server. Add GEMINI_API_KEY to the server's .env file.")
    return key


# The database login used to check results: a saved secret when one is chosen, otherwise SUPABASE_URL and
# SUPABASE_SERVICE_ROLE_KEY from the server's .env. Returns {"url", "key"}; raises WorkflowNotReady when neither exists.
def database_credentials(secret_id: int | None) -> dict[str, Any]:
    if secret_id:
        return _load_secret(secret_id, "db_credential", "database credential")
    url, key = os.getenv("SUPABASE_URL", "").strip(), os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise WorkflowNotReady("Checking results needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the server's .env file.")
    return {"url": url, "key": key}


# Turns the workflow's saved data into Records, using record_id, the match-key value, or the row number as the id.
# Takes the workflow row and an optional limit; raises WorkflowNotReady on empty or duplicate-id data.
def _records_for(workflow: dict[str, Any], limit: int | None) -> list[Record]:
    rows = json.loads(workflow["data_json"] or "[]")
    if not rows:
        rows = [{}]  # no data: the task runs once
    if limit:
        rows = rows[:limit]
    records: list[Record] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        record_id = str(row.get("record_id") or row.get(workflow["match_key"]) or i)
        if record_id in seen:
            raise WorkflowNotReady(f"Two records share the id {record_id!r}. Each record needs a unique {workflow['match_key'] or 'id'}.")
        seen.add(record_id)
        records.append(Record(**{**row, "record_id": record_id}))
    return records


# Builds the RunContext for a workflow row, decrypting the secrets it references.
# Takes the workflow dict; raises WorkflowNotReady if something it needs is missing.
def build_context(workflow: dict[str, Any]) -> tuple[RunContext, dict[str, Any] | None]:
    gemini_key = gemini_primary_key(workflow["llm_secret_id"])
    db_cred = None
    if workflow["verify_kind"] != "none":
        db_cred = database_credentials(workflow["db_secret_id"])

    verify_config = json.loads(workflow["verify_config"] or "{}")
    instructions = (workflow["task_prompt"] or workflow["master_prompt"] or "").strip()
    if not instructions:
        raise WorkflowNotReady("Write what the agent should do (the task prompt) first.")

    ctx = RunContext(
        target_url=workflow["target_url"],
        workflow_name=f"wf{workflow['id']}:e{workflow['cache_epoch']}",
        match_key_field=workflow["match_key"],
        model_name=workflow["model_name"],
        gemini_api_key=gemini_key,
        extra_gemini_keys=tuple(k for k in extra_keys_from_env() if k != gemini_key),
        max_concurrent_replays=workflow["max_concurrency"],
        headless=bool(workflow["headless"]) or FORCE_HEADLESS,
        task_instructions=instructions,
        use_cache=bool(workflow["use_cache"]),
        submit_url_contains=workflow["submit_url_contains"] or None,
        submit_method=(workflow["submit_method"] or "POST").upper(),
        supabase_table=verify_config.get("table", ""),
        supabase_url=db_cred["url"] if db_cred else None,
        supabase_service_role_key=db_cred["key"] if db_cred else None,
    )
    return ctx, verify_config


# Starts a background run for a workflow and returns the new run id right away.
# Takes the workflow dict and an optional record limit; raises RunBusy or WorkflowNotReady.
def start_run(workflow: dict[str, Any], limit: int | None = None) -> int:
    if not _run_lock.acquire(blocking=False):
        raise RunBusy("Another run is still in progress. Wait for it to finish or abort it.")
    try:
        ctx, verify_config = build_context(workflow)
        records = _records_for(workflow, limit)
        with app_db.db(write=True) as conn:
            cur = conn.execute(
                "INSERT INTO runs (workflow_id, workflow_name, status, total, created_at) VALUES (?, ?, 'running', ?, ?) RETURNING id",
                (workflow["id"], workflow["name"], len(records), app_db.now_iso()),
            )
            run_id = cur.fetchone()["id"]
    except BaseException:
        _run_lock.release()
        raise

    _current["run_id"] = run_id
    _current["abort"].clear()
    threading.Thread(
        target=_execute, args=(run_id, workflow, ctx, verify_config, records), daemon=True, name=f"run-{run_id}"
    ).start()
    return run_id


# Asks the current run to stop starting new records; records already in progress finish.
# Takes the run id and returns True if it was the run in progress.
def abort_run(run_id: int) -> bool:
    if _current["run_id"] == run_id:
        _current["abort"].set()
        return True
    return False


# Returns the id of the run in progress, or None.
def current_run_id() -> int | None:
    return _current["run_id"]


# Hides known secret values inside an event before it is stored.
# Takes the event dict and the secrets to hide; returns a cleaned copy.
def _redact(event: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    text = json.dumps(event, default=str)
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "••••")
    return json.loads(text)


# Sets the run's final state in the database.
# Takes the run id, the status, and optionally an error message and the BatchReport.
def _finish(run_id: int, status: str, error: str | None = None, report: BatchReport | None = None) -> None:
    app_db.flush_events()  # the run's last log lines must be stored before it shows as finished
    with app_db.db(write=True) as conn:
        conn.execute(
            "UPDATE runs SET status=?, error=?, report_json=?, finished_at=? WHERE id=?",
            (status, error, report.model_dump_json() if report else None, app_db.now_iso(), run_id),
        )


# The body of the run thread: batch, verification, optional reconciliation, final status.
# Takes the run id, workflow, context, verification settings and records; never raises.
def _execute(run_id: int, workflow: dict[str, Any], ctx: RunContext, verify_config: dict[str, Any], records: list[Record]) -> None:
    secrets = [*ctx.all_gemini_keys, ctx.supabase_service_role_key or ""]
    seen_quota = {"hit": False}

    # Stores one engine event on the run's log, with secrets hidden.
    def on_event(event: dict[str, Any]) -> None:
        if event.get("quota_exhausted"):
            seen_quota["hit"] = True
        clean = _redact(event, secrets)
        app_db.append_event(run_id, clean.get("type", "event"), {k: v for k, v in clean.items() if k != "type"})

    try:
        logger.info("Run %s starting:\n%s", run_id, ctx)
        results = run_batch_cached(records, ctx, on_event=on_event, abort_check=_current["abort"].is_set)
        aborted = _current["abort"].is_set()
        fatal = halt_reason()
        quota_exhausted = not fatal and (seen_quota["hit"] or any(r.quota_exhausted for r in results))

        report: BatchReport | None = None
        if workflow["verify_kind"] != "none":
            with app_db.db(write=True) as conn:
                conn.execute("UPDATE runs SET status='verifying' WHERE id=?", (run_id,))
            client = create_client(ctx.supabase_url, ctx.supabase_service_role_key)
            if workflow["verify_kind"] == "table":
                report = verify_batch(records, client, ctx)
            else:
                report = verify_against_view(records, client, verify_config, ctx.match_key_field)
            on_event(
                {
                    "type": "verification_done",
                    "total_records": report.total_records,
                    "total_fields": report.total_fields,
                    "correct_fields": report.correct_fields,
                    "field_accuracy": report.field_accuracy,
                    "unmatched_records": report.unmatched_records,
                    "aborted": aborted,
                }
            )

        if workflow["reconcile"] and workflow["verify_kind"] == "table" and report and not aborted and not quota_exhausted and not fatal:
            run_reconciliation_pass_sync(records, report, ctx, on_event=on_event)

        if fatal:
            _finish(run_id, "error", _redact({"m": fatal}, secrets)["m"], report)
        elif quota_exhausted:
            _finish(run_id, "error", "The Gemini daily quota is exhausted; the run stopped early. Use another key or wait for it to reset.", report)
        else:
            _finish(run_id, "aborted" if aborted else "done", None, report)
    except Exception as exc:  # noqa: BLE001 - the run thread must always record how it ended
        logger.exception("Run %s failed", run_id)
        message = _redact({"m": f"{type(exc).__name__}: {exc}"}, secrets)["m"]
        on_event({"type": "batch_error", "message": message})
        _finish(run_id, "error", message)
    finally:
        _current["run_id"] = None
        _run_lock.release()
