# 00. Start here

Read these in order. Each one is short enough to finish in one sitting, and each assumes only the ones before it.

| # | File | What you get |
|---|---|---|
| 00 | `00_START_HERE.md` | This page: the one-paragraph picture, reading order, glossary |
| 01 | `01_WHAT_V3_ADDS.md` | v1 to v2 to v3 in one table, and the ideas that make v3 different |
| 02 | `02_ARCHITECTURE_AND_FILES.md` | Every folder and file, what it owns, how a click in the browser reaches the code |
| 03 | `03_DATABASE_AND_SECRETS.md` | The Postgres tables, what "cache" means now, how keys are encrypted |
| 04 | `04_LIFE_OF_A_RUN.md` | What happens between pressing Run and the final accuracy number |
| 05 | `05_THE_RECIPE_ENGINE.md` | Fast path 1: learn once, decide in batches, click one record-labelled button |
| 06 | `06_THE_PLAN_ENGINE.md` | Fast path 2, the general one: values from the record, from pages, or worked out; proven before it is trusted |
| 07 | `07_KEYS_QUOTA_AND_SAFETY_NETS.md` | Gemini key pool, quota, the change tracker, every way a run can stop |
| 08 | `08_VERIFICATION_AND_FRONTEND.md` | How a run is graded, and the two web pages |
| 09 | `09_RESULTS_AND_LESSONS.md` | Accuracy ladders, every test site, what we learned, what is still open |
| 10 | `10_RUNNING_AND_FRESH_TESTS.md` | How to start everything and how to do a truly fresh test |
| 11 | `11_LEARNINGS_9_OCT.md` | What the first real Returns Desk runs and the fourth site (Expense Desk) taught, and everything changed for it |
| 12 | `12_GENERAL_PURPOSE_ROUND.md` | The 10 Oct round: decisions as checked code (no model call), grounding, read-back, coverage, pacing, real-world pages, the fifth site; not run live yet |

You already understand v2 (cache the clicks of the first record, replay them with no AI, fall back to the live agent on a
miss). These docs do **not** re-teach that. Wherever v3 reuses a v2 idea, there is one line saying so and a pointer to
`v2/docs/`.

---

## The whole thing in one paragraph

v3 is a browser agent you configure from a web page instead of from code. You describe the job in plain words, paste
your records, optionally say how to check the results, and press Run. Everything you set (prompt, website, data, keys,
checks) is stored in a PostgreSQL database, not in `.env` or in code. The live agent (an LLM clicking through the site) can
do any task but needs about 15 model calls per record. So v3 watches the agent do the **first record** and tries to turn it
into something cheaper, in this order: **replay** the clicks (only for plain form filling, and only when that is provably
safe), a **recipe** (read pages, decide, click one record-labelled button), or a **plan** (the general case: replay the
route, copy values it needs from pages with one small call, decide the form's values with a small voting call while code does
the arithmetic). A new plan is **proven against the live agent** on the next few records before it is trusted. If nothing
fits, the live agent does every record: slower, never wrong because of the cache. Since 10 Oct (doc 12, not run live yet) the
decision itself can become code too (a **decision program**), so a trusted kind of record needs no model call at all.

## The test sites you will keep seeing

Five sites, each built to be unlike the ones before:

- **Credit Desk** (`testsites/credit-desk`, port 5174): 100 credit requests. Read the request, look the retailer up on a
  Ledger page, apply rules, click **Approve**, **Reject** or **Hold**. The site's own database view holds the correct
  answers. Fits the recipe.
- **AP Desk** (`testsites/ap-desk`, port 5180): 100 invoices. A three-way match across invoice, vendor, purchase order
  and goods receipts, then a form (a dropdown, a reason, a typed note, a date) and a confirm dialog. Built to be *unlike*
  the Credit Desk so the cache could not be tuned to one site. Fits the plan; graded by `grade.py`.
- **Returns Desk** (`testsites/returns-desk`, port 5181): 100 returns. A list with no search that must be paged through, links whose
  text is an id, a branching route, a 19,600-character policy page, a custom calendar widget. Built to find where the plan engine
  breaks; graded by `grade.py`.
- **Expense Desk** (`testsites/expense-desk`, port 5182): 100 travel expense claims. A single-page app (hash routes, content loaded
  late), a type-to-filter search with clickable rows, tabs, collapsed policy sections, currency conversion, and a three-step decision
  wizard with radio buttons and checkboxes whose ids change on every render. Built on 9 Oct to test that the plan engine is general;
  graded by `grade.py`.
- **Claims Desk** (`testsites/claims-desk`, port 5183): 100 insurance claims. A sign-in whose session runs out, a cookie banner, a
  "What's new" pop-up, an endless list with a Retry box, and a decision form inside an iframe built from ARIA widgets and searchable
  comboboxes. Built on 9 to 10 Oct for the hazards of real web apps; graded by `grade.py`. Not run with the live agent yet.

## Glossary

- **Workflow / task**: one saved job (prompt, URL, data, settings). The sidebar calls it a task.
- **Record**: one row of your data. One record is processed in one browser.
- **Run**: one press of the Run button over all (or the first N) records of a workflow.
- **Live agent**: browser-use plus Gemini deciding every step. Slow and costly, but handles anything.
- **Cache (v2 idea)**: remembered clicks and typing for form filling. Table `cached_steps`.
- **Trace**: a recording of everything the live agent did on one record (and the text of the pages it saw). Table `recipe_traces`.
- **Recipe**: what is learned from a trace for the Credit Desk shape: which pages to read and which button to click. Table `recipes`.
- **Plan**: what is learned from a trace for any other shape: the route with its varying values marked. Also in `recipes`.
- **Slot**: a value a plan copies from an earlier page (a vendor name, a PO number) with one small model call.
- **Proof / proof record**: the first records after a plan is learned, where the plan's dry run is compared with the live agent.
- **Dry run**: running a plan up to, but not including, the saving clicks, to see the values it would enter.
- **Decision book**: the shared notebook where one model call decides five records at once (recipe only).
- **Cache epoch**: a number on each workflow. Bump it and the workflow gets a brand-new, empty cache namespace.
- **Verification**: after the run, compare what the site saved with what is correct, and report accuracy.
- **Key pool**: the little scheduler that spreads model calls across your Gemini keys, and (since 10 Oct) paces them under the per-minute limit.
- **Decision program**: (10 Oct) a plan's decision written once by the model as facts + rules and run by code; decides a kind of record alone after agreeing with the model twice.
- **Grounding**: (10 Oct) the check that every number, date and quote an answer uses is on the record's pages.
- **Read-back**: (10 Oct) after a save, opening the record's own page again to see that the saved values are shown.
