# ERP Automation v2 — Caching Layer on top of browser-use

This is the plan v2 was built against, kept verbatim for reference — code
comments throughout v2 (cache/, replay/, orchestrator.py) point back to
specific steps in here.

## Context

v1 is the existing LLM-driven browser automation (browser-use agent, live LLM
call per action). v1 is frozen — no further edits — so it stays a clean
baseline.

v2 = same task capability as v1, but with a caching/replay layer added on
top, so that repeated workflows stop paying LLM-token cost and click-by-click
latency on every single run.

**Design principle (non-negotiable):** v2 never becomes cache-only. The live
browser-use agent stays permanently wired in as the fallback for every step,
not just during rollout. Goal is to minimize LLM calls, not eliminate them.
Cache hit rate is a permanent operational metric, not a one-time launch gate
— a drop in hit rate for a workflow is the early-warning signal that the
ERP's UI changed.

## Verified facts about browser-use (checked against source, v0.13.10)

- Runs on raw Chrome DevTools Protocol (CDP), not Playwright, for live
  execution.
- Live click/type execution uses `backend_node_id` — this is a CDP session
  ID, not reusable across a fresh page load or a new run. Never cache this.
- Public hooks exist, no fork/clone needed:
  - `register_new_step_callback(browser_state, agent_output, step_num)` —
    fires after every step
  - `register_done_callback(history)` — fires when a run finishes
  - `on_step_start` / `on_step_end` — passed into `agent.run()`
- Every completed run's `AgentHistoryList` already includes, per action, an
  `interacted_element` (`DOMInteractedElement`) with:
  - `x_path` — reusable across page loads (unlike `backend_node_id`)
  - `attributes` — dict of real HTML attributes (id, name, data-testid, etc.)
  - `node_name`
  - `stable_hash` — hash of tag-path-from-root + static attributes, with
    transient CSS state (hover/focus/animation classes) filtered out. Built
    for cross-session element re-identification.
- `stable_hash` is not currently used anywhere else in browser-use to
  re-locate an element in a fresh DOM tree — it's a fingerprint, not a
  lookup. Writing that matching function is our work, not something to
  import.
- License: MIT (Gregor Zunic, 2024) — confirmed against repo. No fork
  obligation since we're using it as a plain dependency, not redistributing
  modified source.

## Step 0 — Project setup

- [x] Copy v1's project folder into a new `v2/` folder. v1 stays untouched
      from here on.
- [x] `v2` keeps `browser-use` as a normal pinned pip dependency (no
      clone/fork).
- [x] Set up the new cache store (see Step 2) and a `v2/CHANGELOG.md` so
      v1-vs-v2 comparisons later have a clear record of what changed when.

## Step 1 — Instrumentation (capture what the agent does, don't change behavior yet)

- [x] Wire `register_new_step_callback` into the existing agent-calling code
      in v2 (`agent/runner.py`'s `_run_single_record_async` now accepts it as
      an optional parameter — both v1's call sites and v2's orchestrator go
      through this same function).
- [x] Per action, capture and log: `workflow_name`, `path_variant`,
      `step_index`, `instruction`, `x_path`, `attributes`, `stable_hash`,
      `node_name`, `action_type`; for `type`/`select` actions, the field
      target rather than the literal value typed.
      (`cache/instrumentation.py`)
- [ ] **Checkpoint, not yet run**: v2 should behave identically to v1 at this
      stage, just now logging everything it does. This needs an actual live
      run (real Gemini key + DataDesk + Supabase) to verify, which wasn't
      available in the environment this was built in — see
      `v2/CHANGELOG.md` for exactly what is and isn't verified.

## Step 2 — Cache store

- [x] SQLite store (`cache/store.py`) — no need for anything heavier yet.
- [x] Key: `(workflow_name, path_variant, step_index, action_offset)` — the
      `action_offset` component was added beyond the original plan's key,
      to handle a single agent "step" containing more than one action
      (browser-use's `multi_act`).
- [x] `path_variant` resolved from the record's own data
      (`orchestrator.resolve_path_variant`) — no AI involved. DataDesk's
      People form has no branching fields today, so this defaults to
      `"default"`.
- [x] Populated automatically from Step 1's captured data on every live run.

## Step 3 — Replay engine

- [x] Built as a separate, independent Playwright script
      (`replay/engine.py`) — zero import of `browser_use`. Uses Playwright's
      ASYNC API specifically so it composes with browser-use's own asyncio
      event loop inside `orchestrator.py` (mixing Playwright's *sync* API
      into that same loop is unsupported by Playwright itself).
- [x] Given a cached step, locate the element on the fresh page:
  1. `x_path` match
  2. attribute-based selector (`data-testid`/`data-test`/`data-cy`/`id`/`name`/`aria-label`)
  3. fingerprint sanity check — see `cache/fingerprint.py`'s docstring for
     exactly why this is our own re-derived fingerprint rather than a
     reproduction of browser-use's internal `stable_hash` algorithm (that
     algorithm needs live access to browser-use's own DOM-tree/accessibility
     internals, which a standalone Playwright script doesn't have).
  4. No match on tiers 1–2 → cache miss.
- [x] Sanity-check logged (fingerprint mismatch), not a hard stop — the
      xpath/attribute match is already real evidence.
- [x] No LLM call on a cache hit.

## Step 4 — Fallback wiring

- [x] Orchestrator (`orchestrator.py`): try cached replay first. On miss,
      hand just that step (not the whole workflow) to the live browser-use
      agent, attached via `BrowserProfile(cdp_url=...)` to the SAME browser
      tab replay was already using.
- [x] Live step's result captured the same way as Step 1, and self-heals
      (overwrites) the stale cache entry under the *original* step's key.
- [x] Every fallback logged with a reason (`no_match` / `execution_failed` /
      `field_missing_in_record`) — feeds `run_metrics` / `GET /cache/stats`.

## Step 5 — Verification

- [x] Reused as-is: `evaluation/evaluator.py` already re-reads the record
      back from Supabase and compares field-by-field, regardless of whether
      the run was cold or replayed. No changes needed here — this was
      already decoupled from *how* the record got submitted.

## Step 6 — v1 vs v2 comparison

- [x] Script written: `v2/compare.py`, HTTP-level (POSTs `/run` to both a
      running v1 and a running v2, so it never needs to import them as
      colliding same-named Python packages in one process).
- [x] Compares: total run time, error rate / field_accuracy, unmatched
      records, and (v2 only) cache hit rate per workflow via
      `GET /cache/stats`.
- [ ] **Not yet run** — needs both services actually up with a live Gemini
      key, same as Step 1's checkpoint.
- [x] Ongoing: `CacheStore.hit_rate_stats()` / `GET /cache/stats` is a
      standing endpoint, not a one-off script — intended to keep being
      checked after this initial comparison too.

## LLM token / cost knob explicitly NOT touched here

`THINKING_LEVEL` in config.py was already unused/unwired in v1; still true in
v2. Out of scope for this caching work.
