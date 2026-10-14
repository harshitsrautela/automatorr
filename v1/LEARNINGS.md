# Learnings & gotchas

Things we found out the hard way while building and testing this, so
they don't need rediscovering. Ordered roughly by "how much time this will
save you."

## Gemini's free tier has TWO separate limits, not one

- **15 requests/minute**, per model (`gemini-3.5-flash-lite`) — this is
  the one that breaks concurrency (see below).
- **500 requests/day**, per model, per project — a hard wall that a
  per-minute cooldown cannot fix. Once you hit it, every call fails until
  Google's daily reset, no matter how long you wait within the same day.

Each record costs roughly 12–15 Gemini calls (one per agent "step":
navigate, click a field, read a dropdown, select an option, submit...).
Heavy iteration (re-running batches, debugging one record at a time) adds
up fast — check current usage at https://ai.dev/rate-limit before assuming
a failure is a code bug.

**If a `RunResult` comes back `success=False` with `error_message=None`**,
that's usually this: `browser-use`'s own `Agent` caught the failure
internally (e.g. a 429) after retrying a few times, gave up, and returned
a "not successful" result rather than raising an exception our code could
inspect. The real error text only shows up in the backend's own log
stream, not in the API response.

## Concurrency doesn't work with local Chrome — confirmed by testing, not just theory

We tried running multiple records at once, each in its own Chrome window
(separate `user_data_dir` per record, so no shared browser process). The
isolation itself worked fine. What killed it: all those concurrent agents
share the *same* Gemini quota, and their combined call rate blew past
15/minute almost instantly — a 3-way concurrent test failed 100% of the
time with `429 RESOURCE_EXHAUSTED` on every record.

Separately, `browser-use`'s own bundled docs explicitly warn against
sharing one *browser* across concurrent agents too: "local Chrome is one
shared browser; parallel tasks fight over tabs and focus." Its
`BrowserSession` tracks a single "current tab" pointer internally
(`agent_focus_target_id`), so two agents sharing one session would clobber
each other's clicks regardless of the rate limit issue. Their own
recommended fix for real concurrency is Browser Use Cloud (isolated
managed browsers per task) — a paid, external service, not something to
casually opt into.

**Bottom line**: concurrency isn't blocked by anything in this codebase —
it's blocked by the Gemini free tier being a shared, fixed-rate resource.
More parallel browsers can't make more requests-per-minute exist. Revisit
if/when the API key moves to a paid tier with a meaningfully higher quota.

## Vision (screenshots) is on by default — and probably doesn't need to be

`browser-use`'s `Agent` sends a screenshot on every step by default
(`use_vision=True`), *in addition to* a text-based list of interactive
page elements it always builds regardless of the vision setting (the
`index: 908`, `index: 959` references you see in logs — that's DOM/element
data, not vision). Screenshots are the expensive part of a multimodal call.

We tested `use_vision=False` (text-only) against DataDesk — which
advertises itself as built for exactly this (semantic HTML, stable
element IDs, accessible labels) — and got 89% field accuracy on the first
batch, cheaper and with no fundamental reliability collapse. One
checkbox-state field (`newsletter`) came out wrong once, which *might*
indicate vision helps confirm checkbox state — inconclusive from a single
data point, worth watching with more runs.

Current setup (`agent/runner.py`): text-only is the default path; if a
record fails, it automatically retries once with vision turned back on
before giving up, on the theory that vision is a fallback for the rare
case DOM text isn't enough — not something this particular form usually
needs.

## DataDesk's dropdowns have fixed option lists — test data outside them is expected to partially fail

`country`, `department`, `employment_type`, and `gender` are `<select>`
dropdowns with hardcoded option lists in `App.jsx` (`COUNTRIES`,
`DEPARTMENTS`, etc.). Countries like Pakistan aren't in the list. When we
tested with out-of-list values, the agent generally handled it gracefully
(left the field unset rather than crashing), and the evaluator correctly
flagged just that one field as a mismatch rather than failing the whole
record — this is the system working as intended, not a bug to fix, unless
you specifically want DataDesk's dropdown list expanded.

## The evaluator's `record_not_found_in_db` vs `value_mismatch` distinction matters

If a record shows `matched_in_db: false`, the **whole row is missing** —
usually because the match-key field (`email` by default) was never
correctly submitted, or the form submission failed outright. If a record
shows `matched_in_db: true` with one or more `value_mismatch` fields, the
row exists but a specific field is wrong — a much more targeted signal.
Don't conflate the two when reading a `BatchReport`.

## Isolated browser profiles were originally about concurrency safety — kept them anyway

Giving every record its own temporary `user_data_dir` was built to make
concurrent agents safe (each gets its own browser, not fighting over a
shared one). We reverted to sequential execution, but kept the isolated
profile per record anyway: it means the automation no longer takes over
whatever Chrome window you personally have open, at no extra cost since
only one is ever alive at a time.
