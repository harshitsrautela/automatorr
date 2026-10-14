# Guidebook — what to read, and in what order

This project now has several docs. This page is the map.

## Want the whole picture in one sitting?

Read **[`00_FULL_OVERVIEW.md`](00_FULL_OVERVIEW.md)** first. It's the long
version — no code, but genuinely everything: why this project exists,
every subsystem explained in full (not just diagrammed), a complete
narrated walkthrough of what happens between clicking Start and seeing a
result, and the real story of the bugs that shaped the current design.
If you only ever read one file in this repo, make it this one.

## Want it broken into shorter, focused pages instead?

The numbered docs below cover the same ground as `00_FULL_OVERVIEW.md`,
split into separate, shorter pages you can revisit individually. Read top
to bottom — each one assumes you've read the ones above it.

| # | File | What it answers | How long |
|---|------|------------------|----------|
| 1 | [`01_OVERVIEW.md`](01_OVERVIEW.md) | What is this thing, in plain English? | 3 min |
| 2 | [`02_HOW_CACHING_WORKS.md`](02_HOW_CACHING_WORKS.md) | How does "learn once, replay forever" actually work? | 8 min |
| 3 | [`03_OPERATIONS.md`](03_OPERATIONS.md) | How do I actually run a batch, and what do I do when something looks stuck? | 5 min |
| 4 | [`04_ARCHITECTURE.md`](04_ARCHITECTURE.md) | How does it stay fast and correct with 15 things happening at once? | 12 min |
| 5 | [`05_PORTABILITY.md`](05_PORTABILITY.md) | If I point this at a totally different website tomorrow, does it still work? | 6 min |

## Want to actually read the code next?

**[`06_CODE_WALKTHROUGH.md`](06_CODE_WALKTHROUGH.md)** is different from
`00`-`05` on purpose: it's the only doc here that *does* get into code —
real function names, real files, in the order they actually execute, from
`POST /run/start` all the way down to a single Playwright `.click()`. Read
it after `04_ARCHITECTURE.md`/`05_PORTABILITY.md` (it assumes you already
know *why* the seed lock/circuit breaker/reconciliation exist and just
shows *where in the code* each one lives), ideally with the actual files
open in an editor alongside it so you can jump into whatever it's quoting.

Two more files sit outside this numbered sequence, kept for a different
purpose:

- [`CACHING_LAYER.md`](CACHING_LAYER.md) — the original build plan. Kept
  verbatim because the actual code comments throughout `cache/`, `replay/`,
  and `orchestrator.py` point back to specific steps in it by name (e.g.
  "Step 3 of CACHING_LAYER.md"). Treat it as a historical blueprint, not a
  current-state doc — `04_ARCHITECTURE.md` is the accurate as-built
  description.
- [`FUTURE_PLANS.md`](FUTURE_PLANS.md) — a running list of gaps we've
  talked through but deliberately haven't built yet (more form-field
  types, a second test target, messier-DOM locating, ERP-scale
  verification, ...), so they don't get lost or re-argued from scratch
  later. Check this before assuming something "isn't possible" — it might
  just be "not built yet, and here's why."
- [`V2_BUGS_FIXED.md`](V2_BUGS_FIXED.md) — the real debugging history: every
  bug found (mostly from actually running the thing against a live
  browser/Gemini/Supabase), what broke, and exactly how it got fixed. Not
  required reading to understand the system, but the single best source if
  you're asking "why is the code shaped this weirdly specific way *here*?" —
  the answer is almost always "a real run broke this exact way once."

## If you only read one thing

Read `00_FULL_OVERVIEW.md`. It's not three minutes, but it's the one idea
that explains almost everything else here — **the first record teaches
the system, every record after that just replays what it learned** —
walked all the way through, with nothing important left out.

## Why old docs disappeared

This project accumulated a lot of documentation across two builds (v1,
then v2 built on top of it) and a long debugging session. Several older
files (`ARCHITECTURE.md`, `CODEBASE.md`, `CODE_FLOW.md`, `CONTEXT.md`,
`LEARNINGS.md`, `README.md`, `RUNBOOK.md`, `THESIS.md`, `CHANGELOG.md`,
old `V2_ARCHITECTURE.md`/`V2_CACHING_LIFECYCLE.md`/`V2_OPERATIONS.md`)
described v1, or an earlier and now-outdated shape of v2, and were removed
rather than left around to contradict the docs above.
