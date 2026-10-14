# 04. Life of a run

From pressing Run to the accuracy number. File names in brackets say where each step lives.

## The timeline

```
POST /runs
  |
  1. start_run            lock, build RunContext, insert `runs` row, spawn thread        [run_service.py]
  2. run_batch_cached     reset per-batch state, emit batch_start, asyncio.run(...)      [orchestrator.py]
  3. for each record, concurrently (up to max_concurrency browsers):
       a. choose a mode   cold / live / replay / recipe
       b. open a browser  fresh temp profile, own debugging port
       c. run the record  live agent, v2 replay, or recipe
       d. settle saves    wait for in-flight POST/PUT/PATCH/DELETE, check the last one
       e. bookkeeping     save trace, maybe learn a recipe, write metrics, emit record_done
  4. verify               compare to the table or the answer view -> report              [evaluation/]
  5. reconcile (optional) only for table checks: fix flagged fields through the edit form
  6. finish               status done | aborted | error, flush logs                      [run_service.py]
```

## Step 1: start

`run_service.start_run` refuses with HTTP 409 if another run is going. Otherwise it:

1. builds the `RunContext` (decrypts keys, sets `workflow_name = wf<id>:e<epoch>`, headless, concurrency);
2. builds the records. A record's id is `record_id` if the row has one, else the value of the match-key column, else the
   row number. **Duplicate ids are rejected**, and an empty data list means "run the task once";
3. optionally keeps only the first `limit` rows (a quick test);
4. inserts the `runs` row and starts the thread.

## Step 2: the batch

`run_batch_cached` clears all per-batch memory (locks, recipes in use, failure counters, decision books), because an
`asyncio.Lock` is tied to the event loop that created it and each batch gets a new loop. It also remembers every record's
fields so the decision book can decide records that have not started yet.

Records run through a **pool of port slots**. With `max_concurrency = 2` there are two slots: a record takes a slot (and
the Chrome debugging port `9333 + slot`), runs, and returns the slot. Before starting a record it checks the abort flag
and the quota flag; a record not yet started is skipped with a clear reason, while records already running finish.

## Step 3a: choosing a mode

This is the heart of the orchestrator (`_run_record_cached_async`). Modes:

| Mode | When | What does the work |
|---|---|---|
| **cold** | caching on, nothing learned yet, and this record won the "seed lock" | live agent, and its actions are recorded |
| **live** | caching off, a fallback, or a **proof record** for a new plan | live agent (for a proof record, the plan's dry run is compared with it) |
| **replay** | the first record's clicks were judged safe to replay (plain form filling) | Playwright replays clicks and typing, no AI |
| **recipe** | a recipe has been learned (Credit Desk shape) | recipe engine (doc 05) |
| **plan** | a plan has been learned **and trusted** (any other shape) | plan engine (doc 06) |
| *recipe+live*, *plan+live* | a step failed before any saving click, so the live agent redid the record | fast path, then live agent |

The decision, in order:

1. If caching is on, ask for the **seed lock**. Whoever gets it first is the **seeder**; every other record waits until the
   seeder finishes. This is why concurrency does not help at the very start: record 1 teaches while others wait. Nobody is
   made a seeder when the variant has already been learned (`_already_learned`: replayable steps, a recipe or plan, or the
   verdict "do not replay").
2. If the cache has replayable steps, the record replays them. Whether the first record's steps are replayable is decided
   **once**, right after that record (`replay_verdict`, doc 06): not if any typed or chosen value was not a copy of a record
   field, and not if a click names the first record's own item. When it is not safe the saved steps are deleted. (The old
   check, "at least one step is tied to a data field", was fooled by one mis-tagged click on AP Desk.)
3. Otherwise `_claim_recipe`: use the recipe or trusted plan in memory, else load it from the database, else compile one
   from saved traces (a recipe first; a plan only when replay was judged unsafe). A **plan that is not trusted yet** makes
   this record a **proof record**: it holds the learn lock (so proof records run one at a time) and runs live.
4. A record on the **proof path** first does the plan's **dry run in a second tab**, then the live agent does the record,
   then the two sets of values are compared (doc 06). Agreement counts toward trust; disagreement throws the plan away.

## Step 3b to 3d: running one record

Per record, a brand-new browser:

- Playwright `launch_persistent_context` with a temporary profile folder and `--remote-debugging-port`. The live agent
  attaches to the same window over CDP (`BrowserProfile(cdp_url=...)`), so the recipe and the agent share one tab.
- `headless` comes from the context. Closed and the profile deleted when the record ends.
- A `MutationTracker` is attached to the whole browser context before the first page loads (doc 07).
- The start page is opened with one retry (a stray keystroke into a visible window once sent a browser to a web search).

Then the record runs in its mode. Afterwards, **before closing the browser**:

- `tracker.settle()` waits (up to 8 s) for any save still in flight;
- if the record claimed success but the last save was refused, the record is **downgraded to failed** with the reason;
- a 0.3 s pause lets toasts and modals finish.

## Step 3e: bookkeeping (always runs, even on errors)

- **Trace**: if a live record succeeded and no recipe or plan is in use, save its trace. If this record was the seeder, run
  the **replay verdict** (and delete the click steps if unsafe). If it was the seeder or a learner, try to compile a recipe,
  or a plan, right away so the next record already sees it. (Proof records save their trace but do not recompile.)
- **Decision book**: mark the record finished so it is never included in a later decision call.
- **Locks**: release the learn lock and the seed lock.
- **Metrics**: write a `run_metrics` row (mode, hits, misses, LLM steps, miss reasons).
- **Events**: `record_done` (and an `error` event if it failed).

A first-record label detail: the seeder's mode shows `cold`, but if it ran live only because it was the first, the label
is changed to `live` so the Logs page matches what happened. A `plan+live` record is shown as `live`.

## Step 4 to 6: after all records

- **Verification** (doc 08). Status becomes `verifying` while the report is built, then a `verification_done` event.
- **Reconciliation** only when verification is a *table* check, `reconcile` is on, and the run was not aborted or halted.
- **Final status**:
  - a fatal error (for example Gemini rejected the key) gives `error` with the message;
  - quota exhausted gives `error` with "daily quota exhausted; use another key or wait";
  - an abort gives `aborted`;
  - otherwise `done`.

Secrets are redacted from every message written to the database.

## Aborting and crashing

- **Abort** (`POST /api/runs/{id}/abort`) sets a flag. Records in progress finish; nothing new starts.
- **Server killed mid-run**: on the next start, `mark_orphaned_runs` flips any run still `running` or `verifying` to
  `error` ("Server restarted while this run was in progress"). Its log, written before the crash, is kept. Chrome
  windows are closed by Playwright when the process dies.
