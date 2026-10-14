# CONTEXT.md — full project state, for LLM consumption

This file is a dense context primer, not a human-readable narrative. It is
written so that any LLM given only this file can immediately understand:
what this project is, what has been built and tested so far (with exact
results), every non-obvious decision and why it was made, the current
configuration state, and what the next phase of work is aimed at. Assume
no other memory of this project exists. Assume the reader is an LLM
picking up work, not a person skimming for the gist — prioritize
completeness and precision over brevity or narrative flow.

Other docs exist alongside this one (`ARCHITECTURE.md`, `RUNBOOK.md`,
`LEARNINGS.md`, `CODEBASE.md`, `THESIS.md`, `CODE_FLOW.md`) — this file is
the superset/index of all of them, written later, and should be treated as
the most current and complete single source of truth. Where this file and
an earlier doc disagree on current state (e.g. a config default), trust
this file.

---

## 1. What this project is, and why it exists

The user (email domain `asianfootwears.com` — works at a footwear company,
likely with a real ERP system) is building and testing a system that
automates manual data-entry-style tasks using an LLM-driven browser agent,
as a proof-of-concept before applying the same pattern to automating
simple manual tasks in their actual company ERP.

The proof-of-concept has two parts, each its own separate codebase:

1. **A target web app to automate against** ("DataDesk") — a realistic
   but fake People-Management CRUD app, so testing doesn't touch anything
   real.
2. **An automation agent** that reads a batch of records from a JSON
   file, drives a real browser (via an LLM deciding what to click/type)
   to enter each record into DataDesk's form, and then **independently
   verifies** — by reading the database directly, not by trusting the
   agent's own opinion — whether the data actually landed correctly.

The core thesis being tested: can an LLM-driven browser agent reliably do
structured data entry into an arbitrary web UI, and can you build a
trustworthy way to grade whether it actually worked (since the agent's
own self-report is not reliable evidence). This has been validated: yes,
with caveats around cost/rate limits documented below.

The project is now pivoting toward: **should this same automation
approach be pointed at the user's real ERP, and if so, how** — see
Section 9 (Future direction) for where that stands.

---

## 2. The two codebases (exact paths)

- **Automation agent (backend)** — the original app folder (now `v1/` in this repository). Python
  3.11, FastAPI. **This is the repo all the Markdown docs live in, and the
  one most of the engineering work in this project happened in.** Not a
  git repository (as of last check).
- **DataDesk (frontend target app)** — the `DataDesk` project (now `testsites/credit-desk`).
  React 19 + Vite 8 + Tailwind 4, Supabase (hosted Postgres) as its
  database. Single-file app: almost everything lives in
  `src/App.jsx` (~2600 lines).

These two are connected only via (a) the browser — the agent literally
drives a Chrome window pointed at DataDesk's URL — and (b) Supabase — both
DataDesk and the automation agent's evaluator read/write the same
`people` table, using different credentials (anon key for DataDesk's
frontend, service_role key for the backend evaluator).

---

## 3. Automation agent — full file inventory

All paths are relative to the backend folder (`v1/`).

- `main.py` — FastAPI app creation, mounts the router, one health-check
  `GET /`. No other logic.
- `config.py` — loads all env vars via `python-dotenv`. Required:
  `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
  `TARGET_FORM_URL` (raises at import time via `_require()` if missing).
  Optional with defaults: `SUPABASE_TABLE` (default `records`, but in
  practice set to `people` in this project's `.env`), `MODEL_NAME`
  (default `gemini-3.5-flash-lite`), `THINKING_LEVEL` (default `low`,
  currently unused/not wired anywhere), `MATCH_KEY_FIELD` (default
  `email`), `RESULTS_STORE_PATH` (default `storage/results.json`), and
  **`CONCURRENT_MODE`** (default `false` — added in this session, see
  Section 6).
- `api/routes.py` — the FastAPI router. Three endpoints:
  - `POST /run` — body `{"records_path": "<path>"}`. Loads records,
    refuses if a batch is already running (in-memory `_status` guard
    dict), calls either `run_batch` or `run_batch_concurrent` (picked via
    `config.CONCURRENT_MODE`), then calls `verify_batch` against a fresh
    Supabase client, saves the report, returns it. Any exception →
    500 + `_status["state"] = "error"`.
  - `GET /status` — returns the in-memory `_status` dict
    (`state`/`current`/`total`/`error`). Known limitation, unaddressed:
    `current` jumps straight to `total` at the end, no live per-record
    progress.
  - `GET /results` — re-serves the last saved `BatchReport` from disk, 404
    if none yet.
- `agent/prompts.py` — `build_task_prompt(record) -> str`. Turns a
  `Record`'s fields into the natural-language instructions the browser
  agent executes (list every field/value, wait for slow dropdowns, don't
  invent values, submit once and stop).
- `agent/runner.py` — the browser-driving logic. See Section 5 and 6 for
  full detail; this is the file most heavily modified during this
  project.
- `data/models.py` — Pydantic models: `Record` (input row, `extra="allow"`
  so any field set works, only `record_id` is fixed; `.field_dict()`
  returns all fields except `record_id`), `FieldResult`, `RunResult`
  (agent's own self-reported outcome — explicitly not trusted for
  grading), `RecordVerification`, `BatchReport`.
- `data/loader.py` — `load_records(path) -> list[Record]`. Reads a JSON
  array, synthesizes `record_id` from index if missing, raises
  `FileNotFoundError`/`ValueError` on bad input.
- `evaluation/normalizers.py` — field-type-aware comparison. Infers a
  field's type from its name (date/phone/money/email/default keyword
  matching), normalizes both expected and actual values before comparing
  (e.g. dates to ISO `YYYY-MM-DD`, phone numbers to digits-only with US
  country code stripped, money to plain float). Single entry point:
  `values_match(field_name, expected, actual) -> bool`.
- `evaluation/evaluator.py` — the independent verification pass.
  `verify_batch(records, supabase_client)` fetches every row from
  Supabase, matches each expected record to a row via `MATCH_KEY_FIELD`
  (case/whitespace-insensitive), and either marks every field
  `record_not_found_in_db` (no matching row at all — usually the match
  key itself was wrong or submission failed) or compares field-by-field
  and tags mismatches `value_mismatch`. Produces the final `BatchReport`
  with `field_accuracy = correct_fields / total_fields`. **Never reads
  `RunResult.success` anywhere** — deliberately decoupled from the
  agent's own opinion.
- `storage/results_store.py` — `save_report()`/`load_last_report()`,
  trivial flat-JSON-file persistence so a crash/restart doesn't lose the
  last batch's results.
- `sample_records.json` — the batch input file, repeatedly overwritten
  throughout this project with different test data (see Section 7 for
  exact history of what it's contained at each point; current contents
  described in Section 8).
- `.env` / `.env.example` — real secrets are in `.env` (git-ignored,
  not reproduced here for security — do not paste actual key values into
  any file meant for broad LLM consumption). `.env.example` was
  originally a template but was found to already have real secret values
  filled in by the user; `.env` was created by copying it and fixing
  `TARGET_FORM_URL` to `http://localhost:5173` (the example had a stale
  `http://localhost:3000/form`, which doesn't match DataDesk's actual
  single-page-app structure — DataDesk has no separate `/form` route, the
  form is a modal on the root URL).
- `venv/` — Python 3.11 virtualenv, created and populated
  (`pip install -r requirements.txt`) during this project.
- `requirements.txt` — `fastapi`, `uvicorn[standard]`, `browser-use`,
  `supabase`, `python-dotenv`, `pydantic`.
- Markdown docs (all written during this project, all in this same
  directory): `ARCHITECTURE.md`, `RUNBOOK.md`, `LEARNINGS.md`,
  `CODEBASE.md`, `THESIS.md`, `CODE_FLOW.md`, and this file.

### Important dependency-version fact

The installed `browser-use` is version **0.13.10**. This version does
**not** use Playwright at all (the project's own `README.md` says to run
`playwright install chromium` — that instruction is stale/wrong for this
installed version and should be ignored). Instead, `browser-use` 0.13.10
depends on a package called `browser-harness` (and `cdp-use`), which
drives a **real, actual Chrome browser** directly via the Chrome
DevTools Protocol (CDP) — not a headless/sandboxed instance. By default
(no explicit browser profile given), it will attach to and visibly take
over whatever Chrome the user already has open, including their real
profile/logged-in sessions. This surprised the user the first time it
happened (asked "did I just watch Claude's computer use?" — answer: no,
it was `browser-use`'s own agent, powered by Gemini, driving their real
Chrome via CDP; the orange highlight boxes seen were `browser-use`'s own
element-highlighting overlay, injected into the page, not anything from
Claude). This is exactly why every record is now given its own isolated,
disposable Chrome profile (Section 5) — to stop this hijacking behavior.

---

## 4. DataDesk (target app) — key facts

- `src/App.jsx` — single component tree. Key functions: `loadPeople()`
  (fetch from Supabase or fall back to `localStorage` "local demo mode"
  if Supabase env vars absent), `submitForm(event)` (validates via
  `validateForm`, then insert-or-update to Supabase depending on
  `editingPerson`), `deletePerson(person)`, `exportPeople()` (client-side
  CSV download), `validateForm(form)`.
- Its own `.env` (separate file, separate secrets from the backend's) at
  `.env` in the DataDesk folder — contains
  `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (anon/publishable key,
  safe to expose to a browser — NOT the service_role key). This file is a
  dotfile; the user briefly thought it was missing because dotfiles are
  hidden by default in Finder/`ls` — it was present and correct the whole
  time (`Cmd+Shift+.` in Finder, or plain `ls -la`, reveals it).
- `src/lib/supabase.js` — **deleted** during this project (early on). It
  was a dead/unused duplicate Supabase client (would have thrown at
  import time if ever actually imported, since it lacked the null-fallback
  guard `App.jsx`'s inline client has). Confirmed via grep that nothing
  imported it before deleting. `App.jsx` builds its own Supabase client
  inline (lines ~31-37), with a guard that falls back to `localStorage`
  mode if env vars are absent.
- Its Settings page (`App.jsx`, "Browser Automation Ready" card) literally
  states the app was built with "semantic HTML, stable element IDs,
  accessible labels, real form controls, and deterministic success/error
  states so browser agents can interact with it reliably" — this is real,
  intentional design in the target app, not something this project added.
- **Dropdown option lists are fixed/hardcoded** and matter a lot for test
  data design:
  - `COUNTRIES` (15): India, United States, United Kingdom, Canada,
    Australia, Germany, France, Spain, Italy, Japan, South Korea,
    Singapore, United Arab Emirates, Brazil, Mexico. **Notably does NOT
    include Pakistan** (tested and confirmed absent — see Section 7) or
    Ireland (despite Ireland/Dublin appearing in the app's own
    `SEED_PEOPLE` hardcoded seed data, inconsistently).
  - `DEPARTMENTS` (13): Engineering, Product, Design, Marketing, Sales,
    Finance, Human Resources, Data, Research, Security, Infrastructure,
    Architecture, Operations.
  - `EMPLOYMENT_TYPES` (5): Full Time, Part Time, Contract, Intern,
    Freelance.
  - `GENDERS` (4): Male, Female, Non-binary, Prefer not to say.
  - Using a value outside a dropdown's list doesn't crash anything — the
    agent generally leaves that field unset, and the evaluator correctly
    flags just that one field as a `value_mismatch` rather than failing
    the whole record. This is expected/correct behavior, not a bug,
    unless the goal is specifically to get DataDesk's option lists
    expanded.

---

## 5. Core design decisions in `agent/runner.py`, and why (in order they were arrived at)

### 5.1 Never trust the agent's own success claim

`browser_use.Agent`'s `.run()` result includes an `is_successful()`
self-report. This is recorded in `RunResult.success` for logging/debug
purposes only. **Nothing in `evaluation/evaluator.py` ever reads it.**
Grading is 100% based on independently reading Supabase afterward. This
was a deliberate, foundational design choice, not an afterthought.

### 5.2 One disposable, isolated Chrome profile per record

Each call to `_run_single_record_async` creates a unique
`tempfile.TemporaryDirectory()` and passes it as `user_data_dir` to a
`BrowserProfile(user_data_dir=profile_dir, keep_alive=False)`. This forces
`browser-use` to launch a brand-new, fully separate Chrome process for
that one record, instead of attaching to (and visibly hijacking) whatever
Chrome the user already has open. The directory (and the Chrome window in
it) is torn down automatically when the `with` block exits.

Originally added to make concurrency safe (Section 6), but **kept even
after reverting to sequential-only execution**, because it's a strict
improvement with no downside (only one is ever alive at a time either
way) — it means running a batch never touches the user's real, personal
browser session.

### 5.3 Text-only reasoning by default ("vision off"), with a vision-enabled fallback

`browser_use.Agent`'s default is `use_vision=True` — it sends a
screenshot on every single step, **in addition to** the text-based
element/DOM list it always builds regardless (the `index: 908`,
`index: 959` references seen throughout logs — that text list is the
primary signal the agent already reasons over; the screenshot is a
supplementary channel). Screenshots are the most expensive part of a
multimodal LLM call.

Current implementation (`agent/runner.py`):
- `_run_single_record_async(record, *, use_vision: bool)` — the core unit,
  parameterized on vision.
- `_run_single_record_with_vision_fallback_async(record)` — **Plan A**:
  calls the above with `use_vision=False`. If that didn't succeed
  (`result.success` false), logs a warning, waits
  `RATE_LIMIT_COOLDOWN_SECONDS` (see 5.4), then **Plan B**: retries the
  same record once with `use_vision=True`. This is the function actually
  used as the "run one record" entry point (`run_single_record` wraps it
  in `asyncio.run`).
- Rationale for defaulting to no-vision: DataDesk's semantic/labeled
  markup (Section 4) makes text-only reasoning usually sufficient; tested
  and confirmed (Section 7, the 10-record no-vision run: 89.44% field
  accuracy, 9/10 records fully matched).
- One data point suggesting vision *might* matter for checkbox fields
  specifically: one record's `newsletter` checkbox came out `False` when
  expected `True`, in a no-vision run — inconclusive from n=1, flagged as
  an open question in `LEARNINGS.md`, not resolved.

### 5.4 Rate-limit cooldown after a failed record

`RATE_LIMIT_COOLDOWN_SECONDS = 60` (module-level constant,
`agent/runner.py`). In `run_batch` (sequential, v1): after any record that
comes back `success=False` (except the last record in the batch), sleeps
60s before starting the next record, logging a clear
`⏳ ... pausing 60s ...` / `▶️ Cooldown finished ...` pair so it reads as
an intentional pause, not a hang. Same cooldown constant is reused inside
the vision-fallback function (5.3) before attempting Plan B, since a
Plan-A failure is often the same rate limit that would immediately reject
Plan B too.

**Bug found and fixed in this exact cooldown logic**: the fallback
function originally used `time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)` — a
**blocking** call — inside an `async def` function. Harmless under
sequential execution (each record has its own isolated `asyncio.run()`
event loop, nothing else competing for it), but a real bug under
concurrent execution (Section 6): a blocking `time.sleep` inside a
coroutine running under `asyncio.gather` freezes the **entire shared
event loop**, stalling every other concurrently-running record's agent
for the full 60 seconds, not just the one that failed. Fixed by changing
to `await asyncio.sleep(RATE_LIMIT_COOLDOWN_SECONDS)`. This fix has been
verified correct in an actual concurrent run (Section 7 — the 10-way
concurrent test confirmed 10 genuinely independent Chrome processes
progressing through their own steps with no freeze/stall observed).

---

## 6. Concurrency: v1 (sequential) vs v2 (concurrent) — current state

Two full implementations exist side by side in `agent/runner.py`,
switchable via a config flag, specifically so switching back requires no
code changes:

- **v1 — `run_batch(records)`** (sequential, the default/safe mode). Plain
  `for` loop calling `run_single_record` one at a time. **This is the
  currently active mode** (`config.CONCURRENT_MODE = False` as of the end
  of the last session).
- **v2 — `run_batch_concurrent(records)`** (concurrent). Calls
  `asyncio.gather()` over `_run_single_record_with_vision_fallback_async`
  for every record at once — every record gets its own isolated Chrome
  window (5.2) and they all run genuinely in parallel.

`config.py` defines `CONCURRENT_MODE: bool` (read from the `.env` var of
the same name, default `"false"`). `api/routes.py`'s `POST /run` handler
picks between them with one line:
```python
batch_fn = run_batch_concurrent if CONCURRENT_MODE else run_batch
batch_fn(records)
```
**As of the end of the last session, `.env` has `CONCURRENT_MODE=false`**
— the concurrent code path is fully implemented, tested, and confirmed
working *mechanically* (isolation, non-blocking cooldown), but is
**not currently active**, because it does not actually work end-to-end
under the current Gemini API tier (see Section 7 for why — it's an
external quota problem, not a code problem, confirmed twice).

### Why concurrency doesn't work right now (the actual finding, confirmed twice)

`browser-use`'s own bundled docs explicitly warn: "local Chrome is one
shared browser; parallel tasks fight over tabs and focus" — its
`BrowserSession` internally tracks a single `agent_focus_target_id` (one
"current tab" pointer). This project's isolated-profile-per-record
approach (5.2) sidesteps that specific problem entirely — confirmed
working, multiple truly independent Chrome processes with no
cross-contamination.

**The actual, confirmed blocker is the Gemini API's free-tier rate
limit, not browser/tab sharing.** The free tier has **two separate**
limits:
1. **15 requests/minute**, per model, shared across every concurrent call
   this process makes (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier`).
2. **500 requests/day**, per model, a separate hard daily ceiling
   (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) that a per-minute
   cooldown cannot fix — once hit, every call fails until the provider's
   own daily reset, regardless of how long you wait within the same day.

Each record costs roughly 12–15 Gemini calls (one per agent "step").
Concurrent agents all draw from the *same* per-minute quota — even a
handful running at once collectively exceeds 15/minute almost instantly.
**This was tested twice, on two different days (i.e. two different daily
quota states), and failed identically both times** — proving definitively
that a fresh daily quota reset does NOT fix concurrency, because the
per-minute cap (not the daily one) is what concurrency actually collides
with. See Section 7 for the exact test results.

**Sequential execution avoids this incidentally, not by design intent
alone**: one record's own natural pacing (page loads, dropdown waits,
~12-15 spaced-out API calls over ~60-90 real seconds) happens to stay
under 15/minute on its own. This is why v1 works reliably and v2 does
not, under the current (free-tier) API key.

**Revisit v2 only if/when the Gemini key moves to a paid tier** with a
meaningfully higher per-minute throughput — at that point, flip
`CONCURRENT_MODE=true` in `.env` and nothing else needs to change.

---

## 7. Complete test history with exact results

All batches below were run against DataDesk's real Supabase `people`
table (the project URL is in the DataDesk `.env`). Test
records consistently used the email pattern `firstname.lastname@testmail.dev`
for easy identification/cleanup.

| # | Date | Description | Mode | Records | Field accuracy | Root cause of any failures |
|---|---|---|---|---|---|---|
| 1 | Sep 24 | First working batch: Aditi Verma (India), Marcus Reid (US), Freya Nilsen (UK), Tomas Almeida (Brazil), Haruto Sato (Japan), Layla Haddad (UAE) | Sequential, vision on, original (pre-isolated-profile) `browser-use` default behavior — attached to the user's real Chrome | 6 | **100%** (108/108 fields) | n/a — clean baseline |
| 2 | Sep 24 | 10 India-named records (Rohan Deshmukh, Ananya Iyer, Vikram Chauhan, Priyanka Nair, Karan Malhotra, Sneha Joshi, Arjun Reddy, Meera Bose, Aditya Patel, Divya Krishnan) | Sequential — **run by the user themselves in VS Code**, not by the assistant directly | 10 | Not independently captured as a `BatchReport` in this session; confirmed all 10 landed correctly in Supabase by listing rows directly | n/a |
| 3 | Sep 24 | 10 Pakistan-named records, first concurrency attempt | **Concurrent (v2, first build)** | 10 | **0%** (0/180 fields), all 10 unmatched | `429 RESOURCE_EXHAUSTED`, per-minute cap (15/min) — every record failed to even submit |
| 4 | Sep 24 | Remaining 9 of the above Pakistan records (1 had already been separately inserted via an isolated diagnostic test after finding #3's cause) | Reverted to **sequential (v1)** | 9 | **74.07%** (120/162 fields), 2 unmatched (Usman Tariq, Ali Hussain) | The 2 failures were residual rate-limit pressure from the assistant's own debugging API calls just before the batch started; both succeeded when retried standalone minutes later with zero code changes |
| 5 | Sep 24 | 10 new records, countries chosen to all be valid DataDesk dropdown options this time (Liam Fraser/Canada, Giulia Romano/Italy, Wei Tan/Singapore, Camila Torres/Mexico, Jonas Weber/Germany, Chloe Lefevre/France, Ren Watanabe/Japan, Isla Campbell/Australia, Pablo Moreno/Spain, Soo-jin Park/South Korea) | Sequential, **vision off by default**, vision-on fallback added | 10 | **89.44%** (161/180 fields), 9/10 matched | Record 10 (Soo-jin Park) failed because the **daily** quota (500/day) was exhausted by that day's cumulative testing — confirmed via the exact error text, not assumed. One separate real accuracy issue noted: record 1's `newsletter` checkbox came out `False` vs expected `True` (possible vision-related edge case, inconclusive, n=1) |
| 6 | Sep 25 | 10 new records, all valid-dropdown countries again (Arjun Mehta/India, Emily Turner/US, Oliver Bennett/UK, Charlotte Wilson/Canada, Matteo Ricci/Italy, Hana Suzuki/Japan, Lucas Almeida/Brazil, Amara Ibrahim/UAE, Diego Fernandez/Mexico, Mei Lim/Singapore) | **Concurrent (v2, rebuilt with the async-sleep bug fixed)**, fresh day = fresh daily quota | 10 | **0%** (0/180 fields), all 10 unmatched | Same root cause as run #3: `429 RESOURCE_EXHAUSTED`, `GenerateRequestsPerMinutePerProjectPerModel-FreeTier`, limit 15/min. Confirms the daily reset is irrelevant to this failure mode — the per-minute cap is what concurrency actually hits, every time, regardless of day |

**As of the last verified check (end of run #4, Sep 24), the Supabase
`people` table held 27 rows**: 1 original manual seed record, 6 from run
#1, 10 from run #2, 1 diagnostic solo test (Bilal Ahmed), 9 from run #4.
**Run #6 (Sep 25) added zero new rows** (all 10 failed before submitting)
— so the table should still hold 27 rows unless something outside this
project's visibility changed it.

### Other notable findings during testing (not batch failures, but real discoveries)

- **`browser-use` 0.13.10 does not use Playwright** — see Section 3's
  "Important dependency-version fact." The project's own `README.md`'s
  setup instructions are stale on this point.
- **DataDesk's country dropdown doesn't include every real country**
  (confirmed: Pakistan is absent). When tested with an out-of-list value,
  the agent handled it gracefully — left the field blank rather than
  inventing a value or crashing — and the evaluator correctly isolated
  this as a single-field `value_mismatch` rather than failing the whole
  record. This is the system working as intended, not a bug.
- Gemini API cost/model notes: currently using `gemini-3.5-flash-lite`
  (Google's cheapest tier) on the **free** API tier. Discussed switching
  to Claude specifically for **text-only reasoning** (`use_vision=False`)
  — `browser-use` has built-in Anthropic support (bundled Claude-specific
  system prompts found in the installed package), would need
  `ChatAnthropic` swapped in for `ChatGoogle` plus an `ANTHROPIC_API_KEY`.
  Clarified for the user: a Claude.ai/Claude Code **subscription** and the
  **Anthropic Developer Platform API** (`console.anthropic.com`, what
  this integration would use) are billed **completely separately** — no
  subscription credit carries over; API usage would be a new, separate,
  pay-per-token cost. Not yet implemented — discussion only, decision
  deferred by the user.

---

## 8. Current concrete state (as of end of last session)

- `.env` (in `v1/`): `TARGET_FORM_URL=http://localhost:5173`,
  `SUPABASE_TABLE=people`, `MODEL_NAME=gemini-3.5-flash-lite`,
  `MATCH_KEY_FIELD=email`, `RESULTS_STORE_PATH=storage/results.json`,
  **`CONCURRENT_MODE=false`**. Real `GEMINI_API_KEY` and
  `SUPABASE_SERVICE_ROLE_KEY`/`SUPABASE_URL` values are present but not
  reproduced in this file.
- `sample_records.json` currently holds the 10 records from test run #6
  (Section 7) — Arjun Mehta, Emily Turner, Oliver Bennett, Charlotte
  Wilson, Matteo Ricci, Hana Suzuki, Lucas Almeida, Amara Ibrahim, Diego
  Fernandez, Mei Lim. **None of these landed in Supabase** (run #6 failed
  entirely) — safe to rerun as-is without creating duplicates.
- Both dev servers were running locally at the end of the last session
  (started manually via `nohup` in the background, logs at
  `/tmp/uvicorn_backend.log` and `/tmp/datadesk_dev.log`): DataDesk at
  `http://localhost:5173` (`npm run dev` in
  `testsites`), backend at `http://localhost:8000`
  (`uvicorn main:app --reload` in `~/Downloads/app`, venv activated).
  These do not persist across a machine restart/new terminal session —
  check `curl -o /dev/null -w '%{http_code}' http://localhost:5173` and
  `:8000` before assuming they're still up.
- The Gemini free-tier **daily** quota (500/day) was reported by the user
  as exhausted again at the end of the Sep 25 session (from test run #6's
  10-way concurrent attempt, ~130+ calls, on top of whatever else that
  key was used for that day) — so no further live Gemini-backed testing
  should be attempted until either the next daily reset or a tier
  upgrade.

---

## 9. Future direction (where this was headed when work paused)

The immediate next phase, as discussed but **not yet started**, is
evaluating whether to point this same automation thinking at the user's
actual company ERP, instead of (or in addition to) the DataDesk
proof-of-concept.

Key framing established in discussion (not yet acted on):

- **If the ERP has a real, write-capable API, prefer calling it directly
  over LLM-driven browser automation for actual data entry.** Direct API
  calls are faster, cheaper (no per-step LLM cost, sidesteps the entire
  rate-limit problem documented in Section 6), and deterministic. The
  LLM-driven browser-agent approach built in this project should be
  reserved specifically for parts of the ERP that have **no** API
  coverage, or genuinely visual/judgment-based tasks.
- **The user stated "I think we have an API"** for their ERP, but this is
  **unconfirmed/uninvestigated as of the end of the last session**. Open
  questions, not yet answered:
  1. Who told them there's an API, and where's the documentation
     (vendor site, admin panel, IT/ops contact)?
  2. Is it read-only, or can it write/create records too? (Many ERPs
     expose reporting/read APIs but gate actual data entry behind the UI
     only.)
  3. What kind of API — REST (JSON over HTTP), SOAP (older, XML), or
     something else — and does it need an API key/credentials?
  4. Which modules/data does it cover, and does that match the specific
     manual task the user wants to automate first?
- **No specific ERP task has been named yet** — the user has not yet said
  which manual task in their ERP they want to automate first (only that
  they want to explore "outsourcing simple manual tasks" to automation in
  general).
- Production concerns explicitly identified as unaddressed, should this
  move toward the real ERP (all currently out of scope for the DataDesk
  proof-of-concept, all would need addressing for real use): no auth on
  the automation service itself, no deployment story (everything runs
  locally), no retry logic beyond the basic catch-and-report + vision
  fallback, the rate-limit ceiling documented in Section 6 (would bind
  much harder at real ERP volume — hundreds/thousands of records, not
  batches of 10), and — given ERP data (inventory, orders, invoices) is
  much higher-stakes than test "People" records — likely a need for a
  human review/approval step before trusting a write, rather than only
  grading accuracy after the fact.

**If you are an LLM picking this project up to continue it**: the most
useful next action is almost certainly to help the user investigate their
ERP's actual API surface (the 4 open questions above) before writing any
new automation code, since the answer to "does a usable API exist"
determines almost the entire shape of what should be built next —
whether that's a direct API integration (likely far simpler and more
robust than anything built so far in this project) or an extension of the
existing browser-agent pattern to a new, real target.
