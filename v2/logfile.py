"""Writes every progress event into a markdown run log (docs/Logs.md)."""

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).resolve().parent / "docs" / "Logs.md"

_lock = threading.Lock()
_record_index_by_id: dict[str, int] = {}
# Bumped on every reset() so stale writers from an older batch can be dropped
_generation = 0


# Builds a short "#index" label for a record id.
# Takes a record id (or None) and returns the label, or "" if there is none.
def _tag(record_id: str | None) -> str:
    if record_id is None:
        return ""
    idx = _record_index_by_id.get(record_id)
    return f"#{idx}" if idx is not None else f"#{record_id}"


# Turns a progress event into a markdown log line.
# Takes the event dict and returns the text, or None if that event type should not be logged.
def _format(event: dict[str, Any]) -> str | None:
    t = event.get("type")

    if t == "batch_start":
        return f"## Batch started — {event.get('total')} records\n"
    if t == "record_start":
        _record_index_by_id[event["record_id"]] = event["index"]
        mode = "no cache yet, using live agent" if event.get("mode") == "cold" else "replaying from cache"
        return f"- **{_tag(event['record_id'])}/{event.get('total')} — {event.get('name')}** — {mode}"
    if t == "step":
        instruction = event.get("instruction") or f"(step {event.get('step_index')})"
        return f"  - `step` {instruction}"
    if t == "field_hit":
        return f"  - `hit` {event.get('field')}"
    if t == "field_miss":
        reason = event.get("reason") or "unknown"
        if reason == "circuit_open_skipped":
            return f"  - `skip` {event.get('field')} — circuit open, not retrying right now"
        return f"  - `miss` {event.get('field')} — {reason} — falling back to live agent"
    if t == "error":
        field = f"{event.get('field')}: " if event.get("field") else ""
        return f"  - `error` {field}{event.get('message')}"
    if t == "record_done":
        status = "done" if event.get("success") else "FAILED"
        extra = "" if event.get("success") else f" — {event.get('error_message') or 'failed'}"
        return (
            f"  - **{status}** record {event.get('index')}/{event.get('total')} "
            f"({event.get('record_id')}) — {event.get('mode')}, {event.get('cache_hits')} hits / "
            f"{event.get('cache_misses')} misses, {event.get('llm_steps')} LLM steps{extra}"
        )
    if t == "verification_done":
        pct = (event.get("field_accuracy") or 0) * 100
        aborted = " (batch was aborted)" if event.get("aborted") else ""
        return (
            f"\n## Supabase verification\n"
            f"{event.get('correct_fields')}/{event.get('total_fields')} fields correct "
            f"({pct:.1f}%), {event.get('unmatched_records')} records not found.{aborted}\n"
        )
    if t == "batch_done":
        label = "Aborted" if event.get("aborted") else "Finished"
        return f"\n## {label}: {event.get('succeeded')} succeeded, {event.get('failed')} failed\n"
    if t == "batch_error":
        return f"\n## Batch error: {event.get('message')}\n"
    if t == "reconciliation_start":
        return f"\n## Reconciliation pass started — {event.get('total')} field(s) to retry\n"
    if t == "reconciliation_fix":
        status = "fixed" if event.get("success") else "still failing"
        return f"- **{status}** {event.get('record_id')} / {event.get('field')}"
    if t == "reconciliation_done":
        return f"\n## Reconciliation finished — {event.get('fixed')} fixed, {event.get('skipped')} still open\n"
    if t == "reconciliation_error":
        return f"\n## Reconciliation pass failed: {event.get('message')}\n"
    return None


# Starts a fresh Logs.md for a new batch and clears the record index map.
# Takes the record count and returns the new generation token.
def reset(total: int) -> int:
    global _generation
    _record_index_by_id.clear()
    with _lock:
        _generation += 1
        generation = _generation
        LOG_PATH.write_text(
            f"# Run log — {datetime.now(timezone.utc).isoformat()}Z\n\n", encoding="utf-8"
        )
    return generation


# Appends one event line to Logs.md.
# Drops the event if its generation is stale because a newer batch has reset the file.
def append(event: dict[str, Any], *, generation: int | None = None) -> None:
    if generation is not None and generation != _generation:
        return
    line = _format(event)
    if line is None:
        return
    with _lock:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
