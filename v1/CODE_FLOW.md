# Code Flow

The code flow, start to finish, with file references.

## 1. Entry point

[main.py](main.py) creates the FastAPI app and mounts [api/routes.py](api/routes.py)'s router. Nothing else happens here — it's just wiring.

## 2. You call `POST /run`

[api/routes.py](api/routes.py)'s `run()` function is the orchestrator:
- Calls [data/loader.py](data/loader.py)'s `load_records(path)` → reads your JSON file, turns each object into a `Record` (from [data/models.py](data/models.py)), auto-generating a `record_id` if missing.
- Guards against a batch already running (`_status["state"] == "running"` check).
- Calls `run_batch(records)` from [agent/runner.py](agent/runner.py) — this is the whole "fill the form" phase.
- **Only after that fully finishes**, calls `verify_batch(records, supabase_client)` from [evaluation/evaluator.py](evaluation/evaluator.py) — the "check what actually happened" phase.
- Saves the result via [storage/results_store.py](storage/results_store.py)'s `save_report()`, and returns it as the HTTP response.

## 3. Filling the form — `agent/runner.py`

For each record, in sequence (not concurrent — see `LEARNINGS.md`):
- [agent/prompts.py](agent/prompts.py)'s `build_task_prompt(record)` turns the record's fields into a natural-language instruction ("enter these values, submit once, don't invent data...").
- A fresh, isolated Chrome window is spun up (unique temp profile dir).
- A `browser_use.Agent`, powered by Gemini (`ChatGoogle`), gets that task and drives the browser: reads the page, decides an action, clicks/types, repeats — this is the actual automation.
- Plan A runs with `use_vision=False` (text-only, cheap). If that fails, it waits, then retries once with `use_vision=True` (Plan B, the fallback).
- The window closes; result becomes a `RunResult` (agent's own self-reported success — logged, but not trusted).
- If a record failed, `run_batch` pauses 60s before the next one (rate-limit cooldown), logged clearly so it doesn't look like a hang.

## 4. Verifying — `evaluation/evaluator.py`

This runs completely independently of step 3, using no information from it:
- Pulls every row currently in the real Supabase `people` table.
- For each expected record, looks up a matching row by email (`_find_matching_row`).
- No match → every field marked `record_not_found_in_db`. Match found → compares field-by-field using [evaluation/normalizers.py](evaluation/normalizers.py)'s `values_match()`, which normalizes dates/phones/money/strings before comparing so formatting differences don't count as errors.
- Rolls everything up into a `BatchReport`: total fields, correct fields, `field_accuracy`, per-record breakdown.

## 5. You check it

`GET /status` gives you the in-memory progress; `GET /results` re-serves the last saved report from disk.

That's the whole loop: **JSON → agent fills DataDesk (Gemini-driven, isolated browser, sequential) → independent Supabase readback → graded report.** Nothing in step 4 ever reads step 3's opinion of itself — it only reads the database.
