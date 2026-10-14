# terminautomator v3

A browser agent you configure from a web app: what to do, where, on which records, with which keys. No task settings live in code or `.env`.

## Requirements

- Python 3.11 or newer, Node.js 20 or newer, and Google Chrome (the live agent drives it).
- A PostgreSQL database for the app's own data (secrets, tasks, runs, logs, learned steps). Any PostgreSQL works; a free
  Supabase project is one option.
- One or more Gemini API keys. The free tier allows about 500 calls a day per model per Google project, so keys from different
  projects add quota; a paid key removes the limit.

## Set up (once)

```bash
cd v3
cp .env.example .env     # then set DATABASE_URL and GEMINI_API_KEY (see below)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt          # or requirements-lock.txt for the exact versions this was developed with
playwright install chromium
(cd frontend && npm install && npm run build)
```

**Database.** Put a connection string in `DATABASE_URL`. For Supabase: create a project, click **Connect**, choose **Session
pooler**, copy the URI and put the database password in it. For a local database, any empty PostgreSQL database works. The tables
are created on first start, with row-level security on; on Supabase this also keeps the public API key from reading them.

**Keys.** `GEMINI_API_KEY` in `.env`, plus `GEMINI_API_KEY_2`, `_3`, ... to spread load; the agent switches between them.

## Run

```bash
uvicorn main:app --port 8002
```

Open http://localhost:8002. The server loads code only at start: restart it after any code change.

Tests that need no browser, model or site: `python tests/test_plan.py --offline` (and the other `tests/test_*.py`). Tests of the
database layer need a throwaway PostgreSQL: `TEST_DATABASE_URL=postgresql://... python tests/test_backend.py`.

### Optional: a limited database login

To keep the app's tables apart from other data in a shared Supabase project, create a limited login that owns a private schema
(`agent_state`, not exposed through the project's public API, row-level security on) and use it in `DATABASE_URL`. To remove
everything the app created: `drop schema agent_state cascade; drop role <that login>;` (as the project's admin).

## The two windows

- **Agent**: create a task (plain-language instructions with the website address, your data, run settings, and how to check the results), then run it. Saved tasks are listed under Agent in the sidebar.
- **Logs**: every run, live: each record's steps, the agent's reasoning and final answer, and a verdict from verification (accuracy against the correct answers).

Gemini keys are set in `v3/.env` (`GEMINI_API_KEY`, plus `GEMINI_API_KEY_2`, `_3`, ... to spread load; the agent switches between them). The database login used to check results is entered on the Agent page under "Check the results" and stored encrypted; it can never be read back. There is no separate Settings page any more.

## Notes

- Local use only for now: there is no login. Do not expose the port to a network.
- One run at a time.
- Secrets are encrypted before they reach the database, with a key in `storage/master.key` (created on first use) or `SECRETS_ENCRYPTION_KEY`. Lose that key and saved secrets are unreadable, so back it up.
- `FORCE_HEADLESS=1` in `.env` (or in front of the `uvicorn` command) hides every browser (for servers with no screen).
- Model calls are paced under Gemini's per-minute limit: `GEMINI_RPM` (default 14 calls per key and model per minute; `0` turns
  pacing off, for paid keys). Extra browsers wait for room instead of getting "429" answers (doc 07).
- Gemini's free daily quota is per Google project (not per key) and per model, and it resets at midnight Pacific time (12:30 pm IST until 1 Nov, 1:30 pm IST after), not at 00:00 UTC. Extra keys only add quota if they come from different projects. Before the reset, a project used heavily the day before may already be spent and the run falls back to the second model; the key pool computes the reset from the real Pacific clock, so it retries the primary model by itself afterwards.
- Frontend development with hot reload: `cd frontend && npm run dev` (port 5175, proxies `/api` to 8002).
- The plan and its reasoning are in `../V3_PLAN.md`.

## Docs

`docs/` has the current documentation, in reading order:

1. `01_SUMMARY_SHORT.md`: what happened in v3, short version.
2. `02_PLAN_SHORT.md`: the rebuild plan (v4), short version.
3. `03_WHAT_HAPPENED_FULL.md`: the full account of the v3 work, with every number and lesson.
4. `04_PLAN_DETAILED.md`: the step-by-step rebuild plan (steps A1 to J7).
5. `05_PROJECT_STORY.md`: a self-contained narrative of the project.
6. `problems.md`: every problem met along the way, grouped by source.

`docs_old/` holds the earlier numbered docs (00 to 13). `docs_old/10_RUNNING_AND_FRESH_TESTS.md` says how to start everything and
how to do a truly fresh test (clear the learned cache, reset the test site, then run). `LEARNINGS.md` lists every bug and its fix.

## Test sites

The sites below are bundled in `../testsites/` (see its README). The four Node sites need no database or keys: `../testsites/start.sh`.

- **Credit Desk** (`testsites/credit-desk`, port 5174): read a ledger and click Approve / Reject / Hold. 100% accuracy.
- **AP Desk** (`testsites/ap-desk`, port 5180): a held-out, differently built site (three-way invoice match, a form with
  computed values). The first attempt scored 1 of 10; after the plan engine, **100 of 100** with about 126 AI calls and about 10
  minutes (the live agent would need about 1,500 calls and 70 minutes). See `docs_old/06_THE_PLAN_ENGINE.md` and `docs_old/09_RESULTS_AND_LESSONS.md`.

- **Returns Desk** (`testsites/returns-desk`, port 5181): a third, deliberately hard site (paging with no search, a branching route, a
  19,600-character policy page, a custom calendar). Decisions score 100% offline; on 9 Oct a fresh live run of 8 records scored
  **8 of 8**, with the plan learned from the live agent's own trace, proven, and extended to a branch it had not seen. Two
  16-record runs then scored 15 of 16 (a trusted plan guessed a restocking fee it could not see) and, after **trust per kind of
  record** was added, 14 of 14 with two guesses caught before saving.
- **Expense Desk** (`testsites/expense-desk`, port 5182): a fourth site built like a modern web app (hash routes, a type-to-filter
  search, tabs, collapsed sections, a three-step wizard with radio buttons and checkboxes, control ids that change on every render).
  Fresh live runs on 9 Oct: **8 of 8**, **16 of 16**, **8 of 8** and **8 of 8** (see doc 09).

- **Claims Desk** (`testsites/claims-desk`, port 5183, added 10 Oct): a fifth site with the hazards of real web apps (a sign-in that
  runs out, a cookie banner and a pop-up, an endless list that sometimes fails, a decision form inside an iframe made of ARIA
  widgets and searchable comboboxes). Tested only with a scripted trace and a fake model so far; **no live run yet**.

See `docs_old/06_THE_PLAN_ENGINE.md` and `docs_old/09_RESULTS_AND_LESSONS.md` for how and why; `docs_old/11_LEARNINGS_9_OCT.md` for what the two
newer sites taught; `docs_old/12_GENERAL_PURPOSE_ROUND.md` for the 10 Oct round.

## Experimental parts (off by default)

A second round of work added: decision programs (the model writes a kind of record's decision once as a small program, and code
decides later records with no model call), coverage and pruning of the pages read, read-back of saved records, and slot code.
All were tested with fake models (about 900 checks) but did not hold up in live runs (12 to 15 of 16 records, against 16 of 16
for the engine without them), and no decision program was ever learned live. They are switched off; `TA_FEATURES=all` turns them
on. Always on: pacing under the per-minute limit, grounding (a number, date or quote the model uses must be on the pages), and
handling of sign-ins, pop-ups, iframes, endless lists and custom widgets. See `docs/problems.md` and
`docs_old/12_GENERAL_PURPOSE_ROUND.md`.

## More

- `LEARNINGS.md`: the bugs fixed in v3 and how the Credit Desk test reached 100% accuracy.
- `AGENT_SUMMARY.md`: the earlier experiment results and the bugs still open.
- `FUTURE_PLANS.md`: what is not built yet.
