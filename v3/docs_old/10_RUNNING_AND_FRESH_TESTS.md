# 10. Running it, and doing a truly fresh test

## Start everything

Three things have to be up. All commands run from the project folder.

**1. The target site (Credit Desk)**, port 5174:

```bash
cd testsites/credit-desk
npm run dev -- --port 5174 --strictPort
```

**2. The v3 server**, port 8002 (the web app and the engine):

```bash
cd ~/Downloads/terminautomator/v3
source venv/bin/activate
FORCE_HEADLESS=1 uvicorn main:app --port 8002     # drop FORCE_HEADLESS=1 to watch the Chrome windows
```

**3. Open** `http://localhost:8002`.

First-time setup (once): fill `DATABASE_URL` in `v3/.env` (Supabase Session pooler string), then
`pip install -r requirements.txt`, `playwright install chromium`, and `cd frontend && npm install && npm run build`.

Check it is alive: `curl localhost:8002/api/health` should show `"status":"ok"` and, with the flag set,
`"force_headless":true`.

## The second target: AP Desk (held-out test site)

```bash
cd testsites/ap-desk
node server.js                      # http://localhost:5180, no install needed
curl -X POST localhost:5180/admin/reset        # reset its decisions before every test
python3 grade.py 12                 # grade the first 12 records after a run (or no number for all 100)
```

The saved workflow is **"AP Desk · invoice matching"** (id 34, 100 records, **8 browsers** (a trusted plan shares model calls in groups of 5, so use more browsers than that), no built-in verification: grading is
`grade.py`, which compares the site's decisions with `expected.json`). For a fresh test of the plan engine:

1. `curl -X POST localhost:8002/api/workflows/34/reset-cache` (new cache namespace, nothing deleted);
2. `curl -X POST localhost:5180/admin/reset`;
3. if keys were marked out for the day and Google has reset since, restart the v3 server (doc 07);
4. start with a small batch: `curl -X POST localhost:8002/api/workflows/34/runs -H "Content-Type: application/json" -d '{"limit": 12}'`.

Expect the first 3 records to be slow (the first one teaches, the next two prove the plan, each with the live agent), and the
rest to take about 3 model calls each. If the plan is thrown away, every record costs about 15 agent steps; the log says why.

## The third target: Returns Desk (the hard one)

```bash
cd testsites/returns-desk
node server.js                      # http://localhost:5181
curl -X POST localhost:5181/admin/reset
python3 grade.py 12
```

The saved workflow is **"Returns Desk · RMA decisions"** (id 35, 100 records, 4 browsers (8 live browsers hit the 15-a-minute limit), no built-in verification: graded by
`grade.py`). A live record costs about 15 to 25 agent steps (paging and side trips), so keep fresh tests small (8 records cost
about 120 model calls, most of them in the 4 or 5 live records that teach and prove the plan). `venv/bin/python tests/score_plan.py
100 10 returns` judges the plan's *decisions* without the live agent.

## The fourth target: Expense Desk (a single-page app with a wizard)

```bash
cd testsites/expense-desk
node server.js                      # http://localhost:5182
curl -X POST localhost:5182/admin/reset
python3 grade.py 16
```

The saved workflow is **"Expense Desk · claim audit"** (id 36, 100 records, 8 browsers, graded by `grade.py`). The live agent takes
about 8 to 17 steps per claim. A fresh test of 8 records cost about 50 model calls on 9 Oct (3 live records, then the plan).

## The fifth target: Claims Desk (real-world hazards, added 10 Oct)

```bash
cd testsites/claims-desk
node server.js                      # http://localhost:5183 (127.0.0.1 only)
curl -X POST localhost:5183/admin/reset
python3 grade.py 12
~/Downloads/terminautomator/v3/venv/bin/python scripts/smoke.py   # drives the site with Playwright through five outcomes, then resets
```

An insurance claims desk with a sign-in that runs out (test account `desk.auditor` / `audit-2026`, local test values only), a cookie
banner, a "What's new" pop-up, an endless list that sometimes fails with a Retry box, slow pages, and a decision form inside an
iframe made of an ARIA radio group, searchable comboboxes and a rich-text note (doc 12, and the site's README). Knobs:
`LIST_FAIL_RATE=0` turns the list failures off, `DELAY_MS=300-1500` sets the slowness.

**It has not been run with the live agent yet**, and no v3 workflow for it has been run. To try it: create a task in the web app
with `http://localhost:5183` as the website, the records from `testsites/claims-desk/records.json`, and instructions that include
the sign-in (the username and password written word for word, so the plan can sign in again as a constant) and the five rules from
the site's README; then a **small batch** (8 records), and `python3 grade.py 8`.

## Keeping within the day's model quota

The free tier gives about 500 calls per model per Google project per day. Teaching and proof records are the expensive part
(each is a full live-agent run), so:

- test with **small batches** (8 to 16 records) after any change to the engine, and only run 100 when the small batch is clean;
- prefer `score_plan.py` and the fake-model tests (no live agent) to check decisions and mechanics;
- keep `GEMINI_RPM` at its default (14 per key and model per minute) on the free tier: extra browsers then wait for room instead
  of getting "429" answers (doc 07); set it to 0 only for paid keys;
- **abort** a run that is learning badly (`POST /api/runs/<id>/abort`) instead of letting every record fall back to the live agent.
  Since 9 Oct an abort also stops records that were waiting for the learning lock; on older code they still ran.

## Starting a run without the UI

```bash
curl -X POST localhost:8002/api/workflows/32/runs -H "Content-Type: application/json" -d '{}'          # all rows
curl -X POST localhost:8002/api/workflows/32/runs -H "Content-Type: application/json" -d '{"limit": 10}' # first 10
curl localhost:8002/api/runs/<run_id>            # status, accuracy, report
curl localhost:8002/api/cache/stats              # hit rate per workflow namespace
```

## What "fresh" means: three separate things to reset

A fresh Credit Desk test needs **all three**. Skip one and the result is misleading.

| # | What to reset | Why | How |
|---|---|---|---|
| 1 | **The learned cache** | The recipe is loaded from the database at the start of each run, so a second run would skip learning | Delete rows from `cached_steps`, `recipes`, `recipe_traces`, `run_metrics` (or bump the workflow's `cache_epoch`) |
| 2 | **The test site's decisions** | If all 100 requests are already decided, the run is not testing anything, and the accuracy number is meaningless | On `http://localhost:5174`, click **Reset all decisions** and confirm, then check it says **0 of 100 decided** |
| 3 | **Python's compiled leftovers** | Not logic, but removes any stale bytecode so the run uses the code on disk | Delete `__pycache__` folders under `v3/` (not inside `venv/` or `node_modules/`) |

Not part of "cache", so a fresh test leaves them alone: run history and logs (`runs`, `run_events`), saved workflows, and
secrets.

Two timing caveats for a clean comparison:

- **Gemini quota** resets at midnight Pacific (07:00 UTC, 12:30 pm IST until 1 Nov, then 1:30 pm IST), per Google's docs,
  not at 00:00 UTC. Before that, a key used heavily the day before may already be spent, and the run will fall back to the
  second model partway through. Keys from the same Google project share one quota (doc 07).
- **The server must not be mid-run.** Only one run is allowed; a second start returns HTTP 409.

## Reading the result of a run

On the Logs page, or from the API:

- `status` should be `done`. `error` means quota, a bad key, or a crash (the message says which).
- **Accuracy** (`report.field_accuracy`) is the number that matters. `unmatched_records` should be 0.
- `batch_done`: `succeeded` and `failed` counts, and `quota_exhausted`.
- The `note` events tell the recipe story: *Learned a recipe...* then *Deciding N records in M model calls*.
- `/api/cache/stats`: `total_llm_steps` should be tiny compared with the number of records (about 7 for 100 on the
  Credit Desk), and `hit_rate` close to 1.
- The server log (`uvicorn` output) has lines like *The passes disagreed ... chose ...*, *Unreadable formula ...*, and any
  *Gemini key ... is out of daily quota* warnings.

## Tests

```bash
cd v3
venv/bin/python tests/test_recipe.py                  # recipe pieces: no browser, no AI, no database
venv/bin/python tests/test_plan.py                    # plan engine: part 1 offline; part 2 in a real browser on AP Desk with a FAKE model
venv/bin/python tests/test_plan_returns.py            # the hard shapes: paging, id-text links, calendar widget, long page, messy routes (browser on Returns Desk, FAKE model)
venv/bin/python tests/test_plan_expense.py            # single-page app, tabs, collapsed sections, a three-step wizard with radios and checkboxes (browser on Expense Desk, FAKE model)
venv/bin/python tests/test_plan_hazards.py            # sign-in, pop-ups, endless list, iframe form with ARIA widgets (browser on Claims Desk, FAKE model)
venv/bin/python tests/test_program.py                 # 10 Oct, no site needed: decision programs and facts (FAKE model)
venv/bin/python tests/test_evidence.py                # grounding: numbers, dates and quotes must be on the pages
venv/bin/python tests/test_pacing.py                  # per-minute pacing on a fake clock with fake model clients
venv/bin/python tests/test_orchestration.py           # what the orchestrator learns as records finish (everything faked)
venv/bin/python tests/test_webkit.py                  # real-world page helpers, headless browser on pages the test writes
venv/bin/python tests/test_readback.py                # read-back, headless browser on pages the test serves itself
venv/bin/python tests/test_coverage.py                # coverage and pruning (--offline skips its browser part)
venv/bin/python tests/test_plan_returns.py --offline  # any test file with a part 2: --offline skips the part that resets the site
venv/bin/python tests/score_plan.py 10                # plan decisions vs the right answers on 10 AP records, REAL model, no live agent (about 30 calls)
venv/bin/python tests/score_plan.py 100 10            # all 100, 10 browsers at a time through the batchers (about 62 calls, about 5 minutes)
TEST_DATABASE_URL=postgresql://... venv/bin/python tests/test_backend.py   # API + database
```

`test_plan.py` part 2 needs `node server.js` running in `testsites/ap-desk` (it is skipped otherwise) and resets the
site's decisions; the same goes for `test_plan_returns.py` (v4), `test_plan_expense.py` (v5) and `test_plan_hazards.py` (v6). The
other 10 Oct test files use no test site and never call the real model. Check counts per file are in doc 12. **Never run a part 2 while a real
run is using that site**: it wipes the run's decisions (on 9 Oct it wiped two). Use `--offline` then. `score_plan.py` needs the v3 server on :8002 (it reads workflow 34's prompt) and spends model quota.
`test_backend.py` creates and drops a throw-away schema, so point it at a **local** PostgreSQL (the app's limited login cannot
create schemas).

## Switching things off

Stop the servers with Ctrl-C in their terminals (or kill the processes listening on 8002 and 5174). Chrome windows
launched by a run close with it. To remove everything the app created in Supabase, see the README
(`drop schema agent_state cascade; drop role terminautomator_app;`).
