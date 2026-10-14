# v3 future plans

Things discussed and deliberately **not built yet**. This file is the v3 counterpart of `v2/docs/FUTURE_PLANS.md`
(v3 no longer carries copies of the v2 docs; they stay in `v2/docs/`; this one is the live list). Items carried over from v2 keep their v2
number so the original reasoning is easy to find. The long-form reasoning behind the v3 design is in
`../V3_PLAN.md` (risk IDs like R-13 refer to its bug register).

The full test results and the ranked bug list (with what was reproduced live) are in `AGENT_SUMMARY.md`.
The bugs fixed since then and how accuracy reached 100% are in `LEARNINGS.md`.

Priority: **P1** = hurts real runs now, **P2** = next big step, **P3** = later / nice to have.

---

## 0. What the first two real runs taught us

Task: Credit Desk (36 requests, the agent reads the Retailer Ledger in the UI and clicks Approve / Reject / Hold).

| | Run #1 | Run #2 |
|---|---|---|
| Browsers at once | 2 | 1 |
| Finished | 33 of 36 (stopped by a server restart) | 36 of 36 |
| Agent-reported failures | 24 | 0 |
| Correct (database check) | 8 of the 9 that landed | **35 of 36 = 97.2%** |
| Time | ~6 min before it stopped | 21 min 36 s (avg 36 s per record, 8.3 AI steps) |
| Gemini 429 errors | 802 | 268 |
| Retries with screenshots | 27 | 3 |

- **The failures in run #1 were not the agent's logic.** They were Gemini rate limits (429 RESOURCE_EXHAUSTED), made
  worse by two browsers and by the automatic "retry with screenshots", which doubles traffic exactly when the key is
  already throttled. → items A1, A2.
- **The one miss in run #2 was a false success (record #18).** The agent decided the right answer, a click did not
  register (and 429s swallowed the steps after it), it wrote "decision is Approve" and finished. Nothing was saved.
  The end-of-run database check caught it. → item A3.
- **The agent really does read the ledger UI.** It opened the ledger page in 27 of 27 early records, and the numbers it
  quoted matched the real database in 24 of 25 (the other was a Blocked retailer with nothing to quote). The
  "near-miss name" trap (#12) was handled correctly in run #2 and wrongly in run #1.
- **Caching did nothing here, by design.** Every step depends on what the agent reads (which page, which retailer,
  which button), not on the record's fields. → section B.

---

## A. Reliability and cost (P1)

### A1. Handle Gemini rate limits properly  *(P1)*
- **Today:** a 429 makes browser-use retry immediately up to 6 times, then the record fails. Only the *daily* quota
  message stops the batch.
- **Do:**
  - Never run the "retry with screenshots" pass after a 429 (it doubles load when the key is throttled).
  - On a 429, wait (honour `retryDelay` if the error gives one, else 30-60 s) and retry the same attempt.
  - A **requests-per-minute** setting under Advanced that paces LLM calls across all browsers (a shared limiter),
    so a free-tier key stays under its limit.
  - Optionally lower concurrency automatically when 429s appear.
- The reason shown for a failed record already says "Gemini rate limit reached (429…)" (done).

### A2. Bound the cost of a bad run  *(P1)*
Per-record and per-run AI-step budgets, a cap on consecutive failures that stops the batch with a clear message,
and a cost/time estimate before pressing Run (rows × typical steps). (R-28)

### A3. Catch false successes  *(P1)*
The agent can say "done" without the click having worked.
- Cheapest: tell users to end their prompt with "check the row now shows Approved/Rejected/On hold; if not, click
  again". Worth putting in the example prompt and in a prompt-help panel.
- Better: an engine-level **post-condition**, e.g. after the agent finishes, look for a user-given success text or
  selector; or an optional second, cheap check ("re-open the row and read its badge").
- The Logs page could flag "agent said done but the check disagrees" per record (it already shows expected vs actual
  after verification).

### A4. Logs: skipped records  *(P3)*
When a run stops early (bad key, quota), the records never started have no card. Emit a `record_done` (skipped) event
for each so the counts and the list add up.

### A5. Thinking-effort tuning  *(v2 item 11, P3)*
Still true: the configured model's name matches none of browser-use's checks, so no thinking level is sent and Gemini
uses its own default. Make it a deliberate per-call setting; escalate on failure (together with the vision retry).
Worth revisiting because the Credit Desk result (97%) came from whatever default is in effect, and a stronger model
may be needed for harder judgment.

### A6. Prompt/context caching for the constant part of live calls  *(v2 item 10, P3)*
Mark the constant prefix (system prompt + task) as cacheable in the Gemini API. Small win; larger now that
decision-style tasks make many live calls per record.

---

## B. Making repetitive tasks cheap (P2, the big one)

You are right that the *procedure* is repetitive (read the id, look up the retailer, apply the same rules, click one
button). The current cache is a **selector replay** keyed on record fields, so it cannot use that repetition: the page,
the retailer text and the final button change per row, and none come from the data. Three levels:

1. **Locators that use the record's value.** Learn "the button labelled *Approve request {id}*" (the test site already
   exposes `aria-label` and `data-request-id`) and substitute the new id on replay. Covers "find row N and click its
   button" but not the reading and deciding in the middle. (Related: R-13, ambiguity when two fields hold the same
   value; read the form back before submitting.)
2. **Decide in code, click in the browser.** Compute the decision outside the browser (the
   `credit_requests_expected` view is exactly that), make the data `{id, decision}`, and the task becomes "click the
   {decision} button on row {id}". With level 1 this replays with zero AI calls, but the agent no longer reasons.
3. **Learn once, compile to a script.** The AI does the first few rows and writes a browser script for the whole
   procedure including the if-else. The script is **checked against the AI's own answers** on a few more rows; if they
   agree it runs the rest with no AI, and falls back to the AI for any row that breaks it. This is the real "final
   agent" feature and the lasting fix for rate limits. It needs care: it runs generated code (restrict it to browser
   actions), and it only pays off on large batches.

**Recommended order:** A1-A3 first, then level 1 as a stepping stone, then level 3.

**Built so far (a first version of level 3, in `recipe/`):** the AI does the first record; its trace is compiled into a
recipe (pages to read, and the final button found by its record-specific label, with sibling buttons discovered on the
page). Later records open the pages, get a decision (up to 5 records per model call, in a strict JSON format) and click.
Falls back to the live AI when a step fails before the click. Tested on the Credit Desk only: 10/10 correct, about 7 model
calls for 10 rows. Later additions: the model writes each rule as a small formula and code evaluates them (arithmetic and rule order are
no longer the model's job; the button comes from the user's own rule line), a lookup step searches the pages for the
record's identifying details as exact text, all records' decisions are made in chunks of 5 with up to 3 calls at once, lists
are paged through with Next, and a change-request tracker waits for saves and fails a record whose last save was refused.
Fresh 100-record runs scored 99/100 three times (about 4.5 minutes, 3 browsers, about 7 AI calls on records plus about 20
decision calls and 20 lookup calls). Known limits: only the
"read pages, decide, click one of several buttons" shape, no shadow check against the AI, and the save-request check is not
detected automatically for new tasks.

### B1. Optional lookup tool for the agent  *(P3, discussed)*
Today the agent only reads the UI. A "get this retailer's ledger row" tool (database query) next to the browser would
cut ~3-4 steps per row and make numbers exact, at the cost of no longer proving the agent can read the UI, and it only
works when you have database access. Make it opt-in per task.

### B2. Broader action coverage  *(v2 item 1, P2)*
Only click, type, native `<select>` and checkbox are cacheable. Radio groups, date pickers, multi-selects, toggles,
drag-and-drop and file upload always fall back to the live agent.

### B3. Locator robustness  *(v2 items 3 and 6, P2)*
Add tiers beyond xpath + one attribute: visible text, ARIA role, compound attributes, shadow DOM, iframes. Needed
before pointing the cache at sites we do not control.

### B4. Multi-step / wizard forms  *(v2 item 5, P3)*
The cache assumes one page load, fill everything, submit.

---

## C. Product and UX

### C1. "Check my prompt"  *(P2)*
The generator already exists in the backend (`POST /api/generate-prompt`) but is not in the UI any more. Bring it back as
an optional check that asks plain questions about gaps ("what should it do if the retailer is not in the ledger?")
before the first run. Matters for non-technical users, whose vague prompts become the main source of bad runs.

### C2. Example prompts / prompt help  *(P2)*
A small gallery to start from, and a short "how to write a good task" panel (include the address, say what to do per
row, say how to confirm it worked). The Credit Desk prompt is currently the default for every new task.

### C3. Headless or visible browser as a setting  *(P2, under discussion; v2 item 9)*
Today `headless=False` is hardcoded in two places in `orchestrator.py`. Make it one setting. See the discussion
notes in the chat that created this file; points to settle: default (visible locally), per task vs per run, and a
server-level override that forces headless when deployed. Also screenshots-on-failure for headless runs.

### C4. Logs improvements  *(P3)*
- Show the numbers the agent quoted beside the real ones (the "did it really read the ledger" check, automated).
- Compare two runs; export a run as a report.
- Browser notification when a run finishes.
- A per-record grouped view as an alternative to the flat feed.

### C5. Tasks  *(P3)*
Rename a task; immutable versions with a diff when the prompt changes; a visible "learned steps" panel with reset;
a dry-run mode that fills but never submits; "Test on 1 row" clearly labelled as a real write.

---

## D. Verification

- **D1. More verifiers (P2):** on-page signal (success text/selector), network-response check, read-back through the UI
  for sites with no database access. Today: none, compare to a table, compare to a view.
- **D2. Judgment tasks (P3):** a harder Credit Desk (conflicting signals, free-text notes, arguable answers) needs gold
  labels with *accepted sets*, an evidence check (do the quoted numbers match the database), and a consistency check
  (run twice, measure agreement). An LLM judge only later.
- **D3. Known verifier bugs (P2):** `select("*")` hits the 1000-row cap; duplicate match keys give false passes (R-19);
  substring type inference (`pay` in `payload`) (R-20); delete the unused `delete_all_records` helper (R-21).
- **D4. Reconciliation items from v2 (P3):** its prompt is DataDesk-specific (v2 item 4); the "expected empty" skip is
  one-directional (item 7); it never re-verifies its own fixes (item 8).

---

## E. Platform and deployment (before anyone else uses it)

- **E1. Login (P1 before any network exposure).** There is none; the app and its stored keys are open to anyone who can
  reach the port. Per-user ownership checks on every route (R-38).
- **E2. Concurrency and process state (P2).** One run at a time; quota flag, seed locks and circuit breakers are still
  process-global (R-03 to R-06). Worker separate from the API, a job queue, a heartbeat that detects dead runs (today
  only restarts are cleaned up).
- **E3. Deployment (P2).** *Done: all six tables now live in PostgreSQL (a Supabase project), created on startup with
  row-level security on; log events are written by a background thread so network latency never slows the agent.*
  Still to do: a container image with headless Chromium (`--no-sandbox`, larger `/dev/shm`), a concurrency ceiling tied
  to memory (R-10, R-32), timestamps as real `timestamptz`, JSON columns as `jsonb`, and an index/retention plan for
  `run_events`.
- **E4. SSRF and browsing limits (P1 before deployment).** The server browses any URL a user types: block private and
  link-local ranges, optional domain allowlist (R-25).
- **E5. Secrets (P2).** Wider redaction (events are redacted by exact value only), master-key rotation and backup,
  site logins through browser-use's `sensitive_data` (needs verifying), SSO/MFA explicitly unsupported (R-27, R-35).
- **E6. Data retention (P2).** Uploaded rows, events and reports hold personal data forever; add retention and a delete
  button (R-24).
- **E7. Second provider (P3).** Gemini-specific error handling sits behind a small provider interface first (R-29).

---

## F. Housekeeping before pushing to GitHub

- The app's data lives in the DataDesk Supabase project, in schema `agent_state`, behind the limited login
  `terminautomator_app` (created with `supabase db push --include-roles`). Cleaner long term: its own project.
- Gemini's free tier allows 500 requests a day. A 10-record run costs roughly 55-85, a 36-record run roughly 300, and a
  throttled run burns requests on retries; the day's allowance is gone after a few test runs. Show the user this
  limit (and what a run is expected to cost) before they press Run.

- Remove `DEV_PREFILL_SECRETS=1` from `v3/.env` (the code needs no change; it is off without it).
- `v3/.env` is a copy of the v2 keys and is git-ignored. Consider rotating the Gemini and Supabase keys if they were ever
  copied anywhere else. There is no git remote for v3 yet.
- The test script I used (`test_backend.py`, 34 checks) lives in a scratch folder, not in the repo. Move it to
  `v3/tests/` with a fake site and a fake LLM so it runs without spending tokens.
- *Done: `v3/docs/` held byte-identical copies of v2's documentation and was removed; the originals stay in `v2/docs/`.* `compare.py` does not apply to v3: rewrite or remove.
- Remove the Credit Desk preset and default prompt, or move them behind an "Examples" menu, before showing the app to
  other people.
- A second differently-shaped site to prove generality (v2 item 2). The Credit Desk is now the second shape (read and
  decide); a messy real-world site is still missing.
