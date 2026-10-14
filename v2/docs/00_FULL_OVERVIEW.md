# 0. Full overview — everything that's happening, in one place

This is the long version. The numbered docs (`01` through `05`) are
short, focused reference pages you can dip into later; this one is meant
to be read start to finish and leave you with the *whole* picture — what
this is, why every piece exists, what actually happens when you click
Start, and the real story of how it got to its current, working shape.
No code snippets, but nothing conceptual is skipped either.

---

## Part 1 — Why this exists

The real goal, underneath everything, is automating data entry into a
**real company ERP** — the boring, repetitive, error-prone work of taking
a big list of structured records (people, products, orders, whatever) and
getting each one entered correctly into an internal system's web form,
one record at a time, hundreds or thousands of times over.

That's not a new problem, and "point an AI browser agent at it" is not a
new idea either — an LLM that can look at a page, decide what to click,
and act, can absolutely fill in a form correctly. The problem with doing
that naively is cost and speed: if every single record gets its own full
LLM-driven pass through the form — "what does this page look like, what
field is this, what should I click, did that work, what's next" — you're
paying for the model to *rediscover the same form* over and over. A form
with ~18 fields can take 10-15 LLM calls to fill once. A 500-record batch
done that way is 5,000-7,500 LLM calls to fill in the literal same shape
of form, five hundred times.

Before pointing anything at a *real*, live company ERP — where mistakes
cost real time, real data integrity, and real trust — this project builds
and rehearses the whole approach against a safe, disposable stand-in
first: **DataDesk**, a small React + Supabase "People management" app,
built specifically to be a realistic testing ground. It has normal form
patterns (text inputs, dropdowns, a checkbox, a multi-step feel to some
interactions), a real database behind it, and — critically — that
database can be read directly to independently check whether the
automation actually did what it claimed. That last part matters a lot;
see Part 4 for why.

So: this whole project is really two things bolted together —

1. **v1**: a straightforward LLM-driven browser agent (browser-use +
   Gemini) that fills in DataDesk's form, one record at a time, fully
   live every time. This alone proves the *concept* works, but it's the
   expensive, slow version — every record pays full LLM price.
2. **v2** (what actually runs today, and what this whole doc is about):
   a caching layer built *on top of* v1, whose entire purpose is turning
   "every record costs a full LLM pass" into "the first record teaches
   the system, every record after that replays for free."

---

## Part 2 — The building blocks

**browser-use.** An existing, external library (not written here) that
gives an LLM a real, controllable Chrome browser and a loop: look at the
current page (a structured snapshot of what's clickable/typeable), decide
one action, perform it, look again, repeat, until it believes the task is
done. It's the thing that actually understands "this looks like a
dropdown, I should click it and wait for options" or "this text field's
label says Last Name." This project doesn't reimplement any of that
reasoning — it wraps browser-use and, separately, *watches* what it does.

**Gemini.** The actual LLM behind browser-use's decisions here
(`gemini-3.5-flash-lite`, specifically, on Google's free tier — which
matters later, because free tiers have real rate limits that showed up
during testing more than once).

**Playwright.** A plain, no-LLM browser automation library — "click this
exact element," "type this exact text into this exact input," nothing
smarter than that. This is what actually does the *fast* part: replaying
an already-learned interaction with a new value, at ordinary browser
speed, with zero AI involved and zero AI cost.

**DataDesk + Supabase.** The test target. DataDesk is the web app with
the form; Supabase is its database. Whenever DataDesk's form successfully
submits, a new row lands in a `people` table in Supabase. Because that
database can be queried directly and independently, this project has a
genuine, trustworthy way to check "did the data actually end up correct,"
completely separate from whatever the automation *believes* happened —
which turns out to be an essential design choice, not a nice-to-have (see
the self-report problem in Part 4).

**FastAPI backend + dashboard.** The actual v2 codebase (`v2/`) is a
small FastAPI server that: accepts "run this batch of records" requests,
drives the whole cold-run/replay/cache/verify/reconcile process described
below, streams live progress out over Server-Sent Events, and serves a
one-page dashboard (`static/index.html`) with a Start button, a live log,
and running stats.

---

## Part 3 — The one big idea, in full

> **The first record that needs a given form teaches the system where
> everything on that form is. Every record after that just replays what
> was learned, dropping in its own values — no LLM call needed, unless
> something genuinely new comes up.**

Concretely: the very first time this runs against a brand-new form (empty
cache), it behaves exactly like plain v1 — a full, live, LLM-driven pass,
click by click, dropdown by dropdown, all the way to submit. But while
that's happening, a small watcher is recording *which DOM element*
browser-use interacted with for *which field* — not the value it typed,
just "this specific dropdown, at this specific spot in the page, is where
the `department` field lives." That gets saved to a small SQLite database
(`storage/cache.sqlite3`) as the cache.

Every subsequent record for that same form skips the LLM almost entirely.
Instead, a plain Playwright script walks through the cached list of
steps — find this element, do this action — except this time it fills in
*that record's own* value, not the first record's. Locating "the same
element as before" isn't just re-using a raw coordinate; there's a small
tiered strategy for it (an exact DOM path first, a backup attribute-based
selector second, and a sanity-check hash of the element's tag+attributes
as a third confirmation) so replay stays robust even if the raw DOM path
shifts slightly between page loads.

If a step *can't* be replayed — the cached element genuinely isn't there
anymore, or the action throws an error — that's a "miss," and it doesn't
stall the batch. It falls back to a scoped, single-field LLM call: not
"redo this whole form," just "find and fill in *this one field*, and
stop." Whatever that live call figures out gets written back into the
cache, overwriting the stale entry — so the *next* record that needs this
same field gets a clean cache hit instead of paying for another live
call. A first-ever cache miss for a given field costs exactly one live
LLM call, total, across the whole batch — not one per record.

This is genuinely the entire idea. Everything else in this document is
either "how that idea survives running a hundred records at once without
breaking" or "the story of the specific ways it *did* break, and what
that taught the design."

---

## Part 4 — The full lifecycle of a batch, start to finish

This section walks through everything that happens between clicking
"Start Batch" and seeing a final result, in the order it actually
happens.

### 4.1 — Loading records and starting up

The dashboard's Start button hits the backend, which loads a JSON file of
records (each one a flat dictionary of field → value, e.g. first_name,
email, department, salary...) and kicks off the whole batch on a
background thread, so the HTTP request returns immediately and the
dashboard can watch live progress instead of hanging on one giant
request.

### 4.2 — Dispatch: up to N records running at once

Rather than doing records one at a time, the batch dispatches all of them
into a small pool of concurrent "slots" (a configurable number — this
project has run this at up to 15 at once). Each slot gets its own,
completely isolated, disposable Chrome browser profile and its own
debugging port, so N records can genuinely run in true parallel Chrome
windows without any risk of two records accidentally sharing state or one
record's browser session leaking into another's.

### 4.3 — The very first moment: who gets to be "record 1"?

Here's a subtlety that isn't obvious at first: if 15 records all start at
once against a completely empty cache, naively you'd get 15 *simultaneous*
cold runs — 15 browsers all independently discovering the same form from
scratch, 15x the LLM cost you were trying to avoid, and 15 sets of
learned steps all racing to write into the cache at once.

The actual behavior: only one record is ever allowed to be "the one
teaching the cache" for a given form at a time. Every other record that
finds an empty cache doesn't start its own cold run — it just *waits*
(without even opening a browser yet, so nothing is wasted) until whichever
record is currently seeding the cache finishes. If that first attempt
happens to fail outright (a genuinely dead API, say), the *next* waiting
record automatically becomes the new attempt — one at a time, retried
until one succeeds, never a stampede. The moment the cache is populated,
every record still waiting proceeds straight into fast, LLM-free replay.

### 4.4 — Per-record replay: the actual step-by-step decision

For a record that's replaying (not cold-running), it walks its list of
cached steps in order. For each one: try to find the cached element on
the current page, and if found, perform the action with *this record's*
value. A miss (element not found, or the action fails) triggers a small
decision:

- Is this field's "circuit" currently open (meaning it's failed
  repeatedly, recently)? If so, skip it entirely — no LLM call, no
  waiting — and move on, leaving this one field blank/wrong on this one
  record for now.
- Otherwise, make one scoped live LLM call for just this field, and
  either succeed (self-healing the cache for future records) or record
  the failure (which counts toward opening that field's circuit).

**Why the circuit breaker exists at all**, concretely: during early
testing, a field's live-fallback calls started failing repeatedly because
Gemini's API itself was returning errors for a stretch. Without any
protection, every remaining record in the batch queued up to *also* try
that same doomed field, one after another, each one burning real wall-
clock time on retries before giving up — turning a temporary API hiccup
into the batch's entire runtime. The circuit breaker's rule: after two
consecutive failures on the same field, stop trying it for 30 seconds;
after the cooldown, the next record gets to try again, and if the
underlying problem has cleared, everything goes back to normal on its
own. This is also *why* a whole class of test-data mistakes later in this
project's life (see Part 6) looked, at first, like intermittent flakiness
rather than a hard, permanent failure — the circuit breaker was doing
exactly its job of not hammering something that couldn't succeed, which
made the *real* root cause (invalid dropdown values, not API flakiness)
briefly harder to see.

### 4.5 — What actually counts as the record "succeeding"

This is a deliberate, easy-to-get-wrong distinction: a record is marked
successful if, and only if, the final **submit** action worked. A record
that submitted fine but has one optional field wrong or blank (its live
fallback genuinely failed) still counts as an overall success — because
it *did* land in the target system; that's a data-quality gap for a later
pass to fix, not proof the automation itself failed.

Getting "which step is the submit step" right turned out to be less
obvious than it sounds — a cached form fill doesn't come with a label
saying which cached action was the final submit click; the code has to
infer it. The first, simpler approach ("whichever cached step happens to
sort last") worked until a live run showed the LLM agent, while building
the very first cache entry, doing something harmless-looking but
disruptive: re-selecting a dropdown value *after* it had already clicked
submit — maybe double-checking itself, maybe an artifact of how it
decided the task was done. That redundant, trailing action ended up
sorting *after* the real submit click, got mistaken for it, and every
subsequent replay of that "submit" tried to submit the form with no value
at all (since a real submit click never needs one) — silently failing
every time despite the actual, real submit having already succeeded
earlier in that same replay. The fix: identify the submit step by what it
*is* (a plain click tied to no specific field — submit buttons don't
carry field-specific data, everything else that gets cached does),
instead of by *where it happens to sort*. This kind of thing — a
reasonable-looking shortcut breaking under a real, live, slightly
unpredictable model — is the through-line of most of this project's
actual debugging history (Part 6).

### 4.6 — Confirming the write actually landed

A subtler problem than "did the click work": DataDesk's own submit
handler kicks off an asynchronous database write and doesn't wait around
for it — Playwright's click returns the instant the click *event* fires,
not once whatever that click triggered has actually finished. If the
browser tab gets closed immediately after (to free up that concurrency
slot for the next record), an in-flight write can get aborted mid-flight,
completely silently: the automation logs a clean success — every field
hit, no errors — and the record simply never ends up in the database at
all. This is the single most dangerous class of failure in the whole
project, because every signal the automation produces says everything is
fine.

The fix that actually holds up: instead of guessing with a fixed delay
before closing the browser (which worked at small scale and quietly
stopped being enough once quite a few browser windows were genuinely
hammering the same database at once), the code now waits for the *actual*
network response confirming the database write happened, before ever
closing that tab — with a generous timeout, and structured so that a slow
(but real) response can never be mistaken for a failed one. Getting that
distinction — "slow ≠ failed" — right on the first attempt turned out to
be its own small saga (again, Part 6).

### 4.7 — Batch done — but "done" isn't graded by trusting itself

Once every record has either succeeded or failed, the batch reports a
tally. But — and this is a firm rule throughout this project — **the
automation's own opinion of what happened is never the final grade.** A
completely separate step reads the real target database directly, field
by field, for every single record, and compares what's actually there
against what should be there. This is what actually catches the class of
bug described in 4.6 (a record that reports success but never truly
landed) and any record that landed but has some field subtly wrong. The
numbers you see after a batch — "X/Y fields correct," "N records not
found" — come entirely from this independent check, not from the
automation grading its own homework.

### 4.8 — Reconciliation: closing the loop afterward

The main batch prioritizes speed — an actively-failing field gets skipped
rather than stalling everything, which is the right tradeoff mid-batch
but isn't the end state anyone actually wants. Right after the
verification step above, a follow-up pass starts, on its own background
thread, deliberately decoupled from "is a new batch running" (so the very
next batch can start immediately while this one's cleanup quietly
continues): it looks at exactly the fields verification flagged as wrong,
on records that *did* land successfully, and — skipping anything whose
circuit is still open, since there's no point retrying something already
known to be currently broken — retries just that one field, on that one
record, through the target's *edit* flow (finding the existing row,
opening its edit form, changing one field, saving) rather than the
create-a-new-record flow used everywhere else.

---

## Part 5 — Watching it happen: the dashboard and the logs

The dashboard (a single HTML page) connects to a live event stream and
shows: which record is currently running and whether it's a cold run or a
replay, a running tally of cache hits/misses/LLM steps, individual
field-level hit/miss/error lines as they happen, and the final batch and
verification summary once it's done. There's also an Abort button — it
stops *queueing new* records but deliberately leaves anything already
mid-flight to finish naturally rather than yanking a live browser session
out from under itself.

Every single event the dashboard shows is also mirrored, live, into a
plain file at the repo root: `Logs.md`. It resets at the start of each
batch and is written to as things happen, in the same order the dashboard
shows them — meaning after any run, that one file *is* the complete,
literal story of what happened: every step the cold-run agent took, every
cache hit and miss with its reason, every error, the final verification
numbers, and the reconciliation pass's outcome. This is genuinely the
easiest way to hand someone (or a future debugging session, including
this one) the full, unambiguous record of one specific run.

---

## Part 6 — The actual debugging story, narrated

This project's current shape is the direct result of a long list of real
bugs, found almost entirely by actually *running* the thing against a
live browser, live Gemini calls, and a live Supabase database — not by
guessing in advance what might go wrong. The full blow-by-blow, with
symptoms/root-cause/fix for each one, lives in `V2_BUGS_FIXED.md`; here's
the shape of that story, roughly in the order it happened.

**Early on**, running batches for the first time surfaced the basics of
"concurrent automation is harder than sequential automation": a cache key
that collided across similar steps and kept overwriting the wrong entry;
a browser closing before an async database write finished (the exact
failure described in 4.6, in its very first form); a checkbox that got
blindly toggled the same way for every record regardless of what that
record actually wanted; and fields that were simply never learned at all
if the very first record happened to leave them blank — meaning every
later record silently never got that field filled in either, since
nothing had ever taught the cache it existed.

**As concurrency scaled up** (moving from a handful of records at a time
toward 15 at once), new failure shapes appeared that only exist under
real parallel load: fifteen records all discovering an empty cache
simultaneously and each launching its own redundant cold run (fixed by
the seed-lock behavior in 4.3); many records queuing up to independently
retry the exact same doomed field one at a time during a genuine Gemini
outage, each one burning real time before giving up (fixed by the circuit
breaker in 4.4); and — a step further — a single record's own unexpected
failure (DataDesk being briefly unreachable, for instance) propagating
outward and silently cancelling every *other* record still in flight
across the whole batch, because of how the underlying concurrency
primitive is allowed to behave by default. That last one is a particularly
sharp lesson: one record's bad luck should never be allowed to take down
ninety-nine unrelated records' worth of already-good work, and getting
that isolation right took an explicit, deliberate fix.

**A full, deliberate static code-review pass** (reading every file
end-to-end looking for what live runs simply hadn't exercised yet, rather
than waiting to trip over them) turned up a cluster of related issues in
one sitting: a shared database connection that wasn't actually safe once
a second concurrent background pass (reconciliation) started existing;
a subtle timing gap where two near-simultaneous "start a batch" requests
could both slip through a guard meant to prevent exactly that; two
different kinds of background events reusing the same signal in a way
that could make one unrelated process's failure visibly (and wrongly)
disrupt a completely different, currently-running batch's display; and a
lock that, in one specific unlucky failure path, could stay held forever
and quietly disable this project's ability to ever cold-start that
workflow again.

**Then a live 100-record run surfaced the submit-misidentification
bug** described in 4.5 — a genuinely successful batch of records getting
marked as failed because of exactly the "which cached step is submit"
ambiguity walked through above, discovered only because the underlying
LLM's own behavior (a harmless-looking redundant re-selection after
already submitting) happened to trigger it.

**A 200-record run at higher concurrency then surfaced the write-
confirmation problem** from 4.6 in earnest — 14 records reporting clean
success while genuinely never reaching the database — followed
immediately by a second, self-inflicted bug in the *fix* for that first
one: a misunderstanding of exactly when a particular browser-automation
timeout actually fires meant the corrected code could, under real load,
turn a *slow but genuinely successful* database write into a *falsely
reported failure* — 140 records marked failed despite every single one of
them having actually landed correctly. Both needed fixing, and both are
now verified against real, live, concurrent runs, not just reasoned about
in the abstract.

**Separately, and completely unrelated to any of the above**, one
specific field (`department`) kept failing far more than any other field,
across multiple otherwise-clean runs, in a way that looked at first like
a caching bug — until directly inspecting the real target form's actual
dropdown live in a browser revealed the true cause: the test data being
generated for these runs was asking for department values (e.g.
"Logistics," "Procurement") that simply don't exist as real options in
that dropdown at all. The automation had been faithfully, correctly
reporting a real, permanent failure the entire time; the bug was in the
test data, not the automation. Once the generated test data was corrected
to only use values that are genuinely selectable, that entire class of
failure disappeared completely — a good reminder that "the automation
looks broken" and "the automation IS broken" aren't always the same
thing, and it's worth checking the target system directly before assuming
which one you're looking at.

The throughline across almost all of this: the bugs that mattered were
found by actually running the system under real, sometimes messy,
sometimes concurrent, sometimes rate-limited conditions — and each fix
made the next real run a genuinely better test of whatever was fixed
before it, rather than a repeat of the same shallow, low-concurrency
happy path.

---

## Part 7 — Is any of this tied to DataDesk specifically?

Covered in full, with a diagram, in `05_PORTABILITY.md` — the short
version: the entire caching engine (the learn-once/replay-forever
mechanism, the concurrency and locking, the circuit breaker) has no
DataDesk-specific knowledge anywhere in it and would work against a
completely different form-based website unchanged. A small number of
things are DataDesk-flavored (mainly the wording of the prompt used for
the reconciliation/edit flow) and would need a contained, scoped rewrite.
The one real structural assumption — reading the target's own database
directly to grade correctness — exists *because* DataDesk was deliberately
built to allow that, specifically so this project could have a
trustworthy way to test itself; a real external ERP target likely
wouldn't offer that same access, and would need a different verification
approach.

---

## Part 8 — Quick glossary

- **Cold run** — the first, fully live, LLM-driven pass through a form
  that hasn't been cached yet.
- **Replay** — filling in a record using only cached steps, no LLM call.
- **Cache hit / miss** — whether a given cached step could be located and
  performed on the current page.
- **Live fallback** — the scoped, single-field LLM call used to recover
  from a miss.
- **Self-healing** — overwriting a stale cache entry with what a live
  fallback just learned, so future records get a hit instead.
- **Circuit breaker** — the mechanism that stops repeatedly retrying a
  field that's currently, genuinely broken, and lets it try again after a
  cooldown.
- **Reconciliation** — the follow-up pass, after a batch, that retries
  specific known-wrong fields via the target's edit flow.
- **Verification** — the independent, database-direct check of what
  actually happened, which is the real source of truth throughout this
  entire project.
