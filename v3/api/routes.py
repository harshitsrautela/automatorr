"""HTTP API for the frontend: secrets, workflows, runs and their logs, plus serving the built frontend."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import app_db
import run_service
import secrets_vault
from agent.prompts import build_task_prompt
from data.models import Record
from config import DEFAULT_MODEL, FORCE_HEADLESS
from orchestrator import get_store
from prompt_gen import generate_instructions
from run_context import RunContext

logger = logging.getLogger(__name__)
router = APIRouter()

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------- health

# Health check (GET /api/health); returns a small dict saying the service is up.
@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "running_run_id": run_service.current_run_id(), "force_headless": FORCE_HEADLESS}


# ---------------------------------------------------------------- secrets

# Body for creating a secret: an LLM key (value) or a database credential (url + key).
class SecretIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["llm_key", "db_credential"]
    value: Optional[str] = None  # llm_key
    url: Optional[str] = None  # db_credential
    key: Optional[str] = None  # db_credential


# Body for changing a secret; only the parts given are changed.
class SecretUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=80)
    value: Optional[str] = None
    url: Optional[str] = None
    key: Optional[str] = None


# Counts how many workflows use a secret.
# Takes an open connection and the secret id; returns the count.
def _secret_usage(conn, secret_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM workflows WHERE llm_secret_id=? OR db_secret_id=?", (secret_id, secret_id)
    ).fetchone()["n"]


# Builds the public view of a secret row: never the value, only a masked preview.
# Takes the row dict and a usage count; returns the dict sent to the browser.
def _public_secret(row: dict[str, Any], used_by: int) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "kind": row["kind"],
        "preview": f"••••{row['last4']}" if row["last4"] else "••••",
        "used_by": used_by,
        "updated_at": row["updated_at"],
    }


# Lists all secrets, masked (GET /api/secrets).
@router.get("/api/secrets")
def list_secrets() -> list[dict[str, Any]]:
    with app_db.db() as conn:
        rows = conn.execute("SELECT id, label, kind, last4, updated_at FROM secrets ORDER BY label").fetchall()
        return [_public_secret(dict(r), _secret_usage(conn, r["id"])) for r in rows]


# Checks the values given for a secret and returns the dict to encrypt plus the text for its masked preview.
# Takes the kind and the (possibly partial) values; raises 400 when something needed is missing.
def _secret_payload(kind: str, value: Optional[str], url: Optional[str], key: Optional[str]) -> tuple[dict[str, Any], str]:
    if kind == "llm_key":
        if not value or not value.strip():
            raise HTTPException(400, "Enter the API key.")
        return {"value": value.strip()}, value.strip()
    if not url or not url.strip() or not key or not key.strip():
        raise HTTPException(400, "Enter both the database URL and its key.")
    if not url.strip().startswith(("http://", "https://")):
        raise HTTPException(400, "The database URL must start with http:// or https://.")
    return {"url": url.strip().rstrip("/"), "key": key.strip()}, key.strip()


# Creates a secret (POST /api/secrets); the value is encrypted and cannot be read back.
@router.post("/api/secrets", status_code=201)
def create_secret(body: SecretIn) -> dict[str, Any]:
    payload, preview_source = _secret_payload(body.kind, body.value, body.url, body.key)
    now = app_db.now_iso()
    try:
        with app_db.db(write=True) as conn:
            cur = conn.execute(
                "INSERT INTO secrets (label, kind, ciphertext, last4, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (body.label.strip(), body.kind, secrets_vault.encrypt(payload), secrets_vault.last4(preview_source), now, now),
            )
            row = conn.execute("SELECT id, label, kind, last4, updated_at FROM secrets WHERE id=?", (cur.fetchone()["id"],)).fetchone()
            return _public_secret(dict(row), 0)
    except app_db.IntegrityError as exc:
        raise HTTPException(409, "A secret with that label already exists.") from exc


# Changes a secret's label and/or value (PUT /api/secrets/{id}).
@router.put("/api/secrets/{secret_id}")
def update_secret(secret_id: int, body: SecretUpdate) -> dict[str, Any]:
    try:
        with app_db.db(write=True) as conn:
            row = conn.execute("SELECT * FROM secrets WHERE id=?", (secret_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Secret not found.")
            label = body.label.strip() if body.label else row["label"]
            ciphertext, last4 = row["ciphertext"], row["last4"]
            if any(v is not None for v in (body.value, body.url, body.key)):
                current = secrets_vault.decrypt(row["ciphertext"])
                if row["kind"] == "llm_key":
                    merged = {"value": body.value if body.value is not None else current["value"]}
                else:
                    merged = {
                        "url": body.url if body.url is not None else current["url"],
                        "key": body.key if body.key is not None else current["key"],
                    }
                payload, preview_source = _secret_payload(row["kind"], merged.get("value"), merged.get("url"), merged.get("key"))
                ciphertext, last4 = secrets_vault.encrypt(payload), secrets_vault.last4(preview_source)
            conn.execute(
                "UPDATE secrets SET label=?, ciphertext=?, last4=?, updated_at=? WHERE id=?",
                (label, ciphertext, last4, app_db.now_iso(), secret_id),
            )
            updated = conn.execute("SELECT id, label, kind, last4, updated_at FROM secrets WHERE id=?", (secret_id,)).fetchone()
            return _public_secret(dict(updated), _secret_usage(conn, secret_id))
    except app_db.IntegrityError as exc:
        raise HTTPException(409, "A secret with that label already exists.") from exc


# Deletes a secret (DELETE /api/secrets/{id}); refused while a workflow still uses it.
@router.delete("/api/secrets/{secret_id}")
def delete_secret(secret_id: int) -> dict[str, str]:
    with app_db.db(write=True) as conn:
        if conn.execute("SELECT 1 FROM secrets WHERE id=?", (secret_id,)).fetchone() is None:
            raise HTTPException(404, "Secret not found.")
        used = _secret_usage(conn, secret_id)
        if used:
            raise HTTPException(409, f"This secret is used by {used} workflow(s). Change them to use another one first.")
        conn.execute("DELETE FROM secrets WHERE id=?", (secret_id,))
    return {"status": "deleted"}


# ---------------------------------------------------------------- import from .env

# The keys that can be imported from the server's .env: label, secret kind, and the variables that hold it.
_ENV_IMPORTS = {
    "llm_key": {"label": "Gemini key (from .env)", "vars": ("GEMINI_API_KEY",)},
    "db_credential": {"label": "Supabase (from .env)", "vars": ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")},
}


# Reads the values for one importable kind from the environment; returns None if any variable is missing.
# Takes the kind and returns the dict to encrypt plus the text for its masked preview, or None.
def _env_values(kind: str) -> tuple[dict[str, Any], str] | None:
    names = _ENV_IMPORTS[kind]["vars"]
    values = [os.getenv(n, "").strip() for n in names]
    if not all(values):
        return None
    if kind == "llm_key":
        return {"value": values[0]}, values[0]
    return {"url": values[0].rstrip("/"), "key": values[1]}, values[1]


# LOCAL TESTING ONLY. Returns the keys from the server's .env so the Add-secret form can open pre-filled (GET /api/dev-prefill).
# Off unless DEV_PREFILL_SECRETS=1 is in .env, and it only answers requests from this machine. Unset the variable to turn it off.
@router.get("/api/dev-prefill")
def dev_prefill(request: Request, kind: Optional[str] = None) -> dict[str, Any]:
    if os.getenv("DEV_PREFILL_SECRETS") != "1":
        return {}
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Pre-filling is only available from the machine the server runs on.")
    out: dict[str, Any] = {}
    for kind_name, spec in _ENV_IMPORTS.items():
        if kind_name != "db_credential":
            continue  # the Gemini keys never leave the server; only the database login can be pre-filled
        values = _env_values(kind_name)
        if values is not None:
            out[kind_name] = {"label": spec["label"].replace(" (from .env)", ""), **values[0]}
    return out


# Lists keys found in the server's .env that are not saved yet, masked (GET /api/env-secrets); values never leave the server.
@router.get("/api/env-secrets")
def list_env_secrets() -> list[dict[str, Any]]:
    with app_db.db() as conn:
        existing = {r["label"] for r in conn.execute("SELECT label FROM secrets").fetchall()}
    found = []
    for kind, spec in _ENV_IMPORTS.items():
        values = _env_values(kind)
        if values is None or spec["label"] in existing:
            continue
        found.append(
            {"kind": kind, "label": spec["label"], "preview": f"••••{secrets_vault.last4(values[1])}", "variables": list(spec["vars"])}
        )
    return found


# Saves a key from the server's .env into the encrypted store (POST /api/env-secrets/{kind}/import).
@router.post("/api/env-secrets/{kind}/import", status_code=201)
def import_env_secret(kind: str) -> dict[str, Any]:
    if kind not in _ENV_IMPORTS:
        raise HTTPException(404, "Unknown kind.")
    values = _env_values(kind)
    if values is None:
        raise HTTPException(400, f"Not found in .env: {', '.join(_ENV_IMPORTS[kind]['vars'])}.")
    payload, preview_source = values
    now = app_db.now_iso()
    try:
        with app_db.db(write=True) as conn:
            cur = conn.execute(
                "INSERT INTO secrets (label, kind, ciphertext, last4, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                (_ENV_IMPORTS[kind]["label"], kind, secrets_vault.encrypt(payload), secrets_vault.last4(preview_source), now, now),
            )
            row = conn.execute("SELECT id, label, kind, last4, updated_at FROM secrets WHERE id=?", (cur.fetchone()["id"],)).fetchone()
            return _public_secret(dict(row), 0)
    except app_db.IntegrityError as exc:
        raise HTTPException(409, "That key is already saved.") from exc


# ---------------------------------------------------------------- workflows

# Body for creating or saving a workflow (everything the Agent page edits).
class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_url: str
    master_prompt: str = ""
    task_prompt: str = ""
    match_key: str = ""
    model_name: Optional[str] = None  # left out by the app: new tasks get the default model, saved ones keep theirs
    llm_secret_id: Optional[int] = None
    db_secret_id: Optional[int] = None
    use_cache: bool = True
    max_concurrency: int = Field(default=3, ge=1, le=10)
    submit_url_contains: Optional[str] = None  # left out: new tasks wait for nothing, saved ones keep their setting
    submit_method: Optional[Literal["POST", "PUT", "PATCH", "DELETE"]] = None
    verify_kind: Literal["none", "table", "expected_view"] = "none"
    verify_config: dict[str, Any] = {}
    reconcile: bool = False
    headless: bool = False
    data: list[dict[str, Any]] = []


# Turns a workflow row into the dict the browser gets (JSON columns parsed, booleans real).
# Takes a row dict and returns the public dict; `full` also includes the data rows.
def _public_workflow(row: dict[str, Any], full: bool = True) -> dict[str, Any]:
    data = json.loads(row["data_json"] or "[]")
    out = {
        "id": row["id"],
        "name": row["name"],
        "target_url": row["target_url"],
        "master_prompt": row["master_prompt"],
        "task_prompt": row["task_prompt"],
        "match_key": row["match_key"],
        "model_name": row["model_name"],
        "llm_secret_id": row["llm_secret_id"],
        "db_secret_id": row["db_secret_id"],
        "use_cache": bool(row["use_cache"]),
        "max_concurrency": row["max_concurrency"],
        "submit_url_contains": row["submit_url_contains"],
        "submit_method": row["submit_method"],
        "verify_kind": row["verify_kind"],
        "verify_config": json.loads(row["verify_config"] or "{}"),
        "reconcile": bool(row["reconcile"]),
        "headless": bool(row["headless"]),
        "record_count": len(data),
        "cache_epoch": row["cache_epoch"],
        "updated_at": row["updated_at"],
    }
    if full:
        out["data"] = data
    return out


# Checks the parts of a workflow body that are not just types.
# Takes the body; raises 400 with a plain message on a bad URL, unknown secret or wrong-kind secret.
def _validate_workflow(body: WorkflowIn) -> None:
    if not body.target_url.strip().startswith(("http://", "https://")):
        raise HTTPException(400, "The target URL must start with http:// or https://.")
    with app_db.db() as conn:
        for secret_id, kind, label in ((body.llm_secret_id, "llm_key", "Gemini key"), (body.db_secret_id, "db_credential", "database credential")):
            if secret_id is None:
                continue
            row = conn.execute("SELECT kind FROM secrets WHERE id=?", (secret_id,)).fetchone()
            if row is None:
                raise HTTPException(400, f"The selected {label} does not exist.")
            if row["kind"] != kind:
                raise HTTPException(400, f"The selected {label} is the wrong kind of secret.")
    if len(body.data) > 5000:
        raise HTTPException(400, "Too many records (limit 5000).")


# Lists workflows without their data (GET /api/workflows).
@router.get("/api/workflows")
def list_workflows() -> list[dict[str, Any]]:
    with app_db.db() as conn:
        rows = conn.execute("SELECT * FROM workflows ORDER BY updated_at DESC").fetchall()
    return [_public_workflow(dict(r), full=False) for r in rows]


# Returns one workflow with its data (GET /api/workflows/{id}).
@router.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: int) -> dict[str, Any]:
    with app_db.db() as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Workflow not found.")
    return _public_workflow(dict(row))


# Creates a workflow (POST /api/workflows).
@router.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowIn) -> dict[str, Any]:
    _validate_workflow(body)
    now = app_db.now_iso()
    try:
        with app_db.db(write=True) as conn:
            # Two tasks can start with the same words; number the later one instead of failing.
            base = body.name.strip()
            name, n = base, 1
            while conn.execute("SELECT 1 FROM workflows WHERE name=?", (name,)).fetchone():
                n += 1
                name = f"{base} ({n})"
            body = body.model_copy(update={"name": name})
            cur = conn.execute(
                """INSERT INTO workflows (name, target_url, master_prompt, task_prompt, match_key, model_name,
                       llm_secret_id, db_secret_id, use_cache, max_concurrency, submit_url_contains, submit_method,
                       verify_kind, verify_config, reconcile, headless, data_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    body.name.strip(), body.target_url.strip(), body.master_prompt, body.task_prompt, body.match_key.strip(),
                    (body.model_name or DEFAULT_MODEL).strip(), body.llm_secret_id, body.db_secret_id, int(body.use_cache), body.max_concurrency,
                    (body.submit_url_contains or "").strip(), body.submit_method or "POST", body.verify_kind, json.dumps(body.verify_config),
                    int(body.reconcile), int(body.headless), json.dumps(body.data), now, now,
                ),
            )
            row = conn.execute("SELECT * FROM workflows WHERE id=?", (cur.fetchone()["id"],)).fetchone()
            return _public_workflow(dict(row))
    except app_db.IntegrityError as exc:
        raise HTTPException(409, "A workflow with that name already exists.") from exc


# Saves changes to a workflow (PUT /api/workflows/{id}); a new URL or match key makes the cache forget its learned steps.
@router.put("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: int, body: WorkflowIn) -> dict[str, Any]:
    _validate_workflow(body)
    try:
        with app_db.db(write=True) as conn:
            old = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            if old is None:
                raise HTTPException(404, "Workflow not found.")
            epoch = old["cache_epoch"] + (
                1 if (old["target_url"] != body.target_url.strip() or old["match_key"] != body.match_key.strip()) else 0
            )
            conn.execute(
                """UPDATE workflows SET name=?, target_url=?, master_prompt=?, task_prompt=?, match_key=?, model_name=?,
                       llm_secret_id=?, db_secret_id=?, use_cache=?, max_concurrency=?, submit_url_contains=?,
                       submit_method=?, verify_kind=?, verify_config=?, reconcile=?, headless=?, data_json=?, cache_epoch=?, updated_at=?
                   WHERE id=?""",
                (
                    body.name.strip(), body.target_url.strip(), body.master_prompt, body.task_prompt, body.match_key.strip(),
                    (body.model_name or old["model_name"]).strip(), body.llm_secret_id, body.db_secret_id, int(body.use_cache), body.max_concurrency,
                    ((old["submit_url_contains"] or "") if body.submit_url_contains is None else body.submit_url_contains).strip(),
                    body.submit_method or old["submit_method"], body.verify_kind, json.dumps(body.verify_config),
                    int(body.reconcile), int(body.headless), json.dumps(body.data), epoch, app_db.now_iso(), workflow_id,
                ),
            )
            row = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
            return _public_workflow(dict(row))
    except app_db.IntegrityError as exc:
        raise HTTPException(409, "A workflow with that name already exists.") from exc


# Deletes a workflow and its runs and logs (DELETE /api/workflows/{id}); refused while it is running.
@router.delete("/api/workflows/{workflow_id}")
def delete_workflow(workflow_id: int) -> dict[str, str]:
    running = run_service.current_run_id()
    with app_db.db(write=True) as conn:
        if conn.execute("SELECT 1 FROM workflows WHERE id=?", (workflow_id,)).fetchone() is None:
            raise HTTPException(404, "Workflow not found.")
        if running is not None:
            owner = conn.execute("SELECT workflow_id FROM runs WHERE id=?", (running,)).fetchone()
            if owner and owner["workflow_id"] == workflow_id:
                raise HTTPException(409, "This workflow is running. Abort the run first.")
        conn.execute("DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE workflow_id=?)", (workflow_id,))
        conn.execute("DELETE FROM runs WHERE workflow_id=?", (workflow_id,))
        conn.execute("DELETE FROM workflows WHERE id=?", (workflow_id,))
    return {"status": "deleted"}


# Forgets the learned steps for a workflow (POST /api/workflows/{id}/reset-cache) by moving it to a new cache namespace.
@router.post("/api/workflows/{workflow_id}/reset-cache")
def reset_cache(workflow_id: int) -> dict[str, Any]:
    with app_db.db(write=True) as conn:
        cur = conn.execute("UPDATE workflows SET cache_epoch = cache_epoch + 1 WHERE id=?", (workflow_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Workflow not found.")
        epoch = conn.execute("SELECT cache_epoch FROM workflows WHERE id=?", (workflow_id,)).fetchone()["cache_epoch"]
    return {"cache_epoch": epoch}


# Body for generating agent instructions from a description.
class GenerateIn(BaseModel):
    master_prompt: str = Field(min_length=1)
    target_url: str = ""
    model_name: str = Field(min_length=1)
    llm_secret_id: Optional[int] = None
    sample: dict[str, Any] = {}


# Writes agent instructions from the user's description with their Gemini key (POST /api/generate-prompt).
@router.post("/api/generate-prompt")
def generate_prompt(body: GenerateIn) -> dict[str, Any]:
    try:
        llm = {"value": run_service.gemini_primary_key(body.llm_secret_id)}
    except run_service.WorkflowNotReady as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        return generate_instructions(body.master_prompt, body.target_url, body.sample, body.model_name, llm["value"])
    except Exception as exc:  # noqa: BLE001 - any model/network/parse failure should reach the user readably
        logger.exception("Prompt generation failed")
        text = str(exc).replace(llm["value"], "••••")
        raise HTTPException(502, f"Could not generate instructions: {type(exc).__name__}: {text[:300]}") from exc


# Body for previewing the full prompt the agent would receive.
class PreviewIn(BaseModel):
    target_url: str = ""
    instructions: str = ""
    sample: dict[str, Any] = {}


# Renders the full task prompt (safety rules + instructions + one sample record) without running anything (POST /api/preview-prompt).
@router.post("/api/preview-prompt")
def preview_prompt(body: PreviewIn) -> dict[str, str]:
    ctx = RunContext(
        target_url=body.target_url or "(target URL)",
        workflow_name="preview",
        match_key_field="",
        model_name="",
        gemini_api_key="",
        task_instructions=body.instructions.strip() or "(your instructions go here)",
    )
    return {"prompt": build_task_prompt(Record(record_id="preview", **body.sample), ctx)}


# ---------------------------------------------------------------- runs

# Body for starting a run; `limit` runs only the first N records (a quick test).
class RunIn(BaseModel):
    limit: Optional[int] = Field(default=None, ge=1)


# Starts a run of a workflow (POST /api/workflows/{id}/runs); returns the run id.
@router.post("/api/workflows/{workflow_id}/runs", status_code=201)
def start_run(workflow_id: int, body: RunIn) -> dict[str, Any]:
    with app_db.db() as conn:
        row = conn.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Workflow not found.")
    try:
        run_id = run_service.start_run(dict(row), body.limit)
    except run_service.RunBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except run_service.WorkflowNotReady as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"run_id": run_id}


# Builds the public dict for a run row.
# Takes the row dict and returns it with the report parsed.
def _public_run(row: dict[str, Any], with_report: bool) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "workflow_id", "workflow_name", "status", "total", "error", "created_at", "finished_at")}
    report = json.loads(row["report_json"]) if row["report_json"] else None
    out["accuracy"] = report["field_accuracy"] if report else None
    if with_report:
        out["report"] = report
    return out


# Lists runs, newest first (GET /api/runs).
@router.get("/api/runs")
def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with app_db.db() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (min(limit, 200),)).fetchall()
    return [_public_run(dict(r), with_report=False) for r in rows]


# Returns one run with its verification report (GET /api/runs/{id}).
@router.get("/api/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    with app_db.db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found.")
    return _public_run(dict(row), with_report=True)


# Returns a run's log events after a sequence number (GET /api/runs/{id}/events?after=N), plus the run's status.
@router.get("/api/runs/{run_id}/events")
def run_events(run_id: int, after: int = 0) -> dict[str, Any]:
    with app_db.db() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Run not found.")
    events = app_db.events_after(run_id, after)
    return {"status": row["status"], "events": events, "next": events[-1]["seq"] if events else after}


# Asks a running run to stop starting new records (POST /api/runs/{id}/abort).
@router.post("/api/runs/{run_id}/abort")
def abort_run(run_id: int) -> dict[str, str]:
    if not run_service.abort_run(run_id):
        raise HTTPException(409, "That run is not in progress.")
    return {"status": "aborting"}


# Returns cache hit-rate stats per workflow namespace (GET /api/cache/stats).
@router.get("/api/cache/stats")
def cache_stats() -> dict[str, Any]:
    return {"workflows": get_store().hit_rate_stats()}


# ---------------------------------------------------------------- frontend

# Answers any unknown /api path with a JSON 404 so the frontend never receives HTML by mistake.
@router.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"], include_in_schema=False)
def api_not_found(rest: str) -> JSONResponse:
    return JSONResponse({"detail": "Not found"}, status_code=404)


# Serves the built frontend: real files from frontend/dist, and index.html for every page route.
@router.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if not FRONTEND_DIST.exists():
        return JSONResponse(
            {"detail": "The frontend is not built yet. Run `npm install && npm run build` in the frontend folder."},
            status_code=503,
        )
    candidate = (FRONTEND_DIST / path).resolve()
    if path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(FRONTEND_DIST / "index.html")
