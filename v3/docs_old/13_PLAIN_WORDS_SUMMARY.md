# Everything since v2, in plain words

A catch-up for someone who last looked when we were starting on the Credit Desk. It takes about 30 minutes to read.
It covers what v3 is, what was tried, what broke, what was fixed, the numbers, what is running right now, and what comes next.

---

## Part 0. The one-paragraph version

v2 was a browser agent that did each record with an AI (the "live agent") and cached its **clicks** so the next record
could replay them with no AI. That works for plain form filling and fails as soon as each record needs reading and
deciding. v3 turned this into a **web app** (you configure tasks in a browser, nothing lives in code), then spent most of
its time on one question: *how do you avoid paying an AI call for every record when the records need thinking?* The answer
grew in layers: first a **recipe** (for one shape of task), then a **plan** (the general version: replay the route, ask
the AI only small focused questions, let code do the arithmetic), then a series of safety nets so a cached plan is never
trusted before it has proven itself against the live agent. We built **four test websites**, each designed to break the
previous approach, and each did break it, until the fixes were general enough. Right now I am building the next layer:
turning the AI's *decision* itself into checked code (so a trusted task makes **zero** AI calls per record), plus handling
the messy things real websites do (logins, popups, iframes, endless scrolling, custom dropdowns).

---

## Part 1. Where v2 left off

You know v2 well, so just the essentials:

- A **live agent** (browser-use + Gemini) does a record step by step: look at the page, decide the next click, repeat.
  About 15 AI calls and 60 seconds per record.
- The **cache**: after the first record, its clicks and typed values are saved as selectors. The next records **replay**
  them with no AI. If a selector does not match, the live agent takes over for that step.
- Great for "type these record fields into this form". Useless when the value to type, or the button to press, depends
  on what is on the page. Then the replay either fails or, worse, quietly repeats what the first record did.

v3 started from that limit.

---

## Part 2. v3 the app (the boring but necessary part)

Before the clever parts, v3 became a real application:

- **FastAPI backend + React frontend**, served on port 8002. Two pages: **Agent** (create a task: plain-language
  instructions with the website address, your records, settings, how to check results) and **Logs** (every run live:
  each record's steps, the AI's reasoning, final answer, and an accuracy verdict).
- **Everything is stored in Postgres** (a Supabase project, in a private schema `agent_state` owned by a limited login):
  secrets (encrypted), workflows (tasks), runs, run events, and the learned "cache" tables. Nothing about a task lives in
  `.env` or code any more.
- **Gemini keys**: several keys, used in turn by a **key pool**. A key that hits a limit is set aside and the call goes to
  another key; when the main model is out for the day on every key, it falls back to a second model.
- **Verification**: a run can be graded against a database view of correct answers (the Credit Desk has one), or by an
  outside grader script (the newer test sites).
- **Headless mode** (`FORCE_HEADLESS=1`) so runs do not open Chrome windows.

The docs for all this are `v3/docs/00` to `11` (read in order). They skip what v2 already explained.

---

## Part 3. The Credit Desk and the recipe engine (fast path #1)

### The task
The Credit Desk (`testsites/credit-desk`, port 5174) has 100 credit requests. For each: open the request, look the
retailer up on a Ledger page, apply rules (limits, overdue amounts, etc.), then click **Approve**, **Reject** or **Hold**.
The site's own database view knows the right answers, so we can grade every run.

### The live agent alone
It worked, but: **97.2%** accuracy (35 of 36), about **9 AI calls per record**, and 21 minutes for only 36 records. At
that rate 100 records cost about 900 calls, and the free Gemini tier gives roughly 500 a day per model per Google project.

### The recipe idea
The route for every request is the same: read the request page, read the ledger, decide, click one button. Only the
**decision** differs. So after the live agent does the first record, v3 learns a **recipe**:

1. which pages to read (replayed by code, no AI),
2. **one** AI question per record: "here are the pages, here are the rules, which button?",
3. click the button, which is labelled with the record's value ("Approve request 7"), so code can find it.

Even better: the decision questions are **batched**, so several records are decided in one call.

### How accuracy got to 100%
This took many rounds. Each round found the exact kind of mistake and took that job away from the model:

| Stage | Change | Result |
|---|---|---|
| Live agent only | the starting point | 35/36 (97.2%) |
| Recipe v1 | one call per record | 8/10: it chose a button before reasoning |
| Reason first | answer fields ordered: facts, arithmetic, rule, button | 10/10 |
| 100 fresh records | batches of 5 | 92/100: near-miss matches, boundary sums |
| Exact-text lookup | code checks the copied details really exist on the page | 96/100 |
| **Formulas** | the model writes each rule as a formula; **code** evaluates them in the user's order | 100/100 offline |
| Button from the user's own words | it sometimes attached the wrong button to the right rule | 99/100 three times |
| **Voting** | two passes at once, a third only when they disagree | **100/100** live, repeatedly |

Two lessons from that ladder run through everything since:

- **Never ask a small model to do arithmetic or decide which rule comes first.** It reads and translates rules into
  formulas; code computes.
- **"Similar" is the dangerous word.** When the model was allowed to match things loosely, it made things up. Exact
  checks against the page text fixed it.

### Result
Credit Desk, 100 records: **100/100** in about 6 to 7 minutes, about **66 AI calls in total** (vs about 900 for the live
agent). Re-run several times (runs #37 to #40, and #46 after later changes): still 100/100.

The catch: the recipe only fits one *shape* of task (read, decide, click a record-labelled button).

---

## Part 4. AP Desk: the held-out site, and the plan engine (fast path #2)

### Why a second site
A 100% score on the site you built the engine on proves little. So I built a second site, deliberately different: the
**AP Desk** (`testsites/ap-desk`, port 5180). An accounts-payable desk: for each invoice do a **three-way match**
(invoice, purchase order, goods receipts), then fill a **form** with computed values (a status dropdown, a reason, a typed
note with numbers, a date) and confirm a dialog. Server-rendered pages, opaque addresses, numbered paging, a search box.

### First attempt: 1 out of 10
Run #41 scored **1/10**. The autopsy:

- The old v2 replay kicked in and **replayed the first record's clicks** for the others (including a link to the first
  record's own invoice). Nine records "succeeded" while doing the wrong thing; only the site refusing the saves and the
  outside grader showed it.
- The recipe was never even tried, because the replay looked like it worked.

Lesson: **a cache that is wrong without failing is the most dangerous kind.**

### Fix 1: a safety verdict on replay
After the first record, v3 now judges whether replaying its clicks is safe: unsafe if any typed value was read or worked
out (not a copy of a record field), or if any click names something belonging to that record. If unsafe, the replay is
switched off for that task.

### Fix 2: the plan engine
A **plan** is the live agent's route with every value that changes marked by where it comes from:

| Source | Example | At run time |
|---|---|---|
| **field** | the invoice number typed in search | copied from the record |
| **slot** | a vendor name or PO number seen on one page and used later | read off the page by one small AI call |
| **computed** | the decision, reason, date, note | decided by the AI, arithmetic done by code |

The route is replayed by code. Only two small AI questions remain per record (copy these values; decide this form), and
both are batched across records running at the same time.

### Fix 3: prove a plan before trusting it
One trace cannot show which parts of a route are fixed and which vary. So a new plan is **untrusted**. On the next records
it does a **dry run** (everything except pressing save) in a second tab *before* the live agent touches the record, then
the live agent does the record normally, and the two are compared. Two agreements: trusted. Any different value: the
plan is thrown away. A plan can also correct itself during proof (a "fixed" label that turned out to be record data
becomes a slot).

### Results
- Run #44: 12/12. Run #45: **100/100**, about 343 AI calls, 17.6 minutes.
- After batching and faster page reads, run #47: **100/100**, **126 AI calls**, the plan phase taking under 6 minutes.
  (The live agent would need about 1,500 calls and 70 minutes.)

---

## Part 5. Returns Desk: the hard one

### The site
`testsites/returns-desk`, port 5181. A warehouse returns desk built to find where the plan engine breaks:

- a list that must be **paged through** (no search box),
- links whose text is an **id** (order numbers, item codes),
- a **custom calendar** for the pick-up date (buttons, not a date field),
- a **very long policy page** (the fee table is 9,000 characters down),
- a route that **branches**: a wrong-item return only needs the order page; a defect needs the item's warranty; a
  change-of-mind return needs the item, its category and the policy's fee table.

### What was added
- **Paging**: page-number clicks are dropped from the route; at run time the plan walks the list until the record appears.
- **Id links become slots**: their text is read off the page each time.
- **Widgets**: a run of clicks that fills a field (the calendar) is one control. The AI decides the date; code drives an
  ordinary calendar itself (open it, page to the month, click the day); anything odder goes to a small scoped agent step.
- **Long pages**: table rows are given with their headings, and long pages are cut to the lines that matter to the record.
- **Branches**: the model must say when the pages lack what a rule needs, and a record that falls back to the live agent
  over a richer route **extends** the plan.
- **Shared calls** (`batcher.py`) and cheap page reads made each plan record about 4 seconds and under 1 AI call.

### 8 October: the quota wall
The decisions scored 100/100 offline, but the live learn-and-prove could not run: the main model's daily quota was spent,
and the fallback was too slow. Two side findings that day: Gemini's daily quota resets at **midnight Pacific** (12:30 pm
IST until 1 Nov), not 00:00 UTC, and the key pool was fixed to compute that properly.

### 9 October: the first real live traces
With fresh quota, the live agent ran the Returns Desk for real. Real traces are much messier than scripted ones, and the
compiler broke in five ways at once:

| What the real agent did | Fix |
|---|---|
| clicked a table **cell** instead of the link | plain elements are found by their exact visible text |
| typed **guessed addresses** ("Page not found") | steps that only reach an error page are dropped |
| typed a list page address (`?page=7`) | treated as paging |
| went back to the list and found the same record again | **detours are cut**: one step reopens the address it already reached |
| a typed address lost its target (a plain bug) | fixed |

After that, run #55: **8/8**, with the plan learned from the agent's own trace, proven, used, and **extended** when a record
needed a page the plan did not have.

---

## Part 6. Expense Desk: a modern single-page app

### The site
`testsites/expense-desk`, port 5182, built on 9 Oct to look like a modern web app: one HTML page with **hash routes**
(`/#/claims/kxqfab`), content loaded late from an API, a **type-to-filter search** with clickable rows, **tabs** that keep
the address, **collapsed sections**, money in six currencies, and the decision in a **three-step wizard in a modal**:
radio buttons, then fields that depend on the choice (numbers, a group of checkboxes, a dropdown), then a confirmation
checkbox. Control ids change on **every render**.

### What broke, and the fixes
The plan engine knew nothing of wizards, radio buttons or checkboxes. All general fixes:

- page types understand hash routes and random ids (even ids with no digits);
- an element's own `role` wins (a button that is really a tab);
- every page read opens collapsed sections;
- radio buttons and checkboxes are form controls; "Next" ends a step; controls are found by their stable **name**;
- the plan fills a wizard step by step, and asks about later steps' fields in the first question where possible;
- the records that prove a plan **teach it the fields of their branch** (a deduction's fields, a rejection's reason).

### Results (all fresh, empty cache)
Runs #57, #58, #59, #60: **8/8, 16/16, 8/8, 8/8**. The last one: 45 AI calls total, 3 min 47 s, plan records at 1 call
each.

---

## Part 7. The "right by luck" problem, and trust per kind of record

The most important finding of 9 October.

- Run #55 (Returns): two refunds were decided by the plan **without** the item page that the rules needed. They happened to
  be right. I added a per-rule check: the model must say, rule by rule, whether the pages show what it needs.
- Run #61 (Returns, 16 records): the plan was proven on three "wrong item" returns (which only need the order page). It was
  trusted, then decided a change-of-mind refund with a **guessed 15% restocking fee** (it should have been 20%) without
  ever reading the policy table. The model's own "yes, I can see what I need" answer was false. 15/16. In the same run, a
  rule meant to catch broken plans ("3 failures in a row") wrongly dropped a healthy plan that was refusing exactly as it
  should, so 11 records went to the live agent: 427 calls.

The fixes:

- **Trust per kind of record.** A "kind" is the rule that applied plus the choices made (for example `rule 4: Refund`).
  A trusted plan decides alone only kinds it has already been checked on. A new kind: the plan says what it *would* enter,
  stops before typing, the live agent does the record, and the two are compared. Agree: that kind is trusted from now on.
  Disagree: that kind stays with the live agent.
- Refusals for missing pages are no longer counted as failures.
- When a proof record shows the plan was missing a page, the plan is **extended** instead of thrown away.

Run #62 (Returns, on the fallback model): **14/14** decided correctly (2 left undone when I aborted to save quota), with
**two guesses caught before anything was saved**.

One gap is still open: a single "kind" can mix records that need different pages (an unopened refund needs no fee; an
opened one does). That is one of the things the current work closes (Part 9).

---

## Part 8. Quota, in plain terms

- Free tier: about **500 calls per day per model per Google project**, and **15 per minute**. Your two keys are in **two
  different projects**, so about **1,000 calls a day** on the main model (`gemini-3.5-flash-lite`), then the key pool
  switches to a second model (`gemini-3.1-flash-lite`) with its own allowance.
- It resets at **12:30 pm IST** (1:30 pm after 1 Nov).
- The expensive part is never the plan: it is the **live agent** teaching and proving (15 to 25 calls per record). On 9 Oct
  the main model was used up by run #61.
- Rule we follow now: test with **small batches** (8 to 16 records), and only run 100 when a small batch is clean.

---

## Part 9. What I am doing right now: the general-purpose round

Your ask: make it a general browser automation agent that **caches as much as possible** and calls the AI **only when
necessary**, because real work repeats.

Today, even with a trusted plan, every record still costs about 1 AI call (the decision). The current work turns the
decision itself into checked code, and teaches the engine the messy things real sites do. Seven pieces, built in parallel
by helper agents, each checked by an independent reviewer who tried to break it, then fixed:

| # | Piece | What it does | Status |
|---|---|---|---|
| 1 | **Decision programs** (`recipe/facts.py`, `program.py`) | After a few records, the AI writes the task's rules once as a small program: named facts read off the pages by code (labels, table lookups) plus ordered rules. Code checks it reproduces every example exactly. It is then trusted **per kind of record** after agreeing with the AI twice, and spot-checked every 10th use. Result: a trusted task makes **zero AI calls per record**. | built, 95 checks |
| 2 | **Grounding** (`recipe/evidence.py`) | Every number the AI uses must be on the pages (or derivable in one step from numbers that are); quotes must really exist. Catches the guessed-fee mistake. | built, 76 checks |
| 3 | **Pacing** (`keypool.py`) | Spreads calls to stay under 15 per minute per key, instead of hitting "too many requests". | built and reviewed, 43 checks |
| 4 | **Real-world pages** (`recipe/webkit.py`) | Forms inside **iframes**, **infinite scroll** and "Load more", **cookie banners and popups**, **custom dropdowns** (searchable comboboxes), ARIA radio buttons/checkboxes, rich-text fields, error pages with a **Retry** button, **login** pages. | built, 95 checks |
| 5 | **Read-back** (`recipe/readback.py`) | After saving, reopen the record's page and check the saved values are shown. A success is a claim until confirmed. | built, 83 checks |
| 6 | **Coverage and pruning** (`recipe/coverage.py`) | One AI call reads your instructions and adds the pages the rules mention that the route skipped (the policy page), before a record has to fail for it. Pages that never supply a fact get skipped later. | built, 80 checks |
| 7 | **A fifth test site** (`testsites/claims-desk`, port 5183) | An insurance claims desk with every hazard on purpose: login with **session expiry**, cookie banner, "What's new" popup, **infinite-scroll** list, random errors with Retry, slow pages, a decision form **inside an iframe** with ARIA radios, **searchable comboboxes** and a rich-text note. | built, smoke test passes |

The reviewers found real bugs before anything was wired in, for example: a table lookup that treated "1 Year" and
"1 Month" as the same row (because both start with 1), a cell that wrapped onto two lines splitting into two rows, a
program check that accepted wrong dates, a popup dismisser that could have pressed "OK" on a real confirmation, and a
read-back that ignored the word "not". All fixed, with a test for each.

### The step running right now: wiring it in
The pieces exist but the engine does not use them yet. A workflow is wiring them in, **one step at a time** (they edit the
same core files), each step reviewed by an independent skeptic and fixed:

1. **Learning** (`trace.py`, `plan.py`): plans understand logins (typed values that come from your instructions), popups to
   dismiss, "Load more", ARIA choices, custom dropdowns, rich-text fields, and which page is the record's own page.
2. **Running** (`plan_exec.py`): frames, popups, retries, logging back in after a session ends, endless lists, grounding,
   **decision programs (zero calls)**, slots read by code once proven, read-back after saving, skipping useless pages. Plus
   a scripted trace and tests for the hazard site.
3. **Orchestration** (`orchestrator.py`): when to learn a program, when to trust it, spot checks, dropping a program that
   disagrees, learning whether a site shows saved values, the coverage question, proactive plan extension.
4. **A cross-cutting safety review** with four lenses, each finding re-checked by a skeptic before fixing:
   - can a guessed or unverified value ever be saved?
   - can a save button ever be pressed twice for one record?
   - do the AP, Returns and Expense desks still work exactly as before?
   - does any path make more AI calls than it needs?
5. **Docs**: a new `docs/12_GENERAL_PURPOSE_ROUND.md`, and updates to the existing docs.

Honest status: **none of this round has run live on Gemini yet.** Everything so far is tested with fake AI answers and
real browsers against our test sites. Live testing is the next step.

---

## Part 10. Next steps

1. **Finish the wiring** (running now, in the background). If my session limit interrupts it, it resumes where it stopped.
2. **The 100-record runs**: a separate session is waiting for the quota reset at **12:30 pm IST today** to run the Returns
   Desk and the Expense Desk on 100 records each from an empty cache. It will restart the server on the new code and run
   every test file first.
3. **Live tests of the new round**, small batches first:
   - Expense Desk and Returns Desk again, now watching how many records the **decision program** handles with zero calls;
   - the new **hazard site** (v6): login, popups, iframe form, endless list, custom dropdowns, session expiry.
4. **Then a real website** that we did not build, which is the real test of "general purpose".

## Part 11. Where to look

- `v3/docs/00_START_HERE.md` onwards: the full explanation, in order.
- `v3/docs/09_RESULTS_AND_LESSONS.md`: every run and its numbers.
- `v3/docs/11_LEARNINGS_9_OCT.md`: the 9 Oct story in detail.
- `v3/LEARNINGS.md`: every bug, with a code (F, G, H...).
- Test sites: `testsites/credit-desk` (Credit), `v3` (AP), `v4` (Returns), `v5` (Expense), `v6` (Hazards), each with a
  README and a `grade.py`.

---

## Part 12. Update, 11 Oct: how this round ended

- All 7 pieces were built and wired in, with about 900 test checks passing.
- Live, on the Expense Desk (fresh, 16 records each), the new engine scored 15, 15, 15, 12 and 14 of 16, worse than the 9 Oct
  engine's 16/16. Each run found a new bug; they are fixed. But **no decision program was learned live**, so the main goal
  (zero AI calls per record) is not reached yet.
- **Decision: option B.** The unproven parts (decision programs, coverage, pruning, read-back, slots by code, extending on
  agreement) are now **off by default**. Turn them on with `TA_FEATURES=all` (or a comma list) when you want to work on them. With
  them off, the engine behaves like the 9 Oct one plus the safe improvements (pacing, grounding, real-world page handling, wizard
  fixes).
- Where to continue: `docs/12_GENERAL_PURPOSE_ROUND.md`, section 0, lists the open problems in order.
