# 02. Architecture and files

## The picture

```
 Browser tab (React app, port 8002)         Chrome windows (one per record)
        |                                          ^
        | /api/...                                 | Playwright (recipe, replay)
        v                                          | CDP (live browser-use agent)
 +--------------+   start_run()    +---------------+----------------+
 |  FastAPI     | ---------------> |  run thread (one at a time)    |
 |  api/routes  |                  |  asyncio loop, many records    |
 +------+-------+                  +---------------+----------------+
        |                                          |
        | SQL (psycopg pool)                       | on_event(...) -> queue -> writer thread
        v                                          v
 +------------------------------------------------------------------+
 |  PostgreSQL (Supabase): secrets, workflows, runs, run_events,    |
 |  cached_steps, run_metrics, recipe_traces, recipes               |
 +------------------------------------------------------------------+

 Gemini API  <-- keypool decides which key/model each call uses
 Target site <-- the Chrome windows (here: Credit Desk on :5174)
 Answer DB   <-- verification reads it after the run (Supabase of the target site)
```

Three threads of control matter:

1. **The web server threads** answer HTTP. They never touch a browser.
2. **One run thread** (`run-<id>`), started by `run_service.start_run`. It owns an `asyncio` loop that runs every record.
   A lock allows only one run at a time, because some engine state (the quota flag, the seed and learn locks) is
   process-wide.
3. **The event writer thread** (in `app_db.py`) batches log events into the database so a slow network round trip never
   slows a browser.

## Folder map

```
v3/
  main.py            FastAPI app; on startup marks runs left "running" by a dead process as failed
  config.py          Infra-only settings: DATABASE_URL, FORCE_HEADLESS, master-key path, default model
  run_context.py     RunContext: every per-run setting, passed down explicitly (no globals)
  app_db.py          Postgres pool, schema, event queue and writer
  secrets_vault.py   Fernet encrypt/decrypt of stored keys
  keypool.py         Round-robin over Gemini keys; per-minute pacing (GEMINI_RPM, default 14); cooldowns; daily-quota bookkeeping; fallback model
  run_service.py     Turns a saved workflow into a run: context, records, thread, verification, final status
  orchestrator.py    The engine: decides replay / recipe / plan / live per record, proves new plans, runs the batch (largest file);
                     since 10 Oct also learns as records finish: coverage, decision programs and their trust, slots read by code,
                     whether the site shows saved values (read-back), pruning
  prompt_gen.py      Optional: turn a plain description into agent instructions (one Gemini call)
  api/routes.py      Every HTTP endpoint, plus serving the built frontend
  agent/             The live agent
    prompts.py         Builds the task prompt (safety rules + your instructions + the record)
    runner.py          Runs a browser-use Agent per record; PooledChatGoogle switches keys on 429s
  recipe/            NEW in v3: the two fast paths (recipe, plan)
    trace.py           Records what the live agent did (clicks, typing, and the text of each page it saw)
    compile.py         Turns traces into a recipe (Credit Desk shape)
    execute.py         Runs a recipe on a record: read pages, decide in batches, click
    plan.py            Turns a trace into a plan (any shape): classifies values, builds locators, logins, optional pop-up clicks, ARIA and
                       combobox controls, replay-safety verdict, compares with the live agent
    plan_exec.py       Runs a plan on a record: replay the route (all frames, pop-ups closed, Retry, lists grown, signs in again),
                       copy slots from pages (by code once trusted), decide the form values (decision program or model, answers
                       checked against the pages), fill, save, read back; dry runs; PlanBatchers
    batcher.py         Shares one model call between records that ask the same question at about the same time
    formula.py         Safe evaluator for the rules the model writes as formulas (and {= } numbers in values)
    facts.py           (10 Oct) Reads facts off page text by code: numbers, dates, the value next to a label, table rows
    program.py         (10 Oct) Decision programs: facts + ordered rules written once by the model, run by code; validation and trust
    evidence.py        (10 Oct) Grounding: a decision's numbers, dates and quotes must be on the record's pages (or one step of arithmetic away)
    webkit.py          (10 Oct) Real-world pages: frames, ARIA radios/checkboxes, comboboxes, rich text, load more, pop-ups, Retry, logins
    readback.py        (10 Oct) After saving, opens the record's page again and checks the saved values are shown
    coverage.py        (10 Oct) One model question adds side trips the instructions need; pruning skips side trips that never supply a fact
    llm.py             The small text-only Gemini call (JSON out, key switching, retries)
    network.py         MutationTracker: waits for saves, notices refused saves
    store.py           recipe_traces and recipes tables (a recipes row is a recipe, a plan, or a live_only verdict)
  cache/             v2: step cache (store.py), element fingerprints, step recorder
  replay/            v2: Playwright replay engine
  data/              Record / RunResult / BatchReport models; loader
  evaluation/        Grading: evaluator.py (table check), view_verifier.py (answer-view check), normalizers
  frontend/          React + Vite + Tailwind: pages/AgentPage.jsx, pages/LogsPage.jsx
  tests/             test_backend.py (API + DB), test_recipe.py (recipe pieces), test_plan.py (plan engine, offline + browser with a fake model), test_plan_returns.py (the hard shapes),
                     test_plan_expense.py (single-page app, tabs, wizards with radios and checkboxes), test_plan_hazards.py (the Claims Desk: login, pop-ups, endless list,
                     iframe form with ARIA widgets), score_plan.py (plan decisions vs the right answers, real model, no live agent, optionally several records at once).
                     10 Oct module tests, no test site needed: test_program.py, test_evidence.py, test_pacing.py, test_webkit.py, test_readback.py,
                     test_coverage.py, test_orchestration.py (doc 12 lists their checks).
                     fixtures/ap_trace.json + returns_trace.json + expense_trace.json + hazards_trace.json, make_returns_fixture.py, make_hazards_fixture.py.
                     test_plan*.py and test_coverage.py take --offline to skip their browser part (the test_plan*.py ones reset a test site).
  storage/           master.key (encryption key). Old *.sqlite3 files here are leftovers from before Postgres
  LEARNINGS.md  AGENT_SUMMARY.md  FUTURE_PLANS.md   Long-form notes (docs/ is the short version)
```

## How one HTTP click becomes a run

1. **Agent page, Run** sends `POST /api/workflows/{id}/runs` with an optional `{"limit": N}` (run only the first N).
2. `api/routes.start_run` loads the workflow row and calls `run_service.start_run`.
3. `start_run` takes the run lock, calls `build_context` (decrypts keys, builds the `RunContext`), turns the saved data
   into `Record` objects, inserts a `runs` row with status `running`, and starts the thread. It returns `{run_id}` at once.
4. The Logs page polls `GET /api/runs/{id}/events?after=N` every moment and draws whatever is new.
5. When the thread ends it writes the final status (`done`, `aborted` or `error`) and the verification report.

## The settings object: RunContext

Everything that can differ between runs is a field of `RunContext` (`run_context.py`): target URL, workflow namespace,
match key, model, keys, headless, concurrency, the instructions text, whether caching is on, the verification database.
It is a frozen dataclass handed down explicitly, so two workflows can never leak settings into each other.

Two details worth knowing:

- **`workflow_name` is a namespace**, not the name you see: it is `wf<id>:e<cache_epoch>` (for example `wf32:e1`). All
  cache rows are keyed by it, so changing the epoch gives a fresh empty cache without deleting anything.
- **`headless`** is `workflow.headless OR FORCE_HEADLESS`. The env flag wins, which is how a server with no screen stays
  headless whatever a task says.

## API surface (what the frontend calls)

| Area | Endpoints |
|---|---|
| Health | `GET /api/health` (up, current run id, force_headless) |
| Secrets | `GET/POST /api/secrets`, `PUT/DELETE /api/secrets/{id}` (values never come back, only `••••last4`) |
| Import from `.env` | `GET /api/env-secrets`, `POST /api/env-secrets/{kind}/import`, `GET /api/dev-prefill` (local testing only) |
| Workflows | `GET/POST /api/workflows`, `GET/PUT/DELETE /api/workflows/{id}`, `POST /api/workflows/{id}/reset-cache` |
| Prompts | `POST /api/generate-prompt` (description to instructions), `POST /api/preview-prompt` (what the agent would see) |
| Runs | `POST /api/workflows/{id}/runs`, `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `POST /api/runs/{id}/abort` |
| Cache | `GET /api/cache/stats` (hit rate per workflow namespace) |
| Frontend | everything else serves `frontend/dist` (single page app) |

Handy facts: a save that changes the **target URL or match key** bumps `cache_epoch` automatically (old learned steps no
longer apply). Deleting a workflow also deletes its runs and logs. Deleting a secret is refused while a workflow uses it.
