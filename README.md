# terminautomator

A browser-automation agent for repetitive web work: do record 1 with an AI, then do the other records cheaply and correctly.
This folder holds every version built so far, plus a bench that compares v3 with other tools. **The versions are experiments in
sequence, not a set of finished products.** The honest state of each is below.

| Folder | What it is | State |
|---|---|---|
| `v1/` | The baseline: every step of every record is a live AI call (browser-use + Gemini), behind a small FastAPI app (port 8000). | Frozen. Works, but about 15 AI calls and a minute per record. Not maintained. |
| `v2/` | v1 plus a cache that records the first record's clicks and replays them for the rest (port 8001). | Frozen. Fine for "type these values into a form". Wrong for tasks that need reading and deciding: on the AP Desk it scored 1 of 10, replaying one record's clicks for all and reporting false successes. Not maintained. |
| `v3/` | The current work: a web app (FastAPI + React + Postgres, port 8002) with a recipe engine and a **plan engine** that learns a route from one live record, proves it against the live agent, then runs the rest in code with small batched AI questions. | Works on the in-house test sites: 100/100 on the Credit Desk and AP Desk, 14/14 to 16/16 on small Returns and Expense runs. **Not production-ready**, see below. |
| `testsites/` | The five web apps the agent is tested against, each with a hidden answer key and a grader. The four Node sites need no database or keys. | Bundled; see `testsites/README.md`. |
| `bench/` | The harness for the tool comparison: one call-counting proxy, the same 5 invoices, a grader. Results in `bench/RESULTS.md`. | Done for one site (AP Desk). |
| `v4-stagehand/`, `v5-skyvern/`, `v6-webwright/`, `v7-browser-use/` | Other tools set up for the comparison. | Stagehand and Skyvern ran (0 of 5 each on the cheap model); Webwright and plain browser-use were set up but not run. |
| `V3_PLAN.md` | The original plan for v3 (long, from 5 Oct). | Historical. |

## Where v3 is weak

- **Only in-house sites have been tested.** Not one real-world site has been run, and the Hazards site (login, popups, iframe
  form) was only tested offline.
- **The live agent is the referee and it is sometimes wrong.** A right plan can be thrown away, and a record can be marked done
  when nothing was saved.
- **Decision programs** (the AI writes the rules once, code decides with no AI calls) have never been learned in a live run. They
  are switched off by default (`TA_FEATURES=all` turns them on). About 900 tests pass, but they use a fake AI.
- **Quota:** a free Gemini key allows about 500 calls a day per model per Google project, and each live run costs 100 to 430. The
  test model is `gemini-3.5-flash-lite`, a light one. A stronger model and paid limits are the first thing to try.
- **Size:** about 13,800 lines of engine grown by patches (`orchestrator.py` 2,260 lines, `recipe/plan_exec.py` 1,820). A
  from-scratch rebuild (`v4`) is planned and has not been started.

## Where to start reading

1. `v3/docs/01_SUMMARY_SHORT.md`: what happened in v3, 10 minutes.
2. `v3/docs/problems.md`: every problem met, grouped by source.
3. `bench/RESULTS.md`: v3 against Stagehand and Skyvern.
4. `v3/docs/02_PLAN_SHORT.md`, then `04_PLAN_DETAILED.md`: the rebuild plan.
5. `v3/docs/03_WHAT_HAPPENED_FULL.md`, `05_PROJECT_STORY.md` and `v3/LEARNINGS.md`: the long account and the bug list.

## Getting started

Needs Python 3.11+, Node.js 20+, Google Chrome, a PostgreSQL database (a free Supabase project works) and a Gemini API key.

```bash
git clone <repository-url> terminautomator && cd terminautomator
testsites/start.sh                                  # the four dependency-free test sites, ports 5180 to 5183

cd v3
cp .env.example .env                                # set DATABASE_URL and GEMINI_API_KEY
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && playwright install chromium
(cd frontend && npm install && npm run build)
uvicorn main:app --port 8002                        # http://localhost:8002
```

In the app, create a task on the Agent page (instructions, the site's address, the records from `testsites/<site>/records.json`),
run it, and watch it on the Logs page. After a run, `python3 testsites/<site>/grade.py` scores what the site saved against the
answer key. `v3/README.md` has the full setup, and the server loads code only at start, so it must be restarted after changes.
Live runs use the Gemini quota: start with 8 to 16 records.

## Secrets

Real keys live only in each folder's `.env`, which is git-ignored (see `.gitignore`), as are `storage/master.key` and the local
databases. `.env.example` files hold placeholders only. `v3/storage/master.key` encrypts the secrets stored in the app's
database: without it they cannot be read, so keep a copy.
