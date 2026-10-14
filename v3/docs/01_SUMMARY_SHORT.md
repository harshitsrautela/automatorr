# 01. What happened in v3: the short version

A short account of the v3 work, about a 10-minute read. The long version, with every number and every lesson, is `03_WHAT_HAPPENED_FULL.md`.

---

## Where we started (v2)

v2 had a **live agent**: an AI (browser-use + Gemini) that does one record step by step, looking at the page and deciding each
click. It works, but costs about 15 AI calls and a minute per record. v2 also had a **cache**: it saved the first record's
clicks and replayed them for the next records with no AI. Great for "type these values into a form". Useless when each record
needs reading and deciding, because then every record needs different clicks and values.

**The whole of v3 is about one question: how to avoid paying for the AI on every record when the records need thinking.**

## Step 1. v3 became a real app

A web app instead of scripts: a FastAPI backend and a React frontend on port 8002, with everything stored in Postgres (Supabase):
tasks, records, runs, logs, encrypted keys, and the learned cache. A task is created in the browser (instructions, website,
records), run, and watched record by record on the Logs page, with an accuracy score at the end.

## Step 2. The Credit Desk and the recipe engine

**The Credit Desk** test site has 100 credit requests. For each, read the request, look the shop up on a ledger, apply the rules,
and click Approve, Reject or Hold.

- **Live agent alone:** 97% right, about 9 AI calls per record (about 900 for 100 records). Too expensive.
- **The recipe:** the route is the same for every record; only the decision differs. So after the first record, code replays the
  pages, asks the AI one small question ("which button?"), and clicks it. Questions for several records go in one call.
- **Getting to 100%** took many rounds. Each time we found the kind of mistake and took that job away from the AI:
  - the AI writes each rule as a formula, and **code does the arithmetic**;
  - code checks that copied details really appear on the page;
  - two answers must agree (voting).
- **Result: 100/100, with about 66 AI calls instead of about 900.**

**The catch:** the recipe only fits one shape of task.

## Step 3. AP Desk, and the plan engine

To check the engine had not simply been tuned to one site, a second, different site was built. **AP Desk** is invoice matching across 4 pages, then
a form with computed values.

- **First try: 1 out of 10.** The old v2 replay kicked in and repeated the first record's clicks for every record. Nine
  "successes" were wrong. Lesson: **a cache that is wrong without failing is the most dangerous kind.**
- **Fixes:**
  - a safety check that switches replay off when it's not safe;
  - the **plan engine**: replay the route by code, mark which values come from the record, which are copied from a page, and which
    must be decided;
  - ask the AI only those small questions, batched.
- **Proof before trust:** a new plan must first agree with the live agent on 2 records before it runs alone.
- **Result: 100/100, with about 126 AI calls instead of about 1,500.**

## Step 4. Returns Desk (hard on purpose)

The third site has:
- a list that has to be paged through (no search box);
- links whose text is an id;
- a custom calendar;
- a 20,000-character policy page;
- routes that branch depending on the record.

Each broke the engine, and each fix was general:
- paging is found at run time;
- calendars are driven by code;
- long pages are trimmed to what matters.

**The first runs with the real live agent were messy.** It clicked table cells instead of links, guessed addresses that didn't
exist, and went back and forth. Five compiler fixes later: **8/8**, with the plan learning, proving and extending itself.

## Step 5. Expense Desk (a modern web app)

The fourth site is a single-page app, with:
- hash addresses (`/#/claims/...`);
- tabs and collapsed sections;
- a search box;
- a 3-step decision wizard with radio buttons and checkboxes whose ids change on every page draw.

The engine learned:
- wizards (Next between steps);
- radio buttons and checkboxes;
- finding fields by their stable name.

**Results: 8/8, 16/16, 8/8, 8/8.**

## Step 6. "Right by luck", and trust per kind of record

- On Returns Desk, a trusted plan decided a refund with a **guessed fee**. It had never read the policy page, and the AI claimed it
  could see what it needed. So asking the AI "do you have what you need?" is not a real check.
- **Fix: trust per kind of record.** A plan proven on "wrong item" returns isn't trusted for "change of mind" refunds until it has
  matched the live agent on one of those too.
- **Next run: 14/14, with two guesses caught before saving.**

## Step 7. The "general-purpose" round (10 to 11 Oct)

The goal of this round was a general agent that caches as much as possible. Helper agents built 7 pieces in parallel. Each was checked by an
independent reviewer who tried to break it, then fixed:

| Piece | What it does |
|---|---|
| **Decision programs** | The AI writes the rules once as a small program; code then decides each record with **zero AI calls** |
| **Grounding** | Every number the AI uses must be on the pages |
| **Pacing** | Keeps calls under Google's 15-per-minute limit |
| **Real-world page handling** | Iframes, popups, logins, endless lists, custom dropdowns, retry buttons |
| **Read-back** | Reopens the record after saving to confirm what was saved |
| **Coverage and pruning** | Reads pages the instructions mention; skips pages never used |
| **A fifth test site** | One with all those hazards |

They were wired in and safety-reviewed: **about 900 test checks pass**.

**Then the live runs:** 15, 15, 15, 12 and 14 out of 16. That's **worse** than the old engine's 16/16. Each run found new bugs
(fixed). But **no decision program was ever learned in a live run**, and the live agent itself made several of the mistakes.

## Step 8. How it ended: option B

The unproven parts (decision programs, coverage, pruning, read-back and two smaller ones) are now **switched off by default**. They
can be turned on with `TA_FEATURES=all`.

What stays on:
- pacing;
- grounding;
- page handling;
- all the fixes from the live runs.

**Final check: 7/8.** The one miss was the live agent's own mistake; the plan had the right answer.

## The 6 lessons that matter most

1. **Don't let the AI do arithmetic or decide which rule comes first.** It translates; code computes.
2. **A cache must prove itself before it's trusted**, and per kind of record, not once overall.
3. **A wrong cache that looks fine is worse than a slow one that fails loudly.** Always grade with something independent.
4. **Every new kind of website breaks something.** Test on sites the engine was not tuned on.
5. **The AI's opinion of itself is weak evidence.** Check it with code (numbers on pages, values on the form).
6. **The live agent is also fallible**, and it's the referee. A future design needs a better referee.

## Where things stand

- **v3 works:** 100/100 on Credit Desk and AP Desk, and reliable small runs on Returns and Expense Desk.
- **It's big:** about 13,800 lines of engine and 5,500 lines of tests, grown by patches.
- **Next:** a rebuild from scratch, one step at a time, using v3, its tests and the 5 test sites as the reference and
  the exam. See `02_PLAN_SHORT.md`.
