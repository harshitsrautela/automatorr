# terminautomator v2 — Caching Layer for Browser-Automated Data Entry

> This document is written to be turned into a slide deck (10-15 slides) by
> NotebookLM, for a presentation to a manager. Each `##` section below is
> intended as ONE slide. For every slide there are three parts:
> - **On-slide bullets** — the short, punchy text that should actually
>   appear on the slide itself. Keep these as bullets, not paragraphs.
> - **Speaker notes / detail** — the deeper background, real numbers, and
>   stories behind that bullet list. This is context for whoever presents
>   the deck (or for NotebookLM to draw on when it writes the slide), not
>   text meant to be crammed onto the slide itself.
> - **Visual** — a one-line suggestion for what diagram or graphic that
>   slide should show.
>
> Do not merge slides together. Do not compress the speaker notes onto the
> slide itself — they exist so the presenter (or the model building the
> deck) has real depth to draw from, while the slide itself stays clean.

---

## Slide 1: Title

**On-slide bullets:**
- terminautomator v2
- Adding a caching layer to LLM-driven browser automation
- Built on top of v1 — my own addition is the caching/replay layer

**Speaker notes / detail:**

This project has two layers, built at two different times, by different
efforts. The first layer — referred to throughout this deck as "v1" — is a
working LLM-driven browser automation agent. It uses an open-source library
called `browser-use`, paired with Google's Gemini model, to look at a real
web form and fill it in: read the page, decide what each field is by its
label, type the value in, click the right dropdown options, and submit.
That part was already built and already demonstrated. It works well, and
it is a genuinely capable piece of technology — it does not need to be told
in advance where anything is on the page. It figures the form out fresh
every single time, purely by reasoning about what it sees, the same way a
person would if handed a form and a list of values to enter.

The second layer — "v2" — is the actual subject of this presentation, and
it is the part that was designed and built independently, on top of v1,
without changing v1's own code. The problem v2 solves is not "can an LLM
fill in a form" — that was already answered. The problem is: what happens
when you need to do this hundreds of times in a row, for the same shape of
form? v1 has no memory between one record and the next. It re-discovers the
entire form, field by field, at full cost, every single time. That
becomes extremely expensive and extremely slow the moment the workload
stops being "one record" and becomes "a real batch of data entry" — which
is the actual, realistic shape of the work this project exists to solve.

v2 is a caching and replay system that sits around v1's exact, unmodified
agent. It watches what the live agent does the first time it encounters a
new form, remembers it, and then replays those actions — using a
completely different, much cheaper, non-AI tool — for every record after
that. The live LLM agent is never removed from the system; it remains
available at all times as an automatic fallback the moment anything
doesn't match what was learned. This deck walks through why that was
necessary, how it actually works end to end, how it has been tested and
hardened through real, documented debugging (not just a one-off demo), and
what is deliberately not built yet.

**Visual:** Simple title slide — project name, one-line subtitle, maybe a
small "v1 → v2" arrow motif to signal this builds on prior, already-shown
work rather than replacing it.

---

## Slide 2: The Starting Point (v1, already shown)

**On-slide bullets:**
- v1: Gemini + `browser-use` drives a real Chrome browser
- Reads the page, decides what each field is, types values, submits
- No hardcoded selectors — figures the page out fresh every time
- Works well for filling in a single record

**Speaker notes / detail:**

v1's design is intentionally simple and intentionally general. For every
record, it constructs a plain-English instruction — something like "enter
the following field values exactly as given, matching each value to the
form field with the closest matching label" — and hands that instruction,
along with a live, controllable browser session, to an LLM-driven agent.
The agent then takes a series of steps: it looks at a structured snapshot
of the current page, decides on one action (click this, type that, select
this dropdown option), performs it, looks again, and repeats until it
believes the task is complete.

The reason this approach was chosen over a traditional, hand-written
automation script is durability against change. A traditional script is
built against a fixed set of element selectors — CSS classes, element IDs,
XPath expressions written in advance by a developer who has studied the
page. The moment the target page's layout shifts even slightly, that
script silently breaks. The LLM-driven approach has no such fixed
assumptions baked in; it reasons about the *current* state of the page
every time, the same way a human data-entry worker would if the form
layout changed overnight. That resilience is valuable, and it is not
something v2 discards — the live agent is still exactly how v2's cold
runs and fallback fixes work. v2 does not replace this reasoning
capability; it only avoids re-paying for it when nothing has actually
changed.

v1 also already established the discipline that everything downstream in
this project follows: the automation's own opinion about whether it
succeeded is treated as informational, not authoritative. This distinction
becomes critical later in this deck (see the "self-report" slide) and it
already existed as a design principle in v1, before v2 was ever built.

**Visual:** A simple linear icon flow: source record → LLM agent (with a
small browser icon) → target form → "submitted" checkmark. Keep it clean;
this slide sets up the baseline, the next slide introduces the problem
with scaling it.

---

## Slide 3: The Problem With Doing This 500 Times

**On-slide bullets:**
- The agent has no memory between records
- Record #500 costs exactly as much as record #1
- Estimated cost for an 18-field form: 10-15 LLM calls per record
- For 500 records: an estimated 5,000-7,500 LLM calls
- Every one of those calls is paying, again, for something that never
  actually changes: *where the fields are on the page*

**Speaker notes / detail:**

This is the actual motivating problem for the entire v2 effort, and it is
worth stating with real numbers rather than vaguely. Picture a list of 500
people that need to be entered into an internal "Add Person" style form —
the kind of form present in essentially every ERP, CRM, or internal admin
panel: first name, last name, email, phone, a department dropdown, an
employment-type dropdown, a salary field, a notes box, a checkbox, perhaps
fifteen to twenty fields in total, one submit button at the end.

v1's agent can absolutely fill in that form correctly, one record at a
time, entirely on its own. That is not in question. What is in question is
the *cost* of doing that 500 times in a row for the *same shape of form*.
Every single field the agent fills in costs the same "look at the page,
decide, act" LLM round-trip whether it's the first record or the
five-hundredth — the model has no built-in memory that says "I already
figured out where the department dropdown is an hour ago, for a different
person." A representative estimate for a form of this size is 10 to 15 LLM
calls per record: one call to register a click here, a batch of a few
fields there, an extra call when a dropdown needs a moment to render, and
so on. For 500 records, naively, that is somewhere between five thousand
and seventy-five hundred LLM calls, spent entirely on re-discovering the
literal same page layout, five hundred separate times.

This is not a hypothetical concern, either — it directly collided with a
real, practical constraint during development: Gemini's free tier caps
requests at 15 per minute, and separately, at 500 per day. A single
record's own natural pacing stays comfortably under the per-minute limit.
Attempting genuine record-level concurrency under v1 alone — running many
records' agents at once, each hammering the same shared quota — was tried
early on and failed outright, every single record coming back with a
`429 RESOURCE_EXHAUSTED` error, because concurrent agents share that same
quota ceiling; more browser windows does not mean more API-request
capacity. That constraint is a big part of why a smarter design was
necessary, not just a nice-to-have: doing this at any real scale, and
doing it with any real speed, was never going to work by simply running
v1's agent in a loop, or even by running many copies of it in parallel.

**Visual:** A simple two-bar or flat-line chart: "LLM calls needed"
plotted against "record number," showing a flat, high line all the way
from record 1 to record 500 under the old approach — setting up the
contrast for the next slide, where the line will instead drop to near
zero after record 1.

---

## Slide 4: The Core Idea

**On-slide bullets:**
- "The first record teaches the system. Every record after that just
  replays what it learned."
- Record #1: expensive — live LLM run, learns the whole form
- Record #2 onward: cheap — plain browser automation, zero AI involved
- Anything genuinely new: falls back to the LLM for just that one thing,
  then remembers the fix so no future record pays for it again

**Speaker notes / detail:**

This single idea is the entire reason v2 exists, and everything else in
this deck is really just an elaboration of how this one sentence gets
implemented safely and correctly under real, messy, concurrent conditions.

Record #1, for any given form, is genuinely expensive — there is no way
around that, and no attempt was made to avoid it. The live LLM agent runs
exactly as v1 always did: it reads the page, figures out every field,
every dropdown, every click, all the way through to a successful submit.
The only difference from plain v1 is that this run is being quietly
watched the entire time (more on exactly how, and why that does not
require modifying the underlying library, on the next slide).

Records #2 through however many follow are a completely different story.
Instead of invoking the LLM agent again, a separate, much simpler,
completely non-AI tool called Playwright takes over. Playwright is handed
the exact locations and actions that were learned from record #1, and it
replays them — clicking the same button, filling the same input, selecting
from the same dropdown — except it drops in *this* record's own values
instead of the first record's values. There is no "look at the page and
decide" step at all in this path. It already knows exactly what to do and
exactly where to do it, so it simply does it, at whatever speed a real
browser can physically execute a click or a keystroke.

The system is never brittle in the way a traditional fixed automation
script would be, because the live LLM agent is never removed from the
picture. If a replay attempt fails — the element cannot be found, the
page has changed slightly, a genuinely new field shows up in this record
that no earlier record ever had a value for — the system automatically
falls back to a scoped, single-field live LLM call to fix *just that one
thing*, on the same browser tab, so nothing else already correctly filled
in gets disturbed or redone. Whatever that live fallback learns gets
written back into the cache immediately, so the very next record that
needs the same field benefits from the fix without ever needing its own
live LLM call.

Run the cost math from the previous slide again with this design in place:
instead of 5,000 to 7,500 LLM calls for 500 records, the realistic number
is roughly 10 to 15 calls for record #1, plus a small, bounded number of
extra calls scattered across the rest of the batch for anything genuinely
new — often landing in the range of a few dozen total LLM calls for an
entire 500-record batch, not five thousand. This is not an incremental
optimization. It is a fundamentally different cost curve, and it is the
entire reason this project exists in its current form rather than simply
running v1's plain agent in a loop or in parallel.

**Visual:** The core flow diagram — Record #1 → Live LLM Agent → Cache;
Record #2 through N → Cache → Fast Replay (Playwright, no AI icon). Use
two distinctly different visual styles for the "expensive" path and the
"cheap" path (e.g. a warm color for the LLM path, a cool color for the
replay path) to make the cost contrast visually obvious at a glance.

---

## Slide 5: What Actually Gets Cached

**On-slide bullets:**
- Not code. Not screenshots. Just data.
- What gets saved: where an element is (a locator), what kind of action
  it is, and which record FIELD it maps to
- Replay is a small, fixed dispatcher — reads the row, calls the matching
  browser action
- No LLM is ever asked to "write automation code"

**Speaker notes / detail:**

It is worth being precise about this, because it is easy to imagine the
caching layer as something more exotic than it actually is — it is not.
There is no generated code anywhere in this system, no script that gets
written and then executed, and nothing resembling a macro recorder in the
traditional sense. What actually gets stored, for every learnable action
the live agent takes, is a small, plain row of data: a way to locate the
element on the page again later, what category of action it was (a plain
click, typing text into a field, selecting a dropdown option, or toggling
a checkbox), and — critically — which of the record's own data fields that
action corresponds to.

That last point deserves emphasis, because it is the detail that makes
replay actually useful rather than dangerous. The system does not cache
the literal text the first record typed. If it did, every future record
would have the first record's own name, email, and department typed into
its form, which would obviously be wrong and useless. Instead, immediately
after the live agent types or selects a value, the system works backward:
it compares what was actually typed against the first record's own known
field values, using the same comparison logic that is later used to grade
correctness against the real database, and figures out *which field* that
value came from. That field NAME — not the value — is what gets stored.
On replay, whatever field name is stored gets looked up fresh in whichever
record is currently being processed, and that record's own value gets
dropped into the same location.

Replaying a cached action is then almost embarrassingly simple by design:
a small, fixed piece of logic reads the stored action type and calls the
one matching, ordinary browser action for it — click this, fill that,
select this option from that dropdown, check the current state of this
checkbox before deciding whether to click it. There are exactly four kinds
of actions that get cached today, matching the four kinds of interactive
elements the test form actually uses. Nothing about this dispatcher
involves an LLM, a generated script, or anything unpredictable. It is
readable, boring, and exactly as fast as directly controlling a browser
allows — which is the entire point.

**Visual:** A simple table mockup with four example rows: one column for
"what kind of action," one for "which field it maps to," one for "roughly
where it is on the page." Keep the example values generic and illustrative
(e.g. "text field" / "first_name", "dropdown" / "department").

---

## Slide 6: End-to-End Workflow

**On-slide bullets:**
1. User clicks **Start** on the dashboard
2. A batch of records loads from a file
3. Up to 10 records run at once, each in its own isolated browser window
4. First record with no cache yet → live LLM learns the form
5. Every other record → fast replay from cache
6. A cache miss mid-replay → scoped live fix for just that one field
7. Batch finishes → an independent check reads the real database directly
8. A background cleanup pass retries anything that check found wrong

**Speaker notes / detail:**

This slide is meant to be the one that actually answers "what happens,
start to finish, when I click the button" — walked through in full, since
that is the story a manager watching a live demo will actually see happen
in front of them.

It starts with a simple dashboard: a Start button, a live status line, a
running summary of hits/misses/successes, and a scrolling log of exactly
what is happening in real time. Clicking Start loads a batch of records
from a file and immediately begins dispatching them — up to ten at a time,
each one getting its own completely separate, disposable browser window,
so that no two records can ever interfere with each other's typed state or
navigation.

For the very first record that needs a given form (and only ever one
record at a time, no matter how many are running concurrently — this is
deliberately protected against, see the next slide), the system recognizes
that nothing has been learned yet, and hands that one record's full task
over to the live LLM agent exactly as v1 always worked. Every other
record, running alongside it, checks the same shared cache and finds it
already has everything it needs once record #1 succeeds, so it proceeds
immediately into fast, LLM-free replay.

During replay, if a specific field's cached action ever fails to apply
cleanly — the element cannot be located the same way it was before, or the
action does not go through — the system does not fail that field silently,
and it does not fail the whole record over it either. It logs the miss,
falls back to a small, scoped live LLM call whose only job is to fix that
one field, and continues on. That live fallback's result gets written
straight back into the shared cache, so the very next record that needs
the same fix benefits automatically.

Once every record in the batch has either succeeded or failed, the batch
is not simply declared "done" and trusted at its own word. A completely
separate verification step reads the real target database directly, field
by field, for every single record, and compares what is actually sitting
there against what was supposed to be there. This is the step that
produces the real numbers presented after a run — "X out of Y fields
correct," "N records not found" — and it is intentionally independent of
whatever the automation itself believed happened.

Finally, because keeping the main batch moving quickly sometimes means
deliberately skipping a field that is currently, temporarily broken rather
than stalling everything behind it, a background reconciliation pass kicks
off immediately after verification finishes. It looks specifically at
fields the verification step found wrong on records that did otherwise
land successfully, and retries exactly those fields through the target
system's own edit flow — finding the existing record, opening its edit
form, fixing the one field, saving. This runs quietly in the background;
a brand-new batch can be started again immediately without waiting for it.

**Visual:** A horizontal, left-to-right pipeline diagram with the eight
numbered steps above as boxes, arrows connecting them in sequence. Use a
small branch/fork icon at step 4 to show the "cold vs. replay" split, and
a small loop-back icon at step 8 to show reconciliation feeding back into
already-submitted records.

---

## Slide 7: Concurrency, Done Safely

**On-slide bullets:**
- Up to 10 records run at once, each in its own isolated browser window
- Only ONE record is ever allowed to "teach" the system at a time
- A field that's currently failing hard gets skipped for a cooldown period
  instead of stalling everything behind it
- One record's crash can never take down the other 9

**Speaker notes / detail:**

Running many records at once safely turned out to require real, deliberate
engineering — not just "start ten of them and hope for the best." Two
mechanisms make this safe, and both exist directly because of real
failures observed while testing this system live, not because they were
anticipated in advance on a whiteboard.

The first is what can be described as a seed lock. Since only the very
first record to encounter a brand-new form should ever need to pay the
full live-LLM cost, there has to be a guarantee that exactly one record is
ever doing that at a time, even when ten records are all dispatched
essentially simultaneously and all of them find an empty cache at once. A
naive one-time decision made before the batch even starts — "compute once,
in advance, which single record will be the one that seeds the cache" —
turns out not to be safe, because it only protects against a pile-up if
that first attempt actually succeeds. If it fails outright, every other
concurrently-running record would independently notice the cache is still
empty and each start its own full, live, expensive attempt at the same
time — turning "only one record should ever use the LLM live" into
"potentially ten records all use the LLM live at once," the exact opposite
of the intended behavior. The actual fix is a real runtime lock: whichever
record gets there first claims the responsibility, every other record that
finds the cache empty simply waits, and if the first attempt fails, the
very next waiting record automatically and safely becomes the new attempt,
one at a time, until one succeeds.

The second mechanism is a circuit breaker, added after a live run
surfaced a genuinely different failure mode. At one point, external
conditions meant that every single live-LLM attempt at fixing one specific
field failed, over and over, for an extended stretch. Without protection
against this, every record that needed that field queued up single file
behind the same lock, and every one of them still burned a full, slow,
doomed live-agent attempt before the next one even got a turn — meaning a
large batch spent the overwhelming majority of its wall-clock time
serially losing to something that was never going to succeed at that
moment anyway. The fix: after a field fails a small number of times in a
row, it is marked as "open" for a short cooldown window, during which
every subsequent record skips the live attempt entirely for that one
field — no waiting, no LLM call, just an immediate, cheap skip — until the
cooldown passes and the next record gets a fresh, real attempt. This keeps
the whole batch moving at speed even while one specific thing is
temporarily broken, rather than letting one bad field hold the entire
batch hostage.

On top of both of these, every individual record's work is isolated from
every other record's: a browser crash, a network hiccup, or any other
unexpected failure for one record is guaranteed to only ever fail that one
record, never cascade and take down the nine others running alongside it.
This specific guarantee was itself the fix for a real, confirmed incident
during testing, where a single record's browser trouble once cancelled an
entire in-progress batch.

**Visual:** A diagram showing ten parallel horizontal lanes (representing
ten concurrent records), with one lane highlighted/locked (the current
"teacher") while the other nine wait or replay freely; a small "cooldown
timer" icon on one lane to represent the circuit breaker concept.

---

## Slide 8: Never Trust the Robot's Own Opinion

**On-slide bullets:**
- The automation's self-reported "success" is never used for grading
- After every batch, a separate step reads the real database directly,
  field by field
- This is a firm rule, learned from real incidents, not a theoretical
  precaution

**Speaker notes / detail:**

This is one of the most important design principles in the entire
project, and it is stated here as plainly as possible because it directly
shaped multiple real fixes described later in this deck: whatever the
automation itself believes happened to a record is never trusted as the
final word on whether that record is actually correct.

The reasoning behind this rule is not abstract caution — it came directly
from real, observed failures. At one point during testing, a batch of
replayed records each reported a clean, successful run: every field
matched, no errors logged, the submit action appeared to go through
without incident. And yet, when the real target database was checked
directly, none of those "successful" records had actually landed. The root
cause was a timing issue: the target application's own submit handler
kicks off an asynchronous write, and the automation's click on the submit
button returns as soon as the click itself registers, not after whatever
work that click triggers actually finishes. The browser window was being
closed before the real write had a chance to complete, silently discarding
it, while the automation itself had no way of knowing that had happened
and reported success anyway.

Because of failures exactly like this, the system now treats "what the
automation believes" and "what actually happened" as two entirely separate
questions with two entirely separate sources of truth. The first is
recorded purely for diagnostic purposes — useful for understanding what
the agent thought it was doing, but never used to decide whether a record
counts as correct. The second — an independent step, run once per batch,
that connects directly to the real target database and compares every
single field of every single record against what the source data actually
specified — is the only number that is ever reported as the batch's real
outcome. This is also the step that catches the specific class of failure
above: a record that reported success but genuinely never landed shows up
clearly as "not found" once the real database is checked.

This discipline is also what makes the reconciliation step (the next
slide) trustworthy in the first place: it only ever acts on gaps the
independent database check actually found, never on the automation's own
guess about what might be wrong.

**Visual:** A simple two-path diagram: on one side, a robot/automation icon
saying "I'm done!" with an X or "ignored" mark next to it; on the other
side, a database icon being read directly, with a checkmark labeled
"trusted."

---

## Slide 9: Closing the Loop — Reconciliation

**On-slide bullets:**
- Some fields get deliberately skipped mid-batch to keep things moving
- Right after verification, a background pass retries exactly those wrong
  fields
- Uses the target system's own EDIT flow — finds the record, fixes the
  field, saves
- Runs quietly; a brand-new batch can start immediately without waiting

**Speaker notes / detail:**

The circuit breaker described earlier makes a deliberate trade-off: when
one specific field is currently, temporarily broken, the right choice for
the main batch is to keep moving and leave that one field wrong or blank
on whichever records hit it during the broken window, rather than let the
entire batch grind to a halt behind a single stuck field. That is the
correct trade-off for speed during the batch itself, but it is obviously
not the end state anyone actually wants — nobody wants "the batch finished
quickly but ten records have a wrong department field."

Reconciliation is the other half of that trade-off, and it runs
immediately after the independent database verification step described on
the previous slide finishes. It looks specifically for fields the
verification step flagged as wrong or missing, but only on records that
otherwise did land successfully in the database — a record that never
landed at all needs the full original creation flow attempted again, which
is a different, out-of-scope problem for this pass. For every field that
qualifies, and only if that field's circuit breaker has since closed again
(there is no point immediately retrying something already known to be
currently broken), the system retries just that one field through the
target application's own editing capability: it searches for the
already-existing record by its unique identifier, opens that record's
pre-filled edit form, changes the one field that needs fixing, and saves.

This is a structurally different task from the main batch's create flow,
and it uses its own separate, purpose-built instruction for the live
agent — one that explicitly assumes an existing record needs to be found
and edited, rather than a blank form needing to be filled in from scratch.
Attempting to reuse the main creation flow for this would either fail
outright or create an unwanted duplicate record for the same person.

Operationally, this pass is deliberately decoupled from the main batch's
own "is something currently running" state. It runs on its own background
thread, meaning a brand-new batch can be started immediately, with its own
dashboard and status all working normally, while an earlier batch's
cleanup work is still quietly finishing up in the background. This was a
deliberate choice: cleanup work should never block new, unrelated work
from starting.

**Visual:** A simple before/after pair: a record card showing one field
highlighted in red ("wrong"), an arrow through a small "reconciliation"
gear icon, then the same record card with all fields now green ("fixed").

---

## Slide 10: Built Through Real Debugging, Not Guesswork

**On-slide bullets:**
- 16 real issues found and fixed — from actually running the system live
- A checkbox that replayed the wrong boolean value for every record
- A submit button misidentified by its position instead of what it
  actually was
- A "success" that hadn't actually been saved to the database yet
- An LLM provider's daily usage quota running out mid-batch
- Every single fix is documented: what broke, why, how it was fixed, and
  how the fix was verified

**Speaker notes / detail:**

This slide exists to make an honest, specific case for why this system can
be trusted, rather than simply asserting that it works. Every one of the
issues summarized here was found by actually running the system against a
live browser, a live LLM, and a live database — not from reading the code
and guessing where problems might be, and not from a single clean demo run
that happened to go well.

A few of these are worth walking through by name, because each one taught
a real, permanent lesson that shaped the design described earlier in this
deck. A checkbox field was initially cached the same way as any other
click, with no awareness of which record's data it belonged to — every
future record replayed the exact same click regardless of what it actually
needed, meaning records that wanted the checkbox left unchecked
silently ended up checked instead. The fix made checkbox replay
state-aware: check the box's current state first, and only click it if
that state disagrees with what this specific record actually wants.

A submit button was, for a period, identified by assuming it would always
be whichever action happened to run last in the recorded sequence. A real
live run exposed exactly why that assumption was unsafe: the agent
occasionally re-selected a dropdown value one extra, redundant time after
already clicking submit, which meant that trailing, meaningless action
sorted after the real submit click and got mistaken for it — silently
causing every replay of that workflow to fail its "submit" step on a
nonsensical action, and wrongly marking otherwise-perfectly-successful
records as failed. The fix identifies submit by *what it structurally is*
— the last plain click that has no field of its own — never by
*whichever action happened to run last*.

The "success reported, nothing actually saved" issue described on the
previous slide is itself one of these sixteen documented incidents, and it
was actually rediscovered and fixed twice: the first fix introduced a
fixed short delay after every submit click, which worked at low
concurrency but was proven insufficient once many records were running
at once and the target system's own responses slowed under that load. The
second version of that same fix then introduced a subtle new bug of its
own — a misunderstanding of exactly when a particular waiting mechanism
actually raises its timeout — which very nearly caused the opposite
mistake: marking genuinely successful records as failed. Both were caught
through live retesting, not theoretical review, and the final, correct
version has since been confirmed stable across multiple real runs.

Most recently, a live demo run uncovered that the LLM provider being used
enforces two separate usage limits: a short-term, per-minute cap the
system was already built to absorb gracefully, and a separate, much
larger daily cap that, once exhausted, does not recover until the next
day. The first time this was hit, the system kept trying anyway, one
record at a time, each one burning real time rediscovering the exact same
dead end. This has since been fixed with a dedicated detection mechanism:
the moment any single attempt confirms the daily limit specifically (never
the recoverable per-minute one) has been reached, the entire remaining
batch is stopped immediately, cleanly, and with a clear explanation,
rather than continuing to fail the same way for every remaining record.

**Visual:** A short numbered list or icon row of 3-4 of the specific bugs
named above, each with a one-line "what broke → what fixed it" caption.
Avoid showing actual code; keep it conceptual and story-driven for a
non-technical or semi-technical audience.

---

## Slide 11: Does It Actually Work? (Real Numbers)

**On-slide bullets:**
- Estimated LLM calls for a 500-record batch: ~5,000-7,500 → a few dozen
- Per-record time after the first record: minutes → well under a second
- A clean run has reached 100% field accuracy
- A recent run surfaced two real, fixable issues — both root-caused and
  fixed the same day

**Speaker notes / detail:**

It is worth presenting both the theoretical win and an honest, current
snapshot of real testing results side by side, rather than only the
best-case number, because the debugging discipline described on the
previous slide is itself part of the evidence that this system can be
trusted.

The cost and speed improvements described earlier in this deck are not
theoretical projections dressed up as results — they follow directly and
unavoidably from the architecture itself. Once a form's fields have been
learned, replaying them involves no page-wide reasoning step and no
network round-trip to an external model at all; it is bounded purely by
how fast a real browser can perform a click or fill in a text box, which
is a matter of milliseconds rather than seconds.

On accuracy: a clean, fully verified run — meaning every field, on every
record, independently confirmed correct by reading the real target
database directly rather than trusting the automation's own report — has
been achieved and is documented as this project's demonstrated baseline.
That result held even after this project's independent verification step
was specifically designed never to trust the automation's own self-report,
which makes that number more meaningful than a self-reported success
count would be.

At the same time, the most recent full run available at the time of this
writing surfaced two genuine, real issues rather than a clean result, and
it is more honest — and, frankly, more useful for this presentation — to
walk through them than to omit them. The first was invalid test data: some
of the automatically generated sample values for a specific field did not
match the actual, real set of valid options in the target form's own
dropdown, which is a data problem, not a flaw in the caching or replay
logic itself. The second was an operational one: the target system's
underlying data had been accumulating across many earlier test sessions
without ever being cleared, and its own reload behavior after every
successful submission grows measurably slower as that underlying dataset
grows, which compounded under many records running at once. Both were
identified, explained, and fixed on the same day they were found — the
first by verifying the real, valid options directly and correcting the
test data, the second by clearing the accumulated test data before the
next run and reducing how many records run concurrently at once.

The overall picture this is meant to convey: the underlying mechanism is
sound and has already reached full, independently-verified accuracy once;
the issues that do surface tend to be specific, well-understood, and
quickly fixable, rather than mysterious or unpredictable; and every issue
found has made the system measurably more robust going forward rather than
being patched over superficially.

**Visual:** A simple before/after comparison table with three rows — "LLM
calls per 500-record batch," "time per record after the first," "field
accuracy achieved" — each showing the old/naive figure next to the
new/actual figure.

---

## Slide 12: Is This Locked to Our Test App?

**On-slide bullets:**
- No — the caching/replay engine has zero built-in knowledge of the
  current test app
- Point it at a different form, clear the cache, and it re-learns
  automatically, with no code changes
- Two small, known exceptions: one prompt used only for edit-flow cleanup,
  and how correctness gets verified

**Speaker notes / detail:**

This question matters because the entire system has so far only been
exercised against one specific test application — a small, disposable
practice app built purely so this project could be developed and tested
somewhere safe, with a real, independently-checkable database behind it,
before ever being pointed at anything that actually matters. It is fair to
ask whether everything described in this deck secretly depends on quirks
of that one specific test app, and the honest answer, backed by reading
through the actual implementation rather than just asserting it, is
mostly no.

The core caching engine — the concurrency handling, the seed lock, the
circuit breaker, the cache storage itself, and the replay logic — contains
no reference anywhere to the test application's specific field names,
page layout, or wording. It operates purely on whatever field names happen
to exist in a given batch of source data, and on runtime timing signals
like "has this specific field failed repeatedly and recently." Pointing
this same system at an entirely different form tomorrow — different
fields, a different number of them, a different underlying web framework
— would, by design, cause record #1 to cold-run and teach the cache that
new form's layout in exactly the same way it learned the current test
app's layout, with zero changes required to any of that core machinery.
Even the main instruction handed to the live agent is already written
generically: it lists whatever fields a given record happens to have and
asks the agent to match them to the closest-labeled field on the page — it
never mentions the current test application by name anywhere in its
wording.

There are, honestly, two real, bounded exceptions worth naming rather than
glossing over. The first is the specific instruction used only by the
reconciliation cleanup pass, which currently describes the current test
application's particular navigation pattern for finding and editing an
existing record — a very common pattern for admin-style tools in general,
but not a universal one, and a real target with a different navigation
style would need that one instruction rewritten. This is a small,
isolated piece of text, not an architectural dependency. The second, more
substantial exception is how correctness gets verified in the first place:
the current approach reads the target's real database directly, which is
only possible because the current test application was specifically built
to allow it, precisely so this project could have a trustworthy way to
check its own work while everything else was being developed. A real,
production internal system will very likely not offer that same kind of
direct, external database access, for entirely reasonable security
reasons — and that is the one genuinely open design question that would
need to be solved differently before pointing this system at something
that matters for real, discussed further on the next slide.

**Visual:** A simple split diagram with two labeled zones — "Fully
generic, works unchanged against any form" (the caching engine) on one
side, and "The one real assumption" (direct database read access for
grading) called out distinctly on the other side, with a small note that
only one piece needs rethinking, not the whole system.

---

## Slide 13: What's Next

**On-slide bullets:**
- Support more field types — radio buttons, date pickers, multi-select
- Test against a second, differently-shaped form to prove portability for
  real, not just in theory
- Design a way to verify correctness without needing direct database
  access — required before pointing this at a real internal system
- Longer-term: package this for a real company system, with a real
  database, as something that could eventually be offered to other teams
  or clients

**Speaker notes / detail:**

These items are deliberately listed as a plan rather than something
already built, so that scope is represented honestly: none of the four
items below have been implemented yet, and each has already been thought
through enough to know roughly what it would take, rather than being a
vague aspiration.

Expanding the set of field types the caching layer understands is the
most contained and lowest-risk item on this list. Today, the system only
knows how to learn and replay four kinds of interactions — a plain click,
typing text, selecting from a dropdown, and toggling a checkbox — simply
because those are the only kinds of fields the current test form actually
uses. A real internal system is likely to also include things like radio
button groups, a dedicated date-picker widget, or a multi-select list.
Adding support for each of these is a small, additive change to two
specific, well-understood pieces of the system, not a redesign — it simply
has not been necessary to build yet.

Proving portability with a real second target, rather than only by
reasoning about the code, is intentionally the next priority after that.
Every claim made on the previous slide about the system being generic and
not tied to the current test application is currently backed by reading
the implementation carefully, but it has never actually been *run* against
anything but that one test application. The plan is to build a second,
deliberately differently-shaped small practice form — different field
types, possibly a different underlying web framework — specifically to
stress-test those portability claims for real, the same way the current
test application itself was originally built as a safe rehearsal space.

The harder, more open-ended item is designing an alternative way to verify
correctness that does not depend on direct database access, since that is
the one piece of the current design that is a deliberate simplification
for this development and testing stage, not something expected to survive
unchanged against a real production system. A few realistic directions
exist and have already been considered — reading data back through the
target's own user interface the same way it was entered, leaning more
heavily on the replay engine's own internal signals about what succeeded
or failed, or building a narrow, purpose-built integration if the real
target happens to expose any kind of read-only reporting access — but
choosing between them properly requires knowing more about the actual real
target system than is currently known, so this has intentionally not been
guessed at or built speculatively ahead of time.

Finally, the longer-term ambition behind this whole effort is to grow this
from an internal testing tool into something genuinely usable against a
real company system, and eventually into something that could be offered
more broadly, potentially to other teams or external clients, once it has
real database or equivalent access and has been proven against more than
one target. That is explicitly a later-phase goal, not a near-term
commitment, and it is called out here mainly to give a sense of the
overall trajectory this work is on, beyond just the current testing phase.

**Visual:** A simple left-to-right roadmap timeline with four labeled
milestones matching the four items above, visually distinguishing the
first two ("in progress / next up," lower risk) from the last two
("longer-term," higher uncertainty).

---

## Slide 14: Summary

**On-slide bullets:**
- v1 already proved an LLM can fill in a form entirely on its own
- v2's contribution: after record #1, it (almost) never has to do that
  expensive work again
- Fast, self-healing, independently verified, and not tied to one
  specific website
- Hardened through 16 real, documented, fixed issues — not a one-off demo
- Clear, honest plan for what comes next before this touches anything
  that matters for real

**Speaker notes / detail:**

This closing slide is meant to restate the overall pitch in one breath,
after the detail of the preceding slides, and to leave the audience with a
clear sense of both what has been proven and what is intentionally still
ahead.

The starting point was never in question: an LLM-driven agent can fill in
a web form correctly, entirely on its own, without being told in advance
where anything is. That capability already existed and was already shown
separately. The actual contribution described in this entire deck is
narrower and more specific than "building an automation tool" — it is
specifically about making that already-working capability affordable and
fast at real scale, by teaching the system to remember what it learns
instead of paying full price for the same discovery over and over.

The system that resulted from that goal turned out to require real
engineering depth to get right safely: careful handling of many records
running at once without colliding with each other, a firm rule against
ever trusting the automation's own opinion of its own success, a
self-healing mechanism so that gaps found after the fact get quietly
closed rather than lingering, and a debugging history of sixteen real,
specifically-documented issues — each with a clear account of what broke,
why, how it was fixed, and how that fix was verified — rather than a
single clean demo that happened to go well once.

Just as importantly, this deck has tried to be equally honest about scope:
what has been proven so far, what is a reasonable, well-understood next
step, and what remains a genuinely open question — particularly how
correctness gets verified once this points at a system that will not offer
the same convenient, direct database access the current test environment
was specifically built to provide. That question is called out plainly
rather than glossed over, because solving it properly, with real
requirements from a real target system, matters more than pretending it is
already solved.

**Visual:** A clean closing slide restating the one-line pitch from Slide
1, plus a small "questions" or "thank you" cue depending on presentation
style.

---

## Appendix A: Glossary of Key Terms

> Not intended as its own slide — this is reference material so
> NotebookLM (and the presenter) has accurate, consistent definitions to
> draw on if a term needs explaining live, or if a slide's speaker notes
> need a one-line definition inserted.

**LLM (Large Language Model)** — the underlying AI model (Gemini, in this
project) that does the actual reasoning: looking at a description of a web
page and deciding what action makes sense next. Every "live" or "cold" run
mentioned throughout this deck means an LLM is actively making decisions;
every "replay" run means it is not.

**browser-use** — the existing, external, open-source library (not built
as part of this project) that gives an LLM a real, controllable web
browser and a decision loop: look, decide one action, do it, look again.
This is v1's foundation, and it remains completely unmodified by v2 — v2
only watches it and, separately, replaces it for repeat work.

**Playwright** — a completely separate, ordinary browser-automation
library with no AI involved at all. It only ever does exactly what it is
told — "click this exact thing," "type this exact text into this exact
box" — which is precisely why it is fast and free of any LLM cost. This is
the tool that actually performs every replayed action.

**Cache / Replay** — "cache" refers to the stored record of what was
learned (a locator, an action type, a field name) for a given form.
"Replay" refers to the act of using Playwright to perform that same
learned action again, with a new record's own value substituted in.

**Cold run** — the one-time, expensive pass where the live LLM agent
learns a form for the very first time, because nothing has been cached
for it yet.

**Seed lock** — the mechanism that guarantees only one record is ever
actively cold-running (teaching the system) for a given form at any one
moment, even when many records are dispatched at the same time. Explained
in full on the "Concurrency, Done Safely" slide.

**Circuit breaker** — the mechanism that temporarily stops retrying a
specific field once it has failed a small number of times in a row in
quick succession, instead of letting every subsequent record queue up for
its own doomed, slow attempt at the same broken thing. It automatically
resumes trying again after a short cooldown window.

**Field accuracy** — the headline correctness metric reported after every
batch: the percentage of individual data fields, across every record in
the batch, that were independently confirmed correct by reading the real
target database directly. This is measured at the level of individual
fields, not whole records, so a record with one wrong field out of twenty
barely moves this number, which is intentional — it reflects the real
scale of any data-quality gap rather than exaggerating it.

**Reconciliation** — the background cleanup pass, run automatically right
after a batch's correctness check finishes, that retries specifically the
fields that check found wrong on records that otherwise did land
successfully, using the target system's own editing capability.

**Self-report vs. verification** — "self-report" is whatever the
automation itself believes happened to a record (did it think it
succeeded); "verification" is the independent, separate step that reads
the real target database directly to check what is actually true. This
project treats only verification as authoritative — see the "Never Trust
the Robot's Own Opinion" slide.

**Concurrency** — running more than one record's work at the same time.
This project runs up to ten records concurrently, each in its own fully
isolated browser window, bounded by available local computing resources
rather than any external API limit, since pure replay work involves no
LLM calls at all.

---

## Appendix B: Questions a Manager Might Reasonably Ask

> Also not its own slide — this is preparation material. Each of these is
> a plausible question this presentation might prompt, with an honest,
> accurate answer the presenter can give directly, in the same tone and
> level of detail as the rest of this document.

**"How do I know the caching part is actually faster, not just different?"**
Because the replay path performs no reasoning step and makes no network
call to an external model at all — it is bounded purely by how quickly a
real browser can perform a click or fill in a field, which is a matter of
milliseconds. The cold-run path, by contrast, genuinely does involve
real, and sometimes slow, round-trips to an external LLM provider. The
speed difference is not tuned or estimated; it falls directly out of which
of those two fundamentally different kinds of work a given record needs.

**"What happens if the cached version of a field is wrong, or the page
changes?"**
Replay checks that the element it locates still resembles what was
originally learned, using more than one independent way of finding it.
If it cannot confidently find a match, that is treated as a cache miss,
not a silent wrong guess — the system automatically falls back to a live
LLM call to fix that one specific field, and immediately updates the
cache with whatever the live agent finds, so the fix persists for every
future record.

**"What stops this from silently entering wrong data at scale?"**
The independent verification step, which reads the real target database
directly after every batch and never trusts the automation's own opinion
of what happened. This is the same mechanism that already caught a real
historical case where records reported success but had not actually been
saved at all — described on the "Never Trust the Robot's Own Opinion"
slide.

**"Is this reliable enough to run unattended, without someone watching
it the whole time?"**
For the current, well-understood test environment, yes — it is designed
specifically so that individual failures (a broken field, a temporarily
unavailable API, one record's browser crashing) are contained and do not
require manual intervention to recover from, and every batch ends with an
independently verified, honest accuracy number rather than an assumed one.
For a real, production internal system, the honest answer is: not yet,
specifically because of the open verification-without-direct-database-
access question described on the "What's Next" slide, which should be
solved with real requirements from that system before this runs there
unattended.

**"Why not just write a normal, hand-coded automation script instead of
using an LLM at all, if most records just replay identical actions
anyway?"**
Because a hand-written script only knows the exact page layout it was
written against, and breaks silently the moment that layout changes even
slightly — a new field, a renamed label, a slightly different dropdown
structure. This system gets the same replay speed for the common case,
but never becomes brittle in that way, because the live LLM agent is
always available as an automatic, self-healing fallback the moment
anything about the page stops matching what was learned. It combines the
speed of a fixed script with the adaptability of a reasoning agent,
rather than forcing a choice between the two.

**"How much of this was actually tested, versus designed but never run?"**
Every mechanism described in this deck — the seed lock, the circuit
breaker, the self-healing replay fallback, the independent verification
step, the reconciliation pass, and the sixteen specific fixes described on
the debugging slide — was found, fixed, and confirmed working through
actual live runs against a real browser, a real LLM provider, and a real
database, not through code review alone or a single successful demo. The
one area that is explicitly designed but not yet tested against a second
example is portability to a genuinely different form, which is called out
honestly as the next concrete step on the "What's Next" slide rather than
being presented as already proven.

**"What's the single biggest risk before this touches a real company
system?"**
Verification. The current accuracy-checking approach depends on having
direct read access to the target's own database, which the current test
environment was specifically built to provide, but which a real,
production internal system is very unlikely to offer for good security
reasons. Every other part of this system — the caching engine itself, the
concurrency handling, the self-healing behavior — has already been shown
to generalize cleanly by reading through the implementation. Verification
is the one piece that would need a genuinely new, real design, informed by
what access the actual target system can realistically provide.
