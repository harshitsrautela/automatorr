"""
POST /run     — loads records from a file, runs the full batch through the
                 browser agent, then verifies against Supabase, and returns
                 the BatchReport. Synchronous/blocking, as scoped for day 1.
GET  /status  — simple progress indicator (idle / running / done + counts)
GET  /results — returns the last saved BatchReport, if any
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client

from agent.runner import run_batch, run_batch_concurrent
from config import CONCURRENT_MODE, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from data.loader import load_records
from data.models import BatchReport
from evaluation.evaluator import verify_batch
from storage.results_store import load_last_report, save_report

router = APIRouter()

# Simple in-memory progress state. Fine for a single-process, blocking,
# local-dev-only setup — revisit if this ever needs to run concurrently
# or survive a process restart mid-batch.
_status = {
    "state": "idle",  # idle | running | done | error
    "current": 0,
    "total": 0,
    "error": None,
}


class RunRequest(BaseModel):
    records_path: str


@router.post("/run", response_model=BatchReport)
def run(request: RunRequest) -> BatchReport:
    if _status["state"] == "running":
        raise HTTPException(status_code=409, detail="A batch is already running.")

    try:
        records = load_records(request.records_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _status.update(state="running", current=0, total=len(records), error=None)

    try:
        # NOTE: neither run_batch nor run_batch_concurrent report incremental
        # progress yet — _status["current"] only moves to "total" at the
        # end. Fine for day 1 since /run is blocking anyway; wire up
        # per-record progress here later if /status needs to be live during
        # a run.
        #
        # CONCURRENT_MODE (config.py, from .env) picks v1 (sequential, the
        # safe default) vs v2 (every record at once) — see LEARNINGS.md
        # before flipping this on: v2 needs real headroom on the Gemini
        # quota, since concurrent agents share one rate limit.
        batch_fn = run_batch_concurrent if CONCURRENT_MODE else run_batch
        batch_fn(records)
        _status["current"] = len(records)

        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        report = verify_batch(records, supabase_client)

        save_report(report)
        _status["state"] = "done"
        return report

    except Exception as exc:  # noqa: BLE001
        _status.update(state="error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Batch run failed: {exc}") from exc


@router.get("/status")
def status() -> dict:
    return _status


@router.get("/results", response_model=BatchReport)
def results() -> BatchReport:
    report = load_last_report()
    if report is None:
        raise HTTPException(status_code=404, detail="No results saved yet.")
    return report
