# 12. The general-purpose round (9 to 10 Oct 2026): cache as code, check before trusting

One file for everything built after the 9 Oct runs. The short version is in doc 06 ("General-purpose round"), pacing is in doc 07,
the new site and tests are in doc 10, and the bug list is in `LEARNINGS.md` (codes J1 to J14).

**Read this first: nothing in this file has run live yet.** Every check below used a **fake model** (a function that answers from
the test site's own data) or hand-written inputs. No call went to Gemini. The numbers here are from those tests, and are marked so.
The first live run is the next step (section 7).

## 0. Where this round ended (11 Oct): option B, the unproven parts are switched off

After the wiring, five fresh live runs of 16 records on the Expense Desk scored **15, 15, 15, 12 and 14 of 16**. Before this
round the same site scored **16/16 and 8/8**. Each run found new bugs (fixed, with tests): facts on a second tab were not read,
"Back" pressed inside a wizard was learned as a field, examples for programs missed the wizard's later steps, a confirmation tick
on a later step counted as a disagreement. But no **decision program** was ever learned live, and the new parts made the engine
less reliable, not more. So the decision was **option B**:

- The parts that have not proven themselves live are **off by default**: `programs`, `coverage`, `pruning`, `readback`,
  `slot_code`, `extend_on_agree`. Switch them on with the environment variable `TA_FEATURES` (comma separated, or `all`), for
  example `TA_FEATURES=programs FORCE_HEADLESS=1 uvicorn main:app --port 8002`. The switches are `FEATURES` in `orchestrator.py`.
- What stays **on**: pacing (doc 07), the grounding check (`recipe/evidence.py`), the real-world page handling
  (`recipe/webkit.py`: frames, pop-ups, retries, logins, endless lists, custom controls), and the fixes found in the live runs
  (facts on every view of a page, Back in a wizard, the confirmation-tick comparison, `{= }` and `if()` in programs, fact defaults,
  empty lists).
- Known open problems, in order: (1) **no program has been learned live yet**; offline, from 6 recorded records, the model wrote a
  correct program on its first try twice, but live the examples were still incomplete or held stray fields (the search box `q`);
  (2) a record can be lost when a wizard's Next was pressed and the next step's decision then fails (pressing Next counts as acting,
  so it is never retried); (3) the live agent itself is sometimes wrong (3 of the misses in those runs), and it is the referee the
  plan is checked against; (4) the grounding check sometimes refuses a correct answer (it costs a live record, never a wrong save).
- Next step for programs: work offline, on recorded examples (`scratchpad`-style scripts that dry-run the plan on a few records and
  call `learn_program`), and switch `programs` on only when a program reproduces recorded runs exactly.

## 1. Why: what the 9 Oct runs showed

| What happened on 9 Oct (doc 11) | What it showed |
|---|---|
| Run #61: a trusted plan decided a change-of-mind refund with a **guessed 15% restocking fee** (right: 20%); the policy page was never read | the model's answer was used without any check that its numbers were on the pages |
| Twice the model said, rule by rule, that it could see what it needed (`known: true`) when it could not | the model's own report on itself is a weak guard |
| A trusted plan record still cost model calls every time: a slot call and 2 or 3 voting passes per group of up to 5 records (0.3 to 1.7 calls per record) | the decision was never cached, only the route |
| A record counted as done because the plan pressed Save | nobody looked at what was saved |
| A plan learned from one record only reads that record's pages; every other branch cost a live record (in run #61, 11 records and 427 calls) | the plan does not read what the instructions say to read |
| With 8 browsers running live records, Gemini answered "429 per minute" dozens of times | the key pool reacted to the limit instead of keeping under it |
| The four test sites had no sign-in, no pop-ups, no iframes, no endless lists and no custom widgets | the engine had never met what real web apps put in the way |

The rule for this round: **a model call only where needed; anything cached is checked before it is trusted; when unsure, refuse and
let the live agent do the record (never type or save a guess); never press a saving button twice.**

## 2. What was built

| Piece | File | In one line |
|---|---|---|
| Facts read by code | `recipe/facts.py` | numbers (`₹1,25,000`, `20%`, `45 days`), dates (`05 Sept 2026`), the value next to a label, table rows (both the tab-separated text and the `[table rows with their headings]` block) |
| Decision programs | `recipe/program.py` | the model writes the decision **once** as facts + ordered rules; code runs it for every record; trusted per kind of record |
| Grounding | `recipe/evidence.py` | every number, date and quote an answer uses must be on the record's pages (or one step of arithmetic away; a rate must be printed) |
| Pacing | `keypool.py`, `recipe/llm.py`, `agent/runner.py`, `prompt_gen.py` | every call is booked in its key's minute first; `GEMINI_RPM` (default 14 per key and model, 0 = off) |
| Real-world pages | `recipe/webkit.py` | frames, ARIA radio groups and checkboxes, searchable comboboxes, rich-text boxes, "Load more" and endless scroll, pop-ups, Retry, sign-in pages |
| Read-back | `recipe/readback.py` | after a save, open the record's page again and look for the saved values |
| Coverage and pruning | `recipe/coverage.py` | one question adds the side trips the instructions need; side trips that never supply a fact are skipped |
| Learning from new shapes | `recipe/trace.py`, `recipe/plan.py` (`PLAN_VERSION` 4) | logins, constants from the instructions, optional pop-up clicks, Load more as paging, ARIA and combobox controls, rich text, the record's own page (`own`), route clicks that change something (`saves`), a plan with no form |
| Running them | `recipe/plan_exec.py` | all of the above wired into `run_plan`; `program_mode` (`off`/`shadow`/`use`), `skip`, new `PlanOutcome` fields (`decided_by`, `program_check`, `example`, `used_pages`, `slot_checks`, `slot_labels`, `readback`, `record_url`, `unverified`) |
| Learning as records finish | `orchestrator.py` | coverage on a new plan, verified examples, learning and trusting programs, slots by code, read-back verdict, pruning, proactive extension, a batch-end note |
| A fifth test site | `testsites/claims-desk` (port 5183) | the **Claims Desk**: every hazard above, on purpose (section 5) |

Also: the live agent no longer runs browser-use's "judge" (one more model call per live record, with screenshots, whose verdict
nothing read), and a wizard's dry run no longer presses Next (a Next may save a draft).

How each piece works is in doc 06. Here: how each is checked before it is trusted.

## 3. How each piece is checked before it is trusted

| Piece | Before it is used at all | Before it is used **alone** (no model) | When it goes wrong |
|---|---|---|---|
| **Decision program** | written from 2 to 6 verified examples (live-agent-checked proof records, saved plan records); every key must be a form field; no fixed number the instructions do not print; it must give back **every example's values exactly** (one retry, else no program) | per kind of record (`p<rule>: <choices>`): **2 agreements** with the model's decision on live records, **no disagreement**; an agreement counts only if the save stood (read-back did not doubt it) or the live agent agreed | one disagreement: that kind is never decided by the program; 2: the program is dropped (relearned only from more examples); every **10th** trusted use is decided by the model too and compared; a program whose numbers this record's pages do not show hands the record to the model; a fact not on the pages stops the program (never skips a rule) |
| **Model's answer** (each decision) | grounding: numbers, dates, evidence quotes on the pages; a rule decided as plain true or false must quote a page | (always checked) | "the pages do not show what this answer uses": the live agent does the record and its route extends the plan |
| **Slot read by code** | the label it is printed next to, learned from a model reading, with the value's shape | **3 agreements** with the model's reading | one disagreement deletes the code; every 10th reading goes to the model again and is compared; a reading of the wrong shape, or that is a label, is not used |
| **Read-back verdict** (does the site show saved values?) | learned on proof records: the live agent's saved values looked for on the record's page in a new tab; **2 checks that agree** decide it; values never shown even for the live agent are "hidden" | (no model involved) | a plan record whose page lacks a value read-back has seen confirmed before **fails** (saved, so never retried; not an example); 3 in a row drop the plan |
| **Coverage additions** | each answer checked against the pages: the page is on the route, the link text is on it, a per-record link sits next to its label; at most 4 | the plan is proven again as usual (the extended plan is untrusted) | an answer that does not hold up is dropped; a failed question leaves the route as it was |
| **Pruning** | only side trips (a click followed straight by the step back) | a page type that supplied **no fact in 12+ decisions** | a record refused for missing pages restarts the counts and runs the whole route once more, before any live fallback |
| **Login replay** | a plan only keeps a sign-in whose password it can type again (from the instructions or a record field) | | at most 2 sign-ins per record; a route click that changes something is never pressed again after a re-login |
| **Pacing** | | | a 429 still cools the key 45 s and switches, as before |

## 4. Model calls per record, in each path

"Calls" are model calls (one live-agent step is one call). Numbers marked *live* were measured in earlier live runs; *fake* were
counted in this round's tests with a fake model; the rest is what the code does, not a measurement.

| Path | Model calls | Note |
|---|---|---|
| Live agent (teaching record, fallback) | about 8 to 25 agent steps | *live*, 9 Oct (Expense 8 to 19, Returns about 25). The judge's extra call is now off |
| Proof record | live agent + the plan's dry run (alone, not shared: 1 slot call unless code reads the slots, then 2 voting passes, 3 on a dispute) + the coverage question (1, once per new or extended plan) + learning a program when due (1, or 2 with the retry) | read-back checks cost 0 |
| Trusted plan record, the **model** decides (the program is not trusted for this kind yet, there is no program, or a spot check) | shared per group of up to 5: 1 slot call (0 once code reads the slots) + 2 voting passes (3 on a dispute) | *live* on AP Desk, 8 Oct: 0.62 per record. *fake*, Claims Desk: one record alone = 3 (1 slot + 2 passes) |
| Trusted plan record, the **program** decides | **0** (the slots read by code too) | *fake*: hand-written programs decided 8 kinds of return and 6 Expense claims (every outcome, through the wizard) with 0 calls, and the sites saved the right values |
| A kind of record the plan has not been checked on | the plan's question (shared) + the live agent | as on 9 Oct |
| A record the grounding check refuses | the plan's question + the live agent | the live route may extend the plan |
| A widget the plan cannot set by itself | a few scoped agent steps | as before; counted in the record's calls since 10 Oct |
| Read-back, pruning, slots by code, the program itself | 0 | |

So a long run of a repetitive task should end with most records at **0 calls**: after 4 verified examples a program is learned
(1 or 2 calls), and each kind of record needs 2 agreeing model decisions before the program decides it alone, then 1 model decision
in every 10 for spot checks. That is the design; it has **not** been measured live.

## 5. The fifth site: the Claims Desk (`testsites/claims-desk`, port 5183)

An insurance claims desk built to have the hazards of real web apps (its README has the full table "what is hard on purpose"):

- a **sign-in** (test account `desk.auditor` / `audit-2026`, local test values only) whose session runs out after 25 page loads or 10
  minutes, sending the browser to `/login?expired=1&next=...`;
- a **cookie banner** on every new browser and a **"What's new"** modal once per session;
- a claims list that **loads as you scroll** (20 rows at a time, "Load more" as a fallback, no search box, clickable `<tr>` rows), and
  about **1 load in 15 fails** with a Retry box;
- slow answers (300 to 1,500 ms) with "Loading…";
- the claim page links to its **policy** (a link whose text is an id) with a coverage table;
- the decision form is **inside an iframe**: an **ARIA radio group** (Approve / Approve partially / Decline / Request information), a
  payout number, two **searchable comboboxes** whose options appear in a portal (typing alone chooses nothing), a **contenteditable**
  note; saving is a `fetch` with a toast; control ids are random on every draw.

100 claims (32 Approve, 22 Approve partially, 20 Decline "No cover on loss date", 16 Decline "Peril not covered", 10 Request
information "Police report") with edge cases (a loss on the policy's first or last day, an amount exactly at the limit, a lapsed policy).
`scripts/generate_seed.py` computes `expected.json` by rules independent of the scenario labels; `grade.py [N]` grades;
`scripts/smoke.py` drives the site with Playwright (no agent) through five outcomes.

No v3 run has used it yet (no workflow for it has been run), and the live agent has never been run on it. The plan engine has been
tested on it with a **scripted** trace (`tests/make_hazards_fixture.py` drives the site the way the live agent would and records the
trace in the recorder's format: `tests/fixtures/hazards_trace.json`) and a fake model.

## 6. The tests

All pass on the night of 10 to 11 Oct 2026 with the four test sites running (AP 5180, Returns 5181, Expense 5182, Claims 5183). None calls the real model.

New files:

| File | Checks | What it covers | Needs |
|---|---|---|---|
| `tests/test_program.py` | 108 | facts off realistic page texts (dl, `Label: value`, tab tables, the headings block); the expression language (allowed and refused); two hand-written programs (library late fees; a rate looked up by tier and grade with the column picked by a fact); catching a wrong rule; learning with a fake model (good; bad then fixed on the retry; bad twice); trust bookkeeping | nothing |
| `tests/test_evidence.py` | 85 | the 9 Oct guess (`4*620*(100-15)/100` with no 15 on the pages) refused, in several shapes (a pager's 5 and 10 nearby, a note, a date-shaped formula); the honest version with the fee on the policy page passes; one-step arithmetic; dates; quotes across line breaks; ids are not numbers | nothing |
| `tests/test_pacing.py` | 45 | the window on a fake clock; `GEMINI_RPM` (14 when unset, 0 = off, bad values); two keys sharing; a 40-call burst at 14 a minute takes 2+ minutes; cool-downs and daily marks still honoured; 8 threads booking 400 calls get exactly 14; `ask_json`, the browser agent and "write instructions" stay at 14 a minute; the judge is off | nothing |
| `tests/test_webkit.py` | 99 | in a headless browser on pages it writes itself: an iframe form, ARIA radios and checkboxes, a combobox with a portal, a rich-text note, an endless list and a "Load more" list, cookie banners and a "What's new" box (and a Submit inside a modal that is **not** pressed), a Retry box, login pages | Playwright |
| `tests/test_readback.py` | 83 | numbers, dates, choice stems, short texts; in a headless browser on pages served by the test itself: a page that shows the decision, one that only shows the form, a hash route, a screen that showed a decision it never saved, a missing record | Playwright |
| `tests/test_coverage.py` | 80 | side trips added to a plan compiled from the Returns trace, `revisit` steps renumbered, bad answers dropped, pruning counts; in a browser on a small site served by the test: a record that cannot be decided before coverage and can after | Playwright (`--offline` skips it) |
| `tests/test_orchestration.py` | 72 | the orchestrator with every outside piece faked: instructions reach the compiler, coverage on a new plan, when a program is learned (and one record at a time), trust and dropping, spot checks, slots by code, read-back failures, pruning with the retry, proactive extension, the batch-end note | nothing |
| `tests/test_plan_hazards.py` | 58 | the Claims Desk: compiling the scripted trace (login, optional clicks, the row found by growing the list, the iframe controls); made-up pages in memory (iframe forms, read-back cases, saving route clicks, dialogs, a wizard's dry run); then the real site with a fake model: a fresh browser signs in, gets past the banner, the pop-up and a failed load, scrolls to a deep row and decides in the iframe; every outcome saved; the session running out mid-run; read-back confirming a save and flagging a wrong one; a skipped side trip | the Claims Desk on 5183 for part 2 (`--offline` skips it; it **resets the site**) |

Older files, grown this round: `tests/test_recipe.py` 40 (unchanged), `tests/test_plan.py` 62 (was 54),
`tests/test_plan_returns.py` 72 (was 52: grounding, slots by code, a program deciding every kind of return with 0 calls),
`tests/test_plan_expense.py` 88 (was 42: a program through the wizard with 0 calls, shadow mode in a dry run).

In all: **892 checks** in these 12 files (all passed; the 4 files that use a test site took 40 s to 4.5 minutes each, test_webkit.py 46 s, the rest
under 10 s), against 188 in the 4 files before this round. `tests/test_backend.py` (API and database) was not run: it needs a local
PostgreSQL in `TEST_DATABASE_URL`.

## 7. What is still open

- **Nothing of this round has run live.** Not the programs, not grounding, not pacing, not read-back, not coverage, not the Claims
  Desk. The first step is a small live batch (8 to 16 records) on each site, starting with one the engine already knew (Returns or
  Expense), then the Claims Desk.
- **A program written by the real model has never been tried.** The site tests used hand-written programs; `learn_program` was only
  tested with a fake model. The prompt (`program_prompt`) may need the same kind of tuning the decision prompt needed (doc 09).
- **The Claims Desk has never had a live run** and the live agent has never seen it: whether the real agent's trace there compiles (an
  iframe, ARIA clicks, a combobox picked by typing) is unknown. Doc 11's lesson 1 (a scripted trace is tidier than the agent) applies.
- **Grounding may refuse right answers.** A number written out (not as a `{= formula }`) that is two steps of arithmetic from the
  printed numbers, or a rate that is not printed as a number, is refused, and the live agent does the record. Too many such refusals would cost quota; it has not been measured.
- **Read-back depends on the site.** A site that never shows a saved decision on the record's page gets no read-back (that is learned
  on proof records, after 2 agreeing checks); one that shows it in very different words may be reported as unchecked.
- **Pacing waits.** With two keys about 28 calls a minute go out; more browsers than that just wait. The real per-minute behaviour of
  Gemini under pacing is unmeasured.
- **Trust needs repetition.** Each kind of record needs 2 agreeing model decisions before the program decides it alone, so rare
  kinds keep costing model calls. That is deliberate.
- **Coverage is one model question** whose answers are checked, but a page the instructions name in words the model cannot tie to a
  link on the route stays unread (the old path then applies: the record goes live once and extends the plan).
- **Not tested at all**: a second sign-in factor or a CAPTCHA (the engine never tries them), shadow DOM, frames from another site,
  pop-ups whose buttons are named otherwise than the `DISMISS` words.
- The open items of doc 11 (100-record runs of the Returns and Expense desks) are still open.

## 8. How to check this yourself

```bash
cd ~/Downloads/terminautomator/v3
venv/bin/python tests/test_program.py        # no site, no browser
venv/bin/python tests/test_evidence.py
venv/bin/python tests/test_pacing.py
venv/bin/python tests/test_orchestration.py
venv/bin/python tests/test_webkit.py         # headless browser, pages written by the test
venv/bin/python tests/test_readback.py
venv/bin/python tests/test_coverage.py
venv/bin/python tests/test_plan_hazards.py   # part 2 needs: cd testsites/claims-desk && node server.js (it resets the site)
```

The older files need their sites too (doc 10). Never run a part 2 while a live run uses that site.
