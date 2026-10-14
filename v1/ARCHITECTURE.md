# Architecture

High-level picture of what this system is and how its pieces fit together.
Not a code walkthrough (see `CODEBASE.md` for that) — this is the "explain it
to someone in two minutes" version.

## The goal

Take a batch of data-entry records (a JSON file) and get them into a web
form automatically, using an LLM-driven browser agent instead of a
hand-written scraper/form-filler. Then, independently, check whether the
data actually landed correctly — without trusting the agent's own opinion
of how it did.

## The two projects

This is actually two separate codebases that work together:

1. **DataDesk** (`testsites`) — a React + Vite
   frontend backed by Supabase. This is the *target*: an ordinary "People
   Management" CRUD app with a form (Add Person), a table, filters, CSV
   export. It has no idea it's being automated — it's just a normal app.
   Its Settings page even says it was built to be agent-friendly (semantic
   HTML, stable element IDs, accessible labels).

2. **The automation agent** (this repo, `~/Downloads/app`) — a FastAPI
   backend that reads a records file, drives a browser to fill DataDesk's
   form once per record, then separately verifies the result against
   Supabase. This is the part we've been building and testing.

They're connected only through the browser (the agent literally clicks
around DataDesk like a person would) and through Supabase (both read the
same `people` table — DataDesk to display it, the evaluator to check it).

## End-to-end flow

```
sample_records.json
        │
        ▼
POST /run  (FastAPI)
        │
        ▼
load_records()  →  list[Record]
        │
        ▼
run_batch()  ──loops──▶  run_single_record()  (one at a time)
        │                        │
        │                        ▼
        │                build_task_prompt(record)
        │                        │
        │                        ▼
        │                browser_use.Agent  (LLM: Gemini)
        │                        │
        │                        ▼
        │                isolated Chrome window
        │                        │
        │                        ▼
        │                DataDesk form (localhost:5173)
        │                        │
        │                        ▼
        │                  Supabase `people` table  (INSERT)
        │
        ▼
verify_batch()  →  reads Supabase directly, compares vs. expected
        │
        ▼
BatchReport  (saved to storage/results.json, returned as the HTTP response)
```

Two things are deliberately decoupled here:

- **Filling the form** (the agent's job) and **checking the result** (the
  evaluator's job) don't share any state. The evaluator doesn't know or
  care what the agent *thinks* happened — it just reads the database.
- **Writing** (the whole `run_batch` loop) fully finishes before
  **verifying** starts (`api/routes.py` calls them one after another), so
  there's no race between "did the write land yet" and "let's check."

## Why the agent runs one record at a time, in its own browser window

Two decisions worth understanding, since they weren't the original design:

- **One isolated Chrome profile per record.** Rather than reusing a shared
  browser (which, by default, would mean actually taking over whatever
  Chrome you already have open), each record gets a throwaway, disposable
  Chrome window that closes itself when done. Safer, and a prerequisite for
  ever doing concurrent records properly.
- **Sequential, not concurrent.** We tried running multiple records at once
  (one browser window each) and it's technically fine on the browser side —
  but the Gemini API key's free tier has a shared rate limit (both
  per-minute and per-day) that concurrent agents blow through almost
  instantly. Sequential execution naturally paces itself under that limit;
  concurrency doesn't help when the bottleneck is an external quota, not
  CPU or browser capacity. See `LEARNINGS.md` for the details.

## Why the evaluator never trusts the agent

The agent (via `browser-use`) reports its own opinion of whether it
succeeded — but an LLM clicking around a page can be wrong, or can submit
a form with a typo'd email, or think it worked when it didn't. So
`evaluation/evaluator.py` re-reads the real Supabase table from scratch and
compares every field against what was expected, independent of anything
the agent claimed. A record can come back `agent said success=True` but
`matched_in_db=False` — that mismatch is itself a meaningful result, not a
contradiction to paper over.

## What's explicitly out of scope (for now)

No auth, no deployment, no retry logic beyond a basic catch-and-report, no
concurrency (see above), no UI beyond DataDesk itself. This is a
correctness-testing tool, not a production pipeline.
