# Codebase map

What each file does, what it imports from where, and what each function is
responsible for — at the "explain it to another engineer" level, not a
line-by-line walkthrough. Covers this repo (`~/Downloads/app`) plus a short
section on the separate DataDesk frontend at the end.

## Import graph (who depends on whom)

```
main.py
  └── api/routes.py
        ├── agent/runner.py
        │     └── agent/prompts.py
        │           └── config.py, data/models.py
        ├── data/loader.py
        │     └── data/models.py
        ├── evaluation/evaluator.py
        │     ├── evaluation/normalizers.py
        │     ├── config.py
        │     └── data/models.py
        ├── storage/results_store.py
        │     ├── config.py
        │     └── data/models.py
        └── config.py, data/models.py
```

`data/models.py` and `config.py` are the shared foundation — almost
everything imports one or both. Nothing imports `main.py` (it's the entry
point). `agent/runner.py` and `evaluation/evaluator.py` never import each
other — that separation is deliberate (see `ARCHITECTURE.md`, "why the
evaluator never trusts the agent").

## `main.py`

The FastAPI app itself. Creates the `FastAPI` instance, mounts the router
from `api/routes.py`, and defines one throwaway `GET /` health-check
endpoint. Nothing else lives here on purpose — all real logic is in the
other modules.

## `config.py`

Single source of truth for every environment variable, loaded once at
import time via `python-dotenv`. Two things worth knowing:

- `_require(name)` — a small helper that raises immediately with a clear
  message if a required env var is missing, instead of letting `None`
  silently propagate into, say, a Supabase client constructor and fail
  somewhere confusing later.
- Everything here is a module-level constant (`GEMINI_API_KEY`,
  `SUPABASE_URL`, `TARGET_FORM_URL`, `MATCH_KEY_FIELD`, etc.) — other
  files just `from config import WHATEVER_THEY_NEED`.

## `data/models.py`

Pydantic models shared across the whole app — the "nouns" everything else
operates on:

- **`Record`** — one input row. Only `record_id` is a fixed field; every
  other key is allowed (`extra="allow"`) since the actual field set comes
  from whatever JSON file you load, not a hardcoded schema. `field_dict()`
  returns everything except `record_id` — what actually gets typed into
  the form and compared against the database.
- **`FieldResult`** — one field's expected-vs-actual comparison outcome.
- **`RunResult`** — did the *agent* think it succeeded on one record
  (`success`, self-reported, explicitly not trusted for grading).
- **`RecordVerification`** — did the *evaluator* find a matching database
  row for one record, and how did each field compare.
- **`BatchReport`** — the whole batch's rollup: total fields, correct
  fields, `field_accuracy`, and the list of `RecordVerification`s. This is
  what `POST /run` ultimately returns and what `GET /results` re-serves.

## `data/loader.py`

One function: `load_records(path) -> list[Record]`. Reads a JSON array
from disk, validates it's actually a list, and synthesizes a `record_id`
from the array index for any row that doesn't already have one. Raises
`FileNotFoundError` / `ValueError` on bad input — `api/routes.py` catches
those specifically and turns them into a 400, not a 500.

## `agent/prompts.py`

One function: `build_task_prompt(record) -> str`. Turns a `Record` into
the natural-language instructions the browser agent actually executes:
lists every field/value to enter, plus a fixed set of behavioral rules
(wait for dropdowns to render, don't skip fields, don't invent values,
submit once and stop — don't retry submission repeatedly). Kept separate
from `runner.py` deliberately, since this is the file you'd iterate on
most if the agent's behavior needs tuning, without touching execution
logic.

## `agent/runner.py`

The part that actually drives the browser. Three layers:

- **`_build_llm()`** — constructs the `ChatGoogle` (Gemini) client from
  `config.py`'s `GEMINI_API_KEY` / `MODEL_NAME`.
- **`_run_single_record_async(record, *, use_vision)`** — the core unit of
  work. Builds the task prompt, creates a fresh `browser_use.Agent` inside
  a throwaway Chrome profile (a temp `user_data_dir`, cleaned up
  afterward), runs it, and wraps the outcome in a `RunResult`. Any
  exception here is caught and turned into a failed `RunResult` rather
  than propagating — one bad record can't crash the batch.
- **`_run_single_record_with_vision_fallback_async(record)`** — tries the
  above with `use_vision=False` first (cheaper; DataDesk's semantic markup
  makes this usually sufficient). If that didn't succeed, waits out a
  cooldown and retries once with `use_vision=True` as a fallback.
- **`run_single_record(record)`** — sync wrapper around the above
  (`browser-use`'s `Agent.run()` is async; FastAPI's sync route calls into
  it via `asyncio.run`).
- **`run_batch(records)`** — loops over records **sequentially** (see
  `ARCHITECTURE.md` / `LEARNINGS.md` for why not concurrent), calling
  `run_single_record` one at a time. After any failed record (except the
  last), sleeps `RATE_LIMIT_COOLDOWN_SECONDS` (60s) and logs clearly that
  this is an intentional pause, not a hang, before continuing.

## `evaluation/normalizers.py`

Field-comparison logic, kept separate from the actual comparing
(`evaluator.py`) because normalization rules change independently of
comparison flow. Core idea: two values can be "the same" while being
different strings (a date vs. a timestamp, `"$75,000"` vs `75000.0`).

- **`infer_field_type(field_name)`** — guesses a type (`date`, `phone`,
  `money`, `email`, or `default`) from keyword hints in the field's name
  (e.g. anything with "salary"/"price"/"cost" in it → `money`). Can be
  overridden per-field via `FIELD_NAME_OVERRIDES` if the guess is wrong.
- **`normalize_date` / `normalize_phone` / `normalize_money` /
  `normalize_email` / `normalize_string`** — one normalizer per type, each
  collapsing formatting differences into a canonical form (dates → ISO
  `YYYY-MM-DD`; phone → digits only, US country-code stripped; money →
  plain float; everything else → trimmed/lowercased string).
- **`values_match(field_name, expected, actual)`** — the single entry
  point `evaluator.py` calls: infers the type, normalizes both sides,
  compares.

## `evaluation/evaluator.py`

Independent verification — the "does not trust the agent" half of the
system.

- **`_fetch_db_rows(supabase_client)`** — pulls every row from the target
  table in one query.
- **`_find_matching_row(record, db_rows)`** — looks up the row matching
  this record via `MATCH_KEY_FIELD` (default `email`), case/whitespace
  insensitive.
- **`_compare_record(record, db_row)`** — if no row matched, marks *every*
  field failed with reason `record_not_found_in_db` (distinct from an
  individual field being wrong on a row that *was* found). Otherwise,
  compares field-by-field via `values_match` and tags each failure
  `value_mismatch`.
- **`verify_batch(records, supabase_client)`** — orchestrates the above
  across the whole batch and assembles the final `BatchReport`
  (`field_accuracy = correct_fields / total_fields`).

## `storage/results_store.py`

Trivial persistence: `save_report(report)` writes the last `BatchReport`
to a flat JSON file (path from `config.RESULTS_STORE_PATH`);
`load_last_report()` reads it back, or returns `None` if nothing's been
saved yet. Exists purely so a crash or server restart doesn't lose the
last batch's results.

## `api/routes.py`

Wires everything above into three HTTP endpoints.

- **`POST /run`** — the main entry point. Loads records (`data/loader`),
  refuses to start if a batch is already running (in-memory `_status`
  guard), calls `run_batch` (agent) then `verify_batch` (evaluator) *in
  that order*, saves the report, and returns it. Any exception here is
  caught and turned into a 500 with the batch marked `error` in `_status`.
- **`GET /status`** — returns the in-memory `_status` dict (`state`,
  `current`/`total` counts, last `error`). Note: `current` currently jumps
  straight to `total` at the end rather than incrementing per-record — a
  known, accepted limitation (see the comment in the file), not a bug to
  silently "fix" without deciding whether per-record progress is actually
  worth the added complexity.
- **`GET /results`** — re-serves the last saved `BatchReport` via
  `storage/results_store.load_last_report()`, 404 if nothing's been run
  yet.

---

## The other repo: DataDesk frontend (`testsites`)

Single-file React app, `src/App.jsx` — everything (state, form, table,
modals) lives in one component tree, no separate routing. Worth knowing at
a glance:

- **`loadPeople()`** — fetches all rows from Supabase (or falls back to
  `localStorage` if Supabase env vars aren't set — "local demo mode").
- **`submitForm(event)`** — validates the form (`validateForm`), then
  either inserts or updates a row in Supabase depending on whether
  `editingPerson` is set.
- **`deletePerson(person)`** — deletes a row by id.
- **`exportPeople()`** — builds a CSV client-side from whatever's
  currently filtered and triggers a download.
- **`validateForm(form)`** — required-field and basic email/number
  validation before allowing submit.
- Dropdown option lists (`COUNTRIES`, `DEPARTMENTS`, `EMPLOYMENT_TYPES`,
  `GENDERS`) are hardcoded constants near the top of the file — this is
  what limits which values the automation agent can successfully select
  (see `LEARNINGS.md`).

`src/lib/supabase.js` (a duplicate, unused Supabase client) was removed
earlier — `App.jsx` builds its own client inline with a null-fallback
guard for when env vars aren't configured.
