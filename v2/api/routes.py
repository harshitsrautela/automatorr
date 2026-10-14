"""HTTP routes for the dashboard, batch runs, progress events, results and cache stats."""

import asyncio
import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from supabase import create_client

from config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from data.loader import load_records
from data.models import BatchReport
from evaluation.evaluator import verify_batch
import logfile
from orchestrator import get_store, run_batch_cached, run_reconciliation_pass_sync
from progress import broadcaster
from storage.results_store import load_last_report, save_report

logger = logging.getLogger(__name__)
router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# In-memory progress state, shared by /run and /run/start so only one batch runs at a time
_status = {
    "state": "idle",  # idle | running | done | error | aborted
    "current": 0,
    "total": 0,
    "error": None,
}

# Lock guards the check-then-set on _status["state"]
_status_lock = threading.Lock()

# Thread-safe flag: set by /run/abort, polled by the batch thread between records
_abort_event = threading.Event()


# Request body for starting a batch.
# Holds records_path, the path of the records file to process.
class RunRequest(BaseModel):
    records_path: str


# Serves the dashboard page (static/index.html).
# Takes nothing and returns it as a FileResponse.
@router.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Health-check endpoint (GET /health).
# Returns a small dict saying the service is ok.
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "data-entry-automation-agent"}


# Blocking endpoint (POST /run): runs a full batch, verifies against Supabase and saves the report.
# Takes a RunRequest and returns the BatchReport; 400 on bad records, 409 if a batch is running.
@router.post("/run", response_model=BatchReport)
def run(request: RunRequest) -> BatchReport:
    try:
        records = load_records(request.records_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _status_lock:
        if _status["state"] == "running":
            raise HTTPException(status_code=409, detail="A batch is already running.")
        _status.update(state="running", current=0, total=len(records), error=None)

    try:
        # No incremental progress here; use /run/start with /events for live updates
        results = run_batch_cached(records)
        _status["current"] = len(records)

        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        report = verify_batch(records, supabase_client)

        save_report(report)
        if any(r.quota_exhausted for r in results):
            _status.update(
                state="error",
                error="Gemini's daily free-tier quota is exhausted - batch stopped early.",
            )
        else:
            _status["state"] = "done"
        return report

    except Exception as exc:  # noqa: BLE001
        _status.update(state="error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Batch run failed: {exc}") from exc


# Sends one event to live SSE subscribers and appends it to the Logs.md run log.
# Takes the event dict and an optional log generation token.
def _broadcast_and_log(event: dict, *, generation: int | None = None) -> None:
    broadcaster.publish(event)
    logfile.append(event, generation=generation)


# Retries fields found wrong after verification; runs on its own thread, not tied to _status.
# Takes the records, the BatchReport and the log generation; failures are logged and sent as an event.
def _run_reconciliation_in_background(records: list, report: BatchReport, generation: int) -> None:
    on_event = lambda event: _broadcast_and_log(event, generation=generation)
    try:
        result = run_reconciliation_pass_sync(records, report, on_event=on_event)
        logger.info("Reconciliation pass finished: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("Reconciliation pass failed")
        # Own event type so the UI does not treat it as the current batch dying
        on_event({"type": "reconciliation_error", "message": "Reconciliation pass failed - see server log."})


# Runs a batch on a background thread, publishes progress events, then verifies and saves the report.
# Takes the loaded records, updates _status, and starts reconciliation unless aborted or quota-stopped.
def _run_batch_in_background(records: list) -> None:
    generation = logfile.reset(len(records))
    on_event = lambda event: _broadcast_and_log(event, generation=generation)
    try:
        results = run_batch_cached(records, on_event=on_event, abort_check=_abort_event.is_set)
        _status["current"] = len(records)

        # Surface a quota stop in the status instead of reporting plain "done"
        quota_exhausted = any(r.quota_exhausted for r in results)

        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        report = verify_batch(records, supabase_client)
        save_report(report)

        if quota_exhausted:
            _status.update(
                state="error",
                error=(
                    "Gemini's daily free-tier quota (500 requests/day) is exhausted - batch stopped "
                    "early rather than letting every remaining record fail the same way. This will not "
                    "recover on its own; wait for the quota to reset or swap in a different API key."
                ),
            )
        else:
            _status["state"] = "aborted" if _abort_event.is_set() else "done"
        on_event(
            {
                "type": "verification_done",
                "total_records": report.total_records,
                "total_fields": report.total_fields,
                "correct_fields": report.correct_fields,
                "field_accuracy": report.field_accuracy,
                "unmatched_records": report.unmatched_records,
                "aborted": _abort_event.is_set(),
                "quota_exhausted": quota_exhausted,
            }
        )

        # Reconciliation runs in the background; skipped after an abort or quota exhaustion
        if not _abort_event.is_set() and not quota_exhausted:
            threading.Thread(
                target=_run_reconciliation_in_background, args=(records, report, generation), daemon=True
            ).start()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Background batch run failed")
        _status.update(state="error", error=str(exc))
        on_event({"type": "batch_error", "message": str(exc)})


# Starts a batch on a background thread (POST /run/start) and returns immediately.
# Takes a RunRequest and returns the started status and record count; 400 bad file, 409 if busy.
@router.post("/run/start")
def run_start(request: RunRequest) -> dict:
    try:
        records = load_records(request.records_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with _status_lock:
        if _status["state"] == "running":
            raise HTTPException(status_code=409, detail="A batch is already running.")
        _abort_event.clear()
        _status.update(state="running", current=0, total=len(records), error=None)

    threading.Thread(target=_run_batch_in_background, args=(records,), daemon=True).start()
    return {"status": "started", "total": len(records)}


# Asks the running batch to stop (POST /run/abort); records already in flight finish normally.
# Returns an aborting status, or 409 if no batch is running.
@router.post("/run/abort")
def run_abort() -> dict:
    if _status["state"] != "running":
        raise HTTPException(status_code=409, detail="No batch is currently running.")
    _abort_event.set()
    return {"status": "aborting"}


# Returns the current batch state and progress counts (GET /status).
# Takes nothing and returns the shared _status dict.
@router.get("/status")
def status() -> dict:
    return _status


# Returns the last saved batch report (GET /results).
# Responds with the BatchReport, or 404 if no results have been saved yet.
@router.get("/results", response_model=BatchReport)
def results() -> BatchReport:
    report = load_last_report()
    if report is None:
        raise HTTPException(status_code=404, detail="No results saved yet.")
    return report


# Returns the per-workflow cache hit-rate stats (GET /cache/stats).
# Takes nothing and returns a dict with a list of workflows.
@router.get("/cache/stats")
def cache_stats() -> dict:
    return {"workflows": get_store().hit_rate_stats()}


# Streams live progress events to the browser as Server-Sent Events (GET /events).
# Takes the Request and returns a StreamingResponse fed by a per-connection queue.
@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    # Bind the loop per request so it is always the one currently serving
    broadcaster.bind_loop(asyncio.get_running_loop())
    queue = broadcaster.subscribe()

    # Async generator that yields queued events as SSE data lines, with a heartbeat when idle.
    # Stops when the client disconnects and unsubscribes its queue.
    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"  # keeps the connection alive through idle proxies
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
