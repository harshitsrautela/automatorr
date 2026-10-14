# 11. What 9 Oct 2026 taught: real live traces, and a fourth site

One file with everything learned and changed on 9 Oct. The short versions are in doc 06 ("Generality round"), doc 09 ("9 Oct")
and `LEARNINGS.md` (codes H1 to H21).

## The day in one paragraph

The model quota was fresh, so the live agent could run the **Returns Desk** for the first time (on 8 Oct it had only been tested
with a scripted trace and a test-shortcut plan). The real agent's routes were much messier than the scripted one, and the compiler
broke in five ways; after the fixes a fresh run scored **8 of 8**, learning, proving, using and extending its plan by itself. Then a
fourth site, the **Expense Desk** (`testsites/expense-desk`), was built like a modern web app on purpose: one HTML page, hash routes,
tabs, collapsed sections, and a three-step decision wizard with radio buttons and checkboxes whose ids change on every render. The
plan engine knew nothing of wizards, radio buttons or checkboxes. After general fixes (no site names in the code), fresh runs
scored **8 of 8**, **16 of 16**, **8 of 8** and **8 of 8**. Every test was a small batch (8 or 16 records) to keep the day's Gemini quota for fixing and
re-running.

## What happened, in order

| Run | Site | Result | What broke, and the fix |
|---|---|---|---|
| #53 | Returns | aborted after record 1 | The agent clicked the `<td>` around the RMA link. A cell has no id, role or name, so the compiler gave up on the whole plan. **Fix:** a plain element is found by its exact visible text, with blanks (`{"text": "{rma}"}`). Also: every saved trace is now tried, not just the newest, and the reason each fails is logged |
| #54 | Returns | stopped after 5 (all 5 right, by the live agent) | The agent typed **guessed addresses** (`/items/SKU-5621`, `/returns/RMA-71683`: "Page not found") and typed a list page's address (`?page=7`). A typed address also lost its target (a plain bug), so every dry run went to record 1's order page and the plan was thrown away. **Fixes:** steps that only reach an error page are dropped; a typed `?page=` address is paging; the target is kept; the way back from a side trip is cut to one `revisit` step |
| **#55** | Returns | **8 / 8** | Plan learned from the agent's own trace, agreed with it on 3 records, trusted. 3 records on the plan; the 4th said "the item page is missing", went to the live agent, and the plan was **extended** with the item page. But two refunds had been decided **without** the item page (final sale? warranty?), right only by luck. **Fix:** for every rule the model says whether the pages show what it needs (`known`); a rule it cannot settle, before the one that applies, sends the record to the live agent |
| #56 | Expense | 1 / 1 (live agent, 18 steps) | The plan compiled from this trace was wrong four ways: the radio choice was replayed as a fixed click, the two "Next" buttons were taken for a calendar-like widget, the fields were keyed by random ids (`f50324`), and my new detour cut deleted the "Review claim" click. A tab also expected record 1's own address (`/#/claims/e3lcxu`). **Fixes:** below |
| **#57** | Expense | **8 / 8** | 1 teaching + 2 proof records (a deduction and a rejection; the plan's radio choice, numbers, ticked box and select agreed with the live agent), then 5 plan records. 52 calls, 4 min 16 s |
| **#58** | Expense | **16 / 16** | 1 teaching + 4 proof records (one dry run was inconclusive), then 11 plan records on 19 calls: a deduction, send-back or rejection needed a second question on the wizard's second step, because the plan only knew the fields of record 1's branch (an approval). A proof record had clicked the `<details>` box itself (no text, id or role), so its trace could not be compiled and taught nothing. **Fixes:** proof records teach the plan their branch's fields (`learn_fields`); clicks that open sections are dropped (every read opens them all) |
| **#59** | Expense | **8 / 8** | The same 8 claims on the (nearly) final code. Record 1's trace could not be compiled: the agent clicked the table **row** (`<tr>`, no name, random attributes). Record 2's trace could, so trying every trace rescued the run, and the plan started out knowing the deduction branch. A proof record (a rejection) then taught it the reason field. 4 plan records on 2 shared decision calls. 64 calls, 5 min 41 s. **Fix afterwards:** a nameless element is found as the row that contains the record's value |
| **#60** | Expense | **8 / 8** | The final code, fresh. Both proof records taught the plan their branch fields; 5 plan records at 1 call each. 45 calls, 3 min 47 s |
| #61 | Returns | 15 / 16 | Proven only on "wrong item" returns, the trusted plan (a) refused every other kind correctly, but the refusals counted as failures, so it was dropped and 11 records ran live (427 calls); and (b) decided one change-of-mind refund with a **guessed** 15% fee without the policy page (the only wrong answer of the day). **Fixes:** missing-page refusals are not failures; a proof record whose dry run refused extends the plan; **trust per kind of record** (a trusted plan decides alone only the kinds that agreed with the live agent; a new kind states its answer, goes live once, and is compared) |
| #62 | Returns | **14 / 14** (2 not done: aborted) | Trust per kind of record, live: unchecked kinds stopped before typing, three kinds were checked and trusted, the plan was extended, and **two guesses were caught before saving** (a warranty and a restocking fee the plan could not see). The second catch threw the whole plan away, so I aborted to save quota. **Fix:** a proof disagreement whose live route read pages the plan does not extends the plan instead. 282 calls on the second model |

## What changed in the engine (all general)

**Learning a plan from a trace (`recipe/plan.py`)**

- Plain elements (a table cell) are found by their exact visible text; an element's own `role` attribute wins over its tag (a
  `<button role="tab">` is a tab); radio and checkbox inputs get their proper roles.
- Steps that only reach "Page not found" are dropped; typed `?page=` addresses are paging; a typed address keeps its target.
- Detours are cut: when the route comes back to the exact address it reached earlier, through pages it had already seen, the way
  back becomes one `revisit` step (it reopens the address that this record's own run reached). Only a step that moves to another
  address can start a cut.
- Page types understand hash routes (`/#/claims/*`), UUIDs and short random ids; the page reached by clicking the record's own
  link treats its last part as the record's id whatever it looks like; later steps landing on that address expect the same type;
  `*` matches any one part.
- Clicks that open a `<details>` section are dropped; a click that cannot be found again and did not change the page is skipped
  rather than stopping the whole plan.
- A nameless element (a clickable table row) is found as the one of its kind that contains the record's value, taken from the
  text inside it (the trace recorder keeps that now) or, for older traces, from the agent's goal for the step.
- **Forms in several steps.** Radio buttons and checkboxes are form controls (a group by name). Next / Continue between controls
  end a step (`commit.advance`); each control knows its step. Controls are keyed by name first (ids may change on every render).
- `learn_fields`: another record's live route teaches the plan the form fields of its branch.

**Running a plan (`recipe/plan_exec.py`)**

- Each step of a form is found (by a known control, else by the button that ends it), read, decided, filled and moved on; the
  form must really move on after Next. Controls are read from their form, else their dialog, else the page.
- The first question also decides the fields the plan knows come later; a field it has never seen is asked on its own step.
  Values entered on earlier steps are shown as "already entered".
- Radio answers become the option's own label; checkbox answers become `yes`/`no` or a sorted list of labels; anything not on the
  form is refused.
- Every page read opens all collapsed `<details>` first.
- The per-rule `known` check.
- Only values that really went into the form are reported (so a value decided for another branch's field is never compared).
- The row locator matches a row that has a part whose whole text is the value, or text where the value is not followed by another
  digit (a row's raw text runs its cells together); it never clicks when more than one row matches.

**Comparing with the live agent, extending, aborting (`recipe/plan.py`, `orchestrator.py`)**

- The agent's radio and checkbox clicks are read as values (a second click unticks); an optional message one side left empty is
  not a difference.
- Extension counts what a route **reaches**: page types and in-page clicks (tabs) by name. The plan is only replaced by a route
  that reaches everything the old one did.
- A proof record that agrees teaches the plan its branch's fields; so does a live fallback that reaches nothing new.
- An abort now also stops records that were waiting for the learning lock (they used to run live after the abort).
- **Trust per kind of record**: a trusted plan decides alone only the kinds (the rule that applied plus the choices made) that
  agreed with the live agent; a new kind states its answer before typing anything, the live agent does the record, and the two are
  compared (agree: trusted; differ: that kind stays with the live agent).
- A refusal for missing pages is not a plan failure, and a proof record whose dry run refused for that reason extends the plan; so
  does a proof record where the plan answered differently while the live route read pages the plan does not.

**Tests**

- `tests/test_plan_expense.py` (new, 42 checks): the live agent's real Expense traces (the first record's, one where it clicked the
  table row, and two proof records'), and the wizard in a real browser with a fake model (every outcome, a dry run that saves
  nothing, the row locator).
- `tests/test_plan_returns.py` now 52 checks (messy routes like the agent's real ones, the cell click, `known`, trust per kind of
  record, extending from proof records).
- Every file with a browser part takes `--offline`.
- AP Desk (`test_plan.py`, 54) and the recipe (`test_recipe.py`, 40) still pass after every change. All four files: 188 checks.

## What I learned

1. **A scripted trace is not the agent.** The scripted Returns trace was tidy. The real agent clicked cells instead of links,
   guessed addresses, typed page numbers, went back to the list and found the same return again. The first real trace found
   five compiler bugs in one go. Test a compiler on real traces as early as possible.
2. **A modern web app breaks assumptions that server pages never test.** Hash routes, ids without digits, ids that change on every
   render, tabs that keep the address, sections that hide text, a form in a modal with no `<form>`, a wizard. None of the three
   older sites had any of them.
3. **Look for the stable thing the site gives you.** On the Expense Desk the ids were random but the names were stable; the
   addresses looked random but the page reached by clicking *the record's own link* is by definition the record's page.
4. **Do not let one tidy record define the whole form.** The plan learned from an approval knew nothing about a deduction's
   fields. The records that prove a plan are free information: they now teach it their branch.
5. **Trusted is not the same as proven for this record.** A plan proven on one kind of record guessed on another (run #61). Trust has
   to be per kind of record, checked against the live agent once per kind. And a model's own "I can see what I need" is a weak
   guard: it said yes while the fee table was nowhere on its pages.
6. **"Right by luck" is a bug.** Two refunds were correct but decided without the page that the rules needed. A test that only
   checks the answer would never have shown it; reading what the model based the answer on did. Asking the model, rule by rule,
   whether it can see what it needs, made that a refusal instead of a guess.
7. **Every fix can break something next to it.** My first detour cut ate the click that opens the decision box, because a click
   that stays on the same address looked like a return. Tests on the real trace caught it before a run did.
8. **Do not run a test that resets a site while a run uses it.** I wiped two of a live run's decisions that way. Every such test
   now has `--offline`.
9. **Abort early, and make the abort real.** A run whose plan cannot work spends about 20 calls per record on the live agent. I
   aborted twice to save quota, and found the abort did not stop records queued behind the learning lock (fixed).
10. **Small batches are enough to find what breaks.** 8 records found every problem today; 16 confirmed the fixes. A 100-record run
   is for after the small batch is clean.
11. **Write numbers after you have them.** I caught myself writing "16 of 16" in the README before the run ended (it did end 16 of
    16, but I took it out until it had). Docs say what was measured, and when.
12. **The agent never clicks the same thing twice the same way.** On the same claim it clicked the cell once and the row the next
    time. A plan learned from one trace must survive the other variants: try every saved trace, and find elements by what they
    contain, not only by what they are called.
13. **Test what the browser does, not what Python does.** The row locator looked right in Python and found nothing in the browser
    (an escape the browser rejects, and a row's text with no gaps between cells). Only a check against the real page showed it.

## Cost on 9 Oct

The main model's daily limit (`gemini-3.5-flash-lite`, 500 per Google project per day) was reached on **both keys** during run #61,
after roughly 780 calls by my count (my count misses some calls: proof dry runs, retries). Both keys running out separately, well
past 500, means they are in **two different projects**: about **1,000 calls a day** on the main model. After that the key pool
switched to the second model (`gemini-3.1-flash-lite`), which has its own allowance. Per-minute limits (15 a minute per project and
model) were hit several times with 8 browsers running live records at once; the pool waited and switched keys.

Per run (live-agent steps and plan calls; proof dry runs add about 2 to 4 calls each and are not counted): #53 14, #54 about 108,
#55 122, #56 18, #57 52, #58 97, #59 64, #60 45, #61 427. Most were live-agent steps on teaching and proof records (Returns about
25 per record, Expense 8 to 19). Plan records cost 0.3 to 1.7 calls each; on the Expense Desk, with branch fields learned, 1 call each.

## Still open

- **Small samples on the two newest sites.** A 100-record run of the Returns Desk and the Expense Desk is next, on a day with fresh
  quota (Google's daily reset is 12:30 pm IST until 1 Nov).
- **The Returns plan was extended once** (item page) in run #55, but the extended plan was not re-proven and used in a run yet; a
  change-of-mind return also needs the policy page, which would extend it again.
- **Branches cost live records.** Each new kind of record costs one live record the first time it is met (to check it), and a new
  branch of the route costs one more plus proof records after the extension. Branch *fields* are learned for free from proof
  records; branch *routes* are learned from live records.
- **Trust per kind of record was added last** and has had one live run (#62: 14 of 14, two guesses caught).
- **A gap it does not close**: a kind is the rule plus the choices, so "change-of-mind refund" covers both an unopened return (no
  fee, no policy page needed) and an opened one (fee from the policy table). If the unopened one is checked first, the kind is
  trusted, and an opened one could then be decided without the policy page if the plan still lacks it. The model's own `known`
  answer is supposed to stop that, and on 9 Oct it twice said yes without the facts. Candidate fixes: extend the plan whenever
  any live route reads a page type it lacks (not only on refusals); or have the model quote, for the applying rule, the exact
  page text it used, and check the quotes are on the pages. Until then, keep verification (the grader) on.
- **Not tested**: custom radio/checkbox widgets with no native input, accordions that are not `<details>`, a Next button that saves
  a draft (a wizard's dry run presses Next in its own tab).

## How to check all of this yourself

```bash
cd ~/Downloads/terminautomator/v3
venv/bin/python tests/test_plan_expense.py      # needs node server.js running in testsites/expense-desk for part 2
venv/bin/python tests/test_plan_returns.py      # needs testsites/returns-desk
venv/bin/python tests/test_plan.py              # needs testsites/ap-desk
```

A fresh live run of the Expense Desk (workflow 36): `POST /api/workflows/36/reset-cache`, `POST localhost:5182/admin/reset`, then
`POST /api/workflows/36/runs` with `{"limit": 8}`, and `python3 testsites/expense-desk/grade.py 8` afterwards (doc 10).
