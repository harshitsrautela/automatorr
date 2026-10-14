# 3. Operations — actually running this thing

Builds on `02_HOW_CACHING_WORKS.md`. This page is entirely practical: how
to start it, what every part of the dashboard actually means, how to read
the log file, what to do when something looks wrong, and the handful of
real operational lessons this project learned the hard way.

---

## Part 1 — Starting everything up

Two separate servers need to be running at once, and they're genuinely
independent processes:

```bash
# 1. The target form itself - whatever site is being automated.
#    For this project's own testing, that's DataDesk.
cd /path/to/target-frontend && npm run dev      # usually http://localhost:5173

# 2. The automation backend + dashboard
cd v2 && source venv/bin/activate
uvicorn main:app --reload --port 8000            # dashboard at http://localhost:8000
```

Open `http://localhost:8000` in a browser — that's the dashboard: a
title, a short description of what it does, a big Start Batch button, a
live status panel, and a running summary of numbers. Nothing needs to be
configured through the UI itself; the actual settings (which record file
to load, which model to use, concurrency limits, and so on) live in the
backend's own configuration (`config.py`, reading from a `.env` file) —
see Part 5 below for the ones worth knowing about.

### What "restart the backend" actually involves, and why it sometimes needs force

`uvicorn --reload` runs two processes under the hood: a lightweight
"reloader" process that watches files for changes, and a separate actual
worker process that holds the real FastAPI application (and, importantly,
any in-flight background work — like a batch, or a reconciliation pass).
A plain, polite stop signal to the reloader doesn't always reliably stop
that inner worker process too, especially if it's busy. In practice, a
clean restart sometimes needs a firmer stop on the worker process
specifically (checking `ps` for a lingering process still holding the
port, and force-stopping *that* one), not just the reloader. This isn't a
bug in this project — it's a normal quirk of how `uvicorn --reload`
supervises its own worker — but it's worth knowing about so a restart
that "looks like it worked" (the reloader exits cleanly) doesn't leave a
zombie worker still quietly running a batch, or still holding port 8000,
underneath it.

---

## Part 2 — Starting a batch "from scratch," properly

The cache in `storage/cache.sqlite3` is **intentionally persistent
across restarts and across batches** — that's the entire point of the
design; it's what makes record #2 of *today's* batch benefit from
something record #1 of *yesterday's* batch already learned. Leaving it in
place between runs is the normal, correct, everyday behavior.

But for a genuinely clean demonstration — one where record #1 should
have zero memory, exactly as if this were the very first time this form
had ever been seen — the cache has to be cleared deliberately:

```bash
# The backend must be fully stopped first - it holds the SQLite file open,
# and deleting a file a running process has open can behave unexpectedly.
rm storage/cache.sqlite3
# Restart the backend; a fresh, empty cache database is created
# automatically the first time it's needed - nothing else to do.
```

Skipping this step and then being confused why record #1 of a "fresh"
test run immediately shows `replaying from cache` instead of `no cache
yet, using live agent` is a very easy, very common trip-up — it isn't a
bug, it's the cache doing exactly what it's supposed to do: remembering
what it learned last time, indefinitely, until told otherwise.

### Also clearing the target database, and why that's sometimes worth doing too

Separately from the *cache*, the target system's own database
accumulates real rows across every test batch that's ever been run
against it — nothing in this system ever deletes target-system data on
its own. For pure caching-behavior tests this usually doesn't matter. But
it can matter for a subtler, load-related reason that this project ran
into directly (`V2_BUGS_FIXED.md` #14/#15): DataDesk's own frontend
reloads its *entire* people list after every single successful insert: as
that table quietly grows across a long test session, every record's
post-submit reload gets a little slower, and under genuinely heavy
concurrency (many browser windows all inserting, and all triggering that
same full reload, at once) that compounding slowdown was a real
contributor to write-confirmation timeouts. A utility function exists
specifically to reset the target database to empty before a clean test
run:

```python
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from evaluation.evaluator import delete_all_records

client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
deleted = delete_all_records(client)   # wipes every row, returns how many it deleted
```

This is genuinely destructive (every row in the target table, gone, no
undo) and deliberately not wired into anything automatic — nothing in
this codebase ever calls it on its own. It's a manual, explicit,
know-what-you're-doing operation, used specifically to give a test run a
completely clean starting point on both sides: an empty cache (record 1
has no memory) *and* an empty target database (nothing left over to slow
future inserts down, and a completely unambiguous "N records in, N
records verified" result with no old rows to accidentally match against).

---

## Part 3 — Reading the dashboard while a batch runs

The dashboard connects to a live event stream the moment the page loads
and updates continuously — no manual refreshing needed. Here's what each
part of it actually means:

**The status line** cycles through a small set of states: `idle` (nothing
running, ready to start), `running` (a batch is actively in progress),
`done` (finished normally), `error` (something genuinely broke the whole
batch — rare, since individual record failures are designed to *not*
propagate this far, see `04_ARCHITECTURE.md`), and `aborted` (a user
explicitly stopped it early via the Abort button).

**The current/total counter** tracks how many records have fully
finished — succeeded or failed, doesn't matter which — out of the whole
batch. Because up to several records finish in parallel at nearly the
same instant once concurrency kicks in, this counter can jump by more
than one between updates; that's expected, not a sign of anything being
skipped.

**The live log** shows, per record: whether it's a cold run or a replay,
each field's individual hit/miss (with the miss's specific reason —
"execution failed," "circuit open, not retrying right now," "field
missing from cache," and so on), any scoped live-fallback steps that had
to happen, and a final done/failed line per record with its hit/miss/LLM
step tally.

**The summary numbers** — total records, succeeded, failed, cache hits,
cache misses, LLM steps used — are the batch's own self-reported tally.
Worth remembering, from `02_HOW_CACHING_WORKS.md`: "succeeded" here means
"submitted successfully," not "every field was perfect" — the *actual*
field-by-field grade only shows up once the independent verification pass
finishes and reports back separately (see Part 4, right below).

**The Abort button** stops the batch from *starting* any record that
hasn't begun yet — it does **not** yank anything already in flight out
from under itself. Whatever's mid-run when Abort is pressed is
deliberately left to finish naturally: a live browser session or a live
LLM call in progress is never killed abruptly by this button. This exists
specifically because of a real incident (see Part 6, below) where
manually killing an individual Chrome process mid-batch, outside of this
button's careful design, cascaded into crashing the entire backend.

---

## Part 4 — What happens right after the batch finishes

Once every record has either succeeded or failed, two more things happen
automatically, in sequence, and both show up as their own section in the
live log:

**Verification.** A completely separate pass reads the target database
directly and compares every field of every record against what should be
there, independent of anything the automation itself reported. The
result — something like "1749/1800 fields correct (97.2%), 0 records not
found" — is the real grade for the batch. The two numbers to actually pay
attention to:
- **Field accuracy** — out of every individual field, across every
  record, what fraction landed correctly. A gap here usually traces back
  to one or two specific fields having a rough time (a circuit-broken
  field, say), not the whole batch being broadly unreliable — it's worth
  checking *which* fields are actually wrong before assuming something's
  systemically broken.
- **Records not found** — records the batch believed succeeded that
  simply aren't in the database at all. This should always be zero on a
  healthy run; if it isn't, that's the write-confirmation issue described
  in `02_HOW_CACHING_WORKS.md` Part 5, and worth investigating directly
  rather than dismissed as noise.

**Reconciliation.** Immediately after verification, a follow-up pass
starts on its own background thread and works through exactly the fields
verification flagged as wrong, retrying each one through the target
system's edit flow. It reports its own tally at the end — "N fixed, M
still open" — and a field circuit still being open at the moment
reconciliation runs (because the main batch only just tripped it) means
that field gets skipped this time around too, deliberately, rather than
wasting an attempt on something known to be currently broken. Those
remaining "still open" gaps aren't lost forever — the very next time a
batch runs (whenever that is), those same fields get another chance to
self-heal normally through ordinary replay.

Because reconciliation runs on its own thread, deliberately decoupled
from whether a new batch is running, **a brand-new batch can be started
immediately** without waiting for the previous batch's reconciliation to
finish — they run alongside each other safely (see `04_ARCHITECTURE.md`
for exactly how that isolation is maintained).

---

## Part 5 — The endpoints and settings worth knowing

| Endpoint | What it does |
|---|---|
| `POST /run/start` | Starts a batch in the background, returns immediately. What the dashboard's Start button calls. |
| `GET /events` | Server-Sent Events stream of live progress — what feeds the dashboard's log. |
| `GET /status` | Current state (idle/running/done/error/aborted) plus a progress count — useful to poll from a script or the command line. |
| `POST /run/abort` | Stops queueing new records; leaves anything already in flight to finish naturally. |
| `GET /results` | The last batch's full verification report, as JSON. |
| `GET /cache/stats` | A per-workflow hit-rate rollup — the single best thing to watch *over time, across many runs*, not just within one; a sudden, sustained drop in hit rate is the earliest warning sign that the target form's UI has actually changed underneath the cache. |
| `POST /run` | The same batch, but blocking — waits for the whole thing before responding, instead of running in the background. Mainly useful for scripts and direct `curl` testing, not the interactive dashboard. |

A few configuration values worth knowing exist, all set via environment
variables (`.env`), none of which need code changes to adjust:

- `TARGET_FORM_URL` — which site's form is being automated.
- `MATCH_KEY_FIELD` (default `email`) — whichever field uniquely
  identifies a record; used both by the reconciliation pass (to search
  for an existing entry) and by verification (to match a record against
  its database row).
- `MAX_CONCURRENT_REPLAYS` — how many records run in genuinely parallel
  browser windows at once. RAM, not API quota, is the real limiting
  factor here, since pure cache replay makes no LLM calls at all.
- `MODEL_NAME` — which Gemini model drives the live agent. Swapping this
  is the most direct way to move off a rate-limited free-tier model if
  that becomes a real operational bottleneck.

---

## Part 6 — When something looks stuck or wrong

**Status says "running" but nothing's moved in a noticeably long
while.** First, check for live Chrome windows belonging to this system
(their profile directories are named distinctly, so they're easy to spot
in a process list). If there genuinely are none and the status still says
"running," the backend process itself may be hung rather than any
individual record — check its own log output before doing anything more
drastic.

**A `429` / `RESOURCE_EXHAUSTED` error shows up in the backend's log.**
That's Gemini's own API responding, not a bug in this codebase — but it's
worth reading the exact error message carefully, because it comes in two
meaningfully different flavors that call for completely different
responses:
- **`PerMinutePerProjectPerModel`** — a short-term burst limit (this
  project's free tier caps at 15 requests per minute). This is exactly
  what the circuit breaker exists to absorb gracefully — expect some
  fields to get temporarily skipped and self-recover within moments, not
  a batch-wide emergency.
  - **`PerDayPerProjectPerModel`** — the daily free-tier cap (500 requests
  a day, for this project's own tier). This one genuinely doesn't recover
  on its own until the next day's quota resets; the practical options are
  waiting it out, switching to a different API key, or pointing
  `MODEL_NAME` at a different model with its own separate quota. Running
  a batch into this wall repeatedly just produces a cascade of every
  record individually attempting and failing to seed the cache, one after
  another (the seed lock correctly hands the attempt to the next waiter
  each time, exactly as designed — it just means every single one of them
  is doomed until the quota actually resets).

**Never manually kill an individual Chrome process while a batch might
still be using it.** This is the one hard operational rule this project
learned from a real incident, not a hypothetical: early on, while
diagnosing an unrelated hang, a Chrome process was killed directly to try
to unstick things — and by the time that kill actually landed, a
*different*, unrelated concurrent record was genuinely using that exact
process. Killing it out from under a live session cascaded into errors
across other concurrently-running records and crashed the entire backend
process. The Abort endpoint exists specifically because of this incident,
and it's the right tool for "I want this to stop" precisely because it
never touches anything already in flight — only records that haven't
started yet.

**A field keeps failing, run after run, no matter how many times a batch
is re-run.** Worth strongly suspecting the target system itself before
assuming the caching logic is broken — this project hit exactly this
shape of problem once, and the real cause turned out to be test data
requesting dropdown values that simply didn't exist as real, selectable
options on the actual target form. Checking the target form directly
(what does this dropdown's real option list actually contain?) resolved
it in minutes once someone thought to look there instead of at the
caching code.

---

## Part 7 — Reading `Logs.md`

Every single event the dashboard shows also gets mirrored, live, into a
plain file at the repo root (`Logs.md`), reset at the start of each batch
and appended to in the same order the dashboard displays things. A
finished run's `Logs.md` genuinely *is* the complete, unambiguous story of
that one run: every step the cold-run agent took (in its own,
plain-English words), every cache hit and miss with its specific reason,
every error, the record-by-record done/failed tally, the final
verification numbers, and the reconciliation pass's fixed/still-open
count — all in one ordinary text file, readable start to finish, and easy
to hand to someone else (or paste into a future debugging conversation)
without needing to dig through raw server logs or reconstruct what
happened from scattered pieces.

---

Next: `04_ARCHITECTURE.md`, for what's actually happening underneath all
of this — the concurrency model, the locks, and exactly how a hundred
records end up safely sharing one browser-taught cache at once.
