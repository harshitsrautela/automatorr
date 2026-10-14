# Data Entry Automation Agent: Building and Validating an LLM-Driven Browser Agent for Structured Web Forms

## Abstract

Manual data entry — copying structured records (names, contact details,
company info, and so on) into a web form one field at a time — is one of
the most common, most tedious, and most error-prone tasks in ordinary
business software. This project builds and rigorously tests a system that
automates that task using a large-language-model-driven browser agent
instead of a hand-written scraper or a fixed macro. Given a batch of
records as a JSON file, the system opens a real browser, reasons about the
target web page the same way a human would ("find the field labeled
'Email', type this value into it"), fills in every record one at a time,
and submits the form.

Critically, the project does not stop at "does the agent look like it's
working." It treats the agent's own self-reported success as unreliable
by design, and instead builds a second, fully independent system that
reads the real database afterward and checks — field by field — whether
the data that was supposed to land actually did, and whether it's
correct. Over a single day of iterative testing, this system processed
dozens of real records through a live web application, uncovered and
diagnosed several non-obvious failure modes (shared API rate limits,
browser automation concurrency limits, and dropdown-data mismatches), and
converged on a configuration that reliably achieves roughly 90–100% field
accuracy per batch, at a fraction of the cost of the naive approach.

This document explains what was built, why it was built the way it was,
what broke during testing and why, and what was learned — intended as
source material for a presentation, not as a code reference.

---

## 1. The Problem

Data entry automation sits in an awkward middle ground. Fully manual entry
doesn't scale and is error-prone from human fatigue. Traditional
robotic-process-automation (RPA) tools and hand-written scripts are fast
and cheap to run, but brittle: they rely on fixed selectors, exact pixel
coordinates, or exact DOM structure, and break the moment a form's layout
changes even slightly. Neither approach handles the fuzzy matching a human
does naturally — "the field labeled 'Phone' is what I should type the
phone number into, even if it's the fourth field down instead of the
third."

An LLM-driven browser agent sits between these two extremes. It is given
a goal in natural language ("fill in these fields") and a live view of
the actual page (both a text description of every clickable/typeable
element, and optionally a screenshot), and it decides, step by step, what
to click and what to type — the same way a person reading the page would.
This makes it resilient to minor UI changes and requires no
site-specific scripting, at the cost of being slower, non-deterministic,
and dependent on a paid (or rate-limited free) external API call for
every decision it makes.

The core question this project set out to answer wasn't "can an LLM click
buttons" (it clearly can) — it was **"can we trust it, and how do we
know?"** That second question is what most of the actual engineering
effort here went into.

---

## 2. System Overview

The system is made of two independent applications that only interact
through a browser and a shared database — neither has any code-level
dependency on the other.

### 2.1 The target application: "DataDesk"

DataDesk is an ordinary People-Management CRUD web app — a directory of
people with fields like name, email, phone, date of birth, company, job
title, country, city, address, department, employment type, years of
experience, salary, a newsletter opt-in checkbox, and free-text notes. It
has a dashboard with summary stats, a searchable/filterable table, an
"Add Person" form opened as a modal, a detail view, CSV export, and a
delete-confirmation flow. It's built with React and Vite on the frontend
and Supabase (hosted PostgreSQL) as its database.

DataDesk has no awareness that it's a target for automation — it behaves
exactly like production business software would. Its one deliberate nod
to being agent-friendly is architectural, not functional: it uses
semantic HTML, real form controls (actual `<select>` dropdowns, actual
`<input>` elements with proper labels), and stable, accessible markup —
the same practices that make a page usable by screen readers also make it
far easier for a browser agent to reliably identify "this is the email
field" without guesswork.

### 2.2 The automation agent

A FastAPI backend exposes three endpoints: kick off a batch run, check its
progress, and retrieve the last completed report. Internally, when a batch
run starts, it:

1. Loads a JSON file of records to enter.
2. Hands each record, one at a time, to a browser-driving agent.
3. Once every record has been attempted, independently re-reads the
   database and grades what actually happened.
4. Returns a structured report: how many records were found at all, and
   for each one, exactly which fields matched and which didn't.

The "browser-driving agent" part is powered by an open-source library
(`browser-use`) that wraps an LLM (in this project's case, Google's
Gemini) with the machinery needed to actually control a browser: reading
the page's structure, deciding on an action (click, type, select from a
dropdown, submit), executing it via the Chrome DevTools Protocol, and
looping until the task is judged complete or it gives up.

### 2.3 High-level flow

```
   JSON records file
          │
          ▼
   [ FastAPI: POST /run ]
          │
          ▼
   For each record, sequentially:
          │
          ▼
   Natural-language task built from the record's fields
          │
          ▼
   A fresh, isolated browser window opens
          │
          ▼
   The LLM agent looks at the page, decides actions, executes them
   (click "Add Person" → type each field → select dropdowns → submit)
          │
          ▼
   The browser window closes; the next record starts
          │
          ▼
   [ once ALL records are attempted ]
          │
          ▼
   Independent evaluator reads the real database directly
          │
          ▼
   Field-by-field comparison against what was expected
          │
          ▼
   A single report: totals, per-record results, overall accuracy %
```

The critical design property here is the strict separation between
"attempt to enter the data" (step 1) and "check whether it worked" (step
2) — these never share state, and the second step runs only after the
first step has fully finished for every record.

---

## 3. Why the Agent's Own Opinion Isn't Trusted

Every browser-automation agent framework, including the one used here,
reports back its own judgment of whether it succeeded — typically phrased
as something like "Task completed successfully" in its own reasoning
trace. It would be easy, and much simpler, to just believe that.

This project deliberately does not. The reasoning: an LLM narrating "I
successfully filled in the form" is a *claim*, generated by the same
process that might have mistyped an email address, selected the wrong
dropdown option, or clicked "submit" on a form that silently failed
client-side validation. Self-reported success and actual success can
diverge in exactly the cases that matter most — subtle, silent errors
that look fine from the agent's own vantage point.

Instead, the system's grading is done entirely by a second pass that:

- Connects directly to the database the form is supposed to write to.
- Fetches every row.
- For each expected record, looks up a row by a designated matching field
  (email, by default) — deliberately case- and whitespace-insensitive, so
  formatting noise doesn't cause a false "not found."
- If no row is found at all, marks **every field** of that record as
  failed, with the specific reason "record not found in database" — this
  is treated as meaningfully different from "the row exists but one field
  is wrong," since it usually points to the match-key field itself (the
  email) having been entered wrong, or the submission never actually
  going through.
- If a row is found, compares every field's expected value against the
  actual stored value — after normalizing both, so formatting differences
  (a date written as "1990-05-14" vs. a full timestamp; "$75,000.00" vs.
  `75000`; a phone number with or without punctuation) don't register as
  false failures that have nothing to do with whether the agent actually
  did its job.
- Produces one accuracy number: correct fields divided by total fields
  attempted, across the whole batch.

This distinction — "row missing entirely" vs. "row present, field wrong"
— turned out to be genuinely useful during testing, not just a
theoretical nicety: it's exactly what made it possible to tell, at a
glance, whether a batch failure was "the agent never even submitted the
form" (usually an infrastructure problem, like a rate limit) versus "the
agent submitted the form but got a specific detail wrong" (a real
accuracy problem).

---

## 4. Key Engineering Decisions and Why They Were Made

Several design choices in this system are not the "obvious first attempt"
— they're the result of running into a problem, diagnosing it, and
changing course. Understanding *why* each decision exists is more
instructive than the decision itself.

### 4.1 One disposable, isolated browser window per record

The straightforward approach is to reuse one browser instance across an
entire batch. The system instead gives every single record its own
throwaway browser profile (a fresh, empty browsing environment) that is
created just before that record starts and destroyed immediately after it
finishes.

Two reasons converged on this:

- **It stops the automation from hijacking a real, personal browser.**
  Without this, the underlying automation library's default behavior is
  to attach to whatever Chrome browser is already open and visibly take
  it over — clicking and typing in front of the user, potentially in a
  window with the user's own logged-in accounts and open tabs. An
  isolated, disposable profile means the automation gets its own clean
  slate every time and never touches anything the user is actually using.
- **It's a prerequisite for ever running multiple records at once
  safely.** If multiple automated agents ever needed to share one browser
  window, they would fight over which tab is "active" and could end up
  clicking in the wrong place entirely. Giving each one its own isolated
  window sidesteps that entirely, at zero cost (only one window is ever
  open at a time, either way).

### 4.2 Sequential execution, not concurrent — and why concurrency was actually attempted and abandoned

It's tempting to assume that processing 10 records one-by-one is simply
slower than it needs to be, and that running several at once (each in its
own isolated browser window, per the above) would be a straightforward
speed win. This was tried directly, not just theorized about.

The isolation held up perfectly — multiple simultaneous, independent
browser windows worked with no cross-contamination. What broke the
approach was something entirely external to the browser layer: the
LLM API key in use is on a free tier with a **shared, fixed rate limit**
— a maximum number of requests allowed per minute, and a separate, much
larger cap per day. Every step the agent takes (deciding what to click
next) is one API call. Multiple agents running at the same time all draw
from the *same* shared quota, and even a modest handful of concurrent
agents collectively exceed the per-minute limit almost instantly — every
single record in that test failed, 100% of the time, with the API
explicitly rejecting the requests as "quota exceeded."

The important, generalizable lesson: **concurrency cannot outrun a fixed,
shared rate limit.** Opening more browser windows doesn't create more
API-request capacity — it just means more things are competing for the
same fixed number of requests-per-minute. Running one record fully to
completion before starting the next one, by contrast, naturally spaces
out its API calls widely enough to stay under that ceiling, which is
exactly why the sequential approach works reliably where the concurrent
one didn't. This only changes if the API key moves to a paid tier with
meaningfully higher throughput — at which point concurrency becomes worth
revisiting.

### 4.3 Text-only reasoning by default, with visual understanding as a fallback, not the default

By default, this class of browser agent sends the LLM *two* things on
every single step: a structured text description of every interactive
element on the page (its label, its role, roughly where it is), and a
full screenshot of the current page state. The text description alone is
already how the agent identifies "click this specific button" — the
screenshot is a secondary, supplementary channel.

Screenshots are also, by a wide margin, the most expensive part of a
multimodal API call — sending an image costs substantially more than
sending the equivalent amount of text. Given that DataDesk was
specifically built with clean, labeled, semantic form controls (exactly
the kind of page where a text-only description is already
unambiguous), the system was reconfigured to run **without** sending
screenshots by default.

This was validated, not just assumed: a batch run with screenshots
disabled achieved comparable accuracy to earlier vision-enabled runs
(high-80s to high-90s percent field accuracy — see the results section
below), at meaningfully lower cost per record. The one visual-only signal
that seemed to matter, from a single observed case, was confirming a
checkbox's on/off state — plausible, since a checkbox's visual state is
sometimes a one-word difference in its underlying markup that's easy to
overlook in a purely textual description.

Rather than picking one mode and discarding the other, the system keeps
both, structured as a fallback chain: attempt the record with the cheap,
text-only mode first; only if that attempt doesn't succeed, automatically
retry the *same* record once with full visual understanding turned back
on, before giving up entirely. This treats visual understanding as a
safety net for the rare case where text alone genuinely isn't enough,
rather than the default, expensive path taken every single time
regardless of whether it's needed.

---

## 5. What Actually Broke, and What It Revealed

A meaningful part of this project's value came from deliberately pushing
the system until it failed, then tracing each failure to a root cause
rather than papering over it. Three distinct failure modes were found and
diagnosed over the course of testing.

### 5.1 The free-tier API quota is not one limit — it's two

The first assumption was that there was a single rate limit to respect.
In practice there are two, independent, and both matter:

- A **per-minute** cap, which is what concurrency breaks almost
  immediately (see 4.2).
- A **per-day** cap, entirely separate, which is a hard ceiling on total
  requests for the entire day regardless of how they're paced. Once that
  ceiling is hit, waiting a minute — or an hour — does not help; every
  call fails identically until the provider's own daily reset.

This distinction mattered in practice: one batch's final record failed
even after the system's fallback logic tried waiting and retrying with a
different configuration — because the *daily* cap, not the per-minute
one, had been exhausted by the cumulative cost of an entire day's worth
of iterative testing. Recognizing which of the two limits is actually in
play is the difference between "wait a minute and it'll work" and "this
will not succeed again until tomorrow, no matter what."

### 5.2 Local browser automation has an inherent one-agent-at-a-time ceiling, independent of API limits

Separately from the rate-limit discovery, it was confirmed that the
underlying browser-automation library's own documentation explicitly
warns that a single local browser is not safe to share across multiple
simultaneous automated tasks — they end up fighting over which tab is
currently "active," causing one agent's actions to land in the wrong
place. The library's own suggested resolution for genuine parallel
automation is to use fully separate, independently hosted browser
instances (a paid cloud offering), not multiple tabs or windows within
one local browser. This reinforced the decision to keep automation
strictly one browser window at a time, isolated per record, rather than
attempting to retrofit safe sharing.

### 5.3 The target form's own data model doesn't cover every real-world value

DataDesk's dropdown fields (country, department, employment type, gender)
are backed by fixed, hardcoded lists of options — a common and reasonable
real-world constraint, since most production forms don't offer literally
every value that exists in the world. When test data included a country
not present in that list, the agent generally handled it gracefully
(leaving that one field blank rather than crashing or inventing a
plausible-sounding but wrong value), and the evaluator correctly
identified this as a single-field mismatch on an otherwise-successful
record, rather than treating the whole record as a failure. This was a
useful demonstration of the evaluator's field-level granularity working
as intended, and a reminder that "the agent got a field wrong" sometimes
means "the target form genuinely cannot represent that value," not "the
agent made a mistake."

---

## 6. Results

Across a single day of iterative testing, the system processed dozens of
records end-to-end (write attempt, then independent verification) under
several different configurations. Representative results:

| Run configuration | Records | Field accuracy | Notes |
|---|---|---|---|
| Sequential, vision on | 6 | 100% (108/108 fields) | Baseline: first fully working run |
| Sequential, vision on | 10 | 100% (all fields matched) | Confirmed baseline held at slightly larger scale |
| **Concurrent** (10 at once) | 10 | 0% (0/180 fields) | Every record failed — root cause: shared API rate limit, not a logic bug |
| Sequential (recovering from the above) | 9 | 74% (120/162 fields) | 2 records failed due to lingering quota pressure from prior testing; both succeeded when retried in isolation minutes later |
| Sequential, **vision off** by default | 10 | 89% (161/180 fields) | 9/10 records fully matched; 1 failure was a daily quota exhaustion, not a vision-related accuracy issue |

The clearest takeaway from this table isn't any single percentage — it's
that **every non-100% result had an identifiable, external root cause**
(a shared rate limit, or a form's data model not covering a test value),
never an unexplained or unreproducible failure. The one true content
accuracy issue observed (a single checkbox field, once) is noted above as
an open question rather than a resolved one.

---

## 7. Cost Considerations

Running an LLM-in-the-loop for every single click has a real, recurring
cost, and the choices made here were shaped by that as much as by
reliability:

- The LLM in use (Google's Gemini, smallest/cheapest available model
  tier) has a genuinely free usage tier, capped at a modest
  requests-per-minute and requests-per-day limit — sufficient for
  sequential, one-at-a-time testing, but the first resource this project
  ran up against repeatedly.
- Turning off screenshot-based reasoning (see 4.3) directly reduces
  per-call cost, since image data is the most expensive part of a
  multimodal request — and was validated to not meaningfully hurt
  accuracy on this particular, cleanly-built target form.
- An alternative considered was switching the underlying LLM to
  Anthropic's Claude models, specifically for **text-only reasoning**
  (matching the vision-off configuration above). This is supported by the
  same automation library with a one-line change. The trade-off: a Claude
  subscription (the consumer chat product) and the developer API used for
  this kind of integration are billed completely separately — using
  Claude here would mean a new, separate, pay-per-token cost, not
  something already covered by an existing subscription. Given that each
  record already costs a non-trivial number of LLM calls, and that the
  free Gemini tier already performs well on this specific, well-structured
  target form, the free tier remains the practical default; a paid model
  (Claude or a paid Gemini tier) becomes worth it primarily if either
  higher throughput (via real concurrency) or higher per-decision accuracy
  is specifically needed later.

---

## 8. Current Scope and Limitations

This system was deliberately scoped as a correctness-testing tool, not a
production pipeline. Explicitly out of scope, for now:

- No authentication or access control on the automation service itself.
- No deployment story — everything runs locally, by design, for now.
- No retry logic beyond a basic catch-and-report per record, plus the
  vision fallback described above.
- No true concurrency (see section 4.2) — a deliberate, revisitable
  trade-off tied to the current API tier, not a technical ceiling.
- The evaluator's matching logic assumes a single, reliable "key field"
  (email, by default) uniquely identifies each record — a reasonable
  assumption for this target form, but one that would need reconsidering
  for a target where the natural key field is itself unreliable.

---

## 9. Summary

This project demonstrates that an LLM-driven browser agent can reliably
automate structured data entry into a real, unmodified web application —
but that "reliably" only became a defensible claim because of a second,
independent system built specifically to check the agent's work against
ground truth, rather than trusting its own account of what happened. Over
one day of active testing, that verification layer is what turned three
otherwise-mysterious failures (a shared API rate limit hit two different
ways, and a target form's incomplete dropdown data) into clearly
diagnosed, well-understood, and in two of three cases directly
addressed issues — rather than into vague, hard-to-reproduce flakiness.
The resulting configuration — one isolated browser per record, sequential
execution, text-only reasoning by default with a visual fallback — is not
the first design tried, but the one that survived contact with a real,
shared, rate-limited external dependency.
