# v2 bugs found and fixed — the real debugging history

Bugs #1–6 below were all found by actually running the system against
DataDesk/Supabase/Gemini live. Bugs #7–12 (2026-09-29) were instead found
by a deliberate static code-review pass — reading every file end to end
looking for what a live run *hasn't* exercised yet, rather than waiting to
hit them — and fixed without running anything, then verified with isolated
unit tests (mocked/simulated failures, not live browser/Gemini calls). Bug
#13 (2026-09-29) was back to being found from an actual live 100-record
run — the static review couldn't have caught it, since it depends on the
cold-run agent's own live, non-deterministic behavior (a trailing redundant
re-selection after clicking submit), not just reading the code in isolation.

---

## 1. Cache key collision — retries piled up instead of overwriting

**Symptom**: the very first cold run (record "Lena Fischer") took the
country dropdown 5 attempts to get right (4 failed `select_dropdown` calls,
then a raw JS `evaluate()` workaround). All 5 attempts got cached as
separate rows. Replay for every later record then dutifully retried all 4
dead-end attempts before ever reaching one that might work — not incorrect,
but wasteful, and a sign the caching model was wrong.

**Root cause**: the cache's primary key was
`(workflow_name, path_variant, step_index, action_offset)` — i.e. *when*
during the cold run a step happened, not *what* it was for. Since
`step_index` is browser-use's own per-run step counter, each retry got a
fresh, unique key instead of overwriting the previous attempt.

**Fix**: re-keyed the cache on `target_key` — the field name for anything
field-mapped, or `"<action_type>::<x_path>"` for a click with no field
(`cache/store.py`). A later attempt at the same field/element now naturally
overwrites the earlier one via `INSERT ... ON CONFLICT DO UPDATE`.
`step_index`/`action_offset` stayed as plain (non-key) columns, used only
to order replay.

**Verified**: a direct unit test (5 retries at the same field, 3 retries at
the same click target) collapsed 8 upserts into 4 distinct rows, each
keeping its latest attempt.

---

## 2. Browser closed before the DB write landed — replay "succeeded," nothing was saved

**Symptom**: a 10-record batch reported cache hits across the board (all
replaying "successfully," zero errors) — but only record 1 (the cold run)
actually showed up in Supabase. Records 2–10 vanished entirely.

**Root cause**: after replay's last action (the submit click), the code
went straight to `await context.close()`. DataDesk's submit handler does an
**async** Supabase insert; Playwright's `.click()` returns as soon as the
click *event* fires, not after whatever async work the handler kicked off.
Closing the browser context immediately aborted the in-flight request. The
cold run never hit this because browser-use's own agent needs one more LLM
round-trip after clicking submit to decide `done` — that round-trip
incidentally gave the insert time to finish. Replay has no equivalent
built-in delay.

**Fix**: a flat `await page.wait_for_timeout(2000)` right before closing
the context (`orchestrator.py`, `_run_record_cached_async`). Deliberately
not `wait_for_load_state("networkidle")` — DataDesk may hold a Supabase
realtime/websocket connection open, which would make "networkidle" never
actually trigger and just burn the full timeout every time regardless.

**Verified**: re-ran records 2–10 against the fix; all 10 landed correctly
in Supabase (`unmatched_records: 0`), confirmed by reading the DB directly.

---

## 3. Checkbox blind-toggle — every record got the *first* record's boolean

**Symptom**: after fixing #2, field accuracy was 96.1% instead of 100%.
Every record whose `newsletter` value was `false` ended up `true` in the DB
instead.

**Root cause**: a checkbox click has no typed text to reverse-match against
a field (unlike `input`/`select_dropdown`), so it was captured as a generic
`click` with no field mapping. On replay, a generic click just *toggles*
whatever the box's current state happens to be — correct only for records
that want the same boolean as the record that built the cache (which
happened to want `true`).

**Fix**: at capture time, a `click` on an element whose
`attributes.type == "checkbox"` is now **name-matched** (not
value-matched) against the record's field names — stripping a `field-` id
prefix, matching DataDesk's own convention (`id="field-newsletter"`,
confirmed from a real browser-use log line:
`Clicked input type=checkbox checkbox-state=unchecked id=field-newsletter
name=newsletter`). It's cached as a new action type, `checkbox`, not
`click`. On replay, `checkbox` steps read the element's current
`.is_checked()` state first and only click if it doesn't already match the
record's desired value (`cache/instrumentation.py`'s `_match_checkbox_field`,
`replay/engine.py`'s `checkbox` branch).

---

## 4. Missing-field gap — fields absent from the seed record were never learned

**Symptom**: same 96.1%-not-100% accuracy investigation. Every record with
real `notes` text ended up with `notes` empty/null in the DB, even though
the record's own JSON clearly had notes.

**Root cause**: the record that built the cache (record 1) happened to
have an *empty* `notes` field, so the cold-run agent never typed into that
box at all — no cacheable action, no cache row, ever. Replay only ever
walks what **is** in the cache; it had no mechanism to notice "this record
has a value for a field the cache has never heard of."

**Fix**: `_run_replay()` (`orchestrator.py`) now explicitly computes
`missing_fields` — every field the current record has a non-empty value
for, that no cached step covers at all — and runs a scoped live
single-step fallback for each one (via the same mechanism as any other
cache miss), **before** replaying the assumed-last submit step. That live
call self-heals the cache the same way any fallback does, so the *next*
record with real notes text hits it from cache directly. This is the
concrete mechanism behind "the cache gets smarter over time, not just at
the very first run."

---

## 5. browser-use's own telemetry hung silently, with zero logging

**Symptom**: mid-batch, a Chrome window sat open with no further progress
for minutes — no new log lines at all, right after "Task completed
successfully" printed for the cold run. Looked identical to the network
flakiness we'd already seen with Gemini timeouts, but nothing eventually
recovered.

**Root cause**: `browser_use.Agent.run()` calls `self.telemetry.flush()`
synchronously at the very end of every run (`agent/service.py:2524`) —
a blocking network call to PostHog (`https://eu.i.posthog.com`). browser-use
explicitly silences PostHog's own logger
(`posthog_logger.disabled = True`), so a slow/hanging flush produces
**zero log output** — it just looks like a dead stop. On a day when
Google-adjacent network calls were already slow (same pattern as the
Gemini timeouts), this could hang indefinitely.

**Fix**: `ANONYMIZED_TELEMETRY=false` added to `.env`/`.env.example`
(browser-use reads this via `CONFIG.ANONYMIZED_TELEMETRY`,
`os.getenv('ANONYMIZED_TELEMETRY', 'true')` — confirmed in the installed
package's `config.py`). With telemetry disabled, `flush()` is a no-op.

**Note**: the process that was stuck on this had to be killed to recover
(setting the env var doesn't retroactively unstick an in-flight blocking
call in an already-running process) — see the "operational lesson" entry
below for what went wrong doing that.

---

## 6. Thundering herd on a shared missing field, under concurrency

**Symptom**: in a 100-record run with 10-way concurrency, the live log
showed `newsletter — field_missing_from_cache — falling back to live agent`
and `notes — field_missing_from_cache — falling back to live agent`
repeated well over a dozen times in a short window — far more live Gemini
calls than the "learn once, replay forever" design should ever need.

**Root cause**: when ~10 records start their replay at roughly the same
moment, several of them can independently discover the exact same missing
field *before any one of them has finished self-healing it*. Each one,
unaware of the others, launches its own redundant live agent call for the
identical field.

**Fix**: a per-`(workflow_name, path_variant, field_name)` `asyncio.Lock`
(`orchestrator.py`, `_get_missing_field_lock`), held around the "check
cache → maybe call the LLM → self-heal" sequence for that one field, with a
double-check *after* acquiring the lock — a record that had to wait reuses
whatever the winner just learned instead of calling the LLM itself. Only
the genuinely-first record to reach a given missing field pays for it.

### 6b. The lock alone made things *worse* once the field's API calls started genuinely failing

**Symptom**: a later 100-record live run hit a stretch where Gemini's
`gemini-3.5-flash-lite` started returning `503 UNAVAILABLE - high demand`
for *every single call* (285 occurrences logged, 100% failure rate for
that stretch). Since the lock only ever lets one record attempt a given
field at a time, every record needing `notes` queued up single-file behind
an unbroken chain of doomed attempts — each one burning up to 6 retries
before giving up, then immediately handing the lock to the next record in
line to lose the exact same way. 72 of 100 records in that run ended up
marked `FAILED`, even though — confirmed directly in DataDesk — every one
of them had actually submitted successfully; only `notes` was missing.

**Root cause, two compounding things**:
1. The lock has no notion of "this field is currently unlearnable, stop
   making everyone wait their turn to also lose." A genuinely broken field
   turns "learn once, benefit forever" into "everyone takes their own turn
   failing," which is *slower* than no lock at all in that specific case.
2. `RunResult.success` was being set to `False` whenever *any* field's live
   fallback failed — including a purely cosmetic field like `notes` that
   has zero bearing on whether the record actually landed. This directly
   caused the "why does it say failed when the record was clearly added"
   confusion.

**Fix**:
- **Circuit breaker** (`orchestrator.py`, `_circuit_is_open` /
  `_record_field_failure` / `_record_field_success`): after a field fails
  `_CIRCUIT_FAILURE_THRESHOLD` (2) times in a row, it's marked "open" for
  `_CIRCUIT_COOLDOWN_SECONDS` (30) — every subsequent record targeting that
  field skips the live fallback entirely (no lock wait, no LLM call) until
  the cooldown passes, at which point the next record gets a fresh attempt
  (self-recovers once the API actually comes back). This turns an
  unbounded pile-up into "at most ~2 real attempts, then everyone else
  skips instantly."
- **`RunResult.success` now only reflects the submit step.** Every
  non-submit field (`_replay_or_fallback(..., critical=False)`, the
  default) can miss, fail its fallback, or get circuit-skipped without
  marking the record failed — it's still counted in `cache_misses` /
  `miss_reasons` for honest diagnostics, and `evaluation.verify_batch()`
  (reading Supabase directly) is still the real authority on which fields
  ended up correct. Only the submit step itself
  (`_replay_or_fallback(submit_step, None, critical=True)`) can set
  `submit_failed = True` — because without a successful submit, the record
  genuinely didn't land anywhere.
- Bumped `MAX_CONCURRENT_REPLAYS` default 10 → 15 at the same time (pure
  replay is cheap; RAM was always the actual constraint, not API quota —
  see `config.py`).

---

## Operational lesson (not a code bug): don't kill an individual Chrome process mid-batch

While diagnosing bug #5's hang, a Chrome process (one of several
concurrently in flight) was killed directly via `pkill` to try to unstick
things. That process was, by the time the kill landed, actively being used
by a different in-flight concurrent record (the original stuck run had
actually already resolved on its own and the batch had moved into its
concurrent phase). Killing it out from under a live browser session
cascaded into `BrowserSession` errors across *other* concurrent agents
("No valid agent focus available - target may have detached") and crashed
the whole backend process.

**Takeaway, now built into the system**: never `pkill` an individual
Chrome/profile process while a batch might still be using it. Use
`POST /run/abort` instead (added specifically because of this incident) —
it stops queueing new work but always lets whatever's already in flight
finish naturally, so no live browser session or in-flight Gemini call ever
gets yanked out from under itself.

---

## Confirmed *not* a bug (flagged as a risk, then verified correct)

**`BrowserProfile(cdp_url=...)` tab attachment.** Before any live run
happened, it was an open question whether browser-use, when attaching to
an externally-provided CDP endpoint, would act on the exact tab Playwright
had already navigated (what the design needs) or open a fresh blank one. A
real cold run confirmed it correctly attaches to and continues filling the
in-progress form. No fix was needed; noted here so the historical "unverified
risk" comments in `orchestrator.py` aren't mistaken for an open issue.

---

## Observed anomaly, not diagnosed as v2's own bug

**Duplicate rows in Supabase for the same email**, seen once (two rows for
"Lena Fischer," created ~2.5 hours apart, both field-for-field identical).
The agent only clicked submit once per the backend log for that run. Most
likely a DataDesk frontend quirk (e.g. a double form-submit dispatch), not
anything in v2's Python code — worth investigating separately if it
matters, but out of scope here.

---

## 7. [Critical] One record's failure could crash the entire batch (2026-09-29)

**Found by**: static review, prompted by a real earlier incident (a
`Page.goto: net::ERR_CONNECTION_REFUSED` when DataDesk was briefly down)
that had stopped the whole batch — `_status: {"state": "error", "current":
0}` — rather than just failing the one affected record.

**Root cause**: `_run_record_cached_async`'s Playwright/browser-use calls
(`page.goto()`, launching the browser, running the agent) had no exception
boundary. Any unhandled exception there propagated out of that one record's
coroutine, through `asyncio.gather()` in `_run_many_concurrent_async`
(which cancels every other pending task the instant any one of them raises,
since `return_exceptions=True` wasn't set), all the way up to
`_run_batch_in_background`'s own handler — which then marked the *entire*
batch as failed, having completed zero records.

**Fix**: `_run_record_cached_async`'s body is now wrapped in a try/except
that converts any exception into a normal failed `RunResult` for just that
one record, with `metrics` defaulting to zero hits/misses/steps so
`store.record_run()` and the `record_done` event still have valid data to
report. `_run_many_concurrent_async`'s `_run_one` got the same wrapper
added as defense-in-depth, so even an unforeseen exception from somewhere
else in the chain can never cascade through `asyncio.gather` again.

**Verified**: a unit test mocking `_run_record_cached_async` to raise for
one record out of five (simulating the exact `ERR_CONNECTION_REFUSED`
scenario) confirmed all 5 records now get a result — 4 succeed, 1 fails —
instead of `asyncio.gather` cancelling all 5.

---

## 8. [Critical] Unsafe SQLite access across threads (2026-09-29)

**Found by**: static review, prompted by realizing the reconciliation pass
(added the same day) runs on a genuinely separate `threading.Thread` that
can execute *at the same time* as a new batch's own thread — by design,
since reconciliation is deliberately not gated by the same "is a batch
running" lock.

**Root cause**: `CacheStore` shares one `sqlite3.Connection`
(`check_same_thread=False`) via a process-wide singleton
(`orchestrator.get_store()`). That was safe as long as every caller ran on
the same thread's asyncio event loop (true before reconciliation existed).
Python's own sqlite3 docs are explicit that `check_same_thread=False` only
makes cross-thread *use* legal — it does not make concurrent access from
multiple threads automatically safe.

**Fix**: added a `threading.Lock` around `CacheStore`'s cursor context
manager, serializing all actual database operations regardless of which
thread they're called from.

**Verified**: a stress test with 8 real OS threads each writing 50 rows
concurrently (400 total) completed with zero errors and no deadlock.

---

## 9. [Moderate] TOCTOU race on the batch-running guard (2026-09-29)

**Found by**: static review — `/run` and `/run/start` are plain (non-async)
FastAPI routes, which Starlette dispatches to a thread pool; two
near-simultaneous requests could both observe `_status["state"] == "idle"`
before either one had set it to `"running"`.

**Impact if hit**: two batches launched at once, both claiming the same
`0..MAX_CONCURRENT_REPLAYS-1` CDP port range — colliding Chrome processes.

**Fix**: added `_status_lock` (a `threading.Lock`), and both endpoints now
perform the "already running?" check and the "mark as running" write
atomically inside it.

---

## 10. [Moderate] Reconciliation failures could stomp on an unrelated new batch's UI (2026-09-29)

**Found by**: static review — the reconciliation pass reused the
`batch_error` event type when it failed. The frontend treats `batch_error`
as "the currently-displayed batch just died" (resets the Start button,
turns the status dot red). Since reconciliation can still be running well
after a brand-new, unrelated batch has already started (that's the whole
point of decoupling it from `_status`), a reconciliation failure could
incorrectly reset that new batch's in-progress UI.

**Fix**: reconciliation failures now emit their own `reconciliation_error`
event type. The frontend logs it without touching `setRunning`/the status
dot/status text.

---

## 11. [Moderate] Logs.md could mix two unrelated runs together (2026-09-29)

**Found by**: static review, following directly from bug #10's root cause —
a reconciliation pass can still be writing events well after a *new* batch
has called `logfile.reset()`, truncating the file out from under it.

**Fix**: `logfile.reset()` now returns a "generation" token; every event
written during that run (including its later reconciliation pass) carries
that token. `logfile.append()` silently drops an event whose generation no
longer matches the current one — a stale write from an old run's
reconciliation pass no longer lands in a file a newer run has already
reset. It's still delivered live over SSE regardless (if anyone's still
watching), just not persisted into the wrong transcript.

**Verified**: a unit test resetting the log mid-write (simulating exactly
this race) confirmed the stale event never appears in the file.

---

## 12. [Minor] `_claim_seed_responsibility` wasn't exception-safe (2026-09-29)

**Found by**: static review — between acquiring the seed lock and
returning, a second `store.get_steps()` call could theoretically raise
(e.g. a disk I/O error). Since nothing released the lock in that case, a
sufficiently unlucky failure would have permanently deadlocked that
workflow+variant's seeding forever — no record could ever cold-start it
again for the rest of the process's life.

**Fix**: wrapped the re-check in a try/except that releases the lock and
re-raises on any exception, so a failure here degrades to "this one attempt
failed" (the same as any other cold-run failure) rather than a permanent,
silent deadlock.

---

## 13. [Critical] Submit step misidentified by position, not identity (2026-09-29)

**Found by**: a real 100-record live run (2026-09-29, fresh cache, fresh
100-record dataset) — 57/100 records came back FAILED and Supabase
verification dropped to 84.9% (down from a prior run's 100%), with 218
fields queued for reconciliation. Nearly every failure and reconciliation
entry pointed at the same three fields: `employment_type`, `department`,
`newsletter`.

**Symptom**: the cold-run's own step log for record 1 showed exactly this
sequence at the end of the form:
```
Select 'Marketing' for department and 'Part Time' for employment_type, then submit the form.
Select 'Marketing' for department and 'Part Time' for employment_type.
Finish the task and return success.
```
The agent re-selected department/employment_type a second time, for no
functional reason, *after* it had already clicked submit in the previous
step.

**Root cause**: `_run_replay` identified the submit step as "whatever
cached step sorts last by `(step_index, action_offset)`" —
`cached_steps[-1]`. That assumption broke the instant the cold-run agent
performed any trailing, non-essential action after actually clicking
submit: the redundant department/employment_type re-selection sorted after
the real submit click and got misidentified as it. Since the submit step
is always replayed with `value=None` (a real submit click takes no value),
every replay then tried `select_option(label="None")` on the
employment_type dropdown — guaranteed to fail — and its live-fallback
agent was handed the nonsensical task "set employment_type to None," which
also always failed. Because this step runs with `critical=True`, every one
of those failures marked the *entire record* as failed, even though the
real submit click (now just an ordinary non-critical step earlier in the
list) had already succeeded and the record had already landed in
Supabase — which is exactly why verification showed 84.9% instead of near
0%: most of the data really did land, this was a bookkeeping bug
mislabeling good records as bad, while also poisoning the reconciliation
queue with the same impossible "fix employment_type to None" task on every
one of those records.

**Fix**: the submit step is now identified by *identity*, not position —
the last cached step with `field_name is None and action_type == "click"`.
Submit buttons are always plain, fieldless clicks; every other cacheable
action type (`input`/`select_dropdown`/`checkbox`) is only ever cached
when a field_name was successfully matched (see
`cache/instrumentation.py`), so a fieldless click is an unambiguous
identity signal that survives a trailing redundant re-selection landing
later in step order. Falls back to the old last-row heuristic (with a
logged warning) only in the never-expected case where no fieldless click
exists in the cache at all.

**Verified**: an isolated static test reproducing the exact cached-step
ordering from this run's log (submit click at step 8, trailing
department/employment_type redo at step 9) confirmed the old logic picked
the employment_type redo as "submit" while the fixed logic correctly picks
the real fieldless click — without running anything live.

---

## 14. [Critical] Fixed 2-second wait wasn't actually long enough under real load (2026-09-29)

**Found by**: a real 200-record live run (2026-09-29, 15 concurrent replay
windows) — the batch itself reported "200 succeeded, 0 failed" with **zero**
misses and **zero** errors logged anywhere, yet Supabase verification came
back with **14 records not found at all**. Every single missing field
across the whole run traced back to those 14 records (14 × 18 fields =
252, exactly matching the verification gap) — every OTHER field on every
OTHER record was correct. This is the quietest, most dangerous kind of bug
this project can have: the automation's own signals (RunResult.success,
hit/miss counts, error events) all say everything is fine, and the data is
just... not there.

**Root cause**: this is the same underlying issue as bug #2
(`Browser closed before the DB write landed`), resurfacing because its
original fix doesn't scale. DataDesk's submit handler does an async
Supabase insert (`supabase.from("people").insert(payload)`, confirmed by
reading `src/App.jsx`); a Playwright `.click()` on submit returns the
instant the click event fires, not once that insert actually completes.
Bug #2's fix was `await page.wait_for_timeout(2000)` — a flat 2-second
guess — inserted after every record's actions finish, before the browser
context closes. That guess held up fine through every 100-record test this
session, but broke the moment 15 Chrome windows were genuinely
hammering the same local dev server and the same Supabase project at
once: a fixed sleep has no way to know whether ITS insert is still stuck
behind fourteen others'. The records lost were mostly (but not
exclusively) clustered right at the very start of the batch, exactly when
the first wave of concurrent replays all fire at once right after record
1's cold run releases the seed lock - the highest-contention moment in the
whole run.

**Fix**: replaced the fixed sleep with an actual wait for the specific
network response that proves the insert happened -
`page.expect_response(lambda r: f"/rest/v1/{SUPABASE_TABLE}" in r.url and
r.request.method == "POST", timeout=15000)`, registered *before*
`_run_cold`/`_run_replay` runs (so it can't miss a response that arrives
partway through their execution) and only actually awaited afterward if
`run_result.success` is `True` (a record that never reached/passed submit
was never going to trigger this request in the first place - nothing to
wait for). A response status ≥400, or no response arriving within 15s, is
now logged explicitly as a likely silent-data-loss case instead of
disappearing without a trace. A fixed sleep can never be correct under
variable concurrency; waiting for the actual event that proves the write
landed is the only approach that scales with load instead of guessing at
it.

---

## 15. [Critical] Bug #14's own fix turned slow (but real) successes into false failures (2026-09-29)

**Found by**: a real 200-record live run (2026-09-29, same conditions as
bug #14, run immediately after that fix went live) — 140/200 records came
back FAILED, but every single one showed a clean `24 hits / 0 misses`
replay (submit included), and Supabase confirmed all 200 records had
actually landed. So bug #14's fix didn't just fail to help - it actively
turned genuine, correct successes into loud false failures.

**Root cause**: a misunderstanding of Playwright's `expect_response()` API.
Bug #14's fix was written as:
```python
async with page.expect_response(...) as response_info:
    run_result, metrics = await _run_replay(...)

if run_result.success:
    response = await response_info.value  # assumed ONLY this could time out
```
But `expect_response()`'s actual waiting (and its `TimeoutError`) happens
when the `async with` block itself exits (`__aexit__`), not later at that
explicit `.value` await. So the timeout fired *before* the code ever
reached the `try/except` meant to catch it, propagated straight past it to
the function's outer exception handler, and that handler overwrote an
already-correct, already-successful `run_result` (set moments earlier,
inside the `async with` body, by `_run_replay` itself) with a fresh
`success=False`. Every field the replay engine touched had already been
confirmed correct - the failure was purely in what happened to the
*variable* afterward, not in anything the record actually did.

**Fix**: wrap the `async with page.expect_response(...)` block itself in
a `try/except PlaywrightTimeoutError`, catching the exception at the exact
point it's actually raised. Since `run_result` was already assigned
(correctly) inside the `async with` body before `__aexit__` ever runs,
catching the exception right there means that value survives untouched -
a timeout is now purely a logged diagnostic ("no confirmation observed
within budget, not necessarily lost"), never a verdict on the record.
Also raised the budget from 15s to 30s, since the underlying cause of the
slow responses in the first place is very likely the Supabase `people`
table having grown large across an entire session's worth of test runs
that were never cleared, combined with DataDesk's own `loadPeople()` full
reload after every single insert - both compounding under sustained
15-way concurrency.

**Verified**: an isolated test simulating an async context manager whose
`__aexit__` raises on timeout (mirroring `expect_response()`'s real
behavior) confirmed the corrected try/except placement lets an
already-assigned success result survive the exception, where the original
placement did not - without touching a real browser or Supabase.

---

## 16. [Hardening] No fast-stop when Gemini's DAILY quota (not per-minute) runs out (2026-09-29)

**Not a logic bug** — the system behaved "correctly" the whole time, just
extremely wastefully. Found live, mid-demo: a 50-record run had every
single record fail identically. Gemini's free tier has two separate 429
RESOURCE_EXHAUSTED limits: `GenerateRequestsPerMinutePerProjectPerModel-
FreeTier` (15/min, resets within a minute — exactly what the circuit
breaker and seed lock are built to absorb) and
`GenerateRequestsPerDayPerProjectPerModel-FreeTier` (500/day, a hard wall
that does not reset until the next day). A full day of testing/demos had
used up the daily allowance. Because the seed lock's "if the current cold
attempt fails, the next waiting record automatically becomes the new
attempt" design exists specifically to retry *transient* failures, it kept
feeding the daily-exhausted quota exactly what it didn't need: one more
doomed cold-run attempt, over and over, one record at a time, each burning
~15-20s of browser-use's own internal retries first.

**Fix**: `agent/runner.py` now inspects `AgentHistoryList.errors()` (a
public method browser-use already exposes — no log-scraping) after every
`agent.run()` call and sets `RunResult.quota_exhausted=True` specifically
when a step's error contains `GenerateRequestsPerDayPerProjectPerModel` —
deliberately never triggered by the per-minute variant, which stays fully
recoverable exactly as before. `orchestrator.py` holds a
`_quota_exhausted_event` (a `threading.Event`, since the reconciliation
pass runs on its own thread): the moment ANY record's result carries that
flag, the event trips once, one clear error event is emitted, and every
place that would otherwise make a live LLM call — `_run_cold`,
`_run_live_fallback_step`, `_reconcile_one_field_async` — checks it FIRST
and fails instantly without even trying, and the main batch's dispatch
loop stops handing not-yet-started records into new Chrome windows at all
(mirroring exactly how a user-triggered `/run/abort` already works: records
already in flight finish naturally, nothing gets yanked mid-run). Pure
cache replay is entirely unaffected — this only ever fires at the point an
LLM call is actually attempted, never just because replay alone wouldn't
be "enough." The flag resets at the start of every new `run_batch_cached()`
call, so a fresh batch (new API key, or enough real time passed) always
gets a fresh chance rather than staying tripped for the rest of the
process's lifetime.

**Verified**: isolated tests (no real Gemini/browser calls) confirmed
`_detect_quota_exhausted` fires only on the daily-quota signature and
never on the per-minute one or an errorless run; confirmed
`_note_if_quota_exhausted` flips the event and emits exactly one event
even when called multiple times; confirmed `_run_cold`, once the event is
set, returns a failed result without ever invoking
`_run_single_record_async` (mocked to raise if called).

**Addendum, found the same day from a deliberate static re-review (no live
run) before a demo**: the fix above still had a real gap. `_run_cold`'s own
check-before-call returns instantly once quota is known exhausted, but
that instant return still happened *inside*
`page.expect_response(timeout=30000)` — and per this same file's #14/#15,
that timeout is measured from when the block is entered, not from how
long the code inside it actually took. Since a skipped cold run submits
nothing, no matching POST would ever arrive, so the wait would run out
its full ~30s regardless of how fast `_run_cold` bailed out. With
`MAX_CONCURRENT_REPLAYS` concurrent records all able to queue up on the
seed lock at the moment quota dies, up to *all of them* would have
sequentially eaten a ~30s dead wait (and an unnecessary browser launch
each) before the batch actually stopped — several minutes of a visibly
"stuck" dashboard, the opposite of what this fix was for.

**Fix**: moved the check one level up, to right after the page has
navigated but *before* entering `page.expect_response(...)` at all. A
cold run with quota already known exhausted now skips straight to the
300ms settle buffer — no browser-use call, no 30s wait, just the
unavoidable cost of the one browser window `_run_record_cached_async`
already opens for every record regardless of mode.

**Verified**: re-ran the full existing test suite (detection, dedup,
`_run_cold`/`_run_live_fallback_step`/`_reconcile_one_field_async`
fail-fast checks) after the change — all still pass — plus confirmed via
direct code reading that both branches still route through the same
`finally: await context.close()` and `finally: if must_seed:
_release_seed_responsibility(...)`, so neither path leaks a browser or
double-releases the seed lock.
