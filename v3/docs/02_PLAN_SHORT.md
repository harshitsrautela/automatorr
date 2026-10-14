# 02. The plan, short version: rebuild from scratch, one step at a time

A short version of the rebuild plan, about a 10-minute read. Every step is spelled out in `04_PLAN_DETAILED.md`.

## The idea

The agent is rebuilt in a new folder, **`terminautomator/v4`**, one function at a time. Each step has a reason to exist (most come
from a specific failure in v3), a piece of code to write, and tests that say when it is done. v3 stays untouched as the
**reference**: where a step is unclear, v3 shows how the same job was done, and the v4 version is written more cleanly.

The rebuild does not start from zero. It already has:
- **5 test sites with graders**, in `testsites`:
  - Credit Desk
  - AP Desk
  - Returns Desk
  - Expense Desk
  - Hazards Desk
- **About 900 test checks in v3**, to port as a spec.
- **`LEARNINGS.md`**: every bug v3 hit, so none is repeated.

## Rules for the whole rebuild

- **Same full stack from day 1:** FastAPI backend, React frontend, Postgres.
- **Small steps.** Each step ends with tests passing. Never move on with a red test.
- **Small live runs** (8 to 16 records) to save Gemini quota (about 1,000 calls a day with the 2 keys, 15 per minute).
- **Never save a guess, never press Save twice, never trust a cache that hasn't proven itself.**
- **Plan from day 1 for the referee problem:** the live agent judges the plan, and the live agent is sometimes wrong.

## The phases

**Phase A. Foundations (the boring base)**
1. A new repo with folders, settings, and how to run things.
2. The database: tables for tasks, runs, logs, secrets, and the learned cache.
3. Secrets stored encrypted; Gemini keys in a pool, with pacing under 15 per minute.
4. One small wrapper for every AI call (retries, quota handling, counting calls).
5. A FastAPI skeleton and a React skeleton that talk to each other.

**Phase B. The live agent**

6. Run one record with the live agent on a test site, and **record a trace** (every click and typed value, plus the page text).
7. A run loop: many records, a few browsers at once, live logs in the web page.

**Phase C. Checking results**

8. Grade a run against the site's correct answers, and show accuracy in the app. From here on, every change is measured.

**Phase D. Learning a plan from one trace**

9. Turn a trace into a plan: the route, and for every value whether it comes from the record, is copied from a page, or must be
   decided.
10. Find elements again on the next record: by name, role or text; never by the first record's own ids.
11. Page types, paging, detours and dead ends in the agent's route.

**Phase E. Running a plan**

12. Replay the route by code, copy page values with one small AI question, and decide the form with formulas the code evaluates.
13. Fill the form and press Save exactly once. Batch AI questions across records running together.

**Phase F. Proof and trust (the heart)**

14. A new plan does a dry run (no saving) next to the live agent, and the two answers are compared.
15. Trust per kind of record; extending the plan when a record needs pages it never read.
16. A better referee. The live agent fills the form but stops before saving; its answer is compared with a second,
    independent answer (the plan's, or a separate decision over the same pages). If they differ, a stronger model gives a third
    answer. Two of three must agree, otherwise the record goes to a review queue in the app, with nothing saved.
17. **Checkpoint:** AP Desk 100/100 and Credit Desk 100/100 on v4.

**Phase G. Hard shapes**

18. Calendars and custom widgets, long pages, branching routes (Returns Desk).
19. Wizards, radio buttons and checkboxes, single-page apps (Expense Desk). All of a wizard's decisions are made **before** the
    first Next.
20. Iframes, popups, logins, endless lists, custom dropdowns (Hazards Desk).

**Phase H. Safety checks**

21. Grounding: the numbers the AI uses must be on the pages.
22. Read-back: confirm what was saved by looking at the record afterwards.

**Phase I. Scaling**

23. Bigger runs, concurrency, cost reports, and a review screen in the app for disagreements.
24. **Checkpoint:** 100-record runs on all five sites.

**Phase J. Research: decision programs (only after everything above)**

25. Build an offline test bench from recorded records. The AI writes the rules once as a program; code then decides with zero AI
    calls.
26. Switch it on only after it reproduces recorded runs exactly, and keep spot-checking it.

## Rough time

| Phases | Estimate |
|---|---|
| A to C | a few days |
| D to F | 1 to 2 weeks (this is where most of the learning is) |
| G to I | 1 to 2 weeks |
| J | open-ended research |

## How the short steps map to the detailed plan

The 26 steps above are a summary. `04_PLAN_DETAILED.md` splits them into **75 smaller steps**, named by phase:

| Phase | Detailed steps |
|---|---|
| A | A1 to A11 |
| B | B1 to B8 |
| C | C1 to C4 |
| D | D1 to D7 |
| E | E1 to E9 |
| F | F1 to F8 |
| G | G1 to G11 |
| H | H1 to H4 |
| I | I1 to I6 |
| J | J1 to J7 |

Two choices in the detailed plan worth knowing now:
- The Returns Desk exam moves to phase H, after the grounding check, because that's where v3 saved a guessed fee.
- The old v2-style click replay is dropped, and the Credit Desk recipe becomes part of the plan engine.

## How the work is done

Each step of `04_PLAN_DETAILED.md` is handled the same way:
1. Why the step exists.
2. What to build: the file, the functions and their inputs and outputs.
3. The code is written.
4. The tests are run.
5. The result is compared with how v3 did it.

Progress is kept in `v4/PROGRESS.md`, so work can be picked up where it stopped.
