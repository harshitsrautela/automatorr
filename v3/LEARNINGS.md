# v3 LEARNINGS: the bugs we fixed and how we reached 100% accuracy

This file is the record of the v3 test-and-fix rounds on the Credit Desk site (decide 100 credit requests by reading a
ledger, then click Approve / Reject / Hold). It lists **what was wrong, why, and what fixed it**, and the **order in which
accuracy went from 80% to 100%**. The older v2 learnings stay in `v2/docs/LEARNINGS.md`; this file only covers v3.

Related files: `AGENT_SUMMARY.md` (the earlier round of experiments and the bugs that are still open),
`FUTURE_PLANS.md` (what is not built yet), `../V3_PLAN.md` (the original design).

---

## 1. The result

| | Before the recipe | After |
|---|---|---|
| Records | 36 (later 100) | 100 |
| AI calls | about 9 per record (about 900 for 100 records) | about 7 (learning) + about 20 lookups + about 40 decision votes |
| Records that needed no AI call of their own | 0 | 98 of 100 |
| Time | 21 min for 36 records | about 7 min for 100 records, 3 browsers |
| Accuracy (checked against the site's own answer view) | 97.2% (35 of 36) | **100 of 100** in the last two full runs (#37, #38) |

Honest caveat: 100% was reached on one site and one data set (two runs in a row). The three runs before it were 99 of
100, each with a different single miss. The fixes below close every miss we saw; they are not proof for other sites.

---

## 2. How accuracy moved, step by step

All runs use `gemini-3.5-flash-lite` (a small, cheap model) unless noted. "Offline" means the decision step was scored on
all 100 requests without a browser (section 6), which is how we iterated quickly.

| Stage | What changed | Result |
|---|---|---|
| AI decides every row (v3 start) | the live browser agent reads, decides and clicks, about 9 steps per row | 35/36 (97.2%); one false success |
| Recipe v1, 10 rows | learn the pages and the final button from the AI; one model call per row | 8/10 (the model chose before it reasoned) |
| + reason first, then choose | answer fields ordered request, found, arithmetic, rule, button | 10/10 |
| First 100 rows on fresh data | batch of 5 per call | **92/100**: 6 near-miss/wrong-city matches, 2 boundary sums |
| + stricter prompt, exact-text lookup | a first call finds each record's identifying details; code searches the pages for them as exact text and hands the result to the decision call | 96 to 99 offline, **96/100 live** |
| Run #31 | same code | **95/100**: 3 boundary slips and 2 lost saves |
| + formulas | the model writes each rule as a formula; code evaluates them in the user's order | **100/100 offline** |
| + button from the user's own rule line | the model sometimes attached the wrong button to a correct rule | 99/100 live three times (#34, #35, #36) |
| + voting | decide twice, a third time on disagreement | **100/100 offline, then 100/100 live (#37, #38)** |

The ladder to 100% was always the same move: find the exact kind of mistake, then take that job away from the model.

---

## 3. What the recipe is (so the bugs make sense)

The old cache replayed selectors for form filling. The Credit Desk cannot use that: what gets typed or clicked depends on
what the agent reads, not on the row's data. The recipe splits the job into **where** (cached) and **what** (decided):

1. The AI does the first record (6 steps). Its actions are recorded as a trace.
2. The trace is compiled: which pages it read, and which button it finally clicked. The button's label contains the
   record's value ("Approve request 7"), so it becomes a template ("Approve request {id}"); the other buttons of the
   row are found on the page at run time.
3. Every later record opens those pages, gets its decision, and clicks. Decisions are made 5 records per model call.
4. If a recipe step fails before the click, that record falls back to the full AI. After a click it never retries
   (that could act twice).

Files: `recipe/trace.py` (records the AI's actions), `recipe/compile.py` (trace to recipe), `recipe/execute.py`
(runs it, batches decisions, votes), `recipe/formula.py` (safe formula evaluator), `recipe/llm.py` (model calls),
`recipe/network.py` (watches saves), `keypool.py` (several API keys), `orchestrator.py` (decides live / replay / recipe).

---

## 4. The bugs we fixed

### 4.1 Wrong answers (accuracy)

| # | Symptom | Cause | Fix |
|---|---|---|---|
| A1 | "Not in the ledger" requests were approved with another retailer's numbers (6 of 8 misses in the first 100-row run) | the model matched near-miss names (`KulkarniFootwear` for `Kulkarni Footwear`) and the right name in the wrong city | **lookup step**: the model lists each record's identifying details, code searches the pages for them as exact text, and the decision call is told "no line contains all of ..." |
| A2 | Hold cases at exactly 10% over, or over by 1, came out as Reject | small-model arithmetic: it wrote "65000 <= 65000: false" and "over by 1 is more than 65000" | **formulas**: the model writes each rule as a formula (`935000 - 850000 <= 850000 * 0.10`); code evaluates it with exact decimals, never the model |
| A3 | Overdue requests approved although the model wrote "53 > 30: true" | the model ignored its own check | the first rule (in the user's order) whose formula is true decides, in code |
| A4 | Right rule, wrong button (record 20 and 83: "not in the ledger" followed by Approve) | the model attached the wrong button to a correct rule | the button comes from **the user's own rule line** ("Retailer is not in the ledger: Hold"); matched by Jaccard similarity so a long sentence that merely contains the words cannot win |
| A5 | The first fix for A4 did not work live | it picked the line with most word overlap, and a long earlier sentence tied | switched to Jaccard (overlap divided by union) and tested every rule of the real prompt with a deliberately wrong button |
| A6 | A random miss each run even with temperature 0 (92, 95, 96, 99, 99, 99) | model non-determinism | **voting**: two independent passes (second one in reverse order with a little randomness), a third pass only for records they disagree on |
| A7 | One record decided as Approve although the model wrote the Hold reasoning | the model chose the button before writing the reasoning (JSON key order) | answer fields are ordered reasoning first, button last |
| A8 | A fixed 1-in-3 failure: the model answered only the first record of 5 | it dropped the list to a flat object | **response schema**: Gemini must answer in exactly the shape (a list with one entry per record) |
| A9 | Blank reasoning fields | the schema allowed empty strings | an answer with no written reasoning is rejected and asked for again |
| A10 | The 10-row recipe was 80% | the first prompt put the answer before the reasoning | same as A7 |
| A11 | **The duplicated-schema bug**: several prompt experiments looked like they did nothing | the answer-format class was defined twice in `recipe/execute.py`; the older copy (with the old field names) silently won, so the model never had to write the new fields | removed the stale copy; **lesson: when an experiment shows no effect, check the code actually ran the new version** |

### 4.2 Lost or fake successes

| # | Symptom | Cause | Fix |
|---|---|---|---|
| B1 | 2 of 100 records said success but nothing was saved (#37, #56) | the browser closed 0.4 s after the click, before the save request finished; tasks made in the UI had no "save check" configured | **change tracker** (`recipe/network.py`): watches every POST/PUT/PATCH/DELETE in the browser context, waits for them to finish before closing, and fails a record whose last save was refused; works on any site with no setup |
| B2 | A 30 s wait and a warning "no matching submit request observed" | an old wrapper waited for a configured URL | removed; the tracker replaces it (the "submit check" setting is now unused) |
| B3 | A record failed because the browser was sent to a Google search for "e" | a stray keystroke into a visible Chrome window | the start-page load is retried once; do not type in the windows during a run |
| B4 | The saved task lost its save-request check when saved from the page | the page sent empty values for fields it does not show, and the server overwrote the stored ones | model, key and submit settings are optional in the API; a save without them keeps the stored values |

### 4.3 The recipe engine

| # | Symptom | Cause | Fix |
|---|---|---|---|
| C1 | The recipe needed two AI records to learn | it compared two traces to find what varies | learns from one record: the other buttons in the row are found on the page at run time |
| C2 | A click chosen by page position could hit the wrong row | xpath is the same for different rows | an xpath is never trusted for the record's own button |
| C3 | "Button not found" right after the page opened | the list was still loading ("Loading...") when the buttons were queried | always wait for the page's data, also when already on it |
| C4 | Rows on later pages were unreachable | the list shows 10 rows per page | page through with the "Next" control; read all pages once and share the text; derive other records' button labels from the first record's templates |
| C5 | After reading all pages, the page stayed on page 10 | the list is a single-page app, the address does not change | reopen the list at its first page before finding buttons |
| C6 | Decisions were slow (each batch waited for the last) | one lock around every model call | the first record decides its own batch; the rest are decided in the background, 3 calls at a time; waiting records are released as their chunk finishes |
| C7 | Models answered "high demand" (503) | free tier overload | longer retry with backoff (up to 6), and a fallback model |

### 4.4 Quota and keys

| # | Symptom | Cause | Fix |
|---|---|---|---|
| D1 | A run stopped with "daily quota exhausted" | 500 requests a day per model per key | **key pool** (`keypool.py`): two keys, handed out in turn; a key that is rate-limited is skipped for 45 s; a key out for the day is set aside until the next daily reset (first coded as 00:00 UTC, **corrected on 8 Oct to midnight Pacific**, see F9) |
| D2 | One model's quota gone | the free tier counts per model | `gemini-3.5-flash-lite` first; `gemini-3.1-flash-lite` only after every key is out for 3.5 |
| D3 | Keys on the page | the page asked for a Gemini key and model | removed; keys live in the server's `.env`; extra keys are `GEMINI_API_KEY_2`, `_3`, ... |

### 4.5 Platform and page

| # | Symptom | Cause | Fix |
|---|---|---|---|
| E1 | A task delete returned an error once | Supabase's pooler drops idle connections | the pool tests a connection before use and drops old ones |
| E2 | Leaving the Agent page lost unsaved edits | the sidebar bypassed the page's check | a navigation guard asks before leaving |
| E3 | Task list and key boxes looked wrong | old layout | tasks are a submenu under Agent; the whole site is dark; Settings page, key box and model box removed; Supabase URL and key are two boxes on the Agent page (pre-filled for local testing only) |
| E4 | Task names were the first sentence of the prompt | simple cut | name is the website plus a few words, for example `localhost:5174 · credit desk` |
| E5 | A record showed "live" at the start and "cold" at the end | the first record seeds the cache | the label follows what actually happened |
| E6 | Python's URL library failed on HTTPS | no certificates in the environment | use `httpx` for the test scripts |

---

## 5. Why 100% needed several layers

Each layer removes one source of error, and none is enough alone:

1. **Exact-text lookup** removes "similar" retailers (A1).
2. **Formulas** remove arithmetic, equality and rule-order errors (A2, A3).
3. **The user's own rule line** removes wrong buttons on right rules (A4).
4. **Strict answer shape, reasoning first, no blank reasoning** removes lazy or truncated answers (A7 to A9).
5. **Voting** removes the one-off slips that remain (A6).
6. **The change tracker** removes saves that never reached the server (B1).
7. **The database check** (the site's answer view) is what proves it. Without it, A6 and B1 look like success.

---

## 6. How we tested (worth repeating on any new site)

- **Engineered data.** 100 requests built to hit every rule and its edge: exactly at the limit, one over, exactly 110%,
  one past 110%, days equal to the term, overdue, blocked, and "not in the ledger" three ways (near-miss name, right name
  in the wrong city, brand-new name). The site's own SQL view gave 44 approve, 30 hold, 26 reject, and each scenario
  type came out as designed, which also checks the test data.
- **Fresh data each round.** A new seed and new names, so the fixes could not be tuned to one data set.
- **An offline scorer.** It reads the pages once with a headless browser and asks for all 100 decisions in batches of 5,
  then compares with the answer view. It runs in about 2 to 4 minutes without clicking, so a prompt change can be judged
  with 20 calls instead of a 7-minute run.
- **Repeat before believing.** A prompt that scored 99 once scored 96 and 88 on other runs. Compare repeated scores and
  look at which records fail, not only the total.
- **Read the model's own words for every miss.** The log keeps each record's reasoning. Every fix above started from
  "what did it write for record N".
- **Isolate the cause.** One record per call scored 87%, worse than batches of 5 (96 to 97%), which ruled out batching as
  the cause and pointed to the model's weakness with long pages.

---

## 7. What we learned

1. **Do not ask a small model to do arithmetic or ordering.** Let it read, match and translate rules into formulas; let
   code compute and decide the order.
2. **"Similar" is the dangerous word.** Near-miss matching is where the model hallucinated. A character-for-character
   search gives it a fact to follow.
3. **Use the user's own words.** The button for a rule is whatever the user wrote after the colon.
4. **A cache of where, not what.** Locators repeat across rows; the answers do not. Cache the first, decide the second as
   cheaply as possible (batches, votes only where they pay).
5. **A "success" is a claim.** Check what actually reached the server (the tracker) and what the database shows.
6. **Randomness hides bugs.** Run the check several times, and make failures reproducible (offline scorer, saved data).
7. **Silent failures.** A duplicated class, blank fields and an unconfigured check each made things look fine.
8. **Quota is a design input.** Two keys, a per-model fallback and honest messages kept long runs going.
9. **Free-tier models get busy.** Retries with backoff matter as much as the prompt.

---

## 8. Cost and speed (100 records, 3 browsers)

- Learning record: about 6 AI steps.
- Lookups: about 20 calls (one per batch of 5). Decisions: about 40 calls (two votes per batch) plus a few tie-breaks.
- 98 of 100 records make no AI call of their own; each only opens the page, finds its button and clicks.
- About 7 minutes in the last runs: 150 s before the first decision, about 14 records a minute with 2 browsers, and a slow
  tail for records on pages 8 to 10 of the list.

---

## 9. Still open

- Only proven on the Credit Desk. The recipe assumes a button whose label contains the record's value, sibling buttons
  that differ by one word, "Next" paging and plain page text. A second, differently built site is the real test.
- Speed: jump straight to a record's page of the list; start deciding while record 1 is still learning; do the lookup
  once for all records.
- Daily quota: about 66 calls per 100 records, so a day of testing uses a key up. A stronger model for the decision step
  would help accuracy but the larger models were often overloaded.
- The bugs from `AGENT_SUMMARY.md` that are not fixed: verification settings are not validated at save time, a cache
  poisoned by a failed first record, the screenshot retry doubling load, data-field prompt injection, no login, and the
  hidden `DEV_PREFILL_SECRETS` switch that must be removed before sharing.
- The frontend still starts new tasks from the Credit Desk example; move it to an Examples menu.

---

## 10. Round 2: the AP Desk (a second, differently built site), 8 Oct 2026

The Credit Desk fix only proved the recipe on a site we had tuned it to. A held-out site (`testsites/ap-desk`: server
rendered, no row buttons, a three-way match, a conditional form with computed values and a confirm dialog) was built to break
it. The short story is in `docs_old/06_THE_PLAN_ENGINE.md`; the bugs it exposed and what fixed them:

| # | Symptom | Cause | Fix |
|---|---|---|---|
| F1 | **1 of 10 correct**, nine records "replayed" the first record's page | the v2 replay gate ("any step tied to a data field") was fooled by one mis-tagged click; replay locators and fallback instructions named record 1's items; computed values were never cached | the replay verdict judges the first record's full trace: unsafe when a value was read or worked out, or a click names record 1's item; unsafe clicks are deleted; `_already_learned` replaces "has cached steps" as the "seeded" marker |
| F2 | No fast path fitted the task (live agent: about 15 steps, 60 s per record) | the recipe needs a button labelled with the record's value | the **plan engine**: every value is a field, a slot (read off a page) or computed; replay the route; decide the form's values with the model while code does the arithmetic |
| F3 | A plan could be wrong without any error | one trace cannot tell fixed labels from record data | **proof**: dry-run the plan before the live agent on the next 2 to 3 records and compare; thrown away on different values |
| F4 | The first plan failed on record 2: a vendor link's text was stored as a fixed label | the agent clicked the vendor name without typing it, so there was no sign it was data | during proof only, a fixed label not found for a record becomes a slot (the earlier proof is wiped); trusted plans are never edited on the fly |
| F5 | The decision step failed on `abs(440 - 450) > ...` | the evaluator had no `abs` | `abs`, `min`, `max` allowed (nothing else callable) |
| F6 | `590 > {=469+121}` unreadable | the model used the value helper inside a rule | `{= }` inside a formula is read as a bracket |
| F7 | A rule's `values` came back empty, so the pass failed | the prompt said to list only the fields a rule needs | values are required for every rule, even ones that turn out false |
| F8 | A dry run that only errored threw the plan away | the cost of dropping a good plan (every record goes live) is far above one more proof record | only different values reject at once; up to 3 inconclusive dry runs are tolerated |
| F9 | Key marks "out for today" survived Google's real reset | the pool keeps them in memory for up to 8 hours and assumes 00:00 UTC | **fixed**: the reset is computed as the next midnight in `America/Los_Angeles` (DST-aware, tested including the 25-hour day) plus 60 s; the 8-hour cap stays as a safety net |

The offline scorer (`tests/score_plan.py`) went 7/10 to 10/10 between F5 to F7 using about 30 model calls each, with no live
agent, the same technique as section 6.

---

## 11. Round 3: speed, the quota fix, and the Returns Desk (a third, harder site), 8 Oct 2026

| # | Symptom | Cause | Fix |
|---|---|---|---|
| G1 | A plan record took about 18 s and 3 model calls | page reads waited for the network to go quiet (at least 0.5 s per page), and every record asked its own questions | `_quick_read` (stable read, about 0.15 s) and **shared model calls**: `recipe/batcher.py` answers up to 5 records' questions with one call; 100 of 100 correct at 0.62 calls per record |
| G2 | 5 browsers sharing calls in groups of 5 ran in lockstep (524 s for 100 records) | every browser waited for the same call | use more browsers than the group size: 10 browsers took 320 s |
| G3 | Refund amounts came back as `'{6400}'` | my batch rewrite of the prompt escaped the `{= formula }` example twice, so the model was told to write `{{= ... }}` | the prompt example fixed; doubled braces are also read as one (`render_inline`) |
| G4 | `max(1,2)` was unreadable | the thousands-separator cleanup (`6,50,000`) turned the comma in `max(1,2)` into `12` | commas are only stripped when the formula contains no `abs/min/max` call |
| G5 | The key pool kept a key "out for today" past Google's real reset | it assumed 00:00 UTC | `_seconds_to_quota_reset()`: next midnight in `America/Los_Angeles` (DST-aware) plus 60 s |
| G6 | No plan could be learned when the agent clicked an order or item link | a link whose text is an id has no stable locator, so the compiler gave up | such a link's text is a **slot** read off the page; the `href` is templated too |
| G7 | A list with no search box could not be replayed | the agent's page-number clicks are fixed clicks, and the next record is on another page | pager clicks are dropped from the route; the record's link is found by **paging at run time** |
| G8 | Clicks that set a calendar made the compiler refuse the form | it expected the form's fields to be typed or chosen directly | a run of clicks that fills a field is a **widget**: the model decides its value; a calendar is then **driven by code** (`_set_calendar`: open it, page to the month, click the day), anything else by a **scoped live-agent step** (never saves) |
| G9 | The fee row of a 19,600-character policy page was past the 3,500-character cut | a flat cut keeps only the top of a page | table rows are also given with their headings; a long page keeps its first lines plus the lines that mention something the record's other pages mention |
| G10 | A plan learned from a short branch could answer a long-branch record by guessing | the first record's route has only its own pages | the model must report `missing` when the pages lack what the rules need (an error, so the live agent does the record); a record that falls back to the live agent over a longer route **extends** the plan, which must be proven again |

Lessons: batching works because the per-record question is the same shape for everyone, but the prompt is where small mistakes
hide (G3); a held-out site is what finds the shapes you did not think of (G6 to G10); and a scripted pseudo-agent that records
traces in the recorder's own format lets the compiler and executor be developed when the live model is unavailable.

## 12. Round 4: real live traces, and the Expense Desk (a fourth site, a single-page app), 9 Oct 2026

The first day with model quota after the Returns Desk was built: the live agent ran it from an empty cache, and a fourth site was
built to test generality (`testsites/expense-desk`). The full story is `docs_old/11_LEARNINGS_9_OCT.md`.

| # | Symptom | Cause | Fix |
|---|---|---|---|
| H1 | No plan from the first real Returns trace ("an element has no stable way to find it again") | the agent clicked the `<td>` around the RMA link: no id, role or name | a plain element is found by its **exact visible text** (with blanks, `{"text": "{rma}"}`) |
| H2 | When the first trace could not be compiled nothing else was tried, and the reason was not logged | only `traces[0]` was compiled | every saved trace is tried (newest first); each failure is logged |
| H3 | The plan replayed record 1's order page for every record (dry runs "landed on /orders/* instead of /items/*") | a typed address (`navigate`) had its target overwritten by the page it was typed on | the target is stored separately; a plain bug |
| H4 | Guessed addresses (`/items/SKU-5621`: "Page not found") were in the route | the agent tried addresses; nothing marked them as dead ends | a step whose landing page is short and says "not found" / 404 is dropped |
| H5 | A typed `/returns?page=7` would send every record to page 7 | typed list-page addresses were not seen as paging | a typed address with a page parameter is paging, like a "Next" click |
| H6 | Each plan record paged through the list twice | the agent went back to the list and found the return again; the plan replayed that | **detours are cut**: a return to the exact address reached earlier, through pages already seen, becomes one `revisit` step (it reopens the address this record's own run reached) |
| H7 | My first detour cut ate the "Review claim" click | clicks that stay on the same address (a tab, a button opening a modal) counted as "returning" | only a step that moves to another address can start a cut |
| H8 | Two refunds decided without the item page (final sale, warranty) were right only by luck | the free-text `missing` report did not catch it | per rule, the model says whether the pages show what it needs (`known`); an unknown rule before the one that applies sends the record to the live agent |
| H9 | Every page of the single-page app had the same page type `/` | the URL's fragment (`#/claims/...`) was ignored | the page type includes a fragment route |
| H10 | `/#/claims/e3lcxu` was expected literally for every record | random ids without 2+ digits did not look like ids | the page reached by clicking the record's own link treats its last part as the record's id; later steps landing on that address expect the same; `*` matches one part; mixed ids and UUIDs are recognised |
| H11 | The Receipts tab was "not found" in a dry run (and the plan tried to "heal" it) | `<button role="tab">` was located as a *button* | an element's own role attribute wins over its tag |
| H12 | A three-step wizard compiled into nonsense: the radio choice replayed as a fixed click, "Next" became a calendar-like widget, fields keyed by random ids (`f50324`) | the engine knew only one-page forms of text fields and selects | radio buttons and checkboxes are controls (groups by name); Next/Continue end a **step**; controls are keyed by **name** first; the plan reads, decides, fills and moves on step by step; fields of later steps the plan knows are asked in the first question; a dry run walks the steps and stops before saving |
| H13 | A "with deduction" record needed a second question on step 2 | the plan only knew the fields record 1's branch filled | an agreeing proof record (or a live fallback) teaches the plan the fields its route filled (`learn_fields`) |
| H14 | The exchange-rate section was only read if the first record opened it | collapsed `<details>` text is not on the page | every page read opens all `<details>` first |
| H15 | An aborted run kept running its queued records live | records waiting for the learning lock never looked at the abort flag | the abort is checked again once a record has the lock |
| H16 | Two decisions of a live run were wiped | `test_plan_returns.py` part 2 resets the site, and it was run while a run was using the site | every part 2 takes `--offline`; the docs say never run one during a run |
| H17 | Run #59's first trace could not be compiled again | the agent clicked the table **row** (`<tr>`: no name, only random attributes); a proof record had also clicked a `<details>` box | a nameless element is found as the one of its kind that **contains the record's value** (from its inner text, now recorded, or the agent's goal for the step); section clicks are dropped; a nameless click that did not change the page is skipped |
| H18 | The row locator found nothing in the browser | Python's `re.escape` writes `\-`, which the browser's regular expressions reject; and a row's text runs its cells together ("EXP-10202Krish"), so a word boundary after the number never matches | escape only what browser regexes need; match a part whose whole text is the value, or the value not followed by another digit |
| H19 | Run #61: a good plan was dropped and 11 records ran live (427 calls) | records whose pages lacked what a rule needs refused correctly, but each refusal counted toward "the plan failed 3 records in a row" | a refusal for missing pages is not a plan failure; a proof record whose dry run refused extends the plan from the live route |
| H20 | Run #61: a trusted plan guessed a change-of-mind refund's 15% restocking fee (right: 20%) without the policy page | it was proven only on "wrong item" returns, and the model's per-rule `known` answer said it could see what it needed | **trust per kind of record**: a trusted plan decides alone only kinds (rule + choices) that agreed with the live agent; an unchecked kind states its answer, the live agent does the record, and they are compared |
| H21 | Run #62: a plan was thrown away although it was only missing a page | a proof record's disagreement always discarded the plan; here the plan had refunded without the restocking fee because it never read the policy table, which the live agent's route did | a proof disagreement whose live route read pages the plan does not extends the plan instead of discarding it |

Lessons: a scripted trace is tidier than the real agent, so the first real trace finds compiler bugs at once (H1 to H6); a site built
like a modern app breaks assumptions that server pages never test (H9 to H14); and an engine that only proves itself on what one
record did must also learn from what the *next* records did (H8, H13).


---

## 13. Round 5: cache the decision as code, check everything before trusting it, and a fifth site (9 to 10 Oct 2026)

**Nothing in this section has run live yet**: every fix is checked with fake models and hand-written or scripted inputs. The full
story, the tests and what is still open are in `docs_old/12_GENERAL_PURPOSE_ROUND.md`.

| # | Symptom | Cause | Fix |
|---|---|---|---|
| J1 | Run #61: a refund decided with a **guessed 15% fee** (right: 20%) | the model's answer was used without checking its numbers were on the pages | **grounding** (`recipe/evidence.py`): every number in the rules checked and in the values must be printed on the pages, in the record or in the instructions, or be one step of arithmetic from them; a rate must be printed itself; dates must be on the pages; an answer that fails goes to the live agent |
| J2 | The model said `known: true` while the page it needed was never read (twice on 9 Oct) | a model's report on itself is not evidence | the model must **quote** the page text each rule rests on; quotes are looked for on the pages, and a rule decided as plain true or false must have a quote that is there |
| J3 | Every trusted plan record still cost model calls (0.3 to 1.7 each) | only the route was cached, never the decision | **decision programs** (`recipe/program.py`, `recipe/facts.py`): the model writes facts + rules once from verified examples; code checks it gives back every example; it decides a kind of record alone after 2 agreements with the model (never after a disagreement), with a spot check every 10th use |
| J4 | Every plan record paid a call to copy values from pages | the slot call had no cheaper path | **slots by code**: the label a value is printed next to, used after 3 agreements with the model (re-checked every 10th reading; one disagreement deletes it) |
| J5 | "Success" meant the plan pressed Save | nobody looked at the saved record | **read-back** (`recipe/readback.py`): open the record's page again and look for the values; learned per site on proof records whether the site shows them; a plan record whose page lacks a value fails (never retried) |
| J6 | Each new branch cost a live record, and run #61 ran 11 records live | the plan only reads the pages its first record read | **coverage** (`recipe/coverage.py`): one checked model question per new or extended plan adds the side trips the instructions name; a proof record that agreed while its live route read new page types also extends the plan |
| J7 | Plan records replayed pages that no decision used | nothing counted what was used | **pruning**: a side trip whose page type supplied no fact in 12+ decisions is skipped; a refusal for missing pages restores it at once |
| J8 | "429 per minute" dozens of times with 8 browsers | the pool reacted to the limit instead of keeping under it | **pacing** in `keypool.py`: a sliding 60 s window per key and model, `GEMINI_RPM` (default 14, 0 = off); calls wait for room, never while holding a lock |
| J9 | A bad `GEMINI_RPM` line could stop the server from starting (found while testing pacing) | the value was read with a bare `int()` at import | a blank or bad value logs a warning and uses 14; a value between 0 and 1 means 1, never "off" |
| J10 | One more model call per live record that nothing used | browser-use's "judge" was on by default | `use_judge=False` |
| J11 | A wizard's dry run pressed Next, and a route button that changes something (Start review) could be pressed in a dry run or twice | Next and such buttons were treated like ordinary clicks | a dry run never presses Next; route clicks that may change something are marked `saves`: never in a dry run, once in a real run, never again after a re-login |
| J12 | No site had a sign-in, pop-ups, iframes, endless lists or custom widgets, so the engine had never met them | the four test sites were all friendly | the **Claims Desk** (`testsites/claims-desk`) was built with all of them; `recipe/webkit.py` and the compiler learned logins (replayed when a session runs out), optional dismissals, Retry, Load more and scrolling, frames, ARIA radios and checkboxes, comboboxes and rich text |
| J13 | Records running together would all have asked the model at a program's spot check | the spot check was booked only after the record finished | the record that takes the spot check claims it at once; a claim whose record failed is handed back |
| J14 | A read-back could have counted a value as saved because it was printed before saving, on a success screen, or in a form that lists every option | reading "the page" is not reading the record | the record's page is loaded afresh from the site; labels, buttons and form options are left out; a value already shown before saving does not count when the save changed nothing |
| J15 | Run #72 (Expense Desk): a record was lost after a wizard's Next (3 records on 11 Oct, 1 on 13 Oct) | the plan set `acted = True` before every Next, so a Next that the site rejected could not be handed to the live agent | `run_plan(..., mutations=)`: the engine counts the POST/PUT/PATCH/DELETE requests the browser sent (`MutationTracker.sent`) and sets `acted` only if the count rose across the Next; a rejected Next leaves the record free for the live agent (run #73: 16/16) |
| J16 | Decision programs were rejected again and again over a single checkbox | the model wrote the checkbox's own label as its value | for a single checkbox the value is `yes` (tick) or `no` (leave); a value equal to the label is read as `yes`, and the prompt says so (`recipe/program.py`, `normalise_value`) |

Lessons: a model's word about itself is not a check, but its quotes are (J1, J2); anything cached (a route, a value's label, a whole
decision) must earn trust per kind of case and keep being spot-checked (J3, J4); a save is a claim until the record shows it (J5); and
"tested" here means tested with fakes. Live runs followed (see `docs/03_WHAT_HAPPENED_FULL.md`): the safe parts held, but no decision program has been learned in a live run yet (J16 and the program-learning causes in `docs/problems.md`).
