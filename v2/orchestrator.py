"""Orchestrator for v2: replays cached steps per record, with a live LLM fallback."""

import asyncio
import logging
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from browser_use import BrowserProfile
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from agent.prompts import build_reconciliation_prompt, build_step_prompt
from agent.runner import _run_single_record_async
from cache.instrumentation import StepRecorder
from cache.store import CacheStore, CachedStep
from config import (
    CACHE_DB_PATH,
    CDP_DEBUG_PORT,
    MATCH_KEY_FIELD,
    MAX_CONCURRENT_REPLAYS,
    REPLAY_ELEMENT_TIMEOUT_MS,
    SUPABASE_TABLE,
    TARGET_FORM_URL,
    WORKFLOW_NAME,
)
from data.models import BatchReport, Record, RunResult
from replay.engine import ReplayEngine

logger = logging.getLogger(__name__)

# Optional callback(event_dict) for progress events; the orchestrator has no HTTP deps.
OnEvent = Optional[Callable[[dict[str, Any]], None]]


# Calls on_event safely; callback errors are logged and never break the run.
# Takes the optional callback and an event dict; returns nothing.
def _emit(on_event: OnEvent, event: dict[str, Any]) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.exception("on_event callback raised - ignoring, must not break the run")


_store: CacheStore | None = None


# Returns the shared CacheStore singleton, creating it on first use.
# Takes nothing and returns the CacheStore opened at CACHE_DB_PATH.
def get_store() -> CacheStore:
    global _store
    if _store is None:
        _store = CacheStore(CACHE_DB_PATH)
    return _store


# Set once any record hits the daily LLM quota, so the rest of the batch stops early.
_quota_exhausted_event = threading.Event()


# Tells whether the daily LLM quota has been flagged as exhausted.
# Takes nothing and returns True if the quota flag is set.
def _quota_exhausted() -> bool:
    return _quota_exhausted_event.is_set()


# Flags quota exhaustion and emits one error event (only the first caller acts).
# Takes a RunResult and the on_event callback; returns nothing.
def _note_if_quota_exhausted(run_result: RunResult, on_event: OnEvent) -> None:
    if not run_result.quota_exhausted or _quota_exhausted_event.is_set():
        return
    _quota_exhausted_event.set()
    logger.error(
        "Gemini's DAILY free-tier quota is exhausted (not the per-minute limit - this doesn't "
        "self-recover). Aborting the rest of this batch rather than letting every remaining "
        "record independently discover the same dead end."
    )
    _emit(
        on_event,
        {
            "type": "error",
            "record_id": None,
            "message": (
                "Gemini's daily free-tier quota is exhausted (500 requests/day). "
                "Aborting the remaining batch - this will not self-recover until the quota resets."
            ),
        },
    )


# One lock per (workflow, variant, field) so concurrent records share one live fix.
_missing_field_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


# Returns the lock guarding live self-healing of one missing field, creating it if needed.
# Takes the workflow name, path variant and field name, and returns an asyncio.Lock.
def _get_missing_field_lock(workflow_name: str, path_variant: str, field_name: str) -> asyncio.Lock:
    key = (workflow_name, path_variant, field_name)
    lock = _missing_field_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _missing_field_locks[key] = lock
    return lock


# One lock per (workflow, variant) so only one record cold-seeds the cache at a time.
_seed_locks: dict[tuple[str, str], asyncio.Lock] = {}


# Returns the seed lock for a workflow and path variant, creating it if needed.
# Takes the workflow name and path variant, and returns an asyncio.Lock.
def _get_seed_lock(workflow_name: str, path_variant: str) -> asyncio.Lock:
    key = (workflow_name, path_variant)
    lock = _seed_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _seed_locks[key] = lock
    return lock


# Decides whether the caller must cold-seed the cache, taking the seed lock if so.
# Takes the workflow, variant and store; returns True (lock held) or False if steps already exist.
async def _claim_seed_responsibility(workflow_name: str, path_variant: str, store: CacheStore) -> bool:
    if store.get_steps(workflow_name, path_variant):
        return False
    lock = _get_seed_lock(workflow_name, path_variant)
    await lock.acquire()
    # Release the lock if the re-check fails, so a DB error cannot leave it held forever.
    try:
        already_seeded = bool(store.get_steps(workflow_name, path_variant))
    except Exception:
        lock.release()
        raise
    if already_seeded:
        lock.release()
        return False
    return True


# Releases the seed lock for a workflow and path variant if it is currently held.
# Takes the workflow name and path variant; returns nothing.
def _release_seed_responsibility(workflow_name: str, path_variant: str) -> None:
    lock = _get_seed_lock(workflow_name, path_variant)
    if lock.locked():
        lock.release()


# --- Circuit breaker ---
# After repeated failures a field skips live fallback until the cooldown ends.
_CIRCUIT_FAILURE_THRESHOLD = 2
_CIRCUIT_COOLDOWN_SECONDS = 30
_field_circuit_state: dict[tuple[str, str, str], dict[str, Any]] = {}


# Builds the dict key used for a field in the circuit-breaker state.
# Takes workflow, variant and target names; returns a (workflow, variant, target) tuple.
def _circuit_key(workflow_name: str, path_variant: str, target: str) -> tuple[str, str, str]:
    return (workflow_name, path_variant, target)


# Tells whether live fallback is currently suspended for a field.
# Takes workflow, variant and target names; returns True while the circuit cooldown is active.
def _circuit_is_open(workflow_name: str, path_variant: str, target: str) -> bool:
    state = _field_circuit_state.get(_circuit_key(workflow_name, path_variant, target))
    if not state or state.get("open_until") is None:
        return False
    return datetime.now(timezone.utc) < state["open_until"]


# Counts a failure for a field and opens the circuit once the threshold is reached.
# Takes workflow, variant and target names; updates the circuit state and returns nothing.
def _record_field_failure(workflow_name: str, path_variant: str, target: str) -> None:
    key = _circuit_key(workflow_name, path_variant, target)
    state = _field_circuit_state.setdefault(key, {"consecutive_failures": 0, "open_until": None})
    state["consecutive_failures"] += 1
    if state["consecutive_failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
        state["open_until"] = datetime.now(timezone.utc) + timedelta(seconds=_CIRCUIT_COOLDOWN_SECONDS)
        logger.warning(
            "Circuit opened for %r after %d consecutive failures - skipping live fallback for it for %ds.",
            target,
            state["consecutive_failures"],
            _CIRCUIT_COOLDOWN_SECONDS,
        )


# Resets the failure count for a field after a success.
# Takes workflow, variant and target names; updates the circuit state and returns nothing.
def _record_field_success(workflow_name: str, path_variant: str, target: str) -> None:
    key = _circuit_key(workflow_name, path_variant, target)
    if key in _field_circuit_state:
        _field_circuit_state[key] = {"consecutive_failures": 0, "open_until": None}


# Gets the path variant for a record from its path_variant field.
# Takes a Record and returns that value as a string, or "default" if it is empty.
def resolve_path_variant(record: Record) -> str:
    value = record.field_dict().get("path_variant")
    return str(value) if value else "default"


# Runs the whole record live (no cache yet) and records the steps to populate the cache.
# Takes a Record, the CDP url, store, workflow and variant; returns (RunResult, metrics dict).
async def _run_cold(
    record: Record,
    *,
    cdp_url: str,
    store: CacheStore,
    workflow_name: str,
    path_variant: str,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    metrics = {
        "mode": "cold",
        "cache_hits": 0,
        "cache_misses": 0,
        "llm_steps": 0,
        "miss_reasons": [],
    }

    # Skip the LLM call if the daily quota is already known to be exhausted.
    if _quota_exhausted():
        return (
            RunResult(
                record_id=record.record_id,
                success=False,
                error_message="Skipped - Gemini daily quota already known exhausted",
                timestamp=datetime.now(timezone.utc),
                quota_exhausted=True,
            ),
            metrics,
        )

    recorder = StepRecorder(
        store,
        workflow_name=workflow_name,
        path_variant=path_variant,
        record_fields=record.field_dict(),
        auto_persist=True,
        record_id=record.record_id,
        on_event=on_event,
    )

    run_result = await _run_single_record_async(
        record,
        use_vision=False,
        browser_profile=BrowserProfile(cdp_url=cdp_url),
        register_new_step_callback=recorder.callback,
    )
    _note_if_quota_exhausted(run_result, on_event)

    # Retry once with vision if text-only failed (no sleep: it would hold the seed lock).
    if not run_result.success and not run_result.quota_exhausted and not _quota_exhausted():
        logger.warning(
            "Cold run for record %s didn't succeed on the text-only pass - retrying once with vision enabled.",
            record.record_id,
        )
        run_result = await _run_single_record_async(
            record,
            use_vision=True,
            browser_profile=BrowserProfile(cdp_url=cdp_url),
            register_new_step_callback=recorder.callback,
        )
        _note_if_quota_exhausted(run_result, on_event)

    metrics["llm_steps"] = recorder.steps_seen
    return run_result, metrics


# Runs one live agent call for a single uncached step and self-heals the cache entry.
# Takes a CachedStep, a value, the CDP url and store; returns (succeeded, llm_steps).
async def _run_live_fallback_step(
    step: CachedStep,
    value: Any,
    *,
    cdp_url: str,
    store: CacheStore,
    record_id: str = "",
    on_event: OnEvent = None,
) -> tuple[bool, int]:
    if _quota_exhausted():
        # Don't make a doomed LLM call once quota is known exhausted.
        return False, 0

    record_fields = {step.field_name: value} if step.field_name else {}
    recorder = StepRecorder(
        store,
        workflow_name=step.workflow_name,
        path_variant=step.path_variant,
        record_fields=record_fields,
        auto_persist=False,  # healed step is re-keyed below
        record_id=record_id,
        on_event=on_event,
    )

    task = build_step_prompt(field_name=step.field_name, value=value, instruction=step.instruction)
    # Stub record: only needed for RunResult.record_id (task_override replaces the prompt).
    stub_record = Record(record_id=f"{step.workflow_name}:{step.step_index}.{step.action_offset}")

    try:
        result = await _run_single_record_async(
            stub_record,
            use_vision=False,
            browser_profile=BrowserProfile(cdp_url=cdp_url),
            register_new_step_callback=recorder.callback,
            task_override=task,
        )
        _note_if_quota_exhausted(result, on_event)

        # Retry once with vision if text-only failed (counts as one circuit failure).
        if not result.success and not result.quota_exhausted and not _quota_exhausted():
            logger.warning(
                "Live fallback for step %s.%s (field=%s) didn't succeed on the text-only pass - "
                "retrying once with vision enabled.",
                step.step_index,
                step.action_offset,
                step.field_name,
            )
            result = await _run_single_record_async(
                stub_record,
                use_vision=True,
                browser_profile=BrowserProfile(cdp_url=cdp_url),
                register_new_step_callback=recorder.callback,
                task_override=task,
            )
            _note_if_quota_exhausted(result, on_event)

        self_reported_success = result.success
    except Exception:
        logger.exception("Live fallback failed for step %s.%s (field=%s)", step.step_index, step.action_offset, step.field_name)
        return False, recorder.steps_seen

    # Self-heal the cache under the original step's key.
    if recorder.captured:
        healed = recorder.captured[0]
        healed.step_index = step.step_index
        healed.action_offset = step.action_offset
        healed.field_name = step.field_name or healed.field_name
        store.upsert_step(healed)
    else:
        logger.warning(
            "Live fallback for step %s.%s completed but captured no interactable element - cache entry left stale.",
            step.step_index,
            step.action_offset,
        )

    return self_reported_success, recorder.steps_seen


# Replays cached steps via Playwright, falling back to live single-step calls on a miss.
# Takes a Record, its cached steps, page, CDP url and store; returns (RunResult, metrics dict).
async def _run_replay(
    record: Record,
    cached_steps: list[CachedStep],
    *,
    page,
    cdp_url: str,
    store: CacheStore,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    engine = ReplayEngine(page, timeout_ms=REPLAY_ELEMENT_TIMEOUT_MS)
    fields = record.field_dict()
    workflow_name = cached_steps[0].workflow_name
    path_variant = cached_steps[0].path_variant

    cache_hits = 0
    cache_misses = 0
    miss_reasons: list[str] = []
    llm_steps = 0
    # Only a failed submit step marks the whole record as failed.
    submit_failed = False

    # Replays one step and uses the live fallback on a miss (the submit step is critical).
    # Takes a CachedStep, a value and a critical flag; updates the replay counters in place.
    async def _replay_or_fallback(step: CachedStep, value: Any, *, critical: bool = False) -> None:
        nonlocal cache_hits, cache_misses, llm_steps, submit_failed
        target_key = step.field_name or f"{step.action_type}::{step.x_path}"
        display = step.field_name or f"{step.action_type} (no field)"

        replay_result = await engine.perform(step, value)
        if replay_result.ok:
            cache_hits += 1
            _emit(on_event, {"type": "field_hit", "record_id": record.record_id, "field": display})
            if step.field_name:
                _record_field_success(workflow_name, path_variant, target_key)
            return

        cache_misses += 1

        # Non-critical steps skip live fallback while the circuit is open; submit always tries.
        if not critical and _circuit_is_open(workflow_name, path_variant, target_key):
            miss_reasons.append("circuit_open_skipped")
            logger.info("%r circuit open (repeated recent failures) - skipping live fallback.", target_key)
            _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": display, "reason": "circuit_open_skipped"})
            return

        miss_reasons.append(replay_result.reason or "unknown")
        logger.info(
            "Cache miss on step %s.%s (field=%s, reason=%s) — falling back to a live single-step call.",
            step.step_index,
            step.action_offset,
            step.field_name,
            replay_result.reason,
        )
        _emit(
            on_event,
            {"type": "field_miss", "record_id": record.record_id, "field": display, "reason": replay_result.reason},
        )
        fallback_ok, fallback_llm_steps = await _run_live_fallback_step(
            step, value, cdp_url=cdp_url, store=store, record_id=record.record_id, on_event=on_event
        )
        llm_steps += fallback_llm_steps
        if fallback_ok:
            _record_field_success(workflow_name, path_variant, target_key)
        else:
            _record_field_failure(workflow_name, path_variant, target_key)
            _emit(
                on_event,
                {"type": "error", "record_id": record.record_id, "field": display, "message": "live fallback failed"},
            )
            if critical:
                submit_failed = True

    # Submit is the last cached click with no field; everything else replays before it.
    submit_candidates = [s for s in cached_steps if s.field_name is None and s.action_type == "click"]
    if submit_candidates:
        submit_step = submit_candidates[-1]
        steps_before_submit = [s for s in cached_steps if s is not submit_step]
    else:
        # Should not happen; fall back to treating the last step as submit.
        logger.warning(
            "No fieldless click found among cached steps for %s/%s - falling back to treating the last "
            "cached step as submit, which may be wrong.",
            workflow_name,
            path_variant,
        )
        steps_before_submit = cached_steps[:-1]
        submit_step = cached_steps[-1]

    for step in steps_before_submit:
        if step.field_name is not None:
            if step.field_name not in fields:
                cache_misses += 1
                miss_reasons.append("field_missing_in_record")
                continue
            value = fields[step.field_name]
        else:
            value = None
        await _replay_or_fallback(step, value)

    covered_fields = {s.field_name for s in cached_steps if s.field_name}
    missing_fields = [
        name for name, value in fields.items() if name not in covered_fields and value not in (None, "")
    ]
    for offset, field_name in enumerate(missing_fields):
        if _circuit_is_open(workflow_name, path_variant, field_name):
            cache_misses += 1
            miss_reasons.append("circuit_open_skipped")
            _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": field_name, "reason": "circuit_open_skipped"})
            continue

        lock = _get_missing_field_lock(workflow_name, path_variant, field_name)
        async with lock:
            # Re-check circuit and cache: another record may have changed them while waiting.
            if _circuit_is_open(workflow_name, path_variant, field_name):
                cache_misses += 1
                miss_reasons.append("circuit_open_skipped")
                _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": field_name, "reason": "circuit_open_skipped"})
                continue

            learned_step = next(
                (s for s in store.get_steps(workflow_name, path_variant) if s.field_name == field_name), None
            )
            if learned_step is not None:
                await _replay_or_fallback(learned_step, fields[field_name])
                continue

            cache_misses += 1
            miss_reasons.append("field_missing_from_cache")
            logger.info("Field %r has no cached step yet — falling back to a live single-step call.", field_name)
            _emit(
                on_event,
                {"type": "field_miss", "record_id": record.record_id, "field": field_name, "reason": "field_missing_from_cache"},
            )
            synthetic_step = CachedStep(
                workflow_name=workflow_name,
                path_variant=path_variant,
                # Sorts just before submit once self-healed.
                step_index=max(1, submit_step.step_index - 1),
                action_offset=offset,
                action_type="input",
                field_name=field_name,
                instruction=f"Fill in the {field_name} field.",
                x_path="",
                node_name="",
                attributes={},
                stable_hash=None,
                fingerprint="",
            )
            fallback_ok, fallback_llm_steps = await _run_live_fallback_step(
                synthetic_step, fields[field_name], cdp_url=cdp_url, store=store, record_id=record.record_id, on_event=on_event
            )
            llm_steps += fallback_llm_steps
            if fallback_ok:
                _record_field_success(workflow_name, path_variant, field_name)
            else:
                _record_field_failure(workflow_name, path_variant, field_name)
                _emit(
                    on_event,
                    {"type": "error", "record_id": record.record_id, "field": field_name, "message": "live fallback failed"},
                )

    await _replay_or_fallback(submit_step, None, critical=True)

    run_result = RunResult(
        record_id=record.record_id,
        success=not submit_failed,
        error_message=None,
        timestamp=datetime.now(timezone.utc),
    )
    metrics = {
        "mode": "replay",
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "llm_steps": llm_steps,
        "miss_reasons": miss_reasons,
    }
    return run_result, metrics


# Runs one record end to end in its own browser (cold seed or replay) on its own CDP port.
# Takes a Record, store, workflow name and port offset; returns (RunResult, metrics dict).
async def _run_record_cached_async(
    record: Record,
    *,
    store: CacheStore,
    workflow_name: str,
    port_offset: int = 0,
    index: int = 0,
    total: int = 1,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    path_variant = resolve_path_variant(record)
    started_at = datetime.now(timezone.utc)
    port = CDP_DEBUG_PORT + port_offset

    # Only one record cold-seeds a workflow+variant at a time; the rest replay.
    must_seed = await _claim_seed_responsibility(workflow_name, path_variant, store)
    cached_steps = [] if must_seed else store.get_steps(workflow_name, path_variant)

    display_name = " ".join(
        str(v) for v in (getattr(record, "first_name", None), getattr(record, "last_name", None)) if v
    ) or record.record_id
    _emit(
        on_event,
        {
            "type": "record_start",
            "record_id": record.record_id,
            "index": index,
            "total": total,
            "name": display_name,
            "mode": "cold" if must_seed else "replay",
        },
    )

    # Defaults so the end-of-record bookkeeping works even if this record errors out.
    run_result = RunResult(
        record_id=record.record_id,
        success=False,
        error_message="did not start",
        timestamp=datetime.now(timezone.utc),
    )
    metrics: dict[str, Any] = {
        "mode": "cold" if must_seed else "replay",
        "cache_hits": 0,
        "cache_misses": 0,
        "llm_steps": 0,
        "miss_reasons": [],
    }

    try:
        try:
            with tempfile.TemporaryDirectory(prefix=f"terminautomator_v2_profile_{port_offset}_") as profile_dir:
                async with async_playwright() as playwright:
                    context = await playwright.chromium.launch_persistent_context(
                        profile_dir,
                        headless=False,
                        args=[f"--remote-debugging-port={port}"],
                    )
                    try:
                        page = context.pages[0] if context.pages else await context.new_page()
                        await page.goto(TARGET_FORM_URL)
                        cdp_url = f"http://127.0.0.1:{port}"

                        # Skip the response wait when quota is exhausted: no POST would arrive.
                        skip_due_to_quota = must_seed and _quota_exhausted()
                        if skip_due_to_quota:
                            run_result = RunResult(
                                record_id=record.record_id,
                                success=False,
                                error_message="Skipped - Gemini daily quota already known exhausted",
                                timestamp=datetime.now(timezone.utc),
                                quota_exhausted=True,
                            )
                            await page.wait_for_timeout(300)
                        else:
                            # Wait for the Supabase insert response so the browser isn't closed mid-request.
                            # A timeout is only a warning; run_result is already set and must survive.
                            response_info = None
                            try:
                                async with page.expect_response(
                                    lambda r: f"/rest/v1/{SUPABASE_TABLE}" in r.url and r.request.method == "POST",
                                    timeout=30000,
                                ) as response_info:
                                    if must_seed:
                                        run_result, metrics = await _run_cold(
                                            record,
                                            cdp_url=cdp_url,
                                            store=store,
                                            workflow_name=workflow_name,
                                            path_variant=path_variant,
                                            on_event=on_event,
                                        )
                                    else:
                                        run_result, metrics = await _run_replay(
                                            record, cached_steps, page=page, cdp_url=cdp_url, store=store, on_event=on_event
                                        )
                            except PlaywrightTimeoutError:
                                if run_result.success:
                                    logger.warning(
                                        "Record %s reported success but no Supabase insert response was "
                                        "observed within 30s under current load - not necessarily lost, "
                                        "verify_batch is the actual authority on whether it landed.",
                                        record.record_id,
                                    )
                            else:
                                if run_result.success and response_info is not None:
                                    response = await response_info.value
                                    if response.status >= 400:
                                        logger.warning(
                                            "Record %s: Supabase insert returned status %s - "
                                            "record likely did not actually land.",
                                            record.record_id,
                                            response.status,
                                        )
                            # Short settle buffer for UI state such as toast or modal close.
                            await page.wait_for_timeout(300)
                    finally:
                        await context.close()
        except Exception as exc:  # noqa: BLE001 - one record's failure must not stop the batch
            logger.exception("Record %s failed unexpectedly (mode=%s)", record.record_id, metrics["mode"])
            run_result = RunResult(
                record_id=record.record_id,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp=datetime.now(timezone.utc),
            )
            # metrics stays at the zeroed default.
    finally:
        if must_seed:
            _release_seed_responsibility(workflow_name, path_variant)

    finished_at = datetime.now(timezone.utc)
    store.record_run(
        workflow_name=workflow_name,
        path_variant=path_variant,
        record_id=record.record_id,
        mode=metrics["mode"],
        started_at=started_at,
        finished_at=finished_at,
        cache_hits=metrics["cache_hits"],
        cache_misses=metrics["cache_misses"],
        llm_steps=metrics["llm_steps"],
        miss_reasons=metrics["miss_reasons"],
    )
    _emit(
        on_event,
        {
            "type": "record_done",
            "record_id": record.record_id,
            "index": index,
            "total": total,
            "success": run_result.success,
            "error_message": run_result.error_message,
            "mode": metrics["mode"],
            "cache_hits": metrics["cache_hits"],
            "cache_misses": metrics["cache_misses"],
            "llm_steps": metrics["llm_steps"],
        },
    )
    if not run_result.success:
        _emit(
            on_event,
            {
                "type": "error",
                "record_id": record.record_id,
                "message": run_result.error_message or "record did not complete successfully",
            },
        )
    return run_result, metrics


# Unused for now (kept for running a single record from a script); uncomment if needed.
# # Sync wrapper that runs one record through the cached pipeline.
# # Takes a Record and optional workflow name and port offset; returns its RunResult.
# def run_record_cached(
#     record: Record,
#     *,
#     workflow_name: str = WORKFLOW_NAME,
#     port_offset: int = 0,
#     index: int = 1,
#     total: int = 1,
#     on_event: OnEvent = None,
# ) -> RunResult:
#     run_result, _metrics = asyncio.run(
#         _run_record_cached_async(
#             record,
#             store=get_store(),
#             workflow_name=workflow_name,
#             port_offset=port_offset,
#             index=index,
#             total=total,
#             on_event=on_event,
#         )
#     )
#     return run_result


# Callable that returns True when no new records should be started.
AbortCheck = Optional[Callable[[], bool]]


# Runs all records concurrently, bounded by max_concurrent via a pool of CDP port offsets.
# Takes the records, store, workflow name and limits; returns a list of RunResult.
async def _run_many_concurrent_async(
    records: list[Record],
    *,
    store: CacheStore,
    workflow_name: str,
    max_concurrent: int,
    index_by_id: dict[str, int],
    total: int,
    on_event: OnEvent,
    abort_check: AbortCheck = None,
) -> list[RunResult]:
    port_pool: asyncio.Queue[int] = asyncio.Queue()
    for i in range(max_concurrent):
        port_pool.put_nowait(i)

    # Waits for a free port slot, then runs one record and frees the slot.
    # Takes a Record and returns its RunResult (a failed one if aborted or errored).
    async def _run_one(record: Record) -> RunResult:
        port_offset = await port_pool.get()
        try:
            # Skip records not yet started on abort or quota exhaustion; in-flight ones finish.
            if (abort_check is not None and abort_check()) or _quota_exhausted():
                reason = "quota exhausted before starting" if _quota_exhausted() else "aborted before starting"
                return RunResult(
                    record_id=record.record_id,
                    success=False,
                    error_message=reason,
                    timestamp=datetime.now(timezone.utc),
                    quota_exhausted=_quota_exhausted(),
                )
            result, _metrics = await _run_record_cached_async(
                record,
                store=store,
                workflow_name=workflow_name,
                port_offset=port_offset,
                index=index_by_id[record.record_id],
                total=total,
                on_event=on_event,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - keep one record from cancelling the batch
            logger.exception("Unexpected error running record %s", record.record_id)
            return RunResult(
                record_id=record.record_id,
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp=datetime.now(timezone.utc),
            )
        finally:
            port_pool.put_nowait(port_offset)

    return list(await asyncio.gather(*(_run_one(r) for r in records)))


# Runs records concurrently via cache replay; the LLM is used only for cold starts and misses.
# Takes a list of Records and returns a list of RunResult; resets the quota flag on each call.
def run_batch_cached(
    records: list[Record],
    *,
    workflow_name: str = WORKFLOW_NAME,
    max_concurrent: int = MAX_CONCURRENT_REPLAYS,
    on_event: OnEvent = None,
    abort_check: AbortCheck = None,
) -> list[RunResult]:
    if not records:
        return []

    _quota_exhausted_event.clear()
    # Fresh locks per batch: an asyncio.Lock is tied to the event loop that first contends it
    _seed_locks.clear()
    _missing_field_locks.clear()
    total = len(records)
    index_by_id = {record.record_id: i + 1 for i, record in enumerate(records)}
    _emit(on_event, {"type": "batch_start", "total": total})

    store = get_store()

    results = asyncio.run(
        _run_many_concurrent_async(
            records,
            store=store,
            workflow_name=workflow_name,
            max_concurrent=max_concurrent,
            index_by_id=index_by_id,
            total=total,
            on_event=on_event,
            abort_check=abort_check,
        )
    )
    _emit(
        on_event,
        {
            "type": "batch_done",
            "total": total,
            "succeeded": sum(1 for r in results if r.success),
            "failed": sum(1 for r in results if not r.success),
            "aborted": bool(abort_check() if abort_check is not None else False) or _quota_exhausted(),
            "quota_exhausted": _quota_exhausted(),
        },
    )
    return results


# --- Reconciliation: retry fields flagged by verify_batch via the edit flow ---
# Ports are offset from the main batch's range so both can run at once.
RECONCILE_PORT_OFFSET_BASE = 100
RECONCILE_MAX_CONCURRENT = 3


# Fixes one field on an already-submitted record with a live edit-flow agent call.
# Takes the match key, field name, value and port offset; returns True if the edit succeeded.
async def _reconcile_one_field_async(
    *,
    match_key_field: str,
    match_key_value: Any,
    field_name: str,
    value: Any,
    port_offset: int,
    record_id: str,
    on_event: OnEvent,
) -> bool:
    if _quota_exhausted():
        # Fail fast, before opening a browser, if quota is exhausted.
        return False

    port = CDP_DEBUG_PORT + RECONCILE_PORT_OFFSET_BASE + port_offset

    with tempfile.TemporaryDirectory(prefix=f"terminautomator_v2_reconcile_{port_offset}_") as profile_dir:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                args=[f"--remote-debugging-port={port}"],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(TARGET_FORM_URL)
                cdp_url = f"http://127.0.0.1:{port}"

                recorder = StepRecorder(
                    get_store(),
                    workflow_name="reconciliation",
                    path_variant="edit",
                    record_fields={field_name: value},
                    auto_persist=False,  # editing isn't part of the create-record cache
                    record_id=record_id,
                    on_event=on_event,
                )
                task = build_reconciliation_prompt(
                    match_key_field=match_key_field,
                    match_key_value=match_key_value,
                    field_name=field_name,
                    value=value,
                )
                stub_record = Record(record_id=f"reconcile:{record_id}:{field_name}")
                result = await _run_single_record_async(
                    stub_record,
                    use_vision=False,
                    browser_profile=BrowserProfile(cdp_url=cdp_url),
                    register_new_step_callback=recorder.callback,
                    task_override=task,
                )
                _note_if_quota_exhausted(result, on_event)

                # Retry once with vision if the text-only edit failed.
                if not result.success and not result.quota_exhausted and not _quota_exhausted():
                    logger.warning(
                        "Reconciliation fix for %s/%s didn't succeed on the text-only pass - "
                        "retrying once with vision enabled.",
                        record_id,
                        field_name,
                    )
                    result = await _run_single_record_async(
                        stub_record,
                        use_vision=True,
                        browser_profile=BrowserProfile(cdp_url=cdp_url),
                        register_new_step_callback=recorder.callback,
                        task_override=task,
                    )
                    _note_if_quota_exhausted(result, on_event)

                await page.wait_for_timeout(2000)
                return result.success
            finally:
                await context.close()


# Retries fields flagged by verify_batch via the edit flow.
# Takes the records and a BatchReport; returns a dict with the fixed and skipped counts.
async def run_reconciliation_pass(
    records: list[Record],
    report: BatchReport,
    *,
    workflow_name: str = WORKFLOW_NAME,
    on_event: OnEvent = None,
) -> dict[str, int]:
    if not records:
        return {"fixed": 0, "skipped": 0}

    fields_by_record = {r.record_id: r.field_dict() for r in records}
    path_variant = resolve_path_variant(records[0])

    todo: list[tuple[str, str, Any]] = []
    for rv in report.per_record_results:
        if not rv.matched_in_db:
            continue  # never landed: needs the create flow, not an edit
        for fr in rv.field_results:
            if fr.passed:
                continue
            if fr.expected_value in (None, ""):
                continue  # correctly empty, not a real gap
            if _circuit_is_open(workflow_name, path_variant, fr.field_name):
                continue  # still known-broken, don't waste an attempt on it yet
            todo.append((rv.record_id, fr.field_name, fr.expected_value))

    _emit(on_event, {"type": "reconciliation_start", "total": len(todo)})
    if not todo:
        _emit(on_event, {"type": "reconciliation_done", "fixed": 0, "skipped": 0})
        return {"fixed": 0, "skipped": 0}

    port_pool: asyncio.Queue[int] = asyncio.Queue()
    for i in range(RECONCILE_MAX_CONCURRENT):
        port_pool.put_nowait(i)

    fixed = 0
    skipped = 0

    # Reconciles one field once a port slot is free, then updates the fixed/skipped counters.
    # Takes a record id, field name and value; returns nothing.
    async def _run_one(record_id: str, field_name: str, value: Any) -> None:
        nonlocal fixed, skipped
        port_offset = await port_pool.get()
        try:
            ok = await _reconcile_one_field_async(
                match_key_field=MATCH_KEY_FIELD,
                match_key_value=fields_by_record.get(record_id, {}).get(MATCH_KEY_FIELD),
                field_name=field_name,
                value=value,
                port_offset=port_offset,
                record_id=record_id,
                on_event=on_event,
            )
        except Exception:
            logger.exception("Reconciliation attempt failed for %s/%s", record_id, field_name)
            ok = False
        finally:
            port_pool.put_nowait(port_offset)

        if ok:
            fixed += 1
            _record_field_success(workflow_name, path_variant, field_name)
        else:
            skipped += 1
            _record_field_failure(workflow_name, path_variant, field_name)
        _emit(on_event, {"type": "reconciliation_fix", "record_id": record_id, "field": field_name, "success": ok})

    await asyncio.gather(*(_run_one(rid, fn, val) for rid, fn, val in todo))
    _emit(on_event, {"type": "reconciliation_done", "fixed": fixed, "skipped": skipped})
    return {"fixed": fixed, "skipped": skipped}


# Sync wrapper around run_reconciliation_pass, meant to run in its own background thread.
# Takes the records and a BatchReport; returns the dict of fixed and skipped counts.
def run_reconciliation_pass_sync(
    records: list[Record], report: BatchReport, *, on_event: OnEvent = None
) -> dict[str, int]:
    return asyncio.run(run_reconciliation_pass(records, report, on_event=on_event))
