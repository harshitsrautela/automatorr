# 08. Verification and the frontend

## Verification: how a run is graded

The agent's own "I succeeded" is **never** used for grading (`RunResult.success` is the agent's claim). After the batch,
the app reads the target's database and compares. Three modes, chosen per workflow in `verify_kind`:

| `verify_kind` | What it does | Use it for |
|---|---|---|
| `none` | no grading; the page says to trust the agent | quick experiments |
| `table` | v2's check: read the whole target table, match each record by the **match key** (case and whitespace insensitive), compare every field | form filling (People) |
| `expected_view` | v3's new check: compare what the run **wrote** to a **correct-answer view** | decision tasks (Credit Desk) |

### Why `expected_view` exists

For a decision task there is no input value to compare against. The record only has an id; the *right* decision is
something the site's database already knows. The Credit Desk has a SQL view `credit_requests_expected` that computes the
correct decision for every request. `view_verifier.py` reads two things for all record ids:

- `credit_requests.decision` (what the run clicked), and
- `credit_requests_expected.expected_decision` (what is correct),

and counts a record as correct when they match **and** the actual value is not empty. Reasons for a miss are
`not_decided` (nothing was saved) and `value_mismatch`.

Config keys it requires (the form on the Agent page has a box for each; "Use the Credit Desk check" fills them):

```json
{ "table": "credit_requests",  "key_column": "id",  "value_column": "decision",
  "expected_view": "credit_requests_expected",
  "expected_key_column": "request_id", "expected_value_column": "expected_decision" }
```

A record whose id is missing from the view counts as **unmatched** and is left out of the accuracy denominator.

### Credentials and the report

- The check needs a database login: a saved `db_credential` secret, or `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
  from `.env`. Read access is enough.
- The result is a `BatchReport`: totals, `field_accuracy`, unmatched count, and per-record per-field expected vs actual.
  It is stored in `runs.report_json`, and the Logs page shows it.
- Limits noted in the project's own bug list: the `table` check reads the table with one `select *` (so it hits
  Supabase's 1000-row cap), and **duplicate match keys can produce false passes**.

### A site with no database to read (AP Desk)

The built-in checks read a Supabase database. The AP Desk test site is a plain Node server with no database, so its workflow
uses `verify_kind: none` and is graded outside the app: `testsites/ap-desk/grade.py` compares the site's own
`GET /admin/state` with `expected.json`, which is computed from the raw data by rules independent of the site. The Logs page
then shows the run without an accuracy number; use `grade.py` for that.

### Reconciliation (v2 idea, only for `table`)

If `reconcile` is on, wrong fields are retried through the site's edit form with a live agent (`orchestrator.py`,
`run_reconciliation_pass`). Not used for decision tasks.

## The frontend

React 19 + Vite + Tailwind, in `frontend/`. Build once with `npm run build`; FastAPI then serves `frontend/dist` at
`http://localhost:8002`. For hot reload, `npm run dev` runs on port 5175 and proxies `/api` to 8002.

The sidebar has **two** windows, not three: the Settings window described in the older README no longer exists. Keys are
now handled inside the Agent page (and from `.env`), while the secrets API remains in the backend.

### Agent page (`pages/AgentPage.jsx`)

A task editor. Top to bottom:

1. **What should the agent do?** The task prompt, written like instructions to a colleague. It must include the website
   address and what to do for each row.
2. **Data**: paste JSON or CSV or upload a file. One row per thing to process; no data means the task runs once.
3. **Advanced**: model is not chosen here (new tasks get the default; saved ones keep theirs), plus:
   - browsers at the same time (concurrency);
   - **Show the browser while it works** (a checkbox at the bottom bar, the inverse of `headless`). It is locked off with
     the note "server has no screen" when the server runs with `FORCE_HEADLESS=1`;
   - **"Reuse what the agent learns on the first row"** (`use_cache`), with **Forget what it learned** (bumps the epoch);
   - **Check the results**: none / expected-view / table, with the Supabase URL and key, and the settings above.
4. **Save** and **Run** (optionally "first N rows").

Quality-of-life details that came from bugs: leaving with unsaved edits asks first; saving keeps hidden settings stored
(the page never overwrites a model or key it does not show); a new task starts from the Credit Desk example for now (a
"fill in the example" / "start blank" pair of buttons switches between them).

Task names are generated: the website plus a few words, for example `localhost:5174 · credit desk`, and a number is added
when two share a name.

### Logs page (`pages/LogsPage.jsx`)

Live view of any run, from the `run_events` stream. It polls "everything after sequence N" and groups lines per record:

| Event | Shown as |
|---|---|
| `batch_start`, `batch_done`, `note` | batch lines (for example "Learned a recipe...", "Deciding 99 records in 20 model calls", "Learned a plan...", "Checked the plan against the live agent on record X: the same values", "The plan is trusted", "The plan disagreed with the live agent...", "The plan was extended from the live agent's route...", "The plan answered its questions in N slot call(s) and M decision call(s) (groups of up to 5 records)") |
| `record_start` / `record_done` | one card per record with its mode (live, replay, recipe, **plan**), success and cost |
| `step` | a step: the agent's goal, its reasoning, and the actions it chose |
| `field_hit` / `field_miss` | a green tick (replayed or read) or a yellow warning (fell back) |
| `error` | red, with the reason |
| `verification_done` | the accuracy, plus expected vs actual per record |
| `reconciliation_*` | the edit-form retries (table checks only) |

Filter chips switch hits, misses, errors and steps on and off; record, answer and batch lines always stay.
The run list shows status and accuracy; a green dot on the Logs icon means a run is in progress.

### Things the UI deliberately hides

- Gemini keys are never displayed or typed on the page (only the database login for the check can be pre-filled, and only
  on this machine).
- The "check my prompt" feature (`POST /api/generate-prompt`) exists in the backend but is not on the page any more.
