# 09. Results, lessons and what is still open

The long version of all this is `LEARNINGS.md` (every bug, with a code) and `AGENT_SUMMARY.md` (the earlier experiments).
This is the short one.

## Headline numbers (Credit Desk, 100 requests)

| | Live agent for every row | Recipe engine |
|---|---|---|
| Model calls for 100 records | about 900 (about 9 per record) | about 66: about 7 learning, 20 lookups, 40 decisions, a few tie-breaks |
| Records that make no model call of their own | 0 | about 98 of 100 |
| Time | 21 min for only 36 records | about 6 to 7 min for 100 (3 browsers; 2 in the latest runs) |
| Accuracy against the site's answer view | 97.2% (35 of 36) | **100 of 100** (runs #37, #38, #39, #40, and #46 after the plan engine was added) |

Run #46 (8 Oct, after the replay-safety fix and the plan engine were added to the orchestrator, empty cache, site reset): **100/100
in 6 min 30 s**, 1 teaching record (6 agent steps) and 99 recipe records (1 extra AI step in total), the same three notes as
before (*Learned a recipe*, *Deciding 99 records in 20 model calls*), no plan involved. The recipe path did not regress.

Honest caveat: 100% is on **one site and one data set**. The three runs before the voting layer each scored 99/100 with a
different single miss. The fixes close every miss seen; they are not a proof for other sites.

## How accuracy climbed from 80% to 100%

Every step was the same move: find the exact kind of mistake, then **take that job away from the model**.

| Stage | Change | Result |
|---|---|---|
| Live AI decides every row | the v3 starting point | 35/36 (97.2%), one false success |
| Recipe v1 (10 rows) | one model call per row | 8/10: it chose the button before reasoning |
| Reason first, then choose | answer fields ordered request, found, arithmetic, rule, button | 10/10 |
| First 100 rows, fresh data | batches of 5 | **92/100**: 6 near-miss or wrong-city matches, 2 boundary sums |
| Stricter prompt + exact-text lookup | code searches pages for the details the model copied | 96/100 live |
| Run #31 | same code | 95/100: 3 boundary slips, 2 lost saves |
| Formulas | the model writes rules as formulas, code evaluates them in the user's order | 100/100 offline |
| Button from the user's own rule line | model sometimes attached the wrong button to a right rule | 99/100 live three times |
| Voting | two passes, a third on disagreement | 100/100 offline, then 100/100 live (#37, #38) |

## Latest fresh test: run #39 (8 Oct, headless, empty cache, site reset to 0 of 100)

| | |
|---|---|
| Workflow | `localhost:5174 · credit desk (3)`, 100 rows, 2 browsers |
| Result | 100 of 100 succeeded, **100/100 correct**, 0 unmatched |
| Time | about 6 min 4 s |
| Model calls on the live path | 7 steps in total; hit rate 100% (101 hits, 0 misses) |
| What the log said | *Learned a recipe: read 2 pages, decide once, click.* then *Deciding 99 records in 20 model calls (up to 3 at a time).* |
| Quota | both keys hit the daily limit on `gemini-3.5-flash-lite` partway through; the pool switched to `gemini-3.1-flash-lite` and the result was unaffected |
| Fresh-ness | before the run `cached_steps`, `recipes`, `recipe_traces` and `run_metrics` were empty; after it they held 3, 1, 1 and 100 rows, all from that run |

## The held-out test: AP Desk, from 1 of 10 to 100 of 100

A second site built to be unlike the Credit Desk (details in doc 05). What happened, in order, on 8 Oct 2026:

| Run | Engine | Records | Correct | What it showed |
|---|---|---|---|---|
| #41 | recipe + old v2 replay | 10 | **1 (10%)** | 9 records replayed the first record's page (nine refused saves, 51 wasted agent steps); the recipe was never attempted |
| #42 | replay safety fix, plan v1 | 10 | 10 (100%) | no wrong replay any more; the plan failed its proof (a vendor link stored as a fixed label), so all 10 records ran on the live agent (about 15 steps and 60 s each) |
| #43 | plan with self-correction | 10 | 10 (100%) | the plan was corrected, then failed on `abs(...)` in a formula; live agent for all |
| (offline) | `score_plan.py`, real model, no live agent | 10 | 7, then **10/10** | the three misses were "did not run", never a wrong value; fixes: `abs`, `{=}` in formulas, values required on every rule |
| #44 | plan engine | 12 | **12 (100%)** | learned, corrected, proven on records 2 and 3, trusted, then 9 records at 3 calls each |
| #45 | plan engine | 100 | **100 (100%)** | the full test, below |
| (offline) | `score_plan.py 100 10`, batched | 100 | **100/100** | 0.62 model calls per record (20 groups of 5), 320 s with 10 browsers |
| **#47** | **plan engine + shared calls + cheap reads** | **100** | **100 (100%)** | 126 AI calls in all, 97 plan records on 77 calls, see below |

### Run #45: all 100 AP Desk invoices, from an empty cache

| | |
|---|---|
| Accuracy | **100 / 100**: approve 34/34, price variance 18/18, short receipt 18/18, no open PO 18/18, vendor blocked 12/12 (exact-2% and swapped-digit PO cases included) |
| Modes | 1 `cold` (taught), 2 `live` (proved the plan), **97 `plan`**; 0 plan fallbacks, 0 failures |
| Time | **17 min 38 s** with 2 browsers (the first 3 records took about 4 minutes; each plan record took about 18 s) |
| AI calls | **about 343**: 45 live agent steps (3 records) + 7 for the two proof dry runs + 291 plan calls (exactly 3 per record) |
| The same job on the live agent | about 1,500 calls and about 70 minutes (measured: 15 steps and 60 s per record) |
| So the plan engine is | about **4 times fewer calls** and about **4 times faster** |
| Quota | no daily-quota errors, no fallback model, no per-minute rate limits |

What the log said: *The clicks of the first record cannot be replayed for the others (6 value(s)... were read or worked out)*,
*Learned a plan: 17 repeatable steps, 1 value(s) to copy from pages, 3 form field(s) to decide*, *The plan was corrected on
record INV-26-2820 (a fixed label was record data)*, two *Checked the plan against the live agent ... the same values*, and
*The plan is trusted*.

Where the time goes now: the model calls are not the bottleneck any more. Each plan record is about 15 page loads and waits
(the plan replays the agent's own route, including its detours). Batching the decisions the way the recipe does, and
skipping the detours, would cut both calls and time further.

### Run #47: the same 100 invoices after the speed-up (shared model calls, cheap page reads, 8 browsers)

| | Run #45 (one at a time) | Run #47 |
|---|---|---|
| Accuracy | 100 / 100 | **100 / 100** |
| Plan records | 97 | 97 |
| Model calls for the plan records | 291 | **77** |
| All AI calls in the run | about 343 | **126** |
| Time for the plan phase | about 14.5 min | **5 min 51 s** (about 3.6 s per record) |
| Total time | 17 min 38 s | 18 min 33 s (see below) |

The total is not better only because of what happened around it: the primary model's daily quota had been spent by my own offline
scoring, so the live agent ran on the fallback model, which was slow and busy (the 2 proof records took about 10 minutes each
instead of 1 to 2). The plan phase itself is where the change shows. Under normal conditions the whole run is about 10 minutes.

### How the two engines compare (both on 100 records, both 100% correct)

| | Credit Desk (recipe) | AP Desk (plan, batched) |
|---|---|---|
| Task shape | read, decide, click one record-labelled button | read across 4 pages, then fill a form with computed values |
| AI calls | about 66 | about 126 |
| Time | about 6 to 7 min (3 browsers) | about 10 min normally (8 browsers) |
| Live records before the fast path | 1 | 3 (1 teaches, 2 prove) |
| Needs the site to label buttons with the record's value | yes | no |

The recipe is still cheaper where it fits, so it still goes first; the plan covers what the recipe cannot.

## The third site: Returns Desk (hard on purpose)

A returns desk (doc 06, "Harder shapes", and its own `README.md`): a list with no search that must be paged, links whose text is an
id, a branching route, a 19,600-character policy page, a custom calendar. What was and was not tested:

| Test | Result |
|---|---|
| Plan **decisions** with the real model, no live agent (`score_plan.py 100 10 returns`) | **100/100**, 0.64 calls per record; again 100/100 at 0.68 after the final prompt (the primary model's quota was spent, so this ran on the slower fallback model) |
| The same scoring on AP Desk after the prompt changed | **100/100**, 0.81 calls per record |
| Mechanics against the site with a **fake** model and a scripted calendar (`test_plan_returns.py`, 30 checks) | all pass: finds a return on the last page, reads the long page, sets the calendar, saves; stops before saving without a calendar filler |
| Through the real orchestrator, a plan installed as trusted (a **test shortcut**: the plan came from a scripted trace, the learn-and-prove stage is skipped), real browser, real batchers, real decisions, **real scoped agent step for the calendar** | run #49: 6/6 (5 plan records; one refund fell back to the live agent because `₹6,400` could not be typed into a number field, nothing saved: fixed); run #50: **14/14, all plan, no fallbacks**, 3.5 min |
| **Learn and prove with the live agent on the Returns Desk** | 8 Oct: not done (quota). **9 Oct: done**, see "9 Oct" below: run #55, **8 of 8**, plan learned from the agent's own trace, proven, used, and extended |

On 8 Oct what was proven was the decisions and the mechanics; the live agent's own first trace had never been compiled. On 9 Oct it
was, and it broke the compiler in five ways a scripted trace never had (next section).

## 9 Oct: the first real Returns Desk runs, and a fourth site (Expense Desk)

The day's quota was fresh, so every test was a **small fresh run** (empty cache, site reset, 8 or 16 records) to leave room for
fixing and running again. Everything changed is in doc 06 ("Generality round") and doc 11.

| Run | Site | Records | Correct | What it showed |
|---|---|---|---|---|
| #53 | Returns | 1 of 8, aborted | (1 by the live agent) | no plan: the agent clicked a table **cell** (no id, role or name); the compiler gave up. Fixed (find by visible text), aborted to save quota |
| #54 | Returns | 5 of 8, stopped | 5 of 5 by the live agent | a plan was learned, but the agent had typed **guessed addresses** ("Page not found") and a typed address lost its target, so every dry run landed on the wrong page; the plan was thrown away and the rest ran live. Fixed (dead ends dropped, target kept, typed `?page=` is paging, detours cut). Two of its decisions were wiped by my running a site-resetting test during the run (their summaries match the right answers) |
| **#55** | Returns | 8 | **8 / 8** | plan learned from the live agent's own trace, agreed on 3 records, trusted; 3 records on the plan (1 slot call + 1 decision call for the group); the 4th said "the item page is missing", went live, and **extended the plan** with the item page. 122 calls, 7.5 min. Two refunds had been decided without the item page and were right by luck, which led to the per-rule `known` check |
| #56 | Expense | 1 | 1 / 1 | the first live trace on the new site: 18 steps. The compiled plan was wrong in four ways (the wizard, random ids, a detour cut that ate "Review claim", a tab expecting record 1's address). Fixed: forms in several steps, radio buttons and checkboxes, controls by name, the record's own page type |
| **#57** | Expense | 8 | **8 / 8** | from an empty cache: 1 teaching + 2 proof records (a deduction and a rejection, both agreeing on every wizard field), then 5 records on the plan with 3 shared decision calls. **52 calls, 4 min 16 s** |
| **#58** | Expense | 16 | **16 / 16** | approve 5, deduct 5, reject 3, send back 3 (one with two boxes ticked). 1 teaching + 4 proof records (one dry run was inconclusive: the two voting passes disagreed), then 11 plan records on 19 calls. **97 calls, 7 min 23 s**. A proof record had clicked a `<details>` box itself (no text, id or role): its trace could not be compiled, so it taught the plan nothing. Fixed (section clicks dropped; every read opens all sections) |

| **#59** | Expense | 8 | **8 / 8** | the same 8 claims on the final code. Record 1's trace could not be compiled (the agent clicked the table **row**, which has no name), so **record 2's trace was used** (that is why every trace is tried), and the plan started with the deduction branch's fields. A proof record (a rejection) taught it the reason field ("Its route also filled reason, which the plan now knows too"). Then 4 plan records on 2 shared decision calls (only the send-back needed a second question). **64 calls, 5 min 41 s**. Fixed afterwards: rows are found by the record value they contain |

| **#60** | Expense | 8 | **8 / 8** | the final code, fresh. Learned from record 1; the two proof records (a deduction, a rejection) agreed and taught the plan their fields ("Its route also filled deduction, hotel_over_limit, which the plan now knows too"); trusted after 2. The 5 plan records took **1 call each** (2 shared decision calls; only the send-back asked a second question). **45 calls, 3 min 47 s**, the cheapest and fastest Expense run |

| #61 | Returns | 16 | 15 / 16 | **the most useful failure of the day.** The plan was proven on 3 "wrong item" returns (only the order page), trusted, and then (a) every other kind of return correctly refused (its pages lacked the item or policy page), but each refusal counted toward "3 failures in a row", so the plan was dropped and 11 records ran on the live agent (**427 calls**, 20 min), and (b) one change-of-mind refund was decided by the plan with a **guessed 15% fee** (2,108 instead of 1,984) although the policy page was never read. The main model's daily limit was reached on both keys during this run. Fixes: refusals for missing pages are not failures; proof records extend the plan; **trust per kind of record** (doc 06) |

| #62 | Returns | 16 (14 decided, then aborted) | **14 / 14** | the first run with **trust per kind of record**, on the second model (the main one's daily limit was reached in #61). Unchecked kinds stopped before typing anything and went live; three kinds were checked and trusted; the plan was extended with the item page. It also **caught two guesses before anything was saved**: "out of warranty" without the item page (the live agent rightly refunded), and, after the extension, a full refund with no restocking fee without the policy page (7,680 was right). The second catch threw the plan away (a proof disagreement), so I aborted. 282 calls. Fix: a proof disagreement whose live route read pages the plan does not now **extends** the plan instead of throwing it away |

What it cost: live records are the expensive part (Returns about 25 steps each with paging, Expense 8 to 19). A plan record is
0.3 to 1.7 calls depending on how many records share a call and on whether its branch needs a second question on a later step of
the wizard. Proof dry runs add 2 to 4 calls each and are not in the counts above.

So, after 9 Oct: **four sites, all decided correctly from an empty cache** (the Credit Desk by the recipe, the other three by the
plan engine), and each new site needed general fixes, not site-specific ones. Still small samples on the two newest sites (8 and 16 records).

## What the learning phase looks like in the log

A good run reads like this: first record `cold` or `live`, a note that a recipe or plan was learned (for a plan: two proof
records that run live and agree, then *The plan is trusted*), then every other record `recipe` or `plan`. If you see lots of `live` records or a note like *Could not learn a recipe or a
plan (...)* or *The plan disagreed with the live agent*, the site does not fit either fast path and v3 is behaving like v1.

## Lessons worth keeping

1. **Do not ask a small model to do arithmetic or ordering.** Let it read, copy and translate rules into formulas; code
   computes.
2. **"Similar" is the dangerous word.** Near-miss matching is where the model made things up. A character-for-character
   search gives it a fact to follow.
3. **Use the user's own words.** The button for a rule is whatever the user wrote after the colon.
4. **Cache *where*, not *what*.** Locators repeat across rows; answers do not. Decide the answers as cheaply as possible
   (batches, votes only where they pay).
5. **A success is a claim.** Check what reached the server (the tracker) and what the database shows (verification).
6. **Randomness hides bugs.** Repeat checks; make failures reproducible (an offline scorer on saved data).
7. **Silent failures cost the most.** A duplicated class (the old copy silently won), blank fields, and an unconfigured
   save check each made things look fine. When an experiment shows no effect, check the new code actually ran.
8. **Quota is a design input.** Several keys, a per-model fallback and honest messages kept long runs alive.
9. **Free-tier models get busy.** Retry with backoff matters as much as the prompt.

## More lessons, from the AP Desk round

10. **Test on a site you did not tune against.** The recipe scored 100% on the site it was built on and the first attempt
    on a new site scored 10%.
11. **A cache that is wrong without failing is the dangerous kind.** The replay "succeeded" nine times while doing the wrong
    thing; only a refused save and an outside grader showed it. Never trust a fast path without a check that does not
    come from the agent.
12. **Prove a learned procedure before trusting it**: run it beside the live agent on a few records and compare, before the
    agent changes the page (otherwise the procedure can copy the agent's answer).
13. **Throw away expensively only for the right reason.** Different values reject a plan at once; a dry run that merely
    errored does not, because the live agent does that record anyway and a wrongly dropped plan costs every later record.
14. **A cheap scorer pays for itself.** Dry-running the decisions with the real model and no live agent found three bugs in
    about 60 calls, where the live runs found one per 150.
15. **Make the model's job smaller each time something fails**: arithmetic, ordering and "which button" went to code for the
    recipe; for the plan, sums, comparisons and rule order stay in code too, and values that only need copying must appear
    verbatim on a page.

## Still open (so you do not assume it is solved)

- **Four sites, all written by us.** Each was built to be unlike the ones before (server pages, a hard paged site, a single-page
  app with a wizard), and each broke something general. A real company ERP is still the real test (logins, iframes, very slow
  pages, grids that load as you scroll, custom widgets that are not calendars).
- **A plan's proof is two or three records.** A branch none of them took is unproven (verification should always be on). The
  per-rule `known` check and plan extension narrow this.
- **Plans replay the agent's own route.** Detours that come back to the same address are cut (9 Oct); a page the agent read but did
  not need is still visited.
- **Small samples on the newest sites**: Returns Desk 8 live records, Expense Desk 8 + 16 + 8 + 8 (9 Oct), to save quota. A 100-record run
  of each is the next step on a fresh quota day.
- **The replay-safety verdict is conservative.** It was only checked on synthetic form traces, not on the real DataDesk form
  (which is Supabase-backed); a form fill that is safe but trips it simply loses the free replay and uses a plan or the live
  agent. Worth one real DataDesk run.
- **One leftover DataDesk coupling in the code**: the reconciliation prompt (`agent/prompts.py`,
  `build_reconciliation_prompt`) says "You are on DataDesk's People page", so reconciliation only makes sense on that
  site. (The field normalizers in `evaluation/normalizers.py` are keyword-based and generic.)
- **Cost per day**: a plan record now costs under 1 call, but the live agent's proof and teaching records cost about 15 each, and
  offline scoring spends quota too (about 260 calls on 8 Oct emptied the primary model's day). A free-tier day is about 500 calls
  per model and project.
- **Keys may share one quota.** Google applies limits per *project*, so two keys from the same project do not double the
  daily allowance (doc 07).
- **From the earlier bug list** (`AGENT_SUMMARY.md`): verification settings are not validated at save time; a cache can be
  poisoned by a failed first record; the retry with screenshots can double load; instructions in data fields can inject
  prompts; there is no login; `DEV_PREFILL_SECRETS` must be removed before sharing; the Credit Desk example is still the
  default for new tasks.
- **Verifier limits**: `table` checks read with one `select *` (1000-row cap) and duplicate match keys can give false passes.
- **Not built** (see `FUTURE_PLANS.md`): a "check my prompt" panel, example prompts, a learned-steps viewer, rate-limit
  pacing across browsers, per-run cost budgets.
