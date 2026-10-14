"""Orchestrator for v2: replays cached steps per record, with a live LLM fallback.

For tasks that read and decide, it learns a plan from the live agent's work, proves it against the live agent, and then
learns more as records finish: pages the instructions need (coverage), a decision program that decides kinds of records with
no model call once it has agreed with the model, values copied from pages that code can read, whether the site shows what was
saved (read-back), and side trips that can be left out (pruning)."""

import asyncio
import copy
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from browser_use import BrowserProfile
from playwright.async_api import async_playwright

from agent.prompts import build_reconciliation_prompt, build_step_prompt, build_widget_prompt
from agent.runner import _run_single_record_async
from cache.instrumentation import StepRecorder
from cache.store import CacheStore, CachedStep
from data.models import BatchReport, Record, RunResult
from recipe import coverage, readback
from recipe import program as programs
from recipe import store as recipe_store
from recipe.compile import compile_recipe
from recipe.execute import DecisionBook, run_recipe
from recipe.llm import LlmHalt
from recipe.network import MutationTracker
from recipe.plan import trace_key, _norm as _norm_text
from recipe.plan import PROOF_NEEDED, committed_values, compile_plan, learn_fields, needs_proof, replay_verdict, spec_text, url_shape, values_agree
from recipe.plan_exec import _SLOT_CODE_AGREE, PlanBatchers, _quick_read, run_plan
from recipe.trace import TraceRecorder
from replay.engine import ReplayEngine
from run_context import RunContext

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
# Takes nothing and returns the CacheStore (it uses the app database).
def get_store() -> CacheStore:
    global _store
    if _store is None:
        _store = CacheStore()
    return _store


# Set once any record hits the daily LLM quota or a fatal error (bad API key), so the rest of the batch stops early.
_quota_exhausted_event = threading.Event()

# The fatal error that stopped the batch, if that is why the flag above is set (None for a plain quota stop).
_halt_reason: dict[str, str | None] = {"text": None}


# Tells whether the daily LLM quota has been flagged as exhausted.
# Takes nothing and returns True if the quota flag is set.
def _quota_exhausted() -> bool:
    return _quota_exhausted_event.is_set()


# Says why records are being skipped once the batch has been halted.
# Takes nothing and returns the message shown for each skipped record.
def _skipped_message() -> str:
    return f"Skipped - {_halt_reason['text']}" if _halt_reason["text"] else "Skipped - Gemini daily quota already known exhausted"


# Returns the fatal error that halted the current batch, or None.
def halt_reason() -> str | None:
    return _halt_reason["text"]


# Flags quota exhaustion or a fatal error and emits one error event (only the first caller acts).
# Takes a RunResult and the on_event callback; returns nothing.
def _note_if_quota_exhausted(run_result: RunResult, on_event: OnEvent) -> None:
    if run_result.fatal_error and not _quota_exhausted_event.is_set():
        _halt_reason["text"] = run_result.fatal_error
        _quota_exhausted_event.set()
        logger.error("Stopping the batch: %s", run_result.fatal_error)
        _emit(on_event, {"type": "error", "record_id": None, "message": run_result.fatal_error})
        return
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
                "Gemini's daily free-tier quota is exhausted on every key (500 requests/day per key and model). "
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


# True when this workflow variant has already been learned from: it has replayable steps, a recipe or a plan, or a
# verdict that its clicks cannot be replayed (so a first record must not seed it again).
def _already_learned(workflow_name: str, path_variant: str, store: CacheStore) -> bool:
    return (
        bool(store.get_steps(workflow_name, path_variant))
        or (workflow_name, path_variant) in _no_replay
        or recipe_store.get_recipe(workflow_name, path_variant) is not None
    )


# Decides whether the caller must cold-seed the cache, taking the seed lock if so.
# Takes the workflow, variant and store; returns True (lock held) or False if steps already exist.
async def _claim_seed_responsibility(workflow_name: str, path_variant: str, store: CacheStore) -> bool:
    if _already_learned(workflow_name, path_variant, store):
        return False
    lock = _get_seed_lock(workflow_name, path_variant)
    await lock.acquire()
    # Release the lock if the re-check fails, so a DB error cannot leave it held forever.
    try:
        already_seeded = _already_learned(workflow_name, path_variant, store)
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


# --- Recipes: learned from the AI's own work on a few records, then used for the rest ---
# Per batch: one lock per (workflow, variant) so records learn one at a time, the recipes in use,
# consecutive failures, and workflows where learning or using a recipe was given up.
_learn_locks: dict[tuple[str, str], asyncio.Lock] = {}
_recipes: dict[tuple[str, str], dict[str, Any]] = {}
_recipe_failures: dict[tuple[str, str], int] = {}
_recipe_given_up: set[tuple[str, str]] = set()
# Records of the batch (id -> fields), the ones finished so far, and one decision book per workflow and variant
_all_fields: dict[str, dict[str, Any]] = {}
_finished_keys: set[str] = set()
_books: dict[tuple[str, str], DecisionBook] = {}
# One pair of question batchers per workflow and variant for a trusted plan: records that are in flight together share model calls
_plan_batchers: dict[tuple[str, str], PlanBatchers] = {}
# Workflow variants whose first record showed that replaying its clicks would be wrong (see recipe/plan.replay_verdict)
_no_replay: set[tuple[str, str]] = set()
# The trace each plan in use was learned from (the pages its route reads, for the coverage question)
_plan_traces: dict[tuple[str, str], list[dict[str, Any]]] = {}
# Records the AI may do itself while a recipe cannot be learned, before giving up for the batch
_RECIPE_MAX_TRACES = 4
# A recipe that fails this many records in a row is dropped for the rest of the batch
_RECIPE_MAX_FAILURES = 3


# Returns the learning lock for a workflow and variant, creating it if needed.
def _get_learn_lock(workflow_name: str, path_variant: str) -> asyncio.Lock:
    key = (workflow_name, path_variant)
    lock = _learn_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _learn_locks[key] = lock
    return lock


# Tries to build a recipe from the saved traces; saves and remembers it when it works. A new plan is marked to have its route
# checked against the instructions (coverage) on its next proof record.
# Takes the workflow name, variant, event callback and the task's instructions; returns True when a recipe now exists.
def _try_compile_recipe(workflow_name: str, path_variant: str, on_event: OnEvent, instructions: str = "") -> bool:
    key = (workflow_name, path_variant)
    traces = recipe_store.get_traces(workflow_name, path_variant)
    recipe, reason = compile_recipe(traces)
    if recipe is not None:
        recipe_store.save_recipe(workflow_name, path_variant, recipe)
        _recipes[key] = recipe
        pages = len(recipe["read"])
        _emit(on_event, {"type": "note", "message": f"Learned a recipe: read {pages} page{'s' if pages != 1 else ''}, decide once, click. The remaining records will use it."})
        return True
    plan_reason = "its clicks can be replayed as they are"
    if traces and key in _no_replay:
        # The clicks cannot be replayed as they are (record 1's values are in them): try a plan, which marks what varies.
        # Each saved trace is tried in turn: one record's route can hold a step that cannot be found again where another's does not.
        plan = None
        for record_id, trace_fields, trace in traces:
            plan, plan_reason = compile_plan(trace_fields, trace, instructions)
            if plan is not None:
                _plan_traces[key] = trace
                break
            logger.info("No plan from record %s's trace: %s", record_id, plan_reason)
        if plan is not None:
            plan["coverage_pending"] = True
            recipe_store.save_recipe(workflow_name, path_variant, plan)
            _recipes[key] = plan
            steps = len(plan["gather"]) + len(plan["commit"]["submit"])
            _emit(on_event, {"type": "note", "message": (
                f"Learned a plan: {steps} repeatable steps, {len(plan['slots'])} value(s) to copy from pages, "
                f"{len(plan['commit']['controls'])} form field(s) to decide. It must agree with the live agent on {PROOF_NEEDED} records before it is trusted."
            )})
            return True
    if len(traces) >= _RECIPE_MAX_TRACES:
        _recipe_given_up.add(key)
        why = f"{reason}; plan: {plan_reason}"
        recipe_store.save_recipe(workflow_name, path_variant, {"kind": "live_only", "reason": why})
        _emit(on_event, {"type": "note", "message": f"Could not learn a recipe or a plan ({why}). Every record will use the live agent."})
    return False


# Decides whether a record can use a recipe, or must learn one by running live.
# Takes the workflow name, variant, event callback and the task's instructions; returns (recipe or None, learning).
# When learning is True the learning lock is held and the caller must release it with _release_learn.
async def _claim_recipe(workflow_name: str, path_variant: str, on_event: OnEvent, instructions: str = "") -> tuple[dict[str, Any] | None, bool]:
    key = (workflow_name, path_variant)
    if key in _recipe_given_up:
        return None, False
    if key in _recipes and not needs_proof(_recipes[key]):
        return _recipes[key], False
    lock = _get_learn_lock(workflow_name, path_variant)
    await lock.acquire()
    try:
        if key in _recipe_given_up:
            lock.release()
            return None, False
        recipe = _recipes.get(key) or recipe_store.get_recipe(workflow_name, path_variant)
        if recipe is not None and recipe.get("kind") == "live_only":
            _recipe_given_up.add(key)
            lock.release()
            return None, False
        if recipe is None and recipe_store.get_traces(workflow_name, path_variant):
            _try_compile_recipe(workflow_name, path_variant, on_event, instructions)
            recipe = _recipes.get(key)
        if recipe is not None:
            _recipes[key] = recipe
            if needs_proof(recipe):
                return None, True  # the lock stays held: this record proves the plan against the live agent, one record at a time
            lock.release()
            return recipe, False
        if key in _recipe_given_up:
            lock.release()
            return None, False
    except Exception:
        lock.release()
        raise
    return None, True


# Returns the decision book for a workflow and variant, creating it from the records still open.
def _get_book(workflow_name: str, path_variant: str) -> DecisionBook:
    key = (workflow_name, path_variant)
    book = _books.get(key)
    if book is None:
        book = DecisionBook(dict(_all_fields), [k for k in _all_fields if k not in _finished_keys])
        _books[key] = book
    return book


# Releases the learning lock if it is held.
def _release_learn(workflow_name: str, path_variant: str) -> None:
    lock = _get_learn_lock(workflow_name, path_variant)
    if lock.locked():
        lock.release()


# Runs one record with its learned recipe; when the recipe cannot do it and nothing was clicked yet, the live AI does it instead.
# Takes the record, recipe, RunContext, the open page, CDP url, store, variant and event callback; returns (RunResult, metrics).
async def _run_recipe_with_fallback(
    record: Record,
    recipe: dict[str, Any],
    *,
    ctx: RunContext,
    page,
    cdp_url: str,
    store: CacheStore,
    path_variant: str,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    key = (ctx.workflow_name, path_variant)
    metrics: dict[str, Any] = {"mode": "recipe", "cache_hits": 0, "cache_misses": 0, "llm_steps": 0, "miss_reasons": []}

    # Tags the recipe's events with this record before they go out.
    def emit(event: dict[str, Any]) -> None:
        _emit(on_event, {**event, "record_id": record.record_id})

    try:
        outcome = await run_recipe(
            recipe, record.field_dict(), ctx=ctx, page=page, emit=emit, key=record.record_id, book=_get_book(ctx.workflow_name, path_variant)
        )
    except LlmHalt as halt:
        halted = RunResult(
            record_id=record.record_id,
            success=False,
            error_message=str(halt),
            timestamp=datetime.now(timezone.utc),
            quota_exhausted=halt.quota,
            fatal_error=None if halt.quota else str(halt),
        )
        _note_if_quota_exhausted(halted, on_event)
        return halted, metrics

    metrics["cache_hits"] = outcome.steps
    metrics["llm_steps"] = outcome.llm_calls
    if outcome.ok:
        _recipe_failures[key] = 0
        return RunResult(record_id=record.record_id, success=True, timestamp=datetime.now(timezone.utc), summary=outcome.summary), metrics

    metrics["cache_misses"] = 1
    metrics["miss_reasons"] = [outcome.error or "unknown"]
    _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": "recipe", "reason": outcome.error or "unknown"})
    _recipe_failures[key] = _recipe_failures.get(key, 0) + 1
    if _recipe_failures[key] >= _RECIPE_MAX_FAILURES:
        _recipes.pop(key, None)
        _recipe_given_up.add(key)
        _emit(on_event, {"type": "note", "message": f"The recipe failed {_RECIPE_MAX_FAILURES} records in a row, so the rest use the live agent."})

    if outcome.acted:
        # The click was already attempted, so doing it again could act twice
        return (
            RunResult(
                record_id=record.record_id,
                success=False,
                error_message=f"The recipe clicked but could not confirm it worked: {outcome.error}",
                timestamp=datetime.now(timezone.utc),
                summary=outcome.summary,
            ),
            metrics,
        )

    # Nothing was clicked: start over with the live agent on a fresh page
    try:
        await page.goto(ctx.target_url)
    except Exception:
        logger.exception("Could not reopen the start page for the live fallback")
    run_result, live_metrics = await _run_cold(
        record, ctx=ctx, cdp_url=cdp_url, store=store, path_variant=path_variant, persist=False, on_event=on_event
    )
    live_metrics.pop("trace", None)
    live_metrics["mode"] = "recipe+live"
    live_metrics["llm_steps"] += outcome.llm_calls
    live_metrics["cache_hits"] = outcome.steps
    live_metrics["cache_misses"] = 1
    live_metrics["miss_reasons"] = [outcome.error or "unknown"]
    return run_result, live_metrics


# True when a plan refused a record because its pages do not show what a rule needs (see plan_exec._values_from_answer).
def _lacks_pages(error: str | None) -> bool:
    return re.search(r"pages do not (?:show|hold) what", error or "") is not None


# --- Trust per kind of decision. A plan proven on records of one kind (a "wrong item" return) must not be trusted on another
# (a change-of-mind refund that needs the policy table): a trusted plan only decides a kind of record that has agreed with the
# live agent at least once. A kind is the rule that applied plus the choices made (plan_exec: PlanOutcome.branch).

# True when a plan refused a record because its kind of decision has not been checked against the live agent yet.
def _unchecked_branch(error: str | None) -> bool:
    return "has not been checked against the live agent yet" in (error or "")


# The kinds of decision a plan may make alone: those that agreed with the live agent (a kind that disagreed is marked below 0).
def _trusted_branches(plan: dict[str, Any]) -> set[str]:
    return {b for b, n in (plan.get("trust") or {}).get("branches", {}).items() if n > 0}


# Counts one comparison of a kind of decision: agreeing makes it trusted; disagreeing marks it so the live agent always does it.
def _note_branch(plan: dict[str, Any], branch: str, agreed: bool) -> None:
    branches = plan.setdefault("trust", {}).setdefault("branches", {})
    if agreed and branches.get(branch, 0) >= 0:
        branches[branch] = branches.get(branch, 0) + 1
    elif not agreed:
        branches[branch] = -1


# A trusted plan met a kind of record it had not been checked on: it said what it would enter, then the live agent did the record.
# Compares the two and records the result for that kind. Takes the plan, the plan's outcome, the live trace and the usual context;
# returns True when the plan would have entered the same values as the live agent.
def _check_branch(plan: dict[str, Any], outcome, trace: list[dict[str, Any]], record_id: str, workflow_name: str, path_variant: str, on_event: OnEvent) -> bool:
    actual = committed_values(trace, set(outcome.kinds))
    # A field of a later step the plan did not answer yet (a confirmation tick) is asked on its own step, so it is no difference
    later = {c["key"] for c in plan.get("commit", {}).get("controls", []) if c.get("stage", 0) > 0}
    actual = {k: v for k, v in actual.items() if k in outcome.planned or k not in later}
    same, why = values_agree(outcome.planned, actual, outcome.kinds)
    _note_branch(plan, outcome.branch, same)
    recipe_store.save_recipe(workflow_name, path_variant, plan)
    if same:
        message = f"Checked a new kind of record ({outcome.branch}) on {record_id}: the plan would have entered the same values as the live agent, so it now decides this kind too."
    else:
        message = f"A new kind of record ({outcome.branch}) on {record_id}: {why}. The live agent keeps doing this kind."
    _emit(on_event, {"type": "note", "message": message, "record_id": record_id})
    return same


# Makes the function a plan uses to set a custom control (a calendar): one scoped live-agent step on the open page that sets
# only that control and never saves. Takes the record, the RunContext and the CDP url; the function returns (True when it
# worked, the model calls the agent made).
def _widget_filler(record: Record, ctx: RunContext, cdp_url: str):
    async def fill(label: str, value: str, hint: str) -> tuple[bool, int]:
        steps = [0]

        # Counts the scoped agent's steps (one model call each).
        def count(_state, _output, step_num: int) -> None:
            steps[0] = max(steps[0], int(step_num or 0))

        result = await _run_single_record_async(
            record,
            ctx=ctx,
            use_vision=False,
            browser_profile=BrowserProfile(cdp_url=cdp_url),
            register_new_step_callback=count,
            task_override=build_widget_prompt(label=label, value=value, hint=hint),
        )
        if result.quota_exhausted or result.fatal_error:
            raise LlmHalt(result.fatal_error or "The Gemini daily quota is exhausted.", quota=result.fatal_error is None)
        return bool(result.success), steps[0]

    return fill


# --- Decision programs, slots read by code, read-back and pruning: what the orchestrator learns from records as they finish.
# --- Switches for the parts of the 10 Oct round that have not proven themselves live yet. They are OFF unless the environment
# variable TA_FEATURES names them (comma separated, or "all"): with them off the engine behaves like the 9 Oct engine that scored
# 16/16, plus the safe additions (pacing, grounding, page handling, the wizard fixes). See docs/12 for what each one does.
FEATURE_NAMES = ("programs", "coverage", "pruning", "readback", "slot_code", "extend_on_agree")
_wanted = {f.strip().lower() for f in os.getenv("TA_FEATURES", "").split(",") if f.strip()}
FEATURES = {name: ("all" in _wanted or name in _wanted) for name in FEATURE_NAMES}


# True when an optional part of the engine is switched on. Takes its name (one of FEATURE_NAMES).
def _on(name: str) -> bool:
    return bool(FEATURES.get(name, False))


# Verified examples per (workflow, variant): proof records whose dry run agreed with the live agent, and plan records the model
# decided ({"record_id", "fields", "pages", "values"}); each form field's kind as the records saw it.
_examples: dict[tuple[str, str], list[dict[str, Any]]] = {}
_example_kinds: dict[tuple[str, str], dict[str, str]] = {}
# One lock per (workflow, variant) so only one record asks the model for a decision program at a time
_program_locks: dict[tuple[str, str], asyncio.Lock] = {}
# How many examples there were when a program was last asked for, so a failed or dropped one is only asked again with more
_program_tried: dict[tuple[str, str], int] = {}
# Workflow variants where the model decided a record the program could not decide (a kind of record the program does not cover)
_program_gaps: set[tuple[str, str]] = set()
# How often a program was asked for in this batch, per workflow and variant
_program_asks: dict[tuple[str, str], int] = {}
# Counts for the note at the end of the batch: records the program decided alone, and model calls the run made in total
_batch_counts: dict[str, int] = {"program": 0, "model_calls": 0}
# A program is first asked for once this many verified examples exist; after a failed or dropped one, this many more are needed
_PROGRAM_EXAMPLES = 4
_PROGRAM_MORE = 2
# How many times one batch may ask for a program per workflow and variant
_PROGRAM_MAX_ASKS = 3
# Disagreements with the model after which a program is dropped
_PROGRAM_MAX_DISAGREE = 2
# How many verified examples are kept per workflow and variant (the newest)
_KEEP_EXAMPLES = 12
# Read-back checks that must agree before a plan decides whether the site shows what was saved
_READBACK_CHECKS = 2


# Returns the program-learning lock for a workflow and variant, creating it if needed.
def _get_program_lock(key: tuple[str, str]) -> asyncio.Lock:
    lock = _program_locks.get(key)
    if lock is None:
        lock = _program_locks[key] = asyncio.Lock()
    return lock


# The plan a record's bookkeeping goes to: the plan in use for its workflow variant when it is one (a proof record may have
# replaced the plan it checked by an extended one), else the given plan. Takes the key and the plan the record ran.
def _current_plan(key: tuple[str, str], plan: dict[str, Any]) -> dict[str, Any]:
    current = _recipes.get(key)
    return current if isinstance(current, dict) and current.get("kind") == "plan" else plan


# Keeps a verified example for learning a decision program. Takes the key, the record id, the example ({"fields", "pages",
# "values"}) and the form fields' kinds; a newer example of the same record replaces the older one.
# live_trace: the live agent's trace for this record, when it did the record. Its values are the verified truth and fill in what
# the plan's dry run never reached (the later steps of a form in several steps).
def _add_example(key: tuple[str, str], record_id: str, example: dict[str, Any] | None, kinds: dict[str, str], live_trace: list[dict[str, Any]] | None = None) -> None:
    if not isinstance(example, dict) or not example.get("values") or example.get("pages") is None:
        return
    values = dict(example["values"])
    if live_trace:
        touched = {trace_key(e["el"]) for e in live_trace if e.get("el")} - {None}
        for k, v in committed_values(live_trace, set(kinds or {}) | touched).items():
            # one ticked checkbox is "yes" in a decision (the live agent's trace holds its label)
            values[k] = "yes" if (kinds or {}).get(k) == "check" and v else v
    kept = [e for e in _examples.get(key, []) if e.get("record_id") != record_id]
    kept.append({"record_id": record_id, "fields": dict(example.get("fields") or {}), "pages": list(example["pages"]), "values": values})
    _examples[key] = kept[-_KEEP_EXAMPLES:]
    _example_kinds.setdefault(key, {}).update(kinds or {})


# The id of the plan's decision program (given to a program that has none), or None when the plan has no program. A record
# notes it before it runs, so its program check is only counted toward that same program (not one relearned meanwhile).
def _program_id(plan: dict[str, Any]) -> str | None:
    program = plan.get("program") if isinstance(plan, dict) else None
    if not isinstance(program, dict):
        return None
    if not program.get("id"):
        program["id"] = uuid.uuid4().hex
    return str(program["id"])


# Books what the plan's decision program did on one record (outcome.program_check: its branch and whether it agreed with the
# model): agreeing counts towards trusting that kind of record; a disagreement marks it, and a program that disagreed
# _PROGRAM_MAX_DISAGREE times is dropped (it may be learned again from more examples). A record the model decided while the
# program could not marks a gap (a kind the program does not cover). Takes the plan (changed in place), the outcome, the key,
# the record id, the event callback, optionally the check to book instead of outcome.program_check (after it was compared
# with the live agent) and the id of the program the record ran (a check of another program is ignored); returns True when
# the plan changed.
def _book_program(plan: dict[str, Any], outcome, key: tuple[str, str], record_id: str, on_event: OnEvent,
                  check: tuple[str, bool] | None = None, program_id: str | None = None) -> bool:
    program = plan.get("program")
    if not isinstance(program, dict):
        return False
    check = check if check is not None else outcome.program_check
    if check is None:
        if outcome.decided_by == "model":
            _program_gaps.add(key)
        return False
    if program_id is not None and _program_id(plan) != program_id:
        logger.info("Not counting record %s's program check: the program was replaced while it ran", record_id)
        return False
    branch, agreed = check
    trusted_before = programs.program_trusted_for(program, branch)
    programs.note_program_check(program, branch, agreed, spot=bool(getattr(outcome, "program_spot", False)))
    if agreed:
        if not trusted_before and programs.program_trusted_for(program, branch):
            _emit(on_event, {"type": "note", "record_id": record_id, "message": (
                f"The decision program agreed with the model {programs.TRUST_NEEDED} times on this kind of record ({branch}): "
                "it now decides this kind alone, with no model call."
            )})
        return True
    program["disagreed"] = int(program.get("disagreed", 0) or 0) + 1
    if program["disagreed"] >= _PROGRAM_MAX_DISAGREE:
        plan.pop("program", None)
        _program_tried[key] = max(_program_tried.get(key, 0), len(_examples.get(key, [])))  # relearned only from more examples
        _emit(on_event, {"type": "note", "record_id": record_id, "message": (
            f"The decision program disagreed with the model {program['disagreed']} times (last on {record_id}, {branch}), so it is dropped; "
            "the model decides again, and a program may be learned again from more examples."
        )})
    else:
        _emit(on_event, {"type": "note", "record_id": record_id, "message": (
            f"The decision program disagreed with the model on {record_id} ({branch}); the model keeps deciding this kind of record."
        )})
    return True


# Learns, from one record's outcome, which slots (values copied from pages) code can read: a proposed label becomes the slot's
# code (when it has none), an agreement of the code with the model counts, a disagreement deletes the code. Takes the plan
# (changed in place), the outcome, the record id and the event callback; returns True when the plan changed.
def _learn_slot_code(plan: dict[str, Any], outcome, record_id: str, on_event: OnEvent) -> bool:
    if not _on("slot_code"):
        return False
    slots = plan.get("slots") or {}
    changed = False
    for name, agreed in (outcome.slot_checks or {}).items():
        spec = slots.get(name)
        if not isinstance(spec, dict) or not isinstance(spec.get("code"), dict):
            continue
        changed = True
        if not agreed:
            del spec["code"]
            continue
        spec["code"]["agree"] = int(spec["code"].get("agree", 0) or 0) + 1
        if spec["code"]["agree"] == _SLOT_CODE_AGREE:
            _emit(on_event, {"type": "note", "record_id": record_id, "message": (
                f"The value {name} is now read by code (next to \"{spec['code']['label']}\"): it agreed with the model {_SLOT_CODE_AGREE} times."
            )})
    for name, proposal in (outcome.slot_labels or {}).items():
        spec = slots.get(name)
        if not isinstance(spec, dict) or spec.get("code") or not proposal or not proposal[0]:
            continue
        spec["code"] = {"label": proposal[0], "shape": proposal[1], "agree": 0}
        lines = (getattr(outcome, "slot_pages", None) or {}).get(name)
        if lines:
            spec["code"]["page_lines"] = list(lines)  # the page's labels: a reading that is one of them is never used
        changed = True
    return changed


# The form's controls as a decision program is told about them: the plan's own (key, tag, type, label, options) plus any
# field the records saw that the plan does not list. Takes the plan and the fields' kinds; returns the controls.
def _program_controls(plan: dict[str, Any], kinds: dict[str, str]) -> list[dict[str, Any]]:
    controls = []
    for c in plan.get("commit", {}).get("controls", []):
        if c.get("type") == "widget":
            continue
        controls.append({"key": c["key"], "tag": c.get("node") or "input", "type": c.get("type") or "", "label": c.get("label") or c["key"],
                         "visible": True, "options": list(c.get("options") or [])})
    known = {c["key"] for c in controls}
    return controls + [{"key": k, "kind": kind, "tag": "input", "type": "", "label": k, "visible": True, "options": []} for k, kind in kinds.items() if k not in known]


# The page types a plan knows: its start's and those its steps land on, in route order.
def _known_types(plan: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys([url_shape(plan.get("start") or "")] + [o["expect"] for o in plan.get("gather", []) if o.get("expect")]))


# Asks the model for a decision program when there are enough verified examples and the plan has none, or the model decided a
# kind of record the program does not cover; never while another record of the same workflow variant is asking. The program is
# kept as plan["program"] and saved. Takes the plan, the RunContext, the variant, the event callback and the record id; returns
# the model calls it made (0 when it did not ask).
async def _maybe_learn_program(plan: dict[str, Any], ctx: RunContext, path_variant: str, on_event: OnEvent, record_id: str = "") -> int:
    if not _on("programs"):
        return 0
    key = (ctx.workflow_name, path_variant)
    examples = list(_examples.get(key, []))
    if not isinstance(plan, dict) or plan.get("kind") != "plan" or len(examples) < _PROGRAM_EXAMPLES:
        return 0
    if isinstance(plan.get("program"), dict) and key not in _program_gaps:
        return 0
    tried = _program_tried.get(key)
    if tried is not None and len(examples) < tried + _PROGRAM_MORE:
        return 0
    if _program_asks.get(key, 0) >= _PROGRAM_MAX_ASKS:
        return 0
    lock = _get_program_lock(key)
    if lock.locked():
        return 0  # another record of this workflow is learning one now
    async with lock:
        _program_tried[key] = len(examples)
        _program_asks[key] = _program_asks.get(key, 0) + 1
        kinds = dict(_example_kinds.get(key, {}))
        try:
            program, why, calls = await programs.learn_program(ctx, _program_controls(plan, kinds), examples, kinds, _known_types(plan))
        except LlmHalt as halt:
            # a quota stop (or a fatal error) stops the batch, as it does anywhere else; this record still finishes
            logger.warning("Could not ask for a decision program: %s", halt)
            _note_if_quota_exhausted(_halted_result(record_id, halt), on_event)
            return 0
        except Exception:  # noqa: BLE001 - the model keeps deciding, as before
            logger.exception("Learning a decision program failed")
            return 1
        target = _current_plan(key, plan)
        if program is None:
            _emit(on_event, {"type": "note", "record_id": record_id or None, "message": f"Could not learn a decision program ({why}); the model keeps deciding."})
            return calls
        relearned = isinstance(target.get("program"), dict)
        if relearned:
            # rules the new program keeps unchanged keep what they earned (trust per kind of record is not thrown away)
            carried = programs.carry_trust(target["program"], program)
            if carried:
                logger.info("The relearned decision program keeps the trust of %s", carried)
        program["id"] = uuid.uuid4().hex  # checks made by an older program are never counted toward this one
        target["program"] = program
        _program_gaps.discard(key)
        recipe_store.save_recipe(ctx.workflow_name, path_variant, target)
        _emit(on_event, {"type": "note", "record_id": record_id or None, "message": (
            f"{'Learned a new' if relearned else 'Learned a'} decision program: {len(program.get('facts', []))} facts, {len(program.get('rules', []))} rules. "
            f"It decides alone a kind of record once it has agreed with the model {programs.TRUST_NEEDED} times on that kind."
        )})
        return calls


# Asks the coverage question for a plan marked coverage_pending (newly learned or extended): which pages the instructions need
# that its route does not open; side trips to them are added to the route. The flag is cleared and the plan saved. Takes the
# plan, the RunContext, the variant, the event callback and the record id; returns the model calls made (0 or 1).
async def _cover_plan(plan: dict[str, Any], ctx: RunContext, path_variant: str, on_event: OnEvent, record_id: str = "") -> int:
    key = (ctx.workflow_name, path_variant)
    route_pages: list[tuple[str, str]] = []
    trace = _plan_traces.get(key)
    if trace:
        route_pages = coverage.route_pages_from_trace(plan, trace)
    if not route_pages:
        try:
            for _rid, _fields, saved in recipe_store.get_traces(ctx.workflow_name, path_variant):
                route_pages = coverage.route_pages_from_trace(plan, saved)
                if route_pages:
                    break
        except Exception:  # noqa: BLE001
            logger.exception("Could not read the saved traces for the coverage question")
    asked = 1 if (ctx.task_instructions or "").strip() and route_pages else 0
    try:
        added = await coverage.cover_route(ctx, plan, route_pages)
    except LlmHalt as halt:
        logger.warning("The coverage question could not be asked: %s", halt)
        _note_if_quota_exhausted(_halted_result(record_id, halt), on_event)  # a quota stop stops the batch here too
        return 0  # the flag stays: asked again on the next proof record
    plan.pop("coverage_pending", None)
    if added:
        coverage.reset_pruning(plan)
        _emit(on_event, {"type": "note", "record_id": record_id or None, "message": "Added from the instructions: " + "; ".join(added) + "."})
    recipe_store.save_recipe(ctx.workflow_name, path_variant, plan)
    return asked


# Learns whether the site shows what was saved on the record's own page: after the live agent saved a proof record, the
# dry run's record address is opened in a new tab and the live agent's values are looked for. The verdict is kept once
# _READBACK_CHECKS checks in a row agree (plan["readback"] = {"shows", "checked", "results", "hidden"}). A value missing on
# the page in a check was really saved (the live agent saved it), so the site does not show it: it is kept in "hidden" and
# plan records do not count it as missing. Takes the plan (changed in place), the dry run's outcome, the live trace and the
# open page; returns True when the plan changed.
async def _learn_readback(plan: dict[str, Any], dry, trace: list[dict[str, Any]], page) -> bool:
    if not _on("readback"):
        return False
    state = plan.get("readback") if isinstance(plan.get("readback"), dict) else {}
    if state.get("shows") is not None and int(state.get("checked", 0)) >= _READBACK_CHECKS:
        return False  # decided already
    if dry is None or not dry.record_url or not dry.kinds:
        return False
    values = committed_values(trace, set(dry.kinds))
    if not values:
        return False
    tab = await page.context.new_page()
    try:
        report = await readback.read_back(tab, dry.record_url, values, dry.kinds, _quick_read)
    except Exception:  # noqa: BLE001 - a read-back that cannot be done teaches nothing
        logger.exception("Could not read the record's page back")
        return False
    finally:
        try:
            await tab.close()
        except Exception:  # noqa: BLE001
            pass
    if report.get("error"):
        logger.info("Read-back check skipped: %s", report["error"])
        return False
    results = [*list(state.get("results") or []), readback.site_shows_decisions(report)][-_READBACK_CHECKS:]
    shows = results[0] if len(results) >= _READBACK_CHECKS and len(set(results)) == 1 else None
    hidden = sorted(set(state.get("hidden") or []) | set(report.get("missing") or []))
    plan["readback"] = {"shows": shows, "checked": int(state.get("checked", 0)) + 1, "results": results, "hidden": hidden,
                        "confirmed": list(state.get("confirmed") or [])}
    _confirm_readback(plan, report, values, dry.kinds)
    return True


# What read-back has seen confirmed on this site: a value of a choice (a select, a radio group) counts per option ("status=
# Place on hold"), as a site may print one option the way it is offered and another differently ("On hold"); any other value
# per field. Takes the field's key, its value and its kind; returns the mark.
def _readback_mark(key: str, value: Any, kind: str | None) -> str:
    return f"{key}={_norm_text(value).lower()}" if kind in ("select", "checks") else key


# Notes the values a read-back saw on the record's page (plan["readback"]["confirmed"]). Takes the plan (changed in place),
# the report, the values and their kinds.
def _confirm_readback(plan: dict[str, Any], report: dict[str, Any], values: dict[str, Any], kinds: dict[str, str]) -> bool:
    state = plan.get("readback")
    if not isinstance(state, dict):
        return False
    confirmed = state.setdefault("confirmed", [])
    added = False
    for key in report.get("seen") or []:
        mark = _readback_mark(key, values.get(key), kinds.get(key))
        if key in values and mark not in confirmed:
            confirmed.append(mark)
            added = True
    return added


# True when read-back has seen this value (a choice: this option) shown on the record's page before: only then does its absence
# mean the save did not take. Takes the plan, the key, the value and its kind.
def _readback_confirmed(plan: dict[str, Any], key: str, value: Any, kind: str | None) -> bool:
    state = plan.get("readback") if isinstance(plan.get("readback"), dict) else {}
    return _readback_mark(key, value, kind) in (state.get("confirmed") or [])


# Everything learned from one decided record of a plan: slots read by code, the program's check, which pages supplied facts
# (pruning) and, for a model decision that saved and was not doubted, a verified example. Takes the plan (changed in place),
# the outcome, the key, the record id and the event callback, the program check to count (None: none, see _check_to_book_now)
# and the id of the program the record ran; saves the plan when it changed.
def _learn_from_outcome(plan: dict[str, Any], outcome, key: tuple[str, str], record_id: str, on_event: OnEvent, example_ok: bool,
                        program_check: tuple[str, bool] | None = None, program_id: str | None = None) -> None:
    changed = _learn_slot_code(plan, outcome, record_id, on_event)
    if program_check is not None:
        changed = _book_program(plan, outcome, key, record_id, on_event, check=program_check, program_id=program_id) or changed
    elif outcome.program_check is None:
        changed = _book_program(plan, outcome, key, record_id, on_event) or changed  # marks a gap when the model decided alone
    if outcome.ok and outcome.decided_by:
        coverage.note_page_use(plan, outcome.used_pages or set())
        changed = True
    if example_ok and outcome.decided_by == "model":
        _add_example(key, record_id, outcome.example, outcome.kinds)
    if changed:
        recipe_store.save_recipe(key[0], key[1], plan)


# The RunResult of a record stopped by a quota stop or a fatal error (LlmHalt). Takes the record id and the halt.
def _halted_result(record_id: str, halt: LlmHalt) -> RunResult:
    return RunResult(
        record_id=record_id,
        success=False,
        error_message=str(halt),
        timestamp=datetime.now(timezone.utc),
        quota_exhausted=halt.quota,
        fatal_error=None if halt.quota else str(halt),
    )


# Stops using a plan that failed _RECIPE_MAX_FAILURES records in a row: the rest of the batch (and later runs) use the live
# agent. Takes the key, the RunContext's workflow name, the variant and the event callback.
def _give_up_plan(key: tuple[str, str], workflow_name: str, path_variant: str, on_event: OnEvent) -> None:
    _recipes.pop(key, None)
    _recipe_given_up.add(key)
    recipe_store.save_recipe(workflow_name, path_variant, {"kind": "live_only", "reason": f"the plan failed {_RECIPE_MAX_FAILURES} records in a row"})
    _emit(on_event, {"type": "note", "message": f"The plan failed {_RECIPE_MAX_FAILURES} records in a row, so the rest use the live agent."})


# The program check a record may count before the live agent has confirmed the model's decision: a disagreement always
# counts (it only makes the program less trusted), an agreement only when the plan saved the record (the model's decision
# stood and its read-back did not doubt it). Takes the outcome and whether its save is unverified; returns the check to
# book now, or None.
def _check_to_book_now(outcome, unverified: bool) -> tuple[str, bool] | None:
    check = outcome.program_check
    if check is None:
        return None
    if not check[1] or (outcome.ok and not unverified):
        return check
    return None


# Runs one record with a trusted plan; when the plan cannot do it and nothing was saved yet, the live AI does it instead.
# Takes the record, plan, RunContext, the open page, CDP url, store, variant and event callback; returns (RunResult, metrics).
async def _run_plan_with_fallback(
    record: Record,
    plan: dict[str, Any],
    *,
    ctx: RunContext,
    page,
    cdp_url: str,
    store: CacheStore,
    path_variant: str,
    on_event: OnEvent = None,
    mutations=None,
) -> tuple[RunResult, dict[str, Any]]:
    metrics: dict[str, Any] = {"mode": "plan", "cache_hits": 0, "cache_misses": 0, "llm_steps": 0, "miss_reasons": []}
    program_id = _program_id(plan)  # the program this record runs (run_plan reads it when it starts)
    program = plan.get("program")
    ran: dict[str, Any] = {}
    try:
        return await _run_plan_record(record, plan, ctx=ctx, page=page, cdp_url=cdp_url, store=store, path_variant=path_variant,
                                      on_event=on_event, program_id=program_id, metrics=metrics, ran=ran, mutations=mutations)
    finally:
        if isinstance(program, dict) and getattr(ran.get("outcome"), "program_spot", False):
            # a spot check this record claimed but never booked (it failed, or its save was not confirmed): the next trusted
            # record is the spot check instead
            programs.reopen_spot_check(program)


# The body of _run_plan_with_fallback (which hands back a spot check the record claimed and did not book). Takes the same, plus
# the id of the program the record runs, the metrics to fill and a dict that gets the plan's outcome ("outcome"); returns
# (RunResult, metrics).
async def _run_plan_record(
    record: Record,
    plan: dict[str, Any],
    *,
    ctx: RunContext,
    page,
    cdp_url: str,
    store: CacheStore,
    path_variant: str,
    on_event: OnEvent,
    program_id: str | None,
    metrics: dict[str, Any],
    ran: dict[str, Any],
    mutations=None,
) -> tuple[RunResult, dict[str, Any]]:
    key = (ctx.workflow_name, path_variant)

    # Tags the plan's events with this record before they go out.
    def emit(event: dict[str, Any]) -> None:
        _emit(on_event, {**event, "record_id": record.record_id})

    try:
        batchers = _plan_batchers.get(key)
        if batchers is None:
            batchers = _plan_batchers[key] = PlanBatchers(ctx)
        # side trips to pages that have never supplied a fact are left out (pruning)
        skip = coverage.skippable(plan) if _on("pruning") else set()
        outcome = await run_plan(
            plan, record.field_dict(), ctx=ctx, page=page, emit=emit, key=record.record_id, batchers=batchers,
            widget_fill=_widget_filler(record, ctx, cdp_url), trusted_branches=_trusted_branches(plan),
            program_mode="use" if _on("programs") else "off", skip=skip or None, mutations=mutations,
        )
        if skip and not outcome.ok and not outcome.acted and _lacks_pages(outcome.error):
            # a page the pruning left out was needed after all: count again from nothing and run the whole route once more
            coverage.reset_pruning(plan)
            recipe_store.save_recipe(ctx.workflow_name, path_variant, plan)
            _emit(on_event, {"type": "note", "record_id": record.record_id, "message": (
                f"Record {record.record_id} needed a page the plan had stopped reading ({outcome.error}); the plan reads its whole route again."
            )})
            first = outcome
            if first.program_spot and isinstance(plan.get("program"), dict):
                programs.reopen_spot_check(plan["program"])  # its spot check was never booked: the next trusted record takes it
            try:
                await page.goto(ctx.target_url)
            except Exception:
                logger.exception("Could not reopen the start page to run the plan again")
            outcome = await run_plan(
                plan, record.field_dict(), ctx=ctx, page=page, emit=emit, key=record.record_id, batchers=batchers,
                widget_fill=_widget_filler(record, ctx, cdp_url), trusted_branches=_trusted_branches(plan),
                program_mode="use" if _on("programs") else "off", mutations=mutations,
            )
            outcome.llm_calls += first.llm_calls
            outcome.steps += first.steps
    except LlmHalt as halt:
        halted = _halted_result(record.record_id, halt)
        _note_if_quota_exhausted(halted, on_event)
        return halted, metrics

    ran["outcome"] = outcome
    metrics["cache_hits"] = outcome.steps
    metrics["llm_steps"] = outcome.llm_calls
    # values the proof records showed the site never displays on the record's page (plan["readback"]["hidden"]) cannot be read
    # back: their absence does not make a save unverified
    report = outcome.readback or {}
    hidden = set((plan.get("readback") or {}).get("hidden") or [])
    # a value (a choice: an option) read-back has never seen confirmed on this site may be shown in other words ("Place on
    # hold" as "On hold"): its absence proves nothing, so it is unchecked, not missing
    missing = [k for k in report.get("missing", []) if k not in hidden and _readback_confirmed(plan, k, outcome.planned.get(k), outcome.kinds.get(k))]
    if report.get("seen") and _confirm_readback(plan, report, outcome.planned, outcome.kinds):
        recipe_store.save_recipe(ctx.workflow_name, path_variant, plan)
    unverified = bool(outcome.unverified) and not (report.get("missing") and not missing and not report.get("error"))
    booked_now = _check_to_book_now(outcome, unverified)
    try:
        _learn_from_outcome(plan, outcome, key, record.record_id, on_event, example_ok=outcome.ok and not unverified,
                            program_check=booked_now, program_id=program_id)
    except Exception:  # noqa: BLE001 - bookkeeping must never fail the record
        logger.exception("Could not learn from the plan's outcome on record %s", record.record_id)
    if outcome.ok and outcome.decided_by == "program" and not outcome.llm_calls:
        _batch_counts["program"] += 1  # decided with no model call at all (a widget the agent set made some)
    if outcome.ok and unverified:
        # The plan saved, but the record's page does not show what was saved: a failed record, never retried (it was saved)
        if missing:
            shown = ", ".join(f"{k}={outcome.planned.get(k)!r}" for k in missing)
            error = f"The plan saved, but the record's page does not show {shown} (read-back)"
        else:
            error = f"The plan saved, but the record's page could not be read back ({report.get('error') or 'unknown'})"
        metrics["cache_misses"] = 1
        metrics["miss_reasons"] = [error]
        _recipe_failures[key] = _recipe_failures.get(key, 0) + 1
        _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": "read-back", "reason": error})
        if _recipe_failures[key] >= _RECIPE_MAX_FAILURES and _recipes.get(key) is plan:
            _give_up_plan(key, ctx.workflow_name, path_variant, on_event)  # saves that never show up must not go on all batch
        return RunResult(record_id=record.record_id, success=False, error_message=error, timestamp=datetime.now(timezone.utc), summary=outcome.summary), metrics
    if outcome.ok:
        _recipe_failures[key] = 0
        metrics["llm_steps"] += await _maybe_learn_program(plan, ctx, path_variant, on_event, record.record_id)
        return RunResult(record_id=record.record_id, success=True, timestamp=datetime.now(timezone.utc), summary=outcome.summary), metrics

    metrics["cache_misses"] = 1
    metrics["miss_reasons"] = [outcome.error or "unknown"]
    _emit(on_event, {"type": "field_miss", "record_id": record.record_id, "field": "plan", "reason": outcome.error or "unknown"})
    # A refusal because this record's pages lack what a rule needs is the plan working as meant (a branch it has not learned yet:
    # the live agent does the record and its route extends the plan); only a plan that breaks counts toward dropping it
    if not _lacks_pages(outcome.error) and not _unchecked_branch(outcome.error):
        _recipe_failures[key] = _recipe_failures.get(key, 0) + 1
    if _recipe_failures.get(key, 0) >= _RECIPE_MAX_FAILURES:
        _give_up_plan(key, ctx.workflow_name, path_variant, on_event)

    if outcome.acted:
        # A saving button was already pressed, so doing it again could act twice
        return (
            RunResult(
                record_id=record.record_id,
                success=False,
                error_message=f"The plan pressed save but could not confirm it worked: {outcome.error}",
                timestamp=datetime.now(timezone.utc),
                summary=outcome.summary,
            ),
            metrics,
        )

    try:
        await page.goto(ctx.target_url)
    except Exception:
        logger.exception("Could not reopen the start page for the live fallback")
    run_result, live_metrics = await _run_cold(
        record, ctx=ctx, cdp_url=cdp_url, store=store, path_variant=path_variant, persist=False, on_event=on_event
    )
    trace = live_metrics.pop("trace", None)
    live_metrics.pop("unmatched_inputs", None)
    learn_calls = 0
    if trace and run_result.success:
        live_agreed: bool | None = None
        if _unchecked_branch(outcome.error) and outcome.branch:
            live_agreed = _check_branch(plan, outcome, trace, record.record_id, ctx.workflow_name, path_variant, on_event)
            if live_agreed:
                # the plan's values agreed with the live agent's: a verified example for learning a decision program
                _add_example(key, record.record_id, outcome.example, outcome.kinds, trace)
        if outcome.program_check is not None and outcome.program_check[1] and outcome.planned:
            # the program agreed with the model, whose decision the plan never saved: it counts only if the live agent agrees
            if live_agreed is None:
                live_agreed = values_agree(outcome.planned, committed_values(trace, set(outcome.kinds)), outcome.kinds)[0]
            try:
                _book_program(plan, outcome, key, record.record_id, on_event, check=(outcome.program_check[0], live_agreed), program_id=program_id)
                recipe_store.save_recipe(ctx.workflow_name, path_variant, plan)
            except Exception:  # noqa: BLE001 - bookkeeping must never fail the record
                logger.exception("Could not book the program's check on record %s", record.record_id)
        _extend_plan(plan, record, trace, ctx.workflow_name, path_variant, on_event, instructions=ctx.task_instructions or "")
        learn_calls = await _maybe_learn_program(_current_plan(key, plan), ctx, path_variant, on_event, record.record_id)
    live_metrics["mode"] = "plan+live"
    live_metrics["llm_steps"] += outcome.llm_calls + learn_calls
    live_metrics["cache_hits"] = outcome.steps
    live_metrics["cache_misses"] = 1
    live_metrics["miss_reasons"] = [outcome.error or "unknown"]
    return run_result, live_metrics


# The page types a plan visits: where each of its steps lands, and where its added side trips led when it ran.
def _plan_pages(plan: dict[str, Any]) -> set[str]:
    reach = set(t for t in plan.get("added_types") or [] if isinstance(t, str))  # where the side trips added from the instructions led
    for o in plan["gather"]:
        if not o.get("expect"):
            continue
        # a click that stays on its page shows something there (a tab): it counts as reached too, by what it is called
        reach.add(o["expect"] + " > " + spec_text((o.get("by") or [{}])[-1]) if o.get("stays") else o["expect"])
    return reach


# A record the plan could not do was done by the live agent. When the agent's route went through page types the plan never
# visits (a branch the first record did not take), the plan is replaced by one learned from that richer route, and it has to be
# proven again before it is trusted. Takes the plan in use, the record, the live agent's trace, and the usual context.
# Returns True when the plan was replaced by the extended one. save_trace is False when the trace was saved already (a proof record).
# instructions are the task's (values written in them become constants). The extended plan keeps what was learned about the
# site and the decisions (its decision program, the read-back verdict) and is marked for the coverage question.
def _extend_plan(plan: dict[str, Any], record: Record, trace: list[dict[str, Any]], workflow_name: str, path_variant: str, on_event: OnEvent, save_trace: bool = True,
                 instructions: str = "") -> bool:
    key = (workflow_name, path_variant)
    try:
        if save_trace:
            recipe_store.save_trace(workflow_name, path_variant, record.record_id, record.field_dict(), trace)
        new_plan, why = compile_plan(record.field_dict(), trace, instructions)
    except Exception:
        logger.exception("Could not learn from the live agent's route for record %s", record.record_id)
        return False
    if new_plan is None:
        return False
    extra = _plan_pages(new_plan) - _plan_pages(plan)
    if _recipes.get(key) is not plan:
        return False
    if not extra:
        added = learn_fields(plan, record.field_dict(), trace, instructions)  # same pages, but maybe form fields of another branch
        if added:
            recipe_store.save_recipe(workflow_name, path_variant, plan)
            logger.info("The plan learned form field(s) %s from record %s's live route", added, record.record_id)
        return False
    lost = _plan_pages(plan) - _plan_pages(new_plan)
    if lost:
        # the new route skipped something the plan reads: replacing the plan would break the records that need that
        logger.info("Not extending the plan from record %s: its route reaches %s but skips %s", record.record_id, sorted(extra), sorted(lost))
        return False
    for kept in ("program", "readback"):
        if isinstance(plan.get(kept), dict):
            new_plan[kept] = plan[kept]
    new_plan["coverage_pending"] = True
    _plan_traces[key] = trace
    recipe_store.save_recipe(workflow_name, path_variant, new_plan)
    _recipes[key] = new_plan
    _recipe_failures[key] = 0
    _emit(on_event, {"type": "note", "message": (
        f"The plan was extended from the live agent's route on record {record.record_id}, which went through {len(extra)} page type(s) the plan "
        f"did not visit ({', '.join(sorted(extra))}). The new plan must be proven against the live agent again before it is trusted."
    ), "record_id": record.record_id})
    return True


# Checks a plan that has not earned trust yet: runs it as a dry run (everything except saving) on a second tab of the
# record's browser BEFORE the live agent touches the record, so it cannot copy an answer the agent already saved. The plan's
# decision program, if it has one, runs beside the model ("shadow"): it never decides in a dry run, but its check is reported.
# Returns the dry run's outcome, or None when it could not be done (for example the model is out of quota).
async def _dry_run_plan(plan: dict[str, Any], record: Record, ctx: RunContext, page, on_event: OnEvent):
    tab = await page.context.new_page()
    try:
        return await run_plan(plan, record.field_dict(), ctx=ctx, page=tab, emit=lambda _e: None, dry=True, program_mode="shadow" if _on("programs") else "off")
    except LlmHalt:
        return None
    except Exception:  # noqa: BLE001
        logger.exception("The dry run of the plan failed for record %s", record.record_id)
        return None
    finally:
        try:
            await tab.close()
        except Exception:
            pass


# Compares what the plan would have entered with what the live agent entered on the same record, and updates the
# plan's trust: enough agreements make it trusted, one disagreement throws it away. A record that agreed but whose live route
# read page types the plan lacks also extends the plan. instructions are the task's. Returns True when the values agreed.
def _judge_plan(plan: dict[str, Any], dry, live_trace: list[dict[str, Any]], record_id: str, workflow_name: str, path_variant: str, on_event: OnEvent, record_fields: dict[str, Any] | None = None,
                instructions: str = "") -> bool:
    key = (workflow_name, path_variant)
    trust = plan.setdefault("trust", {"ok": 0, "bad": 0, "signatures": []})
    if dry is None:
        _emit(on_event, {"type": "note", "message": f"Could not check the plan on record {record_id} (the model was not available); it stays unproven.", "record_id": record_id})
        return False
    if dry.healed:
        # The plan was corrected during this check, so what it had earned before no longer counts
        plan["trust"] = trust = {"ok": 0, "bad": 0, "signatures": []}
        _emit(on_event, {"type": "note", "message": f"The plan was corrected on record {record_id} (a fixed label was record data); its proof starts again.", "record_id": record_id})
    if dry.ok:
        committed = committed_values(live_trace, set(dry.kinds))
        # a later step's field the dry run did not reach or answer (a confirmation tick) is no difference
        later = {c["key"] for c in plan.get("commit", {}).get("controls", []) if c.get("stage", 0) > 0}
        committed = {k: v for k, v in committed.items() if k in dry.planned or k not in later}
        same, why = values_agree(dry.planned, committed, dry.kinds)
    else:
        if record_fields is not None and _lacks_pages(dry.error):
            # The plan refused because its pages lack what a rule needs (a branch the first record did not take). The live
            # agent just did this record over a route that reads more: learn from it instead of counting a failure
            try:
                extended = _extend_plan(plan, Record(record_id=record_id, **record_fields), live_trace, workflow_name, path_variant, on_event, save_trace=False, instructions=instructions)
            except Exception:  # noqa: BLE001
                logger.exception("Could not extend the plan from proof record %s", record_id)
                extended = False
            if extended:
                return False
        # A dry run that merely failed to run proves nothing either way, and the live agent does the record regardless, so a
        # few such failures cost little; only values that differ from the live agent's reject a plan at once
        trust["bad"] = int(trust.get("bad", 0)) + 1
        if trust["bad"] < 4:
            recipe_store.save_recipe(workflow_name, path_variant, plan)
            _emit(on_event, {"type": "note", "message": f"The plan could not run on record {record_id} ({dry.error}); it stays unproven.", "record_id": record_id})
            return False
        same, why = False, f"the plan could not run {trust['bad']} times: {dry.error}"
    if same:
        trust["ok"] = int(trust.get("ok", 0)) + 1
        trust.setdefault("signatures", []).append("|".join(f"{k}={v}" for k, v in sorted(dry.planned.items()) if dry.kinds.get(k) == "select"))
        if dry.branch:
            _note_branch(plan, dry.branch, True)
        added = learn_fields(plan, record_fields or {}, live_trace, instructions) if record_fields is not None else []
        recipe_store.save_recipe(workflow_name, path_variant, plan)
        extended = False
        if record_fields is not None and _on("extend_on_agree"):
            # the answer matched, but a live route that read page types the plan lacks still teaches the plan those pages
            try:
                extended = _extend_plan(plan, Record(record_id=record_id, **record_fields), live_trace, workflow_name, path_variant, on_event, save_trace=False, instructions=instructions)
            except Exception:  # noqa: BLE001
                logger.exception("Could not extend the plan from agreeing proof record %s", record_id)
        message = f"Checked the plan against the live agent on record {record_id}: the same values ({trust['ok']} agreeing)."
        if added:
            message += f" Its route also filled {', '.join(added)}, which the plan now knows too."
        if extended:
            message += " Its route also read pages the plan does not, so the plan was extended and must be proven again."
        elif not needs_proof(plan):
            message += " The plan is trusted: the remaining records use it instead of the live agent."
        _emit(on_event, {"type": "note", "message": message, "record_id": record_id})
        return True
    if record_fields is not None and dry.ok:
        # The plan answered differently. If the live agent's route read pages the plan never reads (the policy table), the plan
        # answered without what it needed: learn those pages (the proof starts again) instead of throwing the plan away
        try:
            extended = _extend_plan(plan, Record(record_id=record_id, **record_fields), live_trace, workflow_name, path_variant, on_event, save_trace=False, instructions=instructions)
        except Exception:  # noqa: BLE001
            logger.exception("Could not extend the plan from proof record %s", record_id)
            extended = False
        if extended:
            _emit(on_event, {"type": "note", "message": f"On record {record_id} the plan answered differently from the live agent ({why}), whose route read pages the plan does not; the plan was extended instead of thrown away.", "record_id": record_id})
            return False
    _recipes.pop(key, None)
    _recipe_given_up.add(key)
    recipe_store.save_recipe(workflow_name, path_variant, {"kind": "live_only", "reason": f"the plan disagreed with the live agent on record {record_id}: {why}"})
    _emit(on_event, {"type": "note", "message": f"The plan disagreed with the live agent on record {record_id} ({why}). It is thrown away; every other record uses the live agent.", "record_id": record_id})
    return False


# The program check a proof record counts, judged against the live agent rather than the model: when the dry run agreed with
# the live agent, the program's agreement with the model stands; when the dry run ran but disagreed, the program (which
# agreed with the wrong answer, or disagreed with the model) counts a disagreement; when the dry run did not run through,
# only a disagreement with the model counts. Takes the dry run's outcome (or None) and whether it agreed; returns the check
# to book, or None.
def _proof_program_check(dry, agreed: bool) -> tuple[str, bool] | None:
    if dry is None or dry.program_check is None:
        return None
    branch, with_model = dry.program_check
    if agreed:
        return (branch, with_model)
    if dry.ok or not with_model:
        return (branch, False)
    return None


# Runs a record with the live agent and, when a plan is still being proven (shadow), checks the plan on it: first the coverage
# question when the plan is new or was extended, then a dry run of the plan (its decision program beside the model) before the
# live agent touches the record, then the comparison with what the live agent saved. An agreeing record also teaches the plan
# (slots read by code, the program's check, a verified example, whether the site shows saved values) and may start learning a
# decision program. Takes the record, the plan being proven (or None) and the usual context; returns (RunResult, metrics).
async def _run_live_and_prove(
    record: Record,
    shadow: dict[str, Any] | None,
    *,
    ctx: RunContext,
    page,
    cdp_url: str,
    store: CacheStore,
    path_variant: str,
    persist: bool,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    key = (ctx.workflow_name, path_variant)
    calls = 0
    if _on("coverage") and shadow is not None and shadow.get("coverage_pending"):
        try:
            calls += await _cover_plan(shadow, ctx, path_variant, on_event, record.record_id)
        except Exception:  # noqa: BLE001 - the plan keeps its route
            logger.exception("The coverage question failed for record %s", record.record_id)
    program_id = _program_id(shadow) if shadow is not None else None  # the program the dry run runs beside the model
    dry = await _dry_run_plan(shadow, record, ctx, page, on_event) if shadow is not None else None
    if dry is not None:
        calls += dry.llm_calls
    live_result, live_metrics = await _run_cold(
        record, ctx=ctx, cdp_url=cdp_url, store=store, path_variant=path_variant, persist=persist, on_event=on_event
    )
    trace = live_metrics.get("trace")
    if shadow is not None and live_result.success and trace:
        agreed = _judge_plan(shadow, dry, trace, record.record_id, ctx.workflow_name, path_variant, on_event, record.field_dict(),
                             instructions=ctx.task_instructions or "")
        current = _recipes.get(key)
        check = _proof_program_check(dry, agreed)
        try:
            if dry is not None and current is shadow:
                # what the dry run found out belongs to the plan it ran (an extended plan has other slots and pages)
                _learn_from_outcome(shadow, dry, key, record.record_id, on_event, example_ok=False, program_check=check, program_id=program_id)
            elif check is not None and isinstance(current, dict) and current.get("kind") == "plan":
                _book_program(current, dry, key, record.record_id, on_event, check=check, program_id=program_id)  # the extended plan kept the program
            if agreed and dry is not None:
                _add_example(key, record.record_id, dry.example, dry.kinds, trace)
            if isinstance(current, dict) and current.get("kind") == "plan":
                if await _learn_readback(current, dry, trace, page):
                    recipe_store.save_recipe(ctx.workflow_name, path_variant, current)
                calls += await _maybe_learn_program(current, ctx, path_variant, on_event, record.record_id)
        except Exception:  # noqa: BLE001 - learning must never fail a record the live agent did
            logger.exception("Could not learn from proof record %s", record.record_id)
    live_metrics["llm_steps"] = live_metrics.get("llm_steps", 0) + calls
    return live_result, live_metrics


# Opens the start page for a record, trying again once if the browser was sent somewhere else meanwhile
# (for example by a stray keystroke into a visible window) or the page was slow.
# Takes the Playwright page and the address; raises the error if the second try fails too.
async def _open_start_page(page, url: str) -> None:
    try:
        await page.goto(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Opening %s failed (%s); trying once more", url, str(exc)[:120])
        await asyncio.sleep(1.0)
        await page.goto(url)


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


# Keeps learned steps only if replaying them could make sense for another record.
# Takes the cached steps and returns them unchanged when at least one is tied to a field of the data
# (typing or choosing the record's value); returns [] when they are all plain clicks, because replaying
# those would repeat the first record's clicks for every record (for example the same Approve button).
def _replayable_steps(cached_steps: list[CachedStep]) -> list[CachedStep]:
    return cached_steps if any(s.field_name for s in cached_steps) else []


# Runs the whole record live and, when `persist` is set, records the steps to populate the cache.
# Takes a Record, the RunContext, the CDP url, store and variant; returns (RunResult, metrics dict).
async def _run_cold(
    record: Record,
    *,
    ctx: RunContext,
    cdp_url: str,
    store: CacheStore,
    path_variant: str,
    persist: bool = True,
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
                error_message=_skipped_message(),
                timestamp=datetime.now(timezone.utc),
                quota_exhausted=_halt_reason["text"] is None,
            ),
            metrics,
        )

    recorder = StepRecorder(
        store,
        workflow_name=ctx.workflow_name,
        path_variant=path_variant,
        record_fields=record.field_dict(),
        auto_persist=persist,
        record_id=record.record_id,
        on_event=on_event,
    )

    tracer = TraceRecorder()

    # Gives each recorder the step so the cache and the recipe learner both see what the AI does.
    def _both(browser_state_summary, agent_output, step_num: int) -> None:
        recorder.callback(browser_state_summary, agent_output, step_num)
        tracer.callback(browser_state_summary, agent_output, step_num)

    run_result = await _run_single_record_async(
        record,
        ctx=ctx,
        use_vision=False,
        browser_profile=BrowserProfile(cdp_url=cdp_url),
        register_new_step_callback=_both,
    )
    _note_if_quota_exhausted(run_result, on_event)

    # Retry once with vision if text-only failed (no sleep: it would hold the seed lock).
    if not run_result.success and not run_result.quota_exhausted and not _quota_exhausted():
        logger.warning(
            "Cold run for record %s didn't succeed on the text-only pass - retrying once with vision enabled.",
            record.record_id,
        )
        tracer = TraceRecorder()  # only the retry's actions describe the successful path
        recorder.unmatched = 0
        run_result = await _run_single_record_async(
            record,
            ctx=ctx,
            use_vision=True,
            browser_profile=BrowserProfile(cdp_url=cdp_url),
            register_new_step_callback=_both,
        )
        _note_if_quota_exhausted(run_result, on_event)

    metrics["llm_steps"] = recorder.steps_seen
    metrics["trace"] = tracer.entries
    metrics["unmatched_inputs"] = recorder.unmatched
    return run_result, metrics


# Runs one live agent call for a single uncached step and self-heals the cache entry.
# Takes a CachedStep, a value, the RunContext, the CDP url and store; returns (succeeded, llm_steps).
async def _run_live_fallback_step(
    step: CachedStep,
    value: Any,
    *,
    ctx: RunContext,
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
            ctx=ctx,
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
                ctx=ctx,
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
# Takes a Record, its cached steps, the RunContext, page, CDP url and store; returns (RunResult, metrics dict).
async def _run_replay(
    record: Record,
    cached_steps: list[CachedStep],
    *,
    ctx: RunContext,
    page,
    cdp_url: str,
    store: CacheStore,
    on_event: OnEvent = None,
) -> tuple[RunResult, dict[str, Any]]:
    engine = ReplayEngine(page, timeout_ms=ctx.replay_element_timeout_ms)
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
            step, value, ctx=ctx, cdp_url=cdp_url, store=store, record_id=record.record_id, on_event=on_event
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
                synthetic_step, fields[field_name], ctx=ctx, cdp_url=cdp_url, store=store, record_id=record.record_id, on_event=on_event
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
# Takes a Record, the RunContext, store and port offset; returns (RunResult, metrics dict).
async def _run_record_cached_async(
    record: Record,
    *,
    ctx: RunContext,
    store: CacheStore,
    port_offset: int = 0,
    index: int = 0,
    total: int = 1,
    on_event: OnEvent = None,
    abort_check: "AbortCheck" = None,
) -> tuple[RunResult, dict[str, Any]]:
    workflow_name = ctx.workflow_name
    path_variant = resolve_path_variant(record)
    started_at = datetime.now(timezone.utc)
    port = ctx.cdp_base_port + port_offset

    # With caching on, one record cold-seeds a workflow+variant and the rest replay;
    # with it off, every record runs live and nothing is learned.
    must_seed = False
    if ctx.use_cache:
        must_seed = await _claim_seed_responsibility(workflow_name, path_variant, store)
    cached_steps = []
    if ctx.use_cache and not must_seed:
        cached_steps = _replayable_steps(store.get_steps(workflow_name, path_variant))
    go_live = must_seed or not cached_steps
    run_mode = ("cold" if must_seed else "live") if go_live else "replay"

    # Tasks where the AI has to read and decide cannot replay selectors; they use a recipe learned from a few AI records
    recipe: dict[str, Any] | None = None
    learning = False
    shadow: dict[str, Any] | None = None  # a plan that is still being checked against the live agent on this record
    if ctx.use_cache and go_live and not must_seed:
        recipe, learning = await _claim_recipe(workflow_name, path_variant, on_event, ctx.task_instructions or "")
        if recipe is not None:
            go_live = False
            run_mode = "plan" if recipe.get("kind") == "plan" else "recipe"
        elif learning:
            candidate = _recipes.get((workflow_name, path_variant))
            if candidate is not None and needs_proof(candidate):
                shadow = candidate
    # A record may have waited a long time for the learning or seeding lock: an abort asked for meanwhile stops it here
    if abort_check is not None and abort_check():
        if learning:
            _release_learn(workflow_name, path_variant)
        if must_seed:
            _release_seed_responsibility(workflow_name, path_variant)
        skipped = RunResult(record_id=record.record_id, success=False, error_message="aborted before starting", timestamp=datetime.now(timezone.utc))
        return skipped, {"mode": run_mode, "cache_hits": 0, "cache_misses": 0, "llm_steps": 0, "miss_reasons": []}

    display_name = str(record.field_dict().get(ctx.match_key_field) or record.record_id)
    _emit(
        on_event,
        {
            "type": "record_start",
            "record_id": record.record_id,
            "index": index,
            "total": total,
            "name": display_name,
            "mode": run_mode,
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
        "mode": run_mode,
        "cache_hits": 0,
        "cache_misses": 0,
        "llm_steps": 0,
        "miss_reasons": [],
    }

    try:
        try:
            with tempfile.TemporaryDirectory(prefix=f"terminautomator_v3_profile_{port_offset}_") as profile_dir:
                async with async_playwright() as playwright:
                    context = await playwright.chromium.launch_persistent_context(
                        profile_dir,
                        headless=ctx.headless,
                        args=[f"--remote-debugging-port={port}"],
                    )
                    try:
                        tracker = MutationTracker(context)
                        page = context.pages[0] if context.pages else await context.new_page()
                        await _open_start_page(page, ctx.target_url)
                        cdp_url = f"http://127.0.0.1:{port}"

                        # Skip the response wait when quota is exhausted: no request would be made.
                        skip_due_to_quota = (go_live or recipe is not None) and _quota_exhausted()
                        if skip_due_to_quota:
                            run_result = RunResult(
                                record_id=record.record_id,
                                success=False,
                                error_message=_skipped_message(),
                                timestamp=datetime.now(timezone.utc),
                                quota_exhausted=_halt_reason["text"] is None,
                            )
                            await page.wait_for_timeout(300)
                        else:
                            # Runs the record live or from the cache; returns (RunResult, metrics).
                            async def _do_run() -> tuple[RunResult, dict[str, Any]]:
                                if go_live:
                                    return await _run_live_and_prove(
                                        record,
                                        shadow,
                                        ctx=ctx,
                                        page=page,
                                        cdp_url=cdp_url,
                                        store=store,
                                        path_variant=path_variant,
                                        persist=must_seed,
                                        on_event=on_event,
                                    )
                                if recipe is not None and recipe.get("kind") == "plan":
                                    return await _run_plan_with_fallback(
                                        record,
                                        recipe,
                                        ctx=ctx,
                                        page=page,
                                        cdp_url=cdp_url,
                                        store=store,
                                        path_variant=path_variant,
                                        on_event=on_event,
                                        mutations=lambda: tracker.sent,
                                    )
                                if recipe is not None:
                                    return await _run_recipe_with_fallback(
                                        record,
                                        recipe,
                                        ctx=ctx,
                                        page=page,
                                        cdp_url=cdp_url,
                                        store=store,
                                        path_variant=path_variant,
                                        on_event=on_event,
                                    )
                                return await _run_replay(
                                    record, cached_steps, ctx=ctx, page=page, cdp_url=cdp_url, store=store, on_event=on_event
                                )

                            run_result, metrics = await _do_run()
                            # Do not close the browser while a save is still on its way, and do not call the record done
                            # when its last save was refused.
                            await tracker.settle()
                            failure = tracker.last_failure()
                            if run_result.success and failure:
                                run_result = RunResult(
                                    record_id=record.record_id,
                                    success=False,
                                    error_message=f"The site refused the save: {failure}",
                                    timestamp=datetime.now(timezone.utc),
                                    summary=run_result.summary,
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
        # Keep what the AI did, so a recipe can be learned from successful live records; this happens
        # before the locks are released so the next record sees the new recipe.
        try:
            trace = metrics.pop("trace", None)
            unmatched_inputs = metrics.pop("unmatched_inputs", 0)
            if trace and run_result.success and ctx.use_cache and recipe is None:
                recipe_store.save_trace(workflow_name, path_variant, record.record_id, record.field_dict(), trace)
                if must_seed:
                    # The clicks this record made were just saved for replay. Replaying them is only right when nothing in
                    # them belongs to this record; otherwise every other record would repeat this one's clicks.
                    safe, why = replay_verdict(record.field_dict(), trace, unmatched_inputs)
                    if not safe:
                        store.clear_steps(workflow_name, path_variant)
                        _no_replay.add((workflow_name, path_variant))
                        _emit(on_event, {"type": "note", "message": f"The clicks of the first record cannot be replayed for the others ({why}).", "record_id": record.record_id})
                if (learning or must_seed) and shadow is None:
                    _try_compile_recipe(workflow_name, path_variant, on_event, ctx.task_instructions or "")
        except Exception:
            logger.exception("Could not save or compile the recipe data for record %s", record.record_id)
        finally:
            _finished_keys.add(record.record_id)
            for book in _books.values():
                book.finish(record.record_id)
            if learning:
                _release_learn(workflow_name, path_variant)
            if must_seed:
                _release_seed_responsibility(workflow_name, path_variant)

    finished_at = datetime.now(timezone.utc)
    _batch_counts["model_calls"] += int(metrics.get("llm_steps") or 0)
    if metrics.get("mode") == "cold" and run_mode == "live":
        metrics["mode"] = "live"  # the first record seeds the cache; later ones that run live are just live
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
            "summary": run_result.summary,
            "mode": "live" if metrics["mode"] in ("recipe+live", "plan+live") else metrics["mode"],
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


# Callable that returns True when no new records should be started.
AbortCheck = Optional[Callable[[], bool]]


# Runs all records concurrently, bounded by max_concurrent via a pool of CDP port offsets.
# Takes the records, the RunContext, store and limits; returns a list of RunResult.
async def _run_many_concurrent_async(
    records: list[Record],
    *,
    ctx: RunContext,
    store: CacheStore,
    index_by_id: dict[str, int],
    total: int,
    on_event: OnEvent,
    abort_check: AbortCheck = None,
) -> list[RunResult]:
    port_pool: asyncio.Queue[int] = asyncio.Queue()
    for i in range(ctx.max_concurrent_replays):
        port_pool.put_nowait(i)

    # Waits for a free port slot, then runs one record and frees the slot.
    # Takes a Record and returns its RunResult (a failed one if aborted or errored).
    async def _run_one(record: Record) -> RunResult:
        port_offset = await port_pool.get()
        try:
            # Skip records not yet started on abort or quota exhaustion; in-flight ones finish.
            if (abort_check is not None and abort_check()) or _quota_exhausted():
                reason = _skipped_message() if _quota_exhausted() else "aborted before starting"
                return RunResult(
                    record_id=record.record_id,
                    success=False,
                    error_message=reason,
                    timestamp=datetime.now(timezone.utc),
                    quota_exhausted=_quota_exhausted() and _halt_reason["text"] is None,
                )
            result, _metrics = await _run_record_cached_async(
                record,
                ctx=ctx,
                store=store,
                port_offset=port_offset,
                index=index_by_id[record.record_id],
                total=total,
                on_event=on_event,
                abort_check=abort_check,
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

    try:
        return list(await asyncio.gather(*(_run_one(r) for r in records)))
    finally:
        for book in list(_books.values()):
            await book.close()  # stop any decision calls still running
        for batchers in list(_plan_batchers.values()):
            groups = batchers.slots.groups + batchers.form.groups
            if groups:
                _emit(on_event, {"type": "note", "message": (
                    f"The plan answered its questions in {len(batchers.slots.groups)} slot call(s) and {len(batchers.form.groups)} decision call(s) "
                    f"(groups of up to {max(groups)} records, {sum(batchers.form.groups)} records decided)."
                )})
            await batchers.close()
        if _batch_counts["program"] or _batch_counts["model_calls"]:
            _emit(on_event, {"type": "note", "message": (
                f"The decision program decided {_batch_counts['program']} record(s) with no model call; "
                f"the run made {_batch_counts['model_calls']} model call(s) in total."
            )})


# Runs records concurrently via cache replay; the LLM is used only for cold starts and misses.
# Takes a list of Records and the RunContext and returns a list of RunResult; resets the quota flag on each call.
def run_batch_cached(
    records: list[Record],
    ctx: RunContext,
    *,
    on_event: OnEvent = None,
    abort_check: AbortCheck = None,
) -> list[RunResult]:
    if not records:
        return []

    _quota_exhausted_event.clear()
    _halt_reason["text"] = None
    # Fresh locks per batch: an asyncio.Lock is tied to the event loop that first contends it
    _seed_locks.clear()
    _missing_field_locks.clear()
    _learn_locks.clear()
    _recipes.clear()
    _recipe_failures.clear()
    _recipe_given_up.clear()
    _no_replay.clear()
    _books.clear()
    _plan_batchers.clear()
    _plan_traces.clear()
    _examples.clear()
    _example_kinds.clear()
    _program_locks.clear()
    _program_tried.clear()
    _program_gaps.clear()
    _program_asks.clear()
    _batch_counts.update({"program": 0, "model_calls": 0})
    _finished_keys.clear()
    _all_fields.clear()
    _all_fields.update({r.record_id: r.field_dict() for r in records})
    total = len(records)
    index_by_id = {record.record_id: i + 1 for i, record in enumerate(records)}
    _emit(on_event, {"type": "batch_start", "total": total})

    store = get_store()

    results = asyncio.run(
        _run_many_concurrent_async(
            records,
            ctx=ctx,
            store=store,
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
# Takes the RunContext, match key, field name, value and port offset; returns True if the edit succeeded.
async def _reconcile_one_field_async(
    *,
    ctx: RunContext,
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

    port = ctx.cdp_base_port + RECONCILE_PORT_OFFSET_BASE + port_offset

    with tempfile.TemporaryDirectory(prefix=f"terminautomator_v2_reconcile_{port_offset}_") as profile_dir:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                profile_dir,
                headless=ctx.headless,
                args=[f"--remote-debugging-port={port}"],
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(ctx.target_url)
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
                    ctx=ctx,
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
                        ctx=ctx,
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
# Takes the records, a BatchReport and the RunContext; returns a dict with the fixed and skipped counts.
async def run_reconciliation_pass(
    records: list[Record],
    report: BatchReport,
    ctx: RunContext,
    *,
    on_event: OnEvent = None,
) -> dict[str, int]:
    if not records:
        return {"fixed": 0, "skipped": 0}

    workflow_name = ctx.workflow_name
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
                ctx=ctx,
                match_key_field=ctx.match_key_field,
                match_key_value=fields_by_record.get(record_id, {}).get(ctx.match_key_field),
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
# Takes the records, a BatchReport and the RunContext; returns the dict of fixed and skipped counts.
def run_reconciliation_pass_sync(
    records: list[Record], report: BatchReport, ctx: RunContext, *, on_event: OnEvent = None
) -> dict[str, int]:
    return asyncio.run(run_reconciliation_pass(records, report, ctx, on_event=on_event))
