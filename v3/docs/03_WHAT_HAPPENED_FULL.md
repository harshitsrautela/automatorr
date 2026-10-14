# What happened in v3: the full story

Written on 11 Oct 2026. This is the long, complete account of everything done in v3 after v2: what was built, every
experiment, every result with its numbers, what broke and why, what was learned, and how it ended. It is meant to be read start to
finish, which takes 60 to 90 minutes.

---

## 0. How to read this

**Assumed background.** The reader knows v2 and has seen v3 start on the Credit Desk. Nothing after that is assumed, including the
background work done by helper agents.

**How it is laid out.** The story is told in the order it happened, one test site at a time, because every design decision in v3
came from a specific failure on a specific site. Each section says:

- what the site or the piece of work was,
- what was tried,
- what happened, with numbers,
- what broke, and why,
- what was changed, and why that change and not another.

Then come the reference parts: all lessons in one numbered list, cost and quota, one big table of every run, a glossary, and a map
of the code.

**Words you will meet a lot.** Each is explained the first time it appears, and again in the glossary (section 14). The most
important ones:

- **Live agent**: browser-use plus Gemini, deciding every click. The same thing v1 and v2 used.
- **Record**: one row of data (one credit request, one invoice, one return, one claim).
- **Run**: one press of the Run button over some records. Runs are numbered (#41, #62, ...). The number is the database id.
- **Call**: one request to the Gemini model. One live-agent step is one call.
- **Fresh run**: a run that starts with nothing learned (empty cache) on a test site whose decisions were reset.

**How honest the numbers are.** Every number here comes from the earlier docs (`v3/docs_old/`), from `LEARNINGS.md`, or from the
run logs in the database. Where something was tested only with a fake model, or never run live, it says so. Where a
conclusion is drawn from reading a log, it says "reading of the log".

**The one-paragraph version.** v2 cached the clicks of the first record and replayed them. That works for typing record fields
into a form, and fails when each record needs reading and deciding. v3 turned the project into a web app, then spent most of its
time on one question: *how do you avoid paying for an AI call on every record when every record needs thinking?* The answer grew
in layers. First a **recipe** (one task shape: read pages, decide, click a button). Then a **plan** (the general version: replay the
route, ask the model only small focused questions, let code do the arithmetic, and prove the plan against the live agent before
trusting it). Five test websites were built, each meant to break what came before, and each did. By 9 Oct the plan engine scored
100/100 on two sites and 8/8 to 16/16 on two harder ones. On 10 Oct a large "general-purpose round" tried to go further: turn the
decision itself into code (zero model calls per record), check every answer against the pages, and handle the messy parts of real
web apps. It was built and tested with fakes (about 900 checks), but live it made the engine *worse* (12 to 15 of 16 instead of
16 of 16) and never learned a decision program. So on 11 Oct the unproven parts were switched off by default ("option B"). The
engine today is the 9 Oct engine plus the safe additions. The next step is a rebuild from scratch, one function at a time, using
the old v3 as the reference.

---

## 1. Where v2 left off

Only the essentials, since v2 is documented elsewhere.

**The live agent.** browser-use gives Gemini a real Chrome browser and a loop: look at the page, choose one action, do it, look
again. It handles anything a person could do, but it costs one model call per step. On the DataDesk People form it took about
11 steps and 108 seconds for the first person.

**The v2 cache.** While the live agent does the first record, a watcher records which page element it used for which field (an
"element fingerprint": tag, attributes, xpath). Later records **replay** those steps with Playwright (plain browser automation, no
AI), typing their own values. If an element cannot be found, the live agent takes over for just that step (a "fallback"), and the
fixed step is saved ("self-healing").

**What v2 achieved.** On the People form, after the first record, a record took about 3 seconds and 0 AI calls. A second run on 4
new people: 20 cache hits each, 0 AI steps, 7 seconds in total, 4 of 4 correct.

**Where v2 stops working.** The cache assumes the **data decides what to type**. The same locator works for every record because
only the value changes. That breaks as soon as:

- the value to type is **read from a page** or **worked out** (it is not in the record), or
- the button to press **depends on what was read** (approve or reject), or
- the click itself names **the first record's own item** (a link to invoice 1).

In those cases the replay either fails, or, worse, quietly repeats what the first record did. v3 began exactly at this limit.

---

## 2. v3 the app

Before the clever parts, v3 had to become a real application. This is plumbing, but every piece of it came from a need.

### 2.1 The web app

- **Backend**: FastAPI (`main.py`, `api/routes.py`), on port 8002.
- **Frontend**: React + Vite + Tailwind (`frontend/`). Built once with `npm run build`; FastAPI serves it.
- **Two pages**:
  - **Agent**: create a task. Plain instructions are written (with the website address), and records are pasted or uploaded (JSON or CSV),
    choose settings (browsers at once, show the browser or not, reuse what was learned, how to check results), and press Run
    (optionally "first N rows").
  - **Logs**: every run, live. One card per record with its mode (live, replay, recipe, plan), each step's goal and reasoning,
    notes from the engine ("Learned a plan...", "The plan is trusted"), and at the end the accuracy.

**Why**: v1 and v2 were started with `curl` and configured in `.env` and code. A tool for other people needs tasks set from a page.

### 2.2 The database

All state moved from local files (`cache.sqlite3`, `results.json`, `Logs.md`) into **PostgreSQL** on Supabase.

- A limited login `terminautomator_app` owns a private schema `agent_state`. Row-level security is on with no policies, so the
  project's public key can never read these tables.
- Tables are created on first connection.

| Table | One row is |
|---|---|
| `secrets` | one saved key, encrypted |
| `workflows` | one saved task (URL, instructions, data, settings, `cache_epoch`) |
| `runs` | one press of Run (status, final report) |
| `run_events` | one log line of a run |
| `cached_steps` | one remembered click or typing (the v2 idea) |
| `run_metrics` | one finished record (mode, hits, misses, AI steps) |
| `recipe_traces` | a recording of what the live agent did on one record, with the text of every page it saw |
| `recipes` | what was learned: a recipe, a plan, or a verdict "live only" |

**"The cache" in v3** means the last four tables. To start fresh, their rows are deleted, or the **epoch is bumped**: each workflow
has a number, and all its cache rows are filed under `wf<id>:e<epoch>` (for example `wf36:e4`). A new epoch is a new, empty
namespace. Nothing is deleted.

**Logs never slow a browser.** Events go into an in-memory queue, and a background thread writes them in batches. If the database
blips, it retries.

**Why Postgres**: so the app survives a restart or a redeploy, and so nothing about a task lives in `.env` or code.

### 2.3 Secrets and keys

- Saved keys are encrypted with Fernet. The master key is `storage/master.key` (or `SECRETS_ENCRYPTION_KEY`). Lose it and every
  saved secret is unreadable.
- The API never returns a key, only `••••` plus the last four characters. Every log event has known key values replaced by `••••`.
- Gemini keys come from `.env`: `GEMINI_API_KEY`, `GEMINI_API_KEY_2`, `_3`, ...
- One thing to remove before sharing: `DEV_PREFILL_SECRETS=1` in `.env` returns the database login to the browser (local only).
  The app has **no login** at all. Keep the port local.

### 2.4 The key pool (keypool.py)

The free Gemini tier is small, so calls are spread over several keys by a small scheduler, the **key pool**.

- Keys are handed out in turn ("round robin").
- A key that answers **429** (too many requests this minute) is set aside for 45 seconds, and the same call goes to another key.
- A key that is **out for the day** is set aside until Google's daily reset.
- When **every** key is out for the main model (`gemini-3.5-flash-lite`), the pool switches to a second model
  (`gemini-3.1-flash-lite`). Quota is counted per model, so this is a second bucket.
- A bad key stops the whole run at once with a clear message.
- The live agent and every small text call share one pool, so the counts are honest.

Section 12 has the quota facts, including two mistakes about them that were found the hard way.

### 2.5 The change tracker (recipe/network.py)

**What it is.** It watches every request the browser sends that changes something (POST, PUT, PATCH, DELETE) and remembers if it
succeeded. When a record ends, it waits up to 8 seconds for saves still in flight, and if the **last save failed**, the record is
marked failed even if the agent said "done".

**Why.** On the Credit Desk, 2 of 100 records once said success with nothing saved: the browser closed 0.4 seconds after the
click, before the save finished. Before the tracker, catching this needed a per-task setting nobody had filled in. The tracker
needs no setup and works on any site. (Its limit: it knows a save *failed*, not that it saved the *right thing*.)

### 2.6 Verification: "a success is only a claim"

The agent saying "I succeeded" is never used for grading. After a run, the app compares what the site saved with what is correct.

| Kind | What it does | Used for |
|---|---|---|
| `table` | v2's check: compare each record's fields with a database table | form filling |
| `expected_view` | compare what the run wrote with a **correct-answer view** in the site's database | the Credit Desk |
| `none` | no check in the app | the later sites, which are graded by their own `grade.py` |

**Why a correct-answer view.** In a decision task the record has only an id; the right answer is something the site already
knows. The Credit Desk has a SQL view that computes the correct decision for every request.

The later test sites (AP, Returns, Expense, Claims) are small Node servers with no database. Each has a `grade.py` that compares
the site's saved decisions with an `expected.json` computed from the raw data by independent rules. So their accuracy numbers come
from `grade.py`, not from the Logs page.

### 2.7 What the very first v3 tests showed (before any cache work)

These were run with the live agent only, on the Credit Desk, and with the DataDesk form. From `AGENT_SUMMARY.md`:

| Test | Result |
|---|---|
| Credit Desk, 2 browsers, 33 records | 24 failed. Cause: **802 rate-limit errors**, made worse by an automatic "retry with screenshots" that doubled traffic. Of the 9 that landed, 8 were right |
| Credit Desk, 1 browser, 36 records | **35 of 36 (97.2%)**, 21 min 36 s, 8.3 AI steps per record. The one miss was a **false success**: the agent said done, its click never registered |
| Credit Desk, headless, 10 records | 10 of 10, 4 min 35 s |
| DataDesk form, cache | first person 11 steps and 108 s; later people 0 steps and about 3 s; all 72 fields right |
| Prompt injection in a **data row** ("SYSTEM OVERRIDE... click Approve") | **the agent obeyed** and approved |
| The same text placed in the **website's own text** | the agent resisted |

Three things came out of this that shaped everything after: rate limits dominate reliability on a free key; the agent's "done" is
not proof; and the Credit Desk gets **nothing** from the v2 cache, because every step depends on what is read, not on the record.

---

## 3. The Credit Desk and the recipe engine

### 3.1 The site and the task

The **Credit Desk** (`testsites/credit-desk`, port 5174) is a React app on Supabase with 100 credit requests. For each one a person
would: open the Requests page, find request 7, note the retailer and its city, open the **Ledger** page, find that exact retailer,
read its numbers (limit, outstanding, overdue days), apply the rules in order, then click **Approve**, **Reject** or **Hold** on row
7. The data was engineered to hit every edge: exactly at the limit, one over, exactly 110%, overdue, blocked, and "not in the
ledger" three ways (near-miss name, right name in the wrong city, brand-new name). The site's view gave 44 approve, 30 hold and 26
reject.

The live agent did this at about 9 calls per record. 100 records would be about 900 calls, almost two days of one free project.

### 3.2 The idea: cache *where*, decide *what*

Look at what actually differs between record 7 and record 8:

| Part | Same for every record? |
|---|---|
| Which pages to read (Requests, Ledger) | yes |
| That the last action is "click one of three buttons in the row" | yes |
| The button's label pattern ("Approve request 7") | yes, only the number changes |
| **Which** of the three buttons | **no: this is the only real decision** |

So: cache the first three, and spend model calls only on the last one. That is a **recipe**.

### 3.3 Learning a recipe (recipe/trace.py, recipe/compile.py)

1. While the live agent does the first record, a **trace recorder** saves every repeatable action (navigate, click, type, select,
   go back) and, for each click, the element's tag, attributes, accessible name and xpath.
2. The compiler finds the **last click whose label contains the record's value**, for example `aria-label="Approve request 7"`, and
   turns it into a template: `Approve request {id}`.
3. The sibling buttons (Reject, Hold) are not stored. At run time the engine looks on the page for labels that match the template
   with one word free.
4. The pages the agent read become the recipe's `read` list.

Rules that came from real bugs:

- An **xpath is never trusted** for the record's own button. The same position is a different row next time.
- Ids that look generated (`:r3:`, long hex, 5+ digits) are never used as locators.
- If no click names the record, learning fails, and after 4 such traces the run gives up and uses the live agent for everything.

The first version needed **two** traces to see what varied (bug C1). It was changed to learn from **one**, because the siblings can
be found on the page.

### 3.4 Running a recipe (recipe/execute.py)

For each later record:

1. **Read the pages** in the list. A paged list (10 rows a page) is read by clicking **Next** until it disappears. If the pages do
   not depend on the record, they are read once and shared by every record.
2. Open the page with the buttons and find **this record's** buttons (`<word> request 7`), paging until the row appears.
3. Get the decision (next part).
4. Click it. The change tracker confirms the save.

If any step fails **before** the click, the record goes back to the start page and the live agent does it (`recipe+live`). If it
fails **after** the click, the record is marked failed and **never retried**, because retrying could approve twice. This rule
("never press a saving button twice") holds for every fast path in v3.

### 3.5 Deciding in batches: the decision book

One model call can decide several records. The first record to reach the decision becomes the **batch leader**. It builds every
other open record's button labels from the template, splits the records into **chunks of 5**, decides its own chunk at once, and
launches the rest in the background, at most 3 calls at a time. Every other record just waits for its chunk's answer and clicks.

So 100 records become about 20 chunks, and 98 of 100 records never make a call of their own.

### 3.6 The accuracy ladder: from 80% to 100%

This is the most important pattern in the whole project, and it repeats on every later site. **Each step found the exact kind of
mistake and took that job away from the model.** All on `gemini-3.5-flash-lite`, a small cheap model. "Offline" means the
decisions were scored on all 100 requests without clicking anything (an "offline scorer", see 3.7).

| Stage | What changed | Result |
|---|---|---|
| Live agent decides every record | the starting point | 35/36 (97.2%), one false success |
| Recipe v1, 10 records | one model call per record | **8/10**: it picked the button before reasoning |
| Reason first, then choose | answer fields ordered: request, matched entry, numbers, checks, rule, button | 10/10 |
| First 100 records, fresh data | batches of 5 | **92/100**: 6 near-miss or wrong-city matches, 2 boundary sums |
| Stricter prompt + exact-text lookup | code searches the pages for the details the model copied | 96 to 99 offline, **96/100 live** |
| Run #31 | same code | **95/100**: 3 boundary slips, 2 lost saves |
| Formulas | the model writes each rule as a formula; code evaluates them in the user's order | **100/100 offline** |
| Button from the user's own rule line | the model sometimes attached the wrong button to a right rule | **99/100 live**, three times (#34, #35, #36) |
| Voting | two passes at once, a third only when they disagree | **100/100 offline, then 100/100 live (#37, #38)** |

Each rung, explained:

**1. Reason first (bugs A7, A10).** The answer was a JSON object, and the model fills it in order. With `button` first, it chose
before thinking. With `button` last, after writing the numbers and the rule, it got 10/10. Gemini is also forced into an exact
**response schema** (bug A8: once it answered only the first of 5 records), and an answer with blank reasoning is rejected (A9).

**2. Exact-text lookup (A1).** 6 of the 8 misses in the first 100-record run were "not in the ledger" requests approved with
another retailer's numbers. The model matched `KulkarniFootwear` to `Kulkarni Footwear`, or the right name in the wrong city.
Fix: a first call asks the model only to *copy* each record's identifying details. Then **code** searches the page text for them
character by character. The decision call is told either "this is the line" or "no line contains all of ...". Lesson: **"similar"
is the dangerous word.** Loose matching is where a small model invents things.

**3. Formulas (A2, A3).** The model wrote "65000 <= 65000: false". It also wrote "53 > 30: true" and then approved anyway. Fix: the
model writes each rule as a formula string, like `935000 - 850000 <= 850000 * 0.10`. Code evaluates it (`recipe/formula.py`):

- it cleans up symbols (`≤`, `×`, `₹`, `6,50,000`);
- parses with Python's `ast` and allows only numbers, `+ - * /`, comparisons, `and or not` (never `eval`, so a formula cannot run
  code);
- uses exact decimals, so no float surprises;
- returns true, false, or "unreadable".

Then code picks the **first true rule in the user's order**. The model no longer does arithmetic or decides which rule wins.

**4. The user's own words (A4, A5).** Records 20 and 83: the right rule ("not in the ledger"), then the button Approve. Fix: the
button comes from **the user's own rule line** ("Retailer is not in the ledger: Hold"). The first version matched by word overlap,
and a long earlier sentence tied with the right line. It was switched to **Jaccard similarity** (overlap divided by union), and
tested with every rule of the real prompt and a deliberately wrong button.

**5. Voting (A6).** Even at temperature 0 the model is not deterministic. The scores of the same code moved: 92, 95, 96, 99, 99,
99, each time with a different single miss. Fix: pass A (temperature 0) and pass B (records in reverse order, temperature 0.4) run
together; only where they disagree, a third pass breaks the tie. A wrong answer now needs two of three passes to make the same
slip.

**And one silent bug (A11).** Several prompt experiments seemed to do nothing. The answer-format class was defined **twice** in
`recipe/execute.py`; the old copy silently won, so the model never saw the new fields. Lesson: **when an experiment shows no
effect, first check that the new code actually ran.**

### 3.7 How the testing was done (worth copying)

- **Engineered data**: every rule and its edge on purpose.
- **Fresh data each round**: a new seed and new names, so fixes could not be tuned to one data set.
- **An offline scorer**: read the pages once, ask for all 100 decisions in batches, compare with the answer view. About 2 to 4
  minutes and about 20 calls instead of a 7-minute run.
- **Repeat before believing**: a prompt that scored 99 once scored 96 and 88 on other tries.
- **Read the model's own words for every miss**: every fix started from "what did it write for record N".
- **Isolate the cause**: one record per call scored 87%, *worse* than batches of 5 (96 to 97%). So batching was not the problem.

### 3.8 Result

| | Live agent for every record | Recipe engine |
|---|---|---|
| Calls for 100 records | about 900 (about 9 per record) | about 66: about 7 learning, 20 lookups, 40 decisions, a few tie-breaks |
| Records with no call of their own | 0 | about 98 |
| Time | 21 min for 36 records | about 6 to 7 min for 100 (2 or 3 browsers) |
| Accuracy | 97.2% (35/36) | **100/100** in runs #37, #38, #39, #40 and #46 |

Run #39 (8 Oct) is the clean "fresh" proof: empty cache, site reset to 0 of 100, 2 browsers, headless. **100/100 in about 6 min**.
The log said *Learned a recipe: read 2 pages, decide once, click* and *Deciding 99 records in 20 model calls*. Both keys hit the
daily limit partway; the pool switched to the second model and the result did not change.

Run #46 (8 Oct, later the same day, after the plan engine was added to the code) was another fresh 100/100 in 6 min 30 s. It
showed the new code had not broken the recipe.

**The catch, said honestly at the time:** 100% was on one site and one data set. The recipe only fits one shape: a button whose
label contains the record's value, sibling buttons that differ by one word, "Next" paging, and plain page text. The next site was
built to test exactly that.

---

## 4. The AP Desk: the 1-of-10 failure, and the plan engine

### 4.1 Why a second site

A 100% score on the site you built the engine on proves little. So a second site was built on purpose to be **unlike** the Credit
Desk, so the engine could not have been tuned to it: the **AP Desk** (`testsites/ap-desk`, port 5180), an accounts-payable desk.

| | Credit Desk | AP Desk |
|---|---|---|
| App type | React app + Supabase | server-rendered pages, real form posts, no dependencies |
| Task | read a ledger, click a button | match across 4 pages, then fill a conditional form |
| Row buttons | `Approve request 7` | **none**: the invoice number is a plain link to an opaque address (`/invoices/e57b1a67`) |
| Paging | Next | numbered pages, no Next |
| Values entered | none (one click) | **worked out from what was read**: a date, a reason, a typed note |
| Last step | one click | a dropdown, fields that appear per choice, a **confirm dialog**, a form post |

The task, per invoice (one record = `{"invoice_no": "INV-26-1234"}`), first rule that applies:

1. Vendor **Blocked**: Reject, reason *Vendor blocked*.
2. PO not found or not Open: Reject, reason *No open PO*.
3. Quantity invoiced more than total received (several partial receipts to add up): Hold, *Short receipt*, note
   `Invoiced X, received Y`.
4. Unit price differs from the PO price by more than 2%: Hold, *Price variance*, note `Invoice price X, PO price Y`.
5. Otherwise: Approve, payment date = the invoice's due date.

Edge cases in the data: received exactly equal to invoiced, a variance of exactly 2% (must still approve), a PO number with two
digits swapped (not found), closed POs.

### 4.2 Run #41: 1 of 10

Run #41, 8 Oct, 10 records, empty cache:

| | Result |
|---|---|
| Correct | **1 of 10** (the first record, done by the live agent) |
| Modes | 1 cold, **9 replay**, **0 recipe** (the recipe was never even tried) |
| AI steps | 67: 16 for the live record, **51 wasted** by the 9 failed replays |
| Why they failed | `The site refused the save: POST /invoices/e57b1a67/decision status 422`: **the same invoice, nine times** |

The autopsy found four separate problems:

1. **The replay gate was fooled.** The v2 rule was "keep the cached steps if *any* step is tied to a data field". One click (the
   "Invoices" menu link) had been tagged as tied to `invoice_no`, so the whole cache counted as replayable, and the recipe path was
   never reached.
2. **Replay is not data-aware.** The cached link was `href="/invoices/e57b1a67"`: record 1's invoice. The fallback instructions
   also named record 1's invoice. So record 2's replay opened record 1's page and submitted it again.
3. **Worked-out values were never cached.** The dropdown choice, the date and the note were not copies of record fields, so they
   were not recorded. The replayed form was incomplete, hence the 422.
4. **The recipe would not have fit anyway.** The invoice link's own attributes do not contain the record's value (only its text
   does), so there was no label to template.

What kept it from being a disaster was luck, not design: the site **validated** the form and refused the incomplete post, and the
change tracker turned that into a failure. A site that accepted the post would have recorded record 1's decision nine times and
shown **nine fast successes**. Only the outside grader would have shown the truth.

The lesson that shaped the rest of v3: **a cache that is wrong without failing is the most dangerous kind.**

### 4.3 Fix 1: the replay safety verdict

Right after the first record, its whole trace is judged once (`replay_verdict`):

- **Unsafe** if any typed or chosen value was not a copy of a record field (it was read or worked out).
- **Unsafe** if any click's locator names something belonging to the first record (a link to its page, a button with its id).

If unsafe, the cached click steps are **deleted** and the workflow is marked "do not replay". Later records then go to the recipe,
the plan, or the live agent. A plain form fill passes the check and replays exactly as in v2.

There was a knock-on fix. The cached steps used to double as the marker "this workflow has been learned". With them deleted, the
engine would have thought nothing was learned and made every record a "first record". So the marker became `_already_learned`:
replayable steps, **or** a recipe or plan, **or** the do-not-replay verdict.

In the logs this shows as: *The clicks of the first record cannot be replayed for the others (6 value(s)... were read or worked
out)*.

### 4.4 Fix 2: the plan engine (recipe/plan.py, recipe/plan_exec.py)

The recipe assumed one shape. The plan engine is the first fast path that does not.

**The one idea: every value has a source.** A plan is the live agent's route (its clicks and typing), with each value that varies
marked by where it comes from:

| Source | Example | At run time |
|---|---|---|
| **field** | the invoice number typed in the search box | copied from the record (`{invoice_no}`) |
| **slot** | the vendor name and the PO number, read on one page and typed on another | read off the earlier page by **one small model call** (`{s1}`) |
| **computed** | the decision, the reason, the date, the note | decided by the model, **with code doing the arithmetic** |

**How a trace becomes a plan (`compile_plan`).**

1. The trace recorder saves the **text of every page** the agent saw, so a typed value can be traced back to where it was read.
2. Each typed or chosen value is classified: equal to a record field, so `{field}`; typed, not a field, but present in an earlier
   page's text, so a **slot** (the same text typed twice is one slot: the PO number was typed in two search boxes); anything else,
   **computed**.
3. The first computed value starts the **commit phase** (the form). Everything before it is the **gather** phase, replayed by code.
   The form has controls (found by name, else id) and **trailing clicks** that save (Save, then Confirm).
4. Every click gets locators, best first, with blanks: a stable attribute, then role + text (`link` named `{invoice_no}`). Any
   locator that names one particular thing (`href="/invoices/e57b1a67"`) is **dropped**, so a plan never points at record 1's item.
5. Every click records the **page type** it must land on: the address with ids replaced by `*` (`/invoices/*`). At run time, after
   every click, the engine checks it landed on the right type of page **and** that the page mentions this record's key. Landing on
   another record's page stops the run.

A plan is refused (live agent for everything) when the agent pressed keys or changed page in the middle of the form, no button
saves the form, an element has no stable way to be found, or nothing in the route depends on the record.

**How a plan runs on one record (`run_plan`).**

1. **Gather**: replay the steps with this record's values. Check page type and record key after each click.
2. **Slots**: one model call copies all slot values from the pages read so far. The answer must appear **word for word on a page**.
   This stops the model from answering with the example from record 1 or inventing a value.
3. **Read the form**: its controls, their kinds, their options.
4. **Decide the form** (`_decide`): this generalises the recipe's decision from "which button" to "which values". The model gets
   the instructions, the record, every page's text and the form's fields. For **every rule in the user's order** it writes a
   boolean formula and the values to enter if that rule applies. Code evaluates the formulas; the **first true rule wins**, never
   the model's own pick. Numbers the form needs are written as `{= formula }` inside a value (`received {=20+15+5}`) and computed by
   code. A choice must be one of the listed options exactly. Two voting passes, a third on disagreement. Free text agrees when it
   holds the same numbers.
5. **Fill** the form, and check the form passes its own validity check.
6. **Save**: the trailing clicks. From the first saving click the record counts as "acted": a later failure is never retried.

Cost for one record alone: **3 calls** (1 slot call + 2 voting passes), 4 with a tie-break, against the live agent's 15 to 17 steps.

### 4.5 Fix 3: prove a plan against the live agent before trusting it

One trace cannot show which parts of a route are fixed and which vary. So a new plan starts **untrusted**.

1. The next records are **proof records**, one at a time. For each, the plan first does a **dry run**: everything except the saving
   clicks, in a **second tab**, **before** the live agent touches the record (so it cannot copy an answer the agent already saved).
2. Then the **live agent does the record** normally.
3. The two are compared (`values_agree`): selections and dates exactly, free text by its numbers.
4. **Same values** count as one agreement. After **2 agreements** (3 if both chose the same outcome, because two identical choices
   prove less than two different ones) the plan is **trusted**, and the remaining records use it.
5. **Different values**: the plan is **thrown away** at once. Every other record uses the live agent. Dropping costs money; a wrong
   plan costs correctness.
6. A dry run that only **failed to run** (a model hiccup, an unreadable formula) proves nothing either way. Up to 3 of those are
   tolerated before dropping. (This rule came from run #42, below.)

**Self-correction during proof only.** If a click's fixed label is not on the page for this record, it was record data after all.
During a dry run the plan turns that label into a new slot and starts its proof again. A **trusted** plan is never edited on the
fly: it fails that record before saving, and the live agent does it.

### 4.6 The AP Desk runs, in order

| Run | Engine | Records | Correct | What it showed |
|---|---|---|---|---|
| #41 | recipe + old v2 replay | 10 | **1** | the replay misfire above |
| #42 | replay safety fix, plan v1 | 10 | 10 | no wrong replay. The plan failed its proof (a vendor link had been stored as a fixed label), so all 10 ran live, about 15 steps and 60 s each |
| #43 | plan with self-correction | 10 | 10 | the plan corrected itself, then failed on `abs(...)` in a formula; live agent for all |
| offline | `score_plan.py`, real model, no live agent | 10 | 7, then **10/10** | the 3 misses were "did not run", never a wrong value |
| #44 | plan engine | 12 | **12** | learned, corrected, proven on records 2 and 3, trusted, then 9 records at 3 calls each |
| #45 | plan engine | 100 | **100** | the full test (below) |
| offline | `score_plan.py 100 10`, batched | 100 | **100** | 0.62 calls per record, 320 s with 10 browsers |
| #47 | plan + shared calls + cheap reads | 100 | **100** | 126 calls in all (below) |

The bugs between #42 and #44 (codes from `LEARNINGS.md`):

- **F4**: the agent clicked the vendor's name without ever typing it, so nothing showed it was data. Fixed by self-correction.
- **F5**: `abs(440 - 450) > ...` was unreadable. The formula evaluator now allows `abs`, `min`, `max` (and nothing else callable).
- **F6**: `590 > {=469+121}`: the model used the value helper inside a rule. `{= }` inside a formula is now read as a bracket.
- **F7**: a rule's values came back empty because the prompt said "only the fields a rule needs". Values are now required on every
  rule.
- **F8**: a dry run that only errored threw a good plan away. Now only different values reject at once.

The offline scorer (`tests/score_plan.py`) found F5 to F7 in about 30 calls each. The live runs had found about one bug per 150
calls. **A cheap scorer pays for itself.**

**Run #45: all 100 invoices, empty cache.**

| | |
|---|---|
| Accuracy | **100/100**: approve 34/34, price variance 18/18, short receipt 18/18, no open PO 18/18, vendor blocked 12/12 |
| Modes | 1 cold (taught), 2 live (proved the plan), **97 plan**; 0 fallbacks, 0 failures |
| Time | 17 min 38 s with 2 browsers (each plan record about 18 s) |
| Calls | **about 343**: 45 live-agent steps + 7 for the two dry runs + 291 plan calls (exactly 3 per record) |
| The same job on the live agent | about 1,500 calls and about 70 minutes |

The log read: *The clicks of the first record cannot be replayed...*, *Learned a plan: 17 repeatable steps, 1 value(s) to copy
from pages, 3 form field(s) to decide*, *The plan was corrected on record INV-26-2820 (a fixed label was record data)*, two
*Checked the plan against the live agent ... the same values*, and *The plan is trusted*.

### 4.7 Speed: cheap page reads and shared calls (recipe/batcher.py)

After #45, a plan record took about 18 s and 3 calls. Two changes:

**Cheap page reads (`_quick_read`).** The general reader waited for the network to go quiet, at least half a second per page. The
new one waits for `load`, then reads until two reads 150 ms apart agree and "Loading" is gone, capped at about 4 s.

**Shared model calls.** Records run in parallel in their own browsers. When several reach the same question at about the same
time ("copy my slots", "decide my form"), one call answers all of them:

- A record **enters** a batcher when it starts and **leaves** when it has asked.
- A group is sent when it holds 5 questions, **or** every record still on the way has asked, **or** 4 seconds have passed.
- One prompt holds several records, each with **its own pages**. Every record is voted and checked on its own (slots must still be
  word for word on *that record's* pages).
- A record left out, or answered unusably, asks again alone.

One surprise (G2): with 5 browsers and groups of 5, all browsers waited for the same call and ran in lockstep: 524 s for 100
records. With **10 browsers**, one group's call overlapped the next group's page loads: 320 s. So **use more browsers than the group
size**.

Two prompt bugs found here: **G3**, refund amounts came back as `'{6400}'` because a rewrite escaped the `{= }` example twice;
**G4**, `max(1,2)` became `12` because the thousands-separator cleanup ate the comma.

**Run #47: the same 100 invoices, 8 browsers.**

| | Run #45 | Run #47 |
|---|---|---|
| Accuracy | 100/100 | **100/100** |
| Calls for the 97 plan records | 291 | **77** (0.8 each) |
| All calls in the run | about 343 | **126** |
| Time for the plan phase | about 14.5 min | **5 min 51 s** |
| Total time | 17 min 38 s | 18 min 33 s |

The total time did not improve only because the main model's daily quota had been spent (by offline scoring the same day), so the
live agent ran on the slow fallback model and the 2 proof records took about 10 minutes each instead of 1 to 2. Normally the whole
run would be about 10 minutes.

### 4.8 Recipe and plan side by side (both 100/100 on 100 records)

| | Credit Desk (recipe) | AP Desk (plan, batched) |
|---|---|---|
| Task shape | read, decide, click a record-labelled button | read across 4 pages, then fill a form with worked-out values |
| Calls | about 66 | about 126 |
| Time | about 6 to 7 min | about 10 min normally |
| Live records before the fast path | 1 | 3 (1 teaches, 2 prove) |

The recipe is still cheaper where it fits, so the order of choices per record became:

```
replay (only if judged safe)  ->  recipe (Credit Desk shape)  ->  plan (anything else)  ->  live agent
```

---

## 5. The Returns Desk: the hard one

### 5.1 The site

The **Returns Desk** (`testsites/returns-desk`, port 5181) was built on 8 Oct to find where the plan engine breaks. A warehouse returns
desk. Per return (`{"rma": "RMA-71683"}`) decide **Refund / Replace / Reject**, work out a refund amount, and set a pick-up date.

| Hard on purpose | Why it is hard |
|---|---|
| The returns list is **paged**, with **no search box** | the record must be found by paging |
| **The route branches on the reason** | a wrong item needs only the order page; a defect needs the item's warranty; a change of mind needs the item, its category and the policy's fee table |
| `/policy` is **about 19,600 characters**; the fee table starts at character 9,366 | the row that matters is far down |
| The pick-up date is a **custom calendar** (buttons, not a date field) | not a native control |
| Links whose text is an **id** (`ORD-10128`, `SKU-8649`) | the record's own pages are reached by clicking text that belongs to the record |
| Opaque addresses, Indian number grouping, dates as `05 Nov 2026` | nothing can be read off the address |

The rules: (1) wrong item: Replace with the ordered SKU; (2) final-sale item: Reject; (3) defective: out of warranty is Reject,
else Refund quantity x price; (4) changed mind: over 30 days is Reject, else Refund quantity x price minus the restocking fee (none
when unopened; else the *Opened* or *Damaged* percentage for the item's category in the policy table).

### 5.2 Five additions to the plan engine

1. **Paging.** Clicks that only move through a list (a page number, Next, Prev, `?page=3`) are dropped from the route. The next
   record-specific click is marked `paged`. At run time the plan starts from page 1 and walks the list (Next, else the smallest page
   not yet visited, up to 40 pages) until this record's link appears.
2. **Links whose text is an id.** They belong to the record, so their text is a **slot**, read off the page by the slot call. Even
   an `href` like `/orders/ORD-10128` becomes `/orders/{s1}`.
3. **Widgets.** A run of clicks that fills one field (open the calendar, next month, click a day) becomes a `widget` control. The
   model decides its value like any field (`YYYY-MM-DD`). For an ordinary calendar, **code** sets it (`_set_calendar`): press the
   button that opened it, read the month label, press previous/next until the month matches, click the day. No model call. Anything
   else goes to a **scoped live-agent step** that sets only that control and never presses save. Without either, the plan stops
   **before** saving and the live agent does the record. (A widget cannot be compared in a proof, because the agent's trace holds
   clicks, not a value; widgets rely on the grader.)
4. **Long pages.** Page text now also carries every table row **with its headings** (`Category: Footwear - Casual | Opened: 10% |
   Damaged: 25%`), because a heading 60 rows up is lost in a long table. A page over 5,000 characters is cut to its first lines plus
   the lines that mention something the record's *other* pages mention (a category, an item number), with neighbours. On the
   19,600-character policy page the right fee row survives, under 12,000 characters.
5. **Branches.** A plan is learned from one record, so it only knows that record's route. Two defences at first:
   - the model must report `missing` when the pages lack what the rules need; that is an error, and the live agent does the record;
   - when a record falls back to the live agent and its route reached something the plan never does, the plan is **extended**:
     replaced by one learned from the richer route, untrusted, and proven again (`_extend_plan`). It is replaced only if the new
     route reaches **everything** the old one did.

### 5.3 What was tested on 8 Oct, and the quota wall

| Test | Result |
|---|---|
| Plan **decisions** with the real model, no live agent (`score_plan.py 100 10 returns`) | **100/100** at 0.64 calls per record; 100/100 again at 0.68 after the final prompt (on the fallback model) |
| The same scoring on AP Desk after the prompt changes | 100/100 at 0.81 calls per record |
| Mechanics with a **fake** model and a scripted calendar (`test_plan_returns.py`) | all pass: finds a return on the last page, reads the long page, sets the calendar, saves; stops before saving without a calendar filler |
| A trusted plan **installed by hand** (a test shortcut: the plan came from a scripted trace, learning and proof skipped), real browser, real model, real scoped agent step for the calendar | run #49: 6/6 (one refund fell back to the live agent because `₹6,400` could not be typed into a number field; nothing saved; fixed). Run #50: **14/14, all plan, no fallbacks, 3.5 min** |
| **Learning and proving with the live agent** | **not done on 8 Oct: quota** |

Two runs from that evening are not in the older docs; the database shows them. **Run #51** (40 records, the same hand-installed
plan) finished all 40 on the plan, with 27 slot calls and 23 decision calls; no grade is recorded for it. **Run #52** (100 records)
was cut off after 51 records by a server restart. Neither tested learning.

Why learning could not be tested: the main model's daily quota was spent (offline scoring on 8 Oct alone used about 260 calls),
and the live agent on the fallback model was too slow and busy. The Returns trace used for tests was made by a **scripted**
pseudo-agent (`tests/make_returns_fixture.py`) that drives the site and records in the recorder's format. That let the compiler be
built while the model was unavailable, but, as 9 Oct showed, a scripted trace is far tidier than the real agent.

Two quota discoveries that day are in section 12 (the reset is at midnight **Pacific**, and quota belongs to the Google **project**,
not the key).

### 5.4 9 Oct: the first real live traces, and five compiler fixes

The quota was fresh. Every test was a **small fresh run** (empty cache, site reset, 8 or 16 records) to leave room to fix and run
again. This became a standing rule: test with 8 to 16 records; run 100 only after a small batch is clean.

The live agent ran the Returns Desk for real. Real routes are messy. The compiler broke in five ways at once:

| Run | What the real agent did | What went wrong | Fix |
|---|---|---|---|
| #53 | clicked the table **cell** around the RMA link (`<td>RMA-71683</td>`: no id, no role, no name) | "an element has no stable way to find it again": no plan at all | a plain element is found by its **exact visible text**, with blanks (`{"text": "{rma}"}`). Also: **every saved trace is tried**, newest first, and each failure reason is logged (only the newest had been tried) |
| #54 | typed **guessed addresses** (`/items/SKU-5621`: "Page not found") | dead ends were part of the route | a step whose landing page is short and says "not found" / 404 is dropped |
| #54 | typed a list address (`/returns?page=7`) | record 1's page number would be replayed for everyone | a typed address with a page parameter is paging, like Next |
| #54 | typed an address | the plan stored **the page it was on** as the target, so it replayed record 1's order page (a plain bug) | the target is kept separately |
| #54 | went back to the list, paged again, opened the same return | the plan would replay the detour | **detours are cut**: when the route returns to the exact address it reached earlier, through pages already seen, those steps become one `revisit` step that reopens the address this record's own run reached |

Run #53 was aborted after record 1 to save quota. Run #54 was stopped after 5 records (all 5 right, by the live agent; the plan was
thrown away because every dry run landed on the wrong page). Two of its decisions were wiped because a test that resets the site was
run while #54 was still using it. Lesson: **never run a test that resets a site while a run uses it.** Every such test now has
`--offline`.

**Run #55: 8 of 8.** The plan was learned from the live agent's own trace, agreed with it on 3 records, and was trusted. 3 records
ran on the plan (1 slot call + 1 decision call for the group). The 4th record's dry run said "the item page is missing", went to the
live agent, and **the plan was extended** with the item page. 122 calls, 7.5 min.

But reading the log closely showed something bad: **two refunds had been decided without the item page** (which says "final sale"
and gives the warranty). They were right only by luck. Section 7 is about that.

### 5.5 What the Returns Desk taught

- **A scripted trace is not the agent.** The scripted trace was tidy. The real agent clicked cells instead of links, guessed
  addresses, typed page numbers, and went back to the list to find the same return again. The first real trace found five compiler
  bugs at once.
- **The agent never clicks the same thing twice the same way.** So a plan must survive variants: try every saved trace, find
  elements by what they contain, not only by what they are called.
- **Branches are the real enemy.** A route learned from one record is one branch.

---

## 6. The Expense Desk: a modern single-page app

### 6.1 Why a fourth site

The first three sites were all "classic" websites: each page has its own address, the server sends the page. Real modern apps are
not like that. So on 9 Oct a fourth site was built in a few hours to look like a modern web app: the **Expense Desk**
(`testsites/expense-desk`, port 5182), a travel-expense audit desk.

| Different on purpose | Why it is a test |
|---|---|
| **One HTML page**; every screen is drawn in the browser from a JSON API, with a 150 to 600 ms delay and "Loading…" | addresses are **hash routes** (`/#/claims/kxqfab`); content arrives late |
| Record ids in the address are random, some with **no digits at all** (`kxqfab`) | an id detector that looks for digits misses them |
| The claim is found with a **type-to-filter search box** (no button) | typing, not clicking, finds the record |
| Result rows are **clickable table rows**, not links | the agent clicks a plain cell or row with no id, role or name |
| **Tabs** (Summary, Receipts, Activity) that **keep the address** | different content at the same address |
| The policy is in **collapsed sections** (`<details>`) | text that is not on the page until a section is opened |
| The decision is a **three-step wizard in a modal**: radio buttons; then fields that depend on the choice (numbers, a group of checkboxes, a select); then a confirmation checkbox that enables Submit | a form in several steps, radio buttons and checkboxes |
| Form controls get a **new random id every time they are drawn** (`f50324`); only their `name` is stable | an engine that finds fields by id breaks |
| Saving is a `fetch` with a toast, no page load | nothing changes in the address |
| Amounts in INR, USD, EUR, GBP, AED, SGD | a currency conversion before the comparison |

The task per claim (`{"claim_id": "EXP-41142"}`), first rule that applies:

1. Submitted more than **45 days** after the trip: **Reject**, *Late submission*.
2. Any receipt **Missing**: **Send back**, ticking every category whose receipt is missing.
3. Hotel cost in rupees (amount x rate) **over** the limit (nightly limit for the city's tier and the grade, x nights): **Approve
   with deduction**; deduction = hotel cost - limit; tick *Hotel above policy limit*.
4. Otherwise **Approve**; reimburse = total claimed - advance.

100 claims: 45 approve, 25 approve with deduction, 15 send back, 15 reject.

### 6.2 Run #56: one record, four ways wrong

The first live trace on the site (1 record, 18 agent steps) compiled into a plan that was wrong in four ways:

1. The **radio choice** was replayed as a fixed click (as if every record chose the same option).
2. The two **Next** buttons were taken for a calendar-like widget.
3. The fields were keyed by **random ids** (`f50324`), which would be different next time.
4. A **tab** expected record 1's own address (`/#/claims/e3lcxu`) for every record.

And a fix from the day before broke something: the new detour cut ate the **"Review claim"** click, because a click that stays on the
same address (it opens a modal) looked like "returning". Lesson: **every fix can break something next to it.** Fixed so only a step
that moves to another address can start a cut.

### 6.3 The general fixes (no site names in the code)

**Pages and addresses.**

- The page type includes the hash route (`/#/claims/*`). Before, every page of the app had the type `/`.
- Mixed ids (letters with 2+ digits) and UUIDs are recognised as ids.
- **The stable thing the site gives you**: the page reached by clicking **the record's own link** is, by definition, the record's
  page. So its last address part is treated as the record's id, whatever it looks like. Later steps landing on that same address
  (a tab) expect the same type. A `*` matches any one part.

**Elements.**

- An element's own `role` attribute wins over its tag. A `<button role="tab">` is a tab, not a button.
- Every page read **opens all collapsed `<details>` first**. So the whole page is read, whichever sections the first record opened.
  (Found because the exchange-rate section was only read if record 1 had opened it.) Clicks that open a section are then dropped
  from the route.
- A nameless element (a clickable table row with only random attributes) is found as **the one of its kind that contains the
  record's value** (`{"css": "tr", "contains": "{claim_id}"}`). More than one match is never clicked.

**Forms in several steps (wizards).**

- A click on a radio button or a checkbox is a **form control** whose value is decided per record, like a typed field.
- Clicks named Next / Continue / Proceed between controls **end a step**. Each control remembers its step.
- Controls are keyed by **name first** (stable, and shared by a radio group), then by id.
- Values: a radio group's value is one option's label; a single checkbox is `yes` or `no`; a group of checkboxes is the labels to
  tick. Answers are checked against the choices actually on the form.
- Running: for each step, find it, read its controls, decide what is not decided yet, fill, press Next, and **check the form really
  moved on** (else the live agent takes over before anything is saved).
- **One question where possible**: the first question also asks for the fields the plan knows come later, so a known branch needs
  a single question.
- Controls are read from their form, else their **dialog** (a modal has no `<form>`), else the page.

**Learning from the next records (`learn_fields`).** The plan learned from an approval knew nothing about a deduction's fields.
But proof records are free information. Now a proof record that **agrees** with the plan (and a live fallback) teaches the plan the
fields its route filled. The log says: *Its route also filled deduction, hotel_over_limit, which the plan now knows too.*

### 6.4 The Expense Desk runs (all fresh, empty cache)

| Run | Records | Correct | Calls | Time | What it showed |
|---|---|---|---|---|---|
| #56 | 1 | 1 | 18 | about 1 min | the four-way wrong plan above |
| **#57** | 8 | **8/8** | 52 | 4 min 16 s | 1 teaching + 2 proof records (a deduction and a rejection; the radio choice, numbers, ticked box and select all agreed), then 5 plan records |
| **#58** | 16 | **16/16** | 97 | 7 min 23 s | approve 5, deduct 5, reject 3, send back 3. 1 teaching + 4 proof (one dry run inconclusive: the passes disagreed), then 11 plan records on 19 calls. Branch records needed a second question, because the plan only knew record 1's fields. A proof record had clicked a `<details>` box itself, so its trace taught nothing. Fixed: `learn_fields`, section clicks dropped |
| **#59** | 8 | **8/8** | 64 | 5 min 41 s | record 1's trace could not be compiled (the agent clicked the table **row**), so **record 2's trace was used**: trying every trace rescued the run. Fixed afterwards: rows are found by the value they contain |
| **#60** | 8 | **8/8** | 45 | 3 min 47 s | the final code of 9 Oct. Both proof records taught their branch fields. 5 plan records at **1 call each**. The cheapest and fastest Expense run |

One more bug from this round worth remembering, **H18**: the row locator looked right in Python and found nothing in the browser.
Python's `re.escape` writes `\-`, which the browser's regular expressions reject; and a row's text runs its cells together
(`EXP-10202Krish`), so "the number followed by a word boundary" never matched. Lesson: **test what the browser does, not what Python
does.**

So by the evening of 9 Oct: **four sites, all decided correctly from an empty cache**, the Credit Desk by the recipe and the other
three by the plan engine. Each new site needed general fixes, never site-specific ones. But the samples on the two newest sites were
small (8 and 16 records).

---

## 7. "Right by luck", run #61's guessed fee, and trust per kind of record

This was the most important finding of 9 Oct, and it drove the whole next round.

### 7.1 Run #55: right by luck

In run #55 (Returns, 8/8), two refunds were decided by the plan **without** the item page. The item page says whether the item is
"final sale" and gives its warranty days. Rules 2 and 3 need that. The plan's route had been learned from a "wrong item" return,
which never opens the item page. The two refunds were correct only because neither item happened to be final sale or out of
warranty.

A test that only checks the answer would never show this. Reading what the answer was **based on** did.

**Fix: the per-rule `known` check (H8).** For every rule, the model now says whether the pages show what that rule's condition
needs. A rule it cannot settle that comes **before** the rule that applies is an error, and the live agent does the record. (A
rule after the one that applies does not matter.)

### 7.2 Run #61: the guessed fee, and a healthy plan dropped

Run #61, Returns, 16 records, 9 Oct. **15 of 16.** Two things went wrong.

**(a) The guessed fee.** The plan was proven on three "wrong item" returns, which only need the order page. It was trusted. Then it
decided a **change-of-mind refund** and **guessed a 15% restocking fee** (the right one was 20%) without ever opening the policy
table. It entered 2,108 instead of 1,984. The model's own `known` answer said yes, it could see what it needed. It could not.

**(b) A healthy plan dropped.** Every other kind of return correctly refused (its pages lacked the item or policy page). But each
refusal counted toward an older safety rule, "the plan failed 3 records in a row, drop it". So the plan was dropped, and **11
records ran on the live agent: 427 calls, about 20 minutes**. The main model's daily limit was reached on both keys during this run.

### 7.3 The fixes

- **A refusal for missing pages is not a failure** (H19). It does not count toward "3 in a row".
- **A proof record whose dry run refused** for missing pages now **extends** the plan from the live agent's route, instead of
  counting as an inconclusive dry run.
- **Trust per kind of record** (H20). Being trusted is not enough. A trusted plan now tracks **which kinds of record** it has been
  checked on:
  - a **kind** (in the code: `branch`) is the rule that applied plus the choices made, for example `rule 4: disposition=Refund` or
    `rule 1: outcome=Reject | reason=Late submission`. Amounts and free text are not part of it;
  - a proof record that agrees marks its kind as checked;
  - a trusted plan that meets an **unchecked kind** decides it, writes down what it *would* enter, and **stops before typing
    anything**. The live agent does the record, and the two are compared. Same values: that kind is trusted from now on. Different:
    that kind always goes to the live agent.
  - So each kind costs one live record the first time it is met, and a plan never decides a kind it has not been seen to get right.

### 7.4 Run #62: trust per kind, live

Run #62, Returns, 16 records, on the **second model** (the main one's daily limit had been reached in #61).

- Unchecked kinds stopped before typing and went live. Three kinds were checked and trusted. The plan was extended with the item
  page.
- It **caught two guesses before anything was saved**: an "out of warranty" decision without the item page (the live agent rightly
  refunded), and, after the extension, a full refund **with no restocking fee** without the policy page (7,680 was right).
- The second catch was a proof disagreement, and the rule then was "a disagreement throws the plan away". So the whole plan was
  thrown away, and the run was aborted to save quota.
- **14 of 14 decided were correct** (2 not done, aborted). 282 calls.

Fix (H21): a proof disagreement whose live route read pages the plan does not now **extends** the plan instead of throwing it away.
The plan was not wrong; it was blind.

### 7.5 The gap that stayed open

A kind is "the rule plus the choices". So "change-of-mind refund" covers both an **unopened** return (no fee, no policy page needed)
and an **opened** one (fee from the policy table). If the unopened one is checked first, the kind is trusted, and an opened one could
then be decided without the policy page, if the plan still lacks it. The only guard left was the model's own `known` answer, which
had said yes without the facts **twice** on 9 Oct.

Two candidate fixes were written down that evening: (1) extend the plan whenever *any* live route reads a page type it lacks, not
only on refusals; (2) make the model **quote**, for each rule, the exact page text it used, and check the quotes are on the pages.
Both were built in the next round.

### 7.6 What 9 Oct taught, in one place

From `docs_old/11_LEARNINGS_9_OCT.md`:

1. A scripted trace is not the agent.
2. A modern web app breaks assumptions that server pages never test.
3. Look for the stable thing the site gives you (names, not ids; "the page reached by the record's own link").
4. Do not let one tidy record define the whole form; proof records are free information.
5. Trusted is not the same as proven for this record. A model's "I can see what I need" is a weak guard.
6. "Right by luck" is a bug.
7. Every fix can break something next to it.
8. Do not run a test that resets a site while a run uses it.
9. Abort early, and make the abort real. (The abort did not stop records queued behind the learning lock; fixed, H15.)
10. Small batches are enough to find what breaks.
11. Write numbers after you have them, never before.
12. The agent never clicks the same thing twice the same way.
13. Test what the browser does, not what Python does.

---

## 8. The general-purpose round (10 to 11 Oct)

### 8.1 Why: what 9 Oct left unsolved

The goal at this point: make it a **general** browser automation agent that **caches as much as possible** and calls the model
**only when needed**, because real work repeats. Measured against that, 9 Oct left these problems:

| What happened on 9 Oct | What it showed |
|---|---|
| Run #61: a trusted plan guessed a 15% fee it could not see | the model's answer was used without checking its numbers were on the pages |
| The model twice said `known: true` when it could not see what it needed | the model's report on itself is a weak guard |
| A trusted plan record still cost 0.3 to 1.7 calls (a slot call and 2 or 3 votes per group) | only the **route** was cached, never the **decision** |
| A record counted as done because the plan pressed Save | nobody looked at what was saved |
| A plan read only its first record's pages; every other branch cost a live record (in #61, 11 records and 427 calls) | the plan did not read what the **instructions** say to read |
| With 8 browsers, Gemini answered "429 per minute" dozens of times | the key pool reacted to the limit instead of staying under it |
| The four sites had no sign-in, pop-ups, iframes, endless lists or custom widgets | the engine had never met what real web apps put in the way |

The rule for the round: **a model call only where needed; anything cached is checked before it is trusted; when unsure, refuse and
let the live agent do the record (never type or save a guess); never press a saving button twice.**

### 8.2 How the work was organised

This round was done by **parallel helper agents** in the background. The method, to show how much it can be trusted:

1. **Seven pieces were built in parallel**, each by its own helper agent, each in its own new file where possible.
2. **Each piece was reviewed by an independent skeptic agent**, whose job was to break it, not to approve it. The builder then fixed
   what the skeptic found, with a test for each finding.
3. Then the pieces were **wired in one step at a time** (they all touch the same core files): learning (`trace.py`, `plan.py`), then
   running (`plan_exec.py`), then orchestration (`orchestrator.py`). Each wiring step was reviewed by a skeptic too.
4. A **cross-cutting safety review with four lenses**, each finding re-checked by a skeptic before fixing:
   - can a guessed or unverified value ever be saved?
   - can a saving button ever be pressed twice for one record?
   - do the AP, Returns and Expense desks still work exactly as before?
   - does any path make more model calls than it needs?
5. Docs were updated (old doc 12).

Examples of real bugs the skeptics found **before** anything was wired in:

- a table lookup that treated "1 Year" and "1 Month" as the same row (both start with 1);
- a table cell that wrapped onto two lines being split into two rows;
- a program check that accepted wrong dates;
- a pop-up dismisser that could have pressed "OK" on a **real confirmation**;
- a read-back that ignored the word "not" (it would have read "Not approved" as "approved").

At the end, **about 900 test checks passed across 12 test files** (892 counted on the night of 10 to 11 Oct, against 188 in the 4
files before the round). **None of them called the real model.** Every model in those tests was a fake: a function that answers from
the test site's own data.

### 8.3 Piece 1: pacing (keypool.py)

**Problem.** The free tier allows **15 calls per minute per Google project and model**. With 8 browsers running live records, the
run hit 429 dozens of times; each cost a 45-second cool-down and a key switch.

**What it does.** The pool now **counts before it sends**. For every (key, model) it keeps the times of the calls made in the last 60
seconds (a "sliding window").

- `GEMINI_RPM` sets the limit per key and model per minute. **Default 14** (one under Google's 15, to leave room for clock
  differences). `GEMINI_RPM=0` turns pacing off (for paid keys). A blank or bad value falls back to 14 with a warning (bug J9: a bad
  line once could stop the server starting).
- `paced_pick`: pick a key, book a slot in its minute. If that key is full, try every other key. Only when **no** key has room, sleep
  until the soonest one frees up, then try again. It never sleeps while holding the pool's lock.
- The minute is counted on a steady clock, so changing the computer's clock lets no extra call out.
- Every caller uses it: the live agent, the small text calls, the program and coverage questions, and "write the instructions for
  me", so a click in the web app cannot steal a slot a running batch counts on.

**Example.** Two keys in two projects: about **28 calls a minute** in total. A ninth browser does not get a 429; it waits its turn.
A record that waits is slower, not wrong.

Also in this piece: the live agent no longer runs browser-use's **"judge"** (J10), which asked the model once more at the end of every
live record, with screenshots, for a verdict nothing in v3 read.

**Tested**: 45 checks with a fake clock and fake model clients (a 40-call burst at 14 a minute takes 2+ minutes of fake time; 8
threads booking 400 calls at once get exactly 14).

### 8.4 Piece 2: decision programs (recipe/facts.py, recipe/program.py)

**Problem.** The route was cached, the decision was not. Every trusted record still cost about 1 call.

**The idea.** After a few verified records, the model writes the decision **once**, as a small **program**: named **facts** (read off
the pages by code) and ordered **rules**. Code then runs it for every record, with no model call. This is close to "level 3" from the
original `FUTURE_PLANS.md`: "learn once, compile to a script, check it against the AI".

A program looks like this (shape, Returns Desk):

```
facts:
  fee_pct: number, from the table on /policy, row where Category = {category}, column "Opened"
  final_sale: from the label "Final sale" on the item page
  ...
rules (first true wins):
  1. "final sale":       when final_sale            set disposition = Reject, reason = Final sale
  ...
  n. "otherwise refund": when true                  set disposition = Refund,
                                                        refund_amount = {= qty * price * (100 - fee_pct) / 100 }
```

**Facts are read by code** (`facts.py`): from a record field; the value next to a **label** on a page type; a **table** cell picked by
other cells (equal, contains, in, or less/greater than on numbers and dates; first, sum, count, list, max or min of matching rows); or
a fixed text. Numbers understand `₹1,25,000`, `20%`, `USD 120`, `45 days`. Dates understand `19 Oct 2026`, `05 Sept 2026`,
`2026-10-19`, and `10/19/2026` only when day and month cannot be confused.

**Rules** use a small expression language, parsed with Python's `ast`: exact decimals, `and or not`, comparisons, `in`, and only a
fixed list of functions (`abs min max round floor ceil len lower upper days_between add_days contains startswith join`). Anything
else is refused.

**Running** (`run_program`): read every fact, try the rules in order; the first true rule gives the values. A rule that needs a fact
that is **not on the pages stops the program** for this record. It never skips to a later rule (that would be a guess).

**Learning** (in the orchestrator):

1. Keep **verified examples** per workflow: proof records whose dry run agreed with the live agent, and plan records the model decided
   that saved. The newest 12 are kept.
2. With **4 or more**, one record at a time asks the model to write a program from up to 6 varied examples.
3. Code checks it: every value it sets is a field of the form; a fixed fact may not hold a number the instructions do not print (a
   rate the examples happened to share); and **the program must give back every example's values exactly**.
4. If not: **one** retry, listing the differences. Still wrong: no program (asked again only after 2 more examples, at most 3 times
   per batch).

**Trust, per kind of record.** The program runs beside the model ("shadow") while its kind is not trusted. The model decides; the two
are compared. A kind is trusted after **2 agreements and no disagreement**. One disagreement: that kind is never decided by the
program. Two disagreements: the program is dropped. An agreement only counts when the record really saved (or the live agent agreed).
Once trusted, the program decides that kind with **no model call**, and every **10th** use is a **spot check** (the model decides too,
and they are compared).

**Tested**: 108 checks in `test_program.py` with fakes. Against the real Returns and Expense sites, **hand-written** programs decided
all 8 kinds of return, and 6 claims of every Expense outcome through the 3-step wizard, with **zero model calls**, and the sites saved
the right values. **A program written by the real model was never tried in this round.**

### 8.5 Piece 3: grounding (recipe/evidence.py)

**Problem.** The 15% fee. The model's numbers were used without any check that they came from the pages.

**What it does.** Before anything is typed, every model decision (and every program decision) is checked by code, for the rules up to
and including the one that applies:

- every **number** in the formulas and values must be printed on the record's pages, in the record, or in the instructions, or be
  **one step of arithmetic** from such numbers (`2480 = 4 x 620`);
- a **percentage or rate** must be printed itself. Why: small numbers that every page has (a pager's 5 and 10) would put almost any
  guess one step away: 15 = 10 + 5;
- every **date** in the values must be on the pages or in the record;
- the model now gives **evidence**: word-for-word **quotes** of the page text each rule's condition uses. Every quote must be on a
  page, and a rule decided as plain true or false (a status) must give at least one quote that is there.

Any problem refuses the answer: *the pages do not show what this answer uses: ...*. That is treated as a branch the plan has not
learned: the live agent does the record, and its route extends the plan.

**Example.** The 9 Oct guess `4*620*(100-15)/100`, with no 15 on any page, is refused (a test case in `test_evidence.py`). The honest
version, with the fee on the policy page, passes.

**The known cost**: it can refuse a **right** answer (a number two steps of arithmetic away, a rate written in words). That costs a
live record, never a wrong save.

**Tested**: 85 checks, no site needed.

### 8.6 Piece 4: real-world page handling (recipe/webkit.py)

**Problem.** The four test sites were friendly. Real apps have sign-ins, pop-ups, frames, endless lists and custom widgets.

| Hazard | How it is learned | How it is run |
|---|---|---|
| A **sign-in**, and sessions that run out | the steps on the page where a password was typed become `plan["login"]`; a value typed word for word from the instructions (username, password) is a **constant**; a password the plan cannot type again refuses the plan | landing on the login page unexpectedly replays the login and goes back, at most 2 times per record |
| **Cookie banners, "What's new" pop-ups** | a click named like Accept / Got it / Close that did not change the address is `optional` | visible overlays with such a button are closed before every step (on a cookie banner, "Reject non-essential" is preferred); **never** a Save / Submit / Delete / Pay / Confirm button, never anything outside an overlay |
| **Error box with Retry** | a Retry click is optional | a missing element or wrong page type presses Retry once and tries again |
| **Endless lists** (scroll, "Load more") | "Load more" is paging, like Next | the list is grown (Load more, else scrolling) until the record's row shows, up to 15 times |
| **Forms inside iframes** | | elements and controls are looked for in every frame; page reads add each frame's text |
| **ARIA radio groups and checkboxes** (no native input) | keyed by the group's name | set by clicking the option and checking `aria-checked` |
| **Searchable comboboxes** (options in a portal; typing alone chooses nothing) | a click or typing on the box followed by a click on an option is **one** control | type, wait for options, click the match, check the box shows it |
| **Rich-text notes** (`contenteditable`) | a control of type `richtext` | filled like a note |
| **Route buttons that change something** (Start review) | marked `saves` | never pressed in a dry run; pressed once in a real run, never again after a re-login |
| **A wizard's Next** | | a dry run **never presses Next** (J11: a Next may save a draft) |

**Tested**: 99 checks in `test_webkit.py`, in a headless browser on pages the test writes itself; plus 58 in `test_plan_hazards.py`
(below).

### 8.7 Piece 5: read-back (recipe/readback.py)

**Problem.** "Done" meant "the plan pressed Save".

**What it does.** After a real save, when the plan knows the record's own page, it **opens that page again, fresh from the site** (never
the success screen), and looks for the values it entered: numbers by value, dates by day, choices by word stems (`Approve with
deduction` matches `Approved with deduction`, but not `Not approved`), short text by its words. Labels, buttons and form options are
left out, so a form that still lists every option does not count (J14).

**Does this site show saved values at all?** That is learned on proof records: after the live agent saved, its values are looked for on
the record's page. Two checks that agree decide it. Values the site never shows (even for the live agent's own save) are kept as
"hidden".

**If a value read-back has seen confirmed before is missing**, the record **fails** ("The plan saved, but the record's page does not
show ..."). It was saved, so it is **never retried**. Three such records in a row drop the plan. Read-back costs no model call.

**Tested**: 83 checks, in a headless browser on pages the test serves itself.

### 8.8 Piece 6: coverage and pruning (recipe/coverage.py)

**Coverage. Problem**: a plan reads only what its first record read, so every other branch costs a live record first. **What it does**:
when a plan is new or extended, on its next proof record the model is asked **once**: which pages do the instructions say to read that
this route does not open, and which link on a page the route reads leads there? Code checks each answer (the page is on the route,
the link text is on it) and adds up to 4 side trips: a click and a `revisit` back. Log note: *Added from the instructions: ...*.

**Pruning. Problem**: plan records replay pages no decision uses. **What it does**: after each decided record, count which page types
supplied a fact (the program's facts, or the pages the evidence quotes were found on). A side trip to a page type that supplied
nothing in **12 or more** decisions is skipped. If a record run with skips is refused for missing pages, the counts reset and the
record runs the whole route once more, before any live fallback.

**Proactive extension** ("extend on agree"): a proof record that **agreed**, or a new kind the live agent confirmed, whose live route
read page types the plan lacks, now extends the plan too. Before, only refusals and disagreements did. (This was candidate fix 1
from section 7.5.)

**Tested**: 80 checks; in a browser, a record that cannot be decided before coverage, and can after.

### 8.9 Piece 7: a fifth test site, the Claims Desk (`testsites/claims-desk`, port 5183)

An insurance claims desk built to have the hazards of real web apps. In the code and some notes it is called the "Hazards" site.

- A **sign-in** (local test account) whose session **runs out after 25 page loads or 10 minutes**, sending the browser to
  `/login?expired=1&next=...`.
- A **cookie banner** on every new browser, and a **"What's new"** modal once per session.
- A claims list that **loads as you scroll** (20 rows at a time, "Load more" as a fallback), no search box, clickable rows.
- About **1 list load in 15 fails** with a Retry box.
- **Slow answers** (300 to 1,500 ms) with "Loading…".
- The claim page links to its **policy** (a link whose text is an id) with a coverage table.
- The decision form is **inside an iframe**: an **ARIA radio group** (Approve / Approve partially / Decline / Request information),
  a payout number, two **searchable comboboxes** whose options appear in a portal, and a **contenteditable** note. Control ids are
  random on every draw.

100 claims: 32 Approve, 22 Approve partially, 20 Decline "No cover on loss date", 16 Decline "Peril not covered", 10 Request
information "Police report", with edge cases (a loss on the policy's first or last day, an amount exactly at the limit, a lapsed
policy).

**Tested**: a smoke script drives it with Playwright through five outcomes. The plan engine was tested on it with a **scripted** trace
(`tests/make_hazards_fixture.py`) and a fake model: 58 checks in `test_plan_hazards.py` (sign-in and its expiry, banner, pop-up, Retry,
a deep row, every outcome saved, read-back, a skipped side trip). **The live agent has never been run on it, and no v3 workflow for it
has ever been run.**

### 8.10 Also built: slots read by code

**Problem**: every plan record paid a call to copy values (an order number) from a page. **What it does**: when the model reads a
slot, code proposes a rule for it: the label it is printed next to, and its shape (`ORD-99751`: three letters, a dash, five digits).
Each later model reading is compared with the code's reading. After **3 agreements**, code reads it with no model call (every 10th
reading goes to the model again). One disagreement deletes the code.

### 8.11 The wiring

The pieces then had to be used by the engine. Three steps, each reviewed:

1. **Learning** (`trace.py`, `plan.py`, plan version 4): logins and constants, optional pop-up clicks, Load more as paging, ARIA and
   combobox controls, rich text, the record's own page (`own`), route clicks that change something (`saves`), a plan with no form
   (one button on the record's page).
2. **Running** (`plan_exec.py`): frames, pop-ups, Retry, signing in again, growing lists, grounding, decision programs (`program_mode`
   off / shadow / use), slots by code, read-back, skipped side trips. A record's outcome now carries who decided it (`decided_by`), the
   program comparison, the example it produced, the pages it used, and the read-back result.
3. **Orchestration** (`orchestrator.py`): coverage on a new plan; keeping verified examples; when to learn a program (one record at a
   time); trusting and dropping it; spot checks (J13: records running together would all have taken the same spot check, so the one
   that takes it now claims it at once); slots by code; the read-back verdict; pruning; proactive extension; a batch-end note (*The
   decision program decided N record(s) with no model call; the run made M model call(s) in total.*).

### 8.12 The planned cost after the round (design, not measurement)

| Path | Calls per record |
|---|---|
| Live agent (teaching, fallback) | about 8 to 25 (measured on 9 Oct) |
| Trusted plan, the **model** decides | shared per group of 5: 1 slot call (0 once code reads slots) + 2 votes (3 on a dispute); 0.62 per record measured on AP |
| Trusted plan, the **program** decides | **0** |
| An unchecked kind of record | the plan's question + the live agent |
| Read-back, pruning, slots by code, the program itself | 0 |

So the design promised: a long run of a repetitive task ends with most records at 0 calls. **That had not been measured live.** The
next section is what happened when it was.

---

## 9. The live runs after wiring, and why they were worse

### 9.1 The setup

On the night of 10 to 11 Oct, with every new piece switched on, five **fresh** runs of **16 records** were made on the **Expense Desk**
(workflow 36): runs **#63 to #67**. Same 16 claims each time, empty cache, site reset. The Expense Desk was chosen because the 9 Oct
engine had a clean record there (#58: 16/16; #60: 8/8), so any change would be the new code's doing.

### 9.2 The results

| Run | Correct (grade.py) | Modes (from the log) | Model calls | Time | Programs learned |
|---|---|---|---|---|---|
| #63 | **15/16** | 1 cold, 12 live, 3 plan | 211 | 9 min 13 s | 0 |
| #64 | **15/16** | 1 cold, 15 live, 0 plan | 243 | 10 min 18 s | 0 |
| #65 | **15/16** | 1 cold, 12 live, 3 plan | 231 | 9 min 32 s | 0 |
| #66 | **12/16** | 1 cold, 12 live, 3 plan (3 failed) | 256 | 11 min 16 s | 0 |
| #67 | **14/16** | 1 cold, 14 live, 1 plan (1 failed) | 287 | 16 min 13 s | 0 |
| *#58, 9 Oct engine, for comparison* | *16/16* | *1 teaching, 4 proof, 11 plan* | *97* | *7 min 23 s* | *(did not exist)* |

In words: **71 of 80 right (89%)**, against 16 of 16 before. More than **twice the calls** (211 to 287 against 97). Most records ran
on the **live agent** (12 to 15 of 16) instead of the plan. And the main goal of the round, records decided by a program with zero
calls, happened **zero times**: every run ended with *The decision program decided 0 record(s) with no model call*.

(The per-run scores are as recorded at the time; the `grade.py` output itself was not saved, so which exact record was wrong in #63,
#64 and #65 is not in the log.)

### 9.3 What each run showed (from its log)

**Run #63 (15/16).** The plan was learned and trusted after 3 agreeing proof records. Then the "new kind" check went wrong for
**Send back** and **Reject**: *attest: the plan would enter None but the live agent entered 'I have checked this claim against the travel
policy'*. `attest` is the **confirmation tick on the wizard's last step**. The plan, in a dry run, never reaches that step (a dry run
never presses Next), so it had no value for it, and that was counted as a **disagreement**. Result: those kinds were marked "the live
agent keeps doing this kind". Three attempts to learn a program failed: *fact missing_receipt is not on the pages*.

**Run #64 (15/16).** Same `attest` bug, but this time on a **proof** record: *The plan disagreed with the live agent... It is thrown away;
every other record uses the live agent.* So 15 of 16 records ran live. One proof record was also refused by **grounding**: *the pages do
not show what this answer uses: rule 3 ("Work out the hotel's cost in rupees...")*.

**Run #65 (15/16).** Trusted after 3 proofs; most kinds checked and trusted. One program attempt failed on syntax the language did not
have: *cannot read "if(hotel_currency == 'INR', hotel_amount, hotel_amount * fx_rate) > nightly_limit * nights"*. And on record
EXP-78591: *outcome: the plan would enter 'Approve' but the live agent entered 'Send back to employee'*. That record's kind was then left
to the live agent, and the plan was extended because the live agent had visited the Summary tab.

**Run #66 (12/16).** The worst run. Three plan records failed **after acting**:

```
The plan pressed save but could not confirm it worked: the control 'Back' could not be set to 'Approve with deduction'
The plan pressed save but could not confirm it worked: the control 'Back' could not be set to '2026-09-13'
The plan pressed save but could not confirm it worked: the control 'Back' could not be set to '2026-09-11'
```

A **"Back" button pressed inside the wizard** by the live agent had been **learned as a form field**. The plan tried to "fill" Back with
a value, failed, and because it had already pressed Next (which counts as acting), the record could not be retried. Then *The plan failed
3 records in a row, so the rest use the live agent.* Programs failed three times, once with *rule 3 (hotel above limit) applied and gives
deduction = '490', but the correct value is '' (left empty)*: the examples were incomplete.

**Run #67 (14/16).** A different first trace gave a plan with 6 form fields. Programs failed with *the program sets 'q', which is not a
field of the form*: `q` is the **search box**, which had leaked into the examples as if it were a decision field. One plan record failed:
*The plan pressed save but could not confirm it worked: the model's answers did not agree (the rule that applies gives no values to
enter)*: it had pressed Next, and then the next step's decision failed, so the record was lost. Grounding also refused a proof record on
*"Any receipt is Missing"*.

### 9.4 The bugs found live, all fixed (with tests)

| Bug | Seen in | Fix |
|---|---|---|
| **Facts on a second tab were not read.** Tabs keep the address, so "the page" had several views (Summary, Receipts). Only the latest view was searched for facts and for evidence quotes | grounding refusals in #64, #66, #67 look like this (my reading of the log: the receipts and hotel facts sit on another tab) | facts and quotes are looked for in every view of a page |
| **"Back" pressed inside a wizard was learned as a field** | #66, three records stopped after acting | Back is never a form control |
| **Program examples missed the wizard's later steps** | programs "wrong" against incomplete examples (#66: `deduction = '490'` vs empty) | examples carry every step's values |
| **A confirmation tick on a later step counted as a disagreement** | #63, #64 (`attest`) | a value the dry run could not reach is not compared as a difference |
| **`{= }` and `if()` inside programs** | #65 | the expression language reads both |
| **Fact defaults**: "no INR row in the rate table means rate 1" | programs could not decide rupee claims | facts can carry a default |
| **Empty lists** (no missing receipts) | facts read as "not found" instead of empty | an empty list is a value |

### 9.5 Why the new engine was worse, not better

Putting the logs together, five reasons. The first three are measured; the last two are a reading of the logs.

1. **Most records ran live.** Trust per kind of record was new on this site (it was added after #60). Every kind (Approve, Approve with
   deduction, Send back, Reject) cost a live record the first time. With 8 browsers running at once, several records of the same
   unchecked kind were often in flight together, and the logs show the same kind being "checked" several times in one run (Approve was
   checked on 4 different records in #63). Live records are where calls go (15 to 25 each) and where the live agent's own mistakes
   enter.
2. **New bugs from the wiring cost records directly**: the `attest` comparison (#63, #64, which threw a whole plan away), "Back" as a
   field (#66, three records), a lost record after Next (#67).
3. **No decision program was ever learned live.** Every attempt failed, so the round's main saving never happened, and each attempt
   (1 to 3 calls) was pure cost. The reasons were incomplete examples (later wizard steps missing), stray fields (`q`), facts that
   could not be found on the pages, and syntax the language lacked (`if()`).
4. **The referee is not always right.** The plan is checked against the live agent. In #65 and in the final check #68 (section 10),
   the live agent chose **Send back** where the plan chose **Approve**. In #68 the plan had the right answer. When the referee is wrong,
   a correct kind is marked "live only", and the live agent's wrong answer is what gets saved. Old doc 12 counted **3 of the misses in
   those runs** as the live agent's own wrong answers.
5. **Grounding refused some right answers.** Each refusal sends a record live (costly, never a wrong save). Some of these were the
   second-tab bug; some were the check being strict (a number more than one arithmetic step away).

**The offline contrast.** Away from the live runs, the model was given **6 recorded records** and asked to write a program. It wrote a
**correct program on its first try, twice**. So the idea is not wrong. Live, the examples the engine collected were not yet clean enough
to learn from.

---

## 10. Option B, and where things stand now

### 10.1 The decision (11 Oct)

Five runs, each finding new bugs, and the new parts making the engine **less** reliable. Two choices: keep debugging everything live
(expensive in quota, and the referee itself is noisy), or **switch off what has not proven itself live** and keep what has. The choice
was **option B**: the second.

### 10.2 What is off by default

These six parts are **off** unless switched on:

| Switch name | What it turns on |
|---|---|
| `programs` | learning and using decision programs |
| `coverage` | the one question that adds side trips the instructions name |
| `pruning` | skipping side trips that never supply a fact |
| `readback` | opening the record's page after saving to check the values |
| `slot_code` | slots read by code after 3 agreements |
| `extend_on_agree` | extending the plan from agreeing proof records |

How to switch them on: the environment variable **`TA_FEATURES`**, comma separated, or `all`. For example:

```bash
TA_FEATURES=programs FORCE_HEADLESS=1 uvicorn main:app --port 8002
TA_FEATURES=all FORCE_HEADLESS=1 uvicorn main:app --port 8002
```

In the code these are `FEATURE_NAMES` and `FEATURES` near the middle of `orchestrator.py`, read once at start-up, with a helper `_on(name)`
that every optional path checks. The variable is read when the server starts, so changing it needs a server restart.

### 10.3 What stays on

- **Pacing** (keypool).
- **Grounding** (`recipe/evidence.py`).
- **Real-world page handling** (`recipe/webkit.py`: frames, pop-ups, Retry, logins, endless lists, ARIA and combobox controls).
- **Every fix found in the live runs**: facts on every view of a page, Back in a wizard, the confirmation-tick comparison, `{= }` and
  `if()`, fact defaults, empty lists.

With the six switches off, the engine behaves like the 9 Oct engine (the one that scored 16/16), plus those safe additions.

### 10.4 The final live check: run #68

11 Oct, Expense Desk, fresh, **8 records**, defaults (the six parts off):

| | |
|---|---|
| Correct | **7/8** |
| Calls | **110** |
| Time | 7 min 29 s |
| Modes | 1 cold, 5 live, 2 plan |
| Log | plan learned (11 steps, 3 form fields); trusted after 2 agreeing proof records, which taught it the deduction and reason fields; Send back and Approve checked as new kinds |
| The miss | EXP-86480: *the plan would enter 'Approve' but the live agent entered 'Send back to employee'*. The live agent did the record (it was an unchecked kind), so its answer was saved. **The plan had the right answer.** |

For comparison, #60 on 9 Oct: 8/8 at 45 calls. #68 cost more because each new kind still goes live once (trust per kind), and the one miss
came from the referee, not the plan.

### 10.5 Open problems, in order

From old doc 12, section 0:

1. **No program has been learned live.** Offline it works from clean examples. Live, the examples were incomplete or held stray fields.
2. **A record can be lost inside a wizard**: Next was pressed (that counts as acting), then the next step's decision failed. Since
   acting is never retried, the record is lost (#67).
3. **The live agent is sometimes wrong**, and it is the referee the plan is checked against. Proof, trust per kind, and program trust
   all assume the live agent is right.
4. **Grounding sometimes refuses a correct answer.** It costs a live record, never a wrong save.

And never tested live at all:

- The **Claims Desk** (site 5): no live run, no workflow run, the live agent has never seen it.
- **Read-back, coverage, pruning, slots by code** against the real model.
- **Pacing** has not been measured against the real per-minute limit. It was on during #63 to #68, but nobody counted how many 429
  answers came back with it on.
- **100-record runs** of the Returns Desk and the Expense Desk. Only 8 to 16 were ever run live.
- A second sign-in factor or CAPTCHA (the engine never tries them), shadow DOM, frames from another site, pop-ups with other button names.
- A real website that we did not build. **All five sites were written by us.**

### 10.6 The plan going forward

The plan is to **rebuild from scratch, in a new folder, one function at a time**, to understand every part. The old v3, its ~900 test
checks, and the five test sites stay as the reference. Section 16 suggests an order that follows the story above.

---

## 11. Every lesson, numbered, with the event that taught it

These are the lessons worth carrying into the rebuild. Each one is tied to the moment it was learned.

### About the model

1. **Do not ask a small model to do arithmetic or decide rule order.** Let it read, copy, and translate rules into formulas; code
   computes and picks the first true rule. *Taught by*: the Credit Desk model writing "65000 <= 65000: false", and "53 > 30: true"
   followed by Approve (A2, A3).
2. **"Similar" is the dangerous word.** Loose matching is where a small model invents. Give it an exact-text fact to follow. *Taught by*:
   `KulkarniFootwear` matched to `Kulkarni Footwear`, 6 of 8 misses in the first 100-record run (A1).
3. **Use the user's own words.** The button for a rule is what the user wrote after the colon. *Taught by*: right rule, wrong button on
   records 20 and 83 (A4); and word overlap losing to a long sentence until Jaccard was used (A5).
4. **Order matters in the answer.** Reasoning first, choice last; force the shape with a schema; reject blank reasoning. *Taught by*:
   recipe v1 at 8/10 (A7), the model answering only the first of 5 records (A8).
5. **Randomness hides bugs; vote and repeat.** *Taught by*: the same code scoring 92, 95, 96, 99, 99, 99 with a different miss each time
   (A6).
6. **A model's word about itself is not a check; its quotes are.** *Taught by*: `known: true` while the fee table was never read, twice
   on 9 Oct (H8, J2).
7. **Make the model's job smaller each time something fails.** Arithmetic, ordering, "which button", "which row", "copy this value"
   each moved to code in turn. *Taught by*: the whole accuracy ladder (section 3.6) and the slot rule "must appear word for word on a
   page" (section 4.4).

### About caching

8. **Cache *where*, not *what*.** Locators repeat across records; answers do not. *Taught by*: the Credit Desk getting nothing from the
   v2 cache (section 2.7).
9. **A cache that is wrong without failing is the most dangerous kind.** *Taught by*: run #41, nine "successful" replays of record 1's
   invoice, caught only because the site refused the incomplete post (section 4.2).
10. **Never point a learned step at the first record's own item.** Drop any locator that names one particular thing. *Taught by*:
    `href="/invoices/e57b1a67"` in run #41.
11. **Prove a learned procedure before trusting it, and do it before the agent touches the page.** Otherwise the procedure can copy the
    agent's saved answer. *Taught by*: the plan in #42 storing a vendor name as a fixed label (F3, F4).
12. **Throw away expensively only for the right reason.** Different values: drop. An error, or a refusal for missing pages: not a
    reason to drop. *Taught by*: F8 (an erroring dry run dropped a good plan) and run #61 (refusals counted as failures, 11 records live,
    427 calls).
13. **Trusted is not the same as proven for this record.** Trust per kind of record, checked once per kind. *Taught by*: run #61's
    guessed 15% fee on a kind the plan had never been checked on.
14. **"Right by luck" is a bug.** Look at what an answer was based on, not only at the answer. *Taught by*: two refunds in run #55
    decided without the item page.
15. **Proof records are free information.** Learn the fields (and pages) of their branch. *Taught by*: run #58, where every non-approval
    needed a second question (H13).
16. **A blind plan is not a wrong plan.** If the live route read pages the plan does not, extend it instead of throwing it away. *Taught
    by*: run #62 (H21).

### About sites and traces

17. **Test on a site you did not tune against.** *Taught by*: the recipe at 100% on the Credit Desk and the first try on AP Desk at 10%.
18. **A scripted trace is not the agent.** Test a compiler on real traces as early as possible. *Taught by*: the first real Returns trace
    finding five compiler bugs at once (runs #53, #54).
19. **The agent never clicks the same thing twice the same way.** Try every saved trace; find elements by what they contain. *Taught
    by*: on the same claim, the agent clicked a cell once and a row the next time (#59).
20. **Look for the stable thing the site gives you.** Names, not ids; the page reached by the record's own link is the record's page.
    *Taught by*: the Expense Desk's random ids and digit-less addresses (H10, H12).
21. **A modern web app breaks assumptions that server pages never test.** Hash routes, tabs at the same address, collapsed sections,
    modals with no form, wizards. *Taught by*: run #56.
22. **Test what the browser does, not what Python does.** *Taught by*: the row locator that worked in Python and found nothing in the
    browser (H18).
23. **Every fix can break something next to it.** *Taught by*: the detour cut eating "Review claim" (H7).

### About success and checking

24. **A success is a claim.** Check what reached the server (change tracker), what the record shows, and what the database says
    (verification). *Taught by*: two records "successful" with nothing saved because the browser closed 0.4 s after the click (B1); the
    false success in the first 36-record run.
25. **Never press a saving button twice.** After the first saving click (or a wizard's Next), a failure is a failure, never a retry.
    *Taught by*: the early "retry with screenshots" running a whole task again on a page that may already be submitted.
26. **The referee must be right too.** Checking the plan against the live agent only works if the live agent is right. *Taught by*: runs
    #65 and #68, where the live agent chose Send back and the plan's Approve was correct.

### About process

27. **When an experiment shows no effect, check the new code actually ran.** *Taught by*: the duplicated answer class (A11).
28. **A cheap scorer pays for itself.** Dry-run decisions with the real model and no live agent. *Taught by*: `score_plan.py` finding
    F5 to F7 in about 30 calls each, where live runs found one bug per 150 calls.
29. **Small batches are enough to find what breaks.** 8 to 16 records; 100 only after a clean small batch. *Taught by*: 9 Oct, when 8
    records found every problem and 16 confirmed the fixes, on a limited quota.
30. **Do not run a test that resets a site while a run uses it.** *Taught by*: two of run #54's decisions wiped (H16).
31. **Abort early, and make the abort real.** *Taught by*: an abort that did not stop records waiting for the learning lock (H15).
32. **Write numbers after you have them.** *Taught by*: "16 of 16" written into the README before run #58 finished (it did end 16/16).
33. **Tested with fakes is not tested.** About 900 checks passed with fake models; the first live runs still found seven bugs and a
    drop in accuracy. *Taught by*: runs #63 to #67.
34. **Build fewer new things at once.** Seven pieces were wired in together, so when the live score fell, no single piece could be
    blamed, and every one was suspect. *Taught by*: the decision to switch six of them off (option B). (This one is a conclusion from
    the round, not a line from the older docs.)
35. **Quota is a design input.** *Taught by*: every day of testing (next section).

---

## 12. Cost and quota

### 12.1 The facts

| Fact | Value |
|---|---|
| Free tier, per day | about **500 calls per model per Google project** |
| Free tier, per minute | **15 calls per minute** per project and model |
| API keys | **2 keys in 2 different Google projects**, so about **1,000 calls a day** on the main model |
| Main model | `gemini-3.5-flash-lite` |
| Fallback model | `gemini-3.1-flash-lite` (its own separate daily allowance; used when every key is out for the main one) |
| Daily reset | **midnight Pacific time** = **12:30 pm IST** until 1 Nov 2026, then 1:30 pm IST |
| Pacing default | 14 calls per key and model per minute (`GEMINI_RPM`) |

### 12.2 Two mistakes about quota, found the hard way

**The reset time (bug F9 / G5, 8 Oct).** The key pool assumed the daily quota resets at 00:00 UTC. Google's docs say midnight
**Pacific**. On 8 Oct at 06:13 UTC both keys returned 429 for the *previous* day's usage (it was still 23:13 on 7 Oct in California),
and a key marked "out for today" at 06:15 UTC stayed marked after Google's real reset at 07:00 UTC, so run #40 used the fallback model
for no reason until the server was restarted. Fix: the pool computes the next midnight in `America/Los_Angeles` (handling daylight
saving, including the 25-hour day on 1 Nov) plus 60 seconds.

**Quota belongs to the project, not the key.** Google says limits apply per project. Two keys from the **same** project share one
quota and add no daily headroom. On 8 Oct both keys ran out together. On 9 Oct they ran out **separately**, well past 500 calls (about
780 counted, and the count misses some calls), which showed they are in **two different projects**: about 1,000 a day.

### 12.3 What things cost

| Thing | Calls |
|---|---|
| Live agent, Credit Desk | about 9 per record |
| Live agent, AP Desk | about 15 per record, about 60 s |
| Live agent, Returns Desk | about 25 per record (paging and side trips) |
| Live agent, Expense Desk | 8 to 19 per record |
| Recipe, 100 Credit Desk records | about 66 in total |
| Plan record, one at a time | 3 (1 slot + 2 votes) |
| Plan record, shared calls | 0.3 to 1.7, depending on group size and whether a later wizard step needs a second question |
| Proof dry run | 2 to 4 extra |
| A fresh 8-record Expense run (9 Oct engine) | 45 to 64 |
| A fresh 16-record Expense run (9 Oct engine) | 97 |
| A fresh 16-record Expense run (all new parts on) | 211 to 287 |
| A fresh 8-record Expense run (option B) | 110 |
| Offline scoring of 100 records | about 62 |

The expensive part is **never** the plan. It is the live agent teaching and proving (and, with trust per kind, checking each new kind).

### 12.4 Calls per day in practice

| Day | What used it up |
|---|---|
| 8 Oct | about 260 calls of offline scoring emptied the main model's day; run #47's proof records then ran on the slow fallback |
| 9 Oct | runs #53 to #61 used about 780 counted calls; the main model ran out on both keys during #61; #62 ran on the fallback |
| 10 to 11 Oct | runs #63 to #67 used about 1,230 counted calls, and #68 another 110 |

Rules that came out of this:

- Test with **small batches** (8 to 16 records). Run 100 only after a small batch is clean.
- Prefer fake-model tests and `score_plan.py` to check decisions and mechanics.
- **Abort** a run that is learning badly instead of letting every record fall back to the live agent.
- Keep `GEMINI_RPM` at 14 on the free tier.
- The fallback model is used when testing before 12:30 pm IST after a heavy day.

---

## 13. The full results table

Every run that tested something, in order. "Fresh" = empty cache and reset site. Correct counts for AP, Returns and Expense come from
the site's `grade.py`; for the Credit Desk from the answer view. Times are from the database (start to finish).

### 13.1 Before the recipe (live agent only, early v3)

| Run | Site | Records | Correct | Notes |
|---|---|---|---|---|
| first test | Credit | 33 | 8 of 9 that landed | 2 browsers; 24 failed from 802 rate-limit errors |
| second test | Credit | 36 | **35 (97.2%)** | 1 browser, 21 min 36 s, 8.3 steps per record; 1 false success |
| third test | Credit | 10 | 10 | headless, 4 min 35 s |
| Supabase test | Credit | 10 | 10 | 9 min 31 s on the second model |
| cache test | DataDesk form | 4 + 4 | 4 + 4 (all 72 fields) | replay: 0 steps, about 3 s per person |

### 13.2 Credit Desk, recipe engine

| Run | Records | Correct | Time | Notes |
|---|---|---|---|---|
| recipe v1 | 10 | 8 | | button before reasoning |
| reason first | 10 | 10 | | |
| first 100 | 100 | 92 | | near-miss matches, boundary sums |
| lookup | 100 | 96 | | live |
| #31 | 100 | 95 | 9 min 35 s | 3 boundary slips, 2 lost saves |
| #34 | 100 | 99 | 9 min 51 s | button from the user's line |
| #35 | 100 | 99 | 7 min 37 s | |
| #36 | 100 | 99 | 4 min 28 s | |
| #37 | 100 | **100** | 6 min 57 s | voting |
| #38 | 100 | **100** | 10 min 21 s | |
| #39 | 100 | **100** | 6 min 4 s | fresh; 7 agent steps; keys ran out, fallback model, no effect |
| #40 | 100 | **100** | 6 min 54 s | |
| #46 | 100 | **100** | 6 min 30 s | fresh, after the plan engine was added; recipe did not regress |

### 13.3 AP Desk

| Run | Records | Correct | Calls | Time | Notes |
|---|---|---|---|---|---|
| #41 | 10 | **1** | 67 | 3 min 59 s | old replay repeated record 1 nine times |
| #42 | 10 | 10 | | 7 min 2 s | plan failed proof; all live |
| #43 | 10 | 10 | | 7 min 49 s | plan corrected, then `abs()`; all live |
| offline | 10 | 7, then 10 | about 30 each | | `score_plan.py` |
| #44 | 12 | **12** | | 6 min 49 s | learned, proven, 9 plan records at 3 calls |
| #45 | 100 | **100** | about 343 | 17 min 38 s | 97 plan records, 3 calls each |
| offline | 100 | 100 | 0.62 per record | 320 s | batched, 10 browsers |
| #47 | 100 | **100** | 126 | 18 min 33 s | shared calls; plan phase 5 min 51 s; proofs slow on the fallback model |
| offline | 100 | 100 | 0.81 per record | | after the Returns prompt changes |

### 13.4 Returns Desk

| Run | Records | Correct | Calls | Time | Notes |
|---|---|---|---|---|---|
| offline | 100 | **100** | 0.64 per record | | decisions only, real model |
| offline | 100 | **100** | 0.68 per record | | final prompt, fallback model |
| #48 | 3 | | | | cut off by a server restart |
| #49 | 6 | **6** | | 2 min 57 s | hand-installed plan; one refund fell back (`₹6,400` in a number field) |
| #50 | 14 | **14** | | 3 min 26 s | hand-installed plan; all plan, no fallbacks |
| #51 | 40 | not graded | 50 plan calls + agent steps | 11 min 30 s | hand-installed plan; all 40 finished on the plan |
| #52 | 100 | | | | cut off after 51 records by a server restart |
| #53 | 1 of 8 | (1 by the live agent) | 14 | | aborted: table cell click, no plan |
| #54 | 5 of 8 | 5 of 5 (live) | about 108 | | stopped: guessed addresses, lost target; 2 decisions wiped by a test |
| **#55** | 8 | **8** | 122 | 7 min 36 s | first plan from a real trace; proven, used, extended; 2 refunds right by luck |
| #61 | 16 | **15** | 427 | about 20 min | guessed 15% fee; healthy plan dropped; 11 live |
| #62 | 16 (14 decided) | **14 of 14** | 282 | about 20 min | trust per kind; two guesses caught before saving; aborted |

### 13.5 Expense Desk

| Run | Records | Correct | Calls | Time | Engine |
|---|---|---|---|---|---|
| #56 | 1 | 1 | 18 | 1 min 16 s | 9 Oct (first trace; plan wrong four ways) |
| **#57** | 8 | **8** | 52 | 4 min 16 s | 9 Oct |
| **#58** | 16 | **16** | 97 | 7 min 23 s | 9 Oct |
| **#59** | 8 | **8** | 64 | 5 min 41 s | 9 Oct (record 2's trace used) |
| **#60** | 8 | **8** | 45 | 3 min 47 s | 9 Oct final |
| #63 | 16 | **15** | 211 | 9 min 13 s | all new parts on |
| #64 | 16 | **15** | 243 | 10 min 18 s | all new parts on (plan thrown away) |
| #65 | 16 | **15** | 231 | 9 min 32 s | all new parts on |
| #66 | 16 | **12** | 256 | 11 min 16 s | all new parts on ("Back" as a field) |
| #67 | 16 | **14** | 287 | 16 min 13 s | all new parts on (`q` in programs; record lost after Next) |
| #68 | 8 | **7** | 110 | 7 min 29 s | option B defaults; the miss was the live agent's |

### 13.6 Claims Desk

| Run | Result |
|---|---|
| none | never run live; tested only with a scripted trace and a fake model (58 checks) |

### 13.7 Test checks (fake models, no real Gemini call)

| When | Files | Checks |
|---|---|---|
| after 9 Oct | 4 (`test_recipe`, `test_plan`, `test_plan_returns`, `test_plan_expense`) | 188 |
| night of 10 to 11 Oct | 12 | 892 (about 900 after the live-run fixes) |

---

## 14. Glossary

- **Acted**: a record whose first saving click (or a wizard's Next) has happened. It is never retried after that.
- **ARIA widget**: a control built from plain `div`s with roles (`role="radio"`, `aria-checked`) instead of a native input.
- **Batcher**: shares one model call between records asking the same question at about the same time (`recipe/batcher.py`).
- **Branch / kind of record**: the rule that applied plus the choices made (`rule 4: outcome=Approve`). Trust is tracked per kind.
- **Cache epoch**: a number per workflow. Bump it and the workflow gets a new, empty cache namespace.
- **Change tracker**: watches every save the browser sends and fails a record whose last save failed (`recipe/network.py`).
- **Cold**: the first record of a fresh run, done by the live agent while its actions are recorded.
- **Combobox**: a text box with a list of options that appears as you type; typing alone does not choose.
- **Commit phase**: the part of a plan from the first worked-out value onward: the form and its saving clicks.
- **Computed value**: a value the model decides (a choice, a date, an amount), with code doing the arithmetic.
- **Coverage**: one checked model question that adds the side trips the instructions name (off by default).
- **Decision book**: the recipe's shared batch decision, 5 records per call.
- **Decision program**: the decision written once by the model as facts + ordered rules, run by code (off by default).
- **Detour cut / revisit**: when the agent's route comes back to an address it already reached, the way back becomes one step that
  reopens that address.
- **Dry run**: running a plan up to, but not including, the saving clicks (and never pressing a wizard's Next).
- **Evidence**: word-for-word quotes of page text the model gives for each rule; checked by grounding.
- **Extend (a plan)**: replace it with one learned from a richer live route, untrusted, to be proven again.
- **Fact**: a named value read off the pages by code (a number, a date, a table cell).
- **Fake model**: a test function that answers in the model's shape from the site's own data. No call to Gemini.
- **Fallback (live)**: a fast path step failed before saving, so the live agent redid the record (`recipe+live`, `plan+live`).
- **Fallback model**: `gemini-3.1-flash-lite`, used when every key is out for the main model.
- **Field (source)**: a value copied from the record.
- **Fresh run**: empty cache, reset site, (ideally) restarted server.
- **Gather phase**: the part of a plan before the form: replayed clicks and typing.
- **Grounding**: the check that every number, date and quote an answer uses is on the record's pages.
- **Hash route**: an address like `/#/claims/kxqfab`, used by single-page apps.
- **Iframe**: a page inside a page; its controls are not in the outer page's document.
- **Key pool**: the scheduler that spreads model calls over keys, paces them and falls back to the second model (`keypool.py`).
- **Live agent**: browser-use + Gemini deciding every step.
- **`live_only`**: a stored verdict "nothing could be learned for this workflow" (or a plan was thrown away).
- **Offline scorer**: `tests/score_plan.py`; dry-runs decisions with the real model and grades them, with no live agent.
- **Option B**: the 11 Oct decision to switch the unproven parts off by default.
- **Page type** (`url_shape`): an address with ids replaced by `*` (`/invoices/*`).
- **Pacing**: booking each call in its key's minute before sending, to stay under 14 per minute.
- **Plan**: the live agent's route with each varying value marked as field, slot or computed (the general fast path).
- **Proof record**: a record after a plan is learned, where the plan's dry run is compared with the live agent.
- **Pruning**: skipping side trips that never supplied a fact (off by default).
- **Read-back**: after saving, opening the record's page again to check the saved values are shown (off by default).
- **Recipe**: the Credit Desk fast path: pages to read and a record-labelled button to click.
- **Replay**: v2's cache: replaying the first record's clicks with Playwright.
- **Replay verdict**: the once-per-workflow check whether replaying the first record's clicks is safe.
- **Run**: one press of Run; runs are numbered by their database id.
- **Saving click**: a click that saves (Save, Submit, Confirm), or a route button marked `saves`.
- **Shadow mode**: a program runs beside the model, and the two are compared, but the model decides.
- **Skeptic**: a reviewing helper agent whose job is to break a piece of work.
- **Slot**: a value read off an earlier page (a PO number) by one small model call; must appear word for word on a page.
- **Slot by code**: a slot read by code from the label it is printed next to, after 3 agreements with the model (off by default).
- **Spot check**: every 10th trusted use of a program or a slot-by-code is also asked to the model and compared.
- **`TA_FEATURES`**: the environment variable that switches the six optional parts on.
- **Trace**: a recording of what the live agent did on one record, with the text of the pages it saw.
- **Trust per kind of record**: a trusted plan decides alone only kinds it has been checked on against the live agent.
- **Verification**: comparing what the site saved with the correct answers after a run.
- **Widget**: a run of clicks that sets one field (a calendar).
- **Wizard**: a form in several steps with Next buttons.

---

## 15. Map of the code

All paths are under `~/Downloads/terminautomator/v3/`. Sizes are line counts on 11 Oct (lines are long in this code base, so a
1,000-line file is a lot of logic).

### 15.1 The app shell

| File | Lines | What it does |
|---|---|---|
| `main.py` | 25 | FastAPI app; on start, marks runs left "running" by a dead process as failed |
| `config.py` | 20 | infrastructure settings only: `DATABASE_URL`, `FORCE_HEADLESS`, master-key path, default model |
| `run_context.py` | 125 | `RunContext`: every per-run setting, a frozen object passed down explicitly (no globals) |
| `app_db.py` | 331 | Postgres pool, schema, the background log writer |
| `secrets_vault.py` | 45 | encrypt and decrypt stored keys (Fernet) |
| `keypool.py` | 236 | key rotation, cool-downs, daily marks (Pacific reset), fallback model, pacing |
| `run_service.py` | 243 | turns a saved workflow into a run: context, records, thread, verification, final status, redaction |
| `prompt_gen.py` | 95 | optional: turn a description into agent instructions (one call) |
| `api/routes.py` | 559 | every HTTP endpoint, and serving the built frontend |
| `frontend/src/pages/AgentPage.jsx` | 580 | the task editor |
| `frontend/src/pages/LogsPage.jsx` | 614 | the live run view |
| `frontend/src/App.jsx` and `lib/`, `components/` | about 590 | layout, routing, API client, data parsing, the Credit Desk example |

### 15.2 The engine

| File | Lines | What it does |
|---|---|---|
| `orchestrator.py` | 2,260 | the heart: chooses replay / recipe / plan / live per record; seed and learn locks; proof records; trust per kind; extension; the feature switches (`FEATURES`); learning programs, slots by code, read-back verdict, pruning |
| `agent/runner.py` | 206 | runs one browser-use agent per record; `PooledChatGoogle` switches keys on 429; judge off |
| `agent/prompts.py` | 127 | the agent's prompt (safety rules + the task's instructions + the record); the scoped widget prompt |
| `recipe/trace.py` | 186 | records the live agent's actions and page text |
| `recipe/compile.py` | 163 | trace to recipe (Credit Desk shape) |
| `recipe/execute.py` | 687 | runs a recipe: read pages, decision book, voting, click |
| `recipe/formula.py` | 138 | safe evaluator for rule formulas and `{= }` values (exact decimals, `ast`, no `eval`) |
| `recipe/plan.py` | 849 | trace to plan: classify values, locators, paging, widgets, wizards, logins, pop-ups, replay verdict, comparing with the live agent, extension, `learn_fields` |
| `recipe/plan_exec.py` | 1,820 | runs a plan: replay the route, slots, read the form, decide (votes), fill step by step, save, dry runs, the batchers |
| `recipe/batcher.py` | 99 | shares one model call between records |
| `recipe/llm.py` | 82 | the small text-only call (JSON out, paced, retries) |
| `recipe/network.py` | 58 | the change tracker |
| `recipe/store.py` | 58 | the `recipe_traces` and `recipes` tables |
| `recipe/facts.py` | 698 | reads facts off page text by code (numbers, dates, labels, tables) |
| `recipe/program.py` | 1,395 | decision programs: the expression language, running, validation, learning, trust |
| `recipe/evidence.py` | 658 | grounding: numbers, dates and quotes must be on the pages |
| `recipe/webkit.py` | 1,063 | real-world pages: frames, ARIA, comboboxes, rich text, load more, pop-ups, Retry, logins |
| `recipe/readback.py` | 800 | read-back after saving |
| `recipe/coverage.py` | 640 | coverage question and pruning |
| `cache/`, `replay/` | about 590 | the v2 cache and replay (fingerprints, recorder, store, Playwright replay) |
| `data/` | about 100 | `Record`, `RunResult`, `BatchReport`; loader |
| `evaluation/` | about 350 | grading: table check, answer-view check, normalisers |

The new 10 Oct files (`facts`, `program`, `evidence`, `webkit`, `readback`, `coverage`) add up to about 5,250 lines, roughly as much as
the whole plan engine before them. That is a measure of how big the round was.

### 15.3 Tests (`tests/`)

| File | Checks | Needs | Covers |
|---|---|---|---|
| `test_recipe.py` | 40 | nothing | recipe pieces |
| `test_plan.py` | 62 | AP Desk on 5180 for part 2 | plan engine on the real AP trace; fake-model runs |
| `test_plan_returns.py` | 72 | Returns Desk on 5181 for part 2 | paging, id links, calendar, long page, messy routes, `known`, trust per kind, grounding, programs with 0 calls |
| `test_plan_expense.py` | 88 | Expense Desk on 5182 for part 2 | real Expense traces, the wizard, every outcome, program through the wizard |
| `test_plan_hazards.py` | 58 | Claims Desk on 5183 for part 2 | scripted trace; sign-in, pop-ups, endless list, iframe ARIA form |
| `test_program.py` | 108 | nothing | facts, expression language, programs, learning with a fake model, trust |
| `test_evidence.py` | 85 | nothing | grounding, including the 9 Oct guessed fee |
| `test_pacing.py` | 45 | nothing | pacing on a fake clock |
| `test_webkit.py` | 99 | Playwright | page helpers on pages the test writes |
| `test_readback.py` | 83 | Playwright | read-back on pages the test serves |
| `test_coverage.py` | 80 | Playwright (`--offline` skips) | coverage and pruning |
| `test_orchestration.py` | 72 | nothing | what the orchestrator learns as records finish, everything faked |
| `score_plan.py` | | the v3 server and **real model quota** | plan decisions vs the right answers, no live agent |
| `test_backend.py` | | a **local** Postgres in `TEST_DATABASE_URL` | API and database (not run in the last round) |

Fixtures: `fixtures/ap_trace.json` (real AP trace), `returns_trace.json` (scripted; rebuilt by `make_returns_fixture.py`),
`expense_trace.json` (real, run #56, plus two real proof traces from #57), `hazards_trace.json` (scripted; `make_hazards_fixture.py`).

**Warning that still matters**: every part 2 **resets its test site**. Never run one while a live run uses that site. Use `--offline`.

### 15.4 The test sites

| Site | Folder | Port | Workflow id | Grading | Built to test |
|---|---|---|---|---|---|
| Credit Desk | `testsites/credit-desk` | 5174 | 32 (also 29) | answer view in Supabase; reset by clicking "Reset all decisions" | read, decide, click a labelled button |
| AP Desk | `testsites/ap-desk` | 5180 | 34 | `grade.py`; `POST /admin/reset` | server pages, search, worked-out form values, confirm dialog |
| Returns Desk | `testsites/returns-desk` | 5181 | 35 | `grade.py` | paging with no search, id links, calendar, long page, branches |
| Expense Desk | `testsites/expense-desk` | 5182 | 36 | `grade.py` | single-page app, tabs, sections, wizard, random ids |
| Claims Desk | `testsites/claims-desk` | 5183 | none yet | `grade.py` | sign-in, pop-ups, endless list, Retry, iframe ARIA form |

Each has a README with its rules and "what is hard on purpose", an `expected.json` computed by independent rules, and (except the
Credit Desk) `node server.js` with no install.

---

## 16. For the rebuild: an order that follows the story

For a rebuild one function at a time, a suggested order (not a requirement) is the order the problems appeared, because then
each piece arrives with the failure that justifies it, and each can be checked against the old tests and sites before moving on.

1. **The live agent on one record**, with the change tracker (`network.py`). Check: a Credit Desk record, and a deliberately refused
   save.
2. **Verification** against a site's answers (`grade.py` or the answer view). Nothing after this is believable without it.
3. **The key pool with pacing and the Pacific reset.** Small, self-contained, and every later step depends on it.
4. **The trace recorder**, with page text.
5. **The formula evaluator** (`formula.py`): small, pure, and the base of "code does the arithmetic".
6. **The recipe** on the Credit Desk, then the accuracy ladder in order (reason first, exact-text lookup, formulas, the user's own
   button, voting), using an offline scorer.
7. **The replay verdict.** Reproduce run #41's failure on purpose first, then fix it.
8. **The plan engine, gather phase only** (field and slot values, page types, locators that never name record 1), then the form
   decision, then proof against the live agent. Check on AP Desk.
9. **Batching** of slot and decision calls.
10. **Returns Desk shapes** (paging, id links, widgets, long pages), using **real** live traces from the start, not scripted ones.
11. **Expense Desk shapes** (hash routes, name-keyed controls, wizards, `learn_fields`).
12. **Trust per kind of record** and grounding (evidence quotes). These two answer "right by luck".
13. Only then, one at a time and each proven live before the next: read-back, slots by code, coverage, pruning, decision programs
    (learn them **offline from clean recorded examples first**), and the Claims Desk hazards.
14. Before trusting any of it: a **real site we did not build**.

And keep the rule that ran through all of v3: **a model call only where needed; anything cached is checked before it is trusted; when
unsure, refuse and let the live agent do the record; never press a saving button twice.**
