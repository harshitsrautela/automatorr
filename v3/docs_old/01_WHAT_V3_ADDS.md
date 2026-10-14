# 01. What v3 adds

## v1, v2, v3 side by side

| | v1 | v2 | v3 |
|---|---|---|---|
| Task | Fill DataDesk's People form | Same | **Anything you can describe**, set from a web page |
| How the browser is driven | browser-use + Gemini, every step live | Replay cached clicks with Playwright, live agent only on a miss | Those, plus **recipes** (read-and-decide) and **plans** (the general case), each proven before use |
| Where settings live | `.env` and code | `.env` and code | **Database** (Settings are per workflow) |
| Where state lives | files | `cache.sqlite3`, `results.json`, `Logs.md` | **PostgreSQL** (Supabase) |
| API keys | `.env` | `.env` | Encrypted in the database, plus `.env` fallback, **several keys pooled** |
| Starting a run | `curl POST /run` | `curl POST /run` | A **web app** (Agent and Logs pages) |
| Grading | Compare fields to a DB table | Same | Table check **or** "compare to a correct-answer view" |
| Browser windows | visible | visible | Visible or **headless**, per workflow or forced for the server |
| Safety on saves | none | wait for a configured request | **Change tracker**: watches every save, no setup |

## The four things that are actually new (everything else is plumbing)

### 1. The product shell
A FastAPI server plus a React app. You create a task, paste or upload data (JSON or CSV), write the instructions,
optionally set up a check, and press Run. Logs stream into the Logs page while it works. Details: docs 02, 03, 08.

### 2. The recipe engine
The v2 cache assumes a form: *type this value into that field*. The same locator works for every record because the
**data** decides what to type. The Credit Desk breaks that assumption. The page you open, the retailer text you read and
the button you click all depend on **what you read**, not on the record's fields. A selector cache is useless there.

The recipe splits a task into two jobs:

- **Where** (stable, so cache it): which pages to read, which button to press at the end. Learned from the AI's own work
  on the first record.
- **What** (varies, so decide it cheaply): which of Approve / Reject / Hold. Decided by a small model reading the page
  text, five records per call, with code doing arithmetic and rule order.

Result on the Credit Desk: about 900 AI calls for 100 records become about 66, and time drops from about 21 minutes
(for only 36 records) to about 6 minutes for 100. Details: doc 05.

### 3. The plan engine (the general cache)
The recipe only fits one shape. A second test site (AP Desk: a three-way invoice match, then a form with computed values)
broke both it and the old click replay: **1 of 10 correct**, with the replay quietly re-submitting the first record's page
for the others. The plan engine is the answer: it learns the agent's route, and marks each varying value by its source
(copied from the record, read off an earlier page, or worked out). Replayed steps are free; the values cost about 3 small
model calls per record instead of about 15 agent steps. A new plan is **dry-run against the live agent on the next 2 to 3
records and only trusted if the values match**; the old click replay now refuses when it would repeat the first record.
Details: doc 06.

### 4. The "a success is only a claim" layer
The first Credit Desk runs showed a record that said *done* when nothing had been saved. v3 therefore:

- watches every save the browser sends (POST / PUT / PATCH / DELETE) and refuses to call a record done if its last save
  failed (doc 07);
- grades the whole run against the site's own answer view, so a quietly wrong click shows up as a lower accuracy number
  (doc 08).

## What v3 inherits from v2 unchanged (not re-explained here)

| Idea | Where it lives | Explained in |
|---|---|---|
| Cold run teaches, warm run replays (now guarded by a safety check, doc 06) | `cache/`, `replay/`, `orchestrator.py` | `v2/docs/02_HOW_CACHING_WORKS.md` |
| Element fingerprints and locator tiers | `cache/fingerprint.py`, `replay/engine.py` | same |
| Per-field live fallback and self-healing | `orchestrator.py` (`_run_replay`, `_run_live_fallback_step`) | same |
| Circuit breaker per field | `orchestrator.py` (`_circuit_*`) | same, Part 4 |
| Reconciliation pass (fix fields via the edit form) | `orchestrator.py` | same, Part 6 |
| Concurrency by a pool of CDP ports | `orchestrator.py` (`_run_many_concurrent_async`) | `v2/docs/04_ARCHITECTURE.md` |

Everything v3 changed inside those pieces is called out in the docs that follow.

## What did *not* change in spirit

The live browser-use agent is still the permanent fallback. The recipe and the plan never replace it; they only remove it
from the cases where it is wasteful. If a recipe step fails **before** the click, that record falls back to the live agent. If it
fails **after** the click, the record is marked failed and never retried, because retrying could approve or reject twice.
