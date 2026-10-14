# 05. The recipe engine

This is the part of v3 worth understanding properly. Everything else is plumbing around it. Code: `recipe/` plus the
hooks in `orchestrator.py`.

## The problem in one example

Credit Desk record 7. A human would: open the Requests page, find request #7, note the retailer and city, open the
Ledger, find that exact retailer, read its numbers, apply the rules, go back, and click one button on row 7.

With the live agent that is about 9 model calls **per record**, and 100 records is about 900 calls and a lot of waiting.
But look at what actually differs between record 7 and record 8:

| Part | Same for every record? |
|---|---|
| Which pages to read (Requests, Ledger) | yes |
| That the last action is "click one of three buttons in the row" | yes |
| The button's label pattern ("Approve request 7", "Reject request 7", "Hold request 7") | yes, only the number changes |
| **Which** of the three buttons | **no. This is the only real decision** |

So cache the first three, and spend model calls only on the last one.

## The pipeline

```
 FIRST RECORD (live agent, about 6 steps)             EVERY OTHER RECORD
 ----------------------------------------             ------------------------------------------
 TraceRecorder logs every click/type/navigate         read pages (once, shared)  ----+
          |                                           find this record's 3 buttons  |
          v                                                                         v
 compile_recipe(trace)  -->  recipe  (saved)          DecisionBook: decide 5 records per call
   read: [requests page, ledger page]                   1. lookup call  (exact-text hints)
   act:  click button labelled "Approve request {id}"   2. decide pass A (temperature 0)
         siblings found on the page at run time         3. decide pass B (reversed order, 0.4)
                                                        4. pass C only where A and B disagree
                                                      code: evaluate formulas, pick button
                                                      click it  -> change tracker confirms the save
```

## Part 1: learning (trace.py, compile.py)

**Trace.** While the live agent works on the first record, a callback records every *repeatable* action: navigate,
click, input, key press, select, go back. Reading, scrolling and waiting are ignored. For clicks it keeps the element's
tag, attributes, accessible name and xpath, plus the URL it was on.

**Compile.** `compile_recipe` tries two routes:

- **From a pair** of traces (`compile_pair`): needs two successful records that agree. It finds each trace's last click that
  *belongs to the record* (its locator contains the record's value), checks both clicks have a shared label, and takes the
  pages both traces visited.
- **From one trace** (`compile_single`, the normal case now): find the last click whose label contains the record's value,
  for example `aria-label="Approve request 7"`. Replace the value with a blank: **`Approve request {id}`**. The sibling
  buttons (Reject, Hold) are *not* stored; they are discovered on the page at run time by looking for labels that match
  the template with one word free.

Rules baked in because of real bugs:

- An **xpath is never trusted for a record's own button** (the same position is a different row next time).
- "Dynamic" ids (`:r3:`, long hex, ids with 5+ digits) are never used as locators.
- If no click names the record, learning fails: `"the AI never clicked anything that belongs to the record"`. Only
  *successful* live records leave a trace, so each one that still cannot be compiled counts. Once 4 such traces exist
  without a recipe, the batch gives up and every record uses the live agent, with a note in the log.

A recipe looks like this (shape, not literal):

```json
{ "version": 2,
  "read": ["http://localhost:5174/", "http://localhost:5174/ledger"],
  "act":  { "page": "http://localhost:5174/",
            "goal": "click the Approve/Reject/Hold button for the request",
            "locator": { "node": "button", "attr": "aria-label", "value": "Approve request {id}" },
            "siblings": "auto" } }
```

## Part 2: running (execute.py, `run_recipe`)

For each record after the first:

1. **Read the pages** listed in `read`. For a paged list (10 rows per page) it clicks **Next** until the button is gone,
   keeping each page's text and dropping repeated headings. Waits until "Loading..." disappears and the network is
   quiet. If the pages do not depend on the record, **the first record reads them and everyone shares the text**.
2. **Open the page with the final button** and find *this record's* buttons: for the template
   `Approve request {id}` it looks for labels like `<word> request 7`. If the row is not on this list page, it clicks Next
   until it is. Two or more matching buttons are required (otherwise the recipe falls back to the fixed locator).
3. **Get the decision** from the decision book (next section). If a record was not covered, it asks the model alone.
4. **Click** that button (scroll into view, click), pause 0.4 s, done.

The page list is a single-page app (the address never changes), so after paging through, the page is reopened at page 1
before looking for buttons. That was bug C5.

## Part 3: deciding in batches (the decision book)

One `DecisionBook` per workflow per batch. The first recipe record to take its lock becomes the **batch leader**:

1. It reads the pages and finds its own buttons.
2. It builds each *other* open record's button labels by filling the template with that record's id, so it can decide
   records that have not opened a browser yet.
3. It splits open records into **chunks of 5** (`BATCH_SIZE`).
4. The leader decides its own chunk right away. The remaining chunks are launched as background tasks, **at most 3
   model jobs at once** (`MAX_PARALLEL_DECISIONS`).
5. Every other record simply waits on a future ("another record's call covers me") for up to 240 s, then clicks.

So 100 records become about 20 chunks, and most records never make a model call of their own.

### What one chunk costs

| Call | Purpose | Notes |
|---|---|---|
| 1 lookup | For each record, copy its own entry and the identifying details a *matching* entry must have | only when there are 2+ pages |
| 2 decisions | Pass A (temperature 0), pass B (reverse order, temperature 0.4) | run in parallel |
| 0 or 1 tie-break | Only for records where A and B chose different buttons | majority of three wins |

About 20 lookups, 40 decisions and a few tie-breaks for 100 records, roughly 66 calls in total.

## Part 4: the four jobs taken away from the model

The accuracy ladder (doc 09) was always the same move: find the exact kind of mistake, then **take that job away from the
model and give it to code**.

| Job | Who does it | Why |
|---|---|---|
| Find the retailer in the Ledger | **Code**: a character-for-character search of the pages for the details the model copied (`_search_lines`) | The model matched near-misses (`KulkarniFootwear` for `Kulkarni Footwear`) and right-name-wrong-city |
| Do the arithmetic and compare to limits | **Code**: the model writes each rule as a formula string, `formula.py` evaluates it with exact decimals | A small model wrote "65000 <= 65000: false" |
| Decide which rule applies first | **Code**: the first rule, in the *user's* order, whose formula is true | The model ignored its own check |
| Map a rule to a button | **Code**: from the user's own rule line ("... : Hold"), matched by Jaccard similarity | The model sometimes attached the wrong button to a correct rule |

What the model still does: read pages, copy entries exactly, understand English rules, and translate each into a formula
and a button. That is what a small cheap model is good at.

### The formula evaluator (formula.py)

The model answers with strings like `935000 - 850000 <= 850000 * 0.10`. The evaluator:

- normalizes symbols (`≤`, `×`, `₹`, `6,50,000`, `true`, `&&`);
- parses with Python's `ast` and walks only whitelisted nodes (numbers, `+ - * /`, comparisons, `and or not`);
- uses `Decimal`, so there are no float surprises;
- returns `True`, `False`, or `None` when unreadable. On `None` the code falls back to the model's own button and logs a
  warning.

It never calls `eval`, so a malicious formula cannot run code.

### Choosing the button (`_decide_from_checks`)

For each rule in the user's order: evaluate its formula. The **first true rule** wins. Its button comes from the user's
own rule line when that line names exactly one button word; the model's mapping is only a fallback. If the formulas and
the model's stated button disagree, the formulas win and it is logged.

### The strict answer shape

Gemini is forced into a JSON **schema** (`_Decisions`), with fields ordered `request, matched_entry, identical, numbers,
checks[], rule, button`. Reasoning comes first and the button last, because small models otherwise pick the button
before thinking. An answer with **no written reasoning is rejected and asked again**, as is any record the model left
out.

### Voting

Models are non-deterministic even at temperature 0. Pass B reads the records in reverse order with a little randomness so
its mistakes are independent of pass A's. Agreement is the usual case. A wrong final answer now needs two of three
passes to make the same slip.

## Part 5: when things go wrong

| Situation | What happens |
|---|---|
| A recipe step fails **before** the click (page not loaded, buttons not found) | The browser is sent back to the start page and the **live agent** does the record (`recipe+live`) |
| The click happened but the save was refused or unconfirmed | Record **fails**. Never retried (it could act twice) |
| 3 records in a row fail with the recipe | The recipe is dropped for the rest of the batch; everyone goes live |
| A decision call fails | Its records decide one by one on their own |
| The lookup call fails | Carry on without exact-text hints (decisions still work) |
| Daily quota gone on every key | Raise `LlmHalt`; the batch stops cleanly (doc 07) |
| Model busy (503) | Up to 6 retries with growing waits |

## Will it work on a different website?

Short answer: it will not *break*, and since 8 Oct 2026 most shapes get a fast path. Nothing in the engine is hardcoded to
the Credit Desk (a search of the code finds its words only in comments and tests). The prompt, the URL, the data, the match
key and the verification are all per task. What *is* tied to a task **shape** is which fast path can recognise it.

| Kind of new task | What happens | Cost |
|---|---|---|
| **Fill a form** (type record values, native dropdowns, checkboxes, one submit) | v2's click replay, **if** the first record's clicks pass the safety check (doc 06) | cheap, fast |
| **Read pages, decide, click one of several buttons whose label holds the record's value** (the Credit Desk shape) | this doc's **recipe** | about 66 calls per 100 records |
| **Anything else that is a route through pages ending in a form or button** (values read off a page, values worked out, a confirm dialog, searches) | the **plan** (doc 06), after it has been proven against the live agent | about 3 calls per record after about 3 live records |
| **Anything the plan cannot represent** (a date picker between fields, a route that branches on the data) or a plan that fails its proof | **the live agent does every record** | v1 prices: about 11 to 17 steps and 40 to 100 s per record (measured on AP Desk) |
| Any step that breaks mid-way | that record falls back to the live agent (before any save) | one extra attempt |

The live agent is the permanent safety net, so a brand-new site gets *slower and more expensive*, not *wrong*, in the
normal case.

### What the recipe (this doc's fast path) quietly assumes

These are the places where a new site could trip **the recipe**; the plan engine (doc 06) does not assume most of them:

1. **A button label that contains the record's value** (`Approve request 7`). The record's own button is found by an
   attribute (`aria-label`, `data-testid`, `id`, `name`, `title`, ...) containing that value. A button with no such label
   cannot be learned, and an xpath is deliberately never trusted for it.
2. **Sibling buttons that differ by one word** (Approve / Reject / Hold). They are discovered at run time with that
   pattern. If the options are not like that, only the single recorded button is used.
3. **Paging by a control named "Next"** (also `next page`, `›`, `»`, `>`, `→`). Another style of paging is not followed.
4. **Plain page text is enough to decide.** The model never sees the page's layout or images, only `innerText`. Each page
   is cut at 14,000 characters (a paged list at 60,000), so a **very large page is silently truncated** and the
   decision may be made without the line that mattered.
5. **The decision is a choice among visible buttons by rules.** Tasks that need typed free text, computed values or
   several different actions per record are outside the shape.
6. **Every page it needs can be reached by its URL.** The recipe only *opens addresses* (the record's value may be a
   blank inside the address); it does not replay searches, filters or clicks made to reach a page. A page that exists
   only after typing into a search box is not reproduced (the list is just paged through with Next).
7. **One example is enough** (`compile_single`). A recipe learned from one record can hide a branch that only appears
   for other records. Three failures in a row drop the recipe, but a recipe that is *wrong without failing* is only
   caught by verification afterwards.

### Measured, before the plan engine existed: a second site (AP Desk, 8 Oct 2026)

`testsites/ap-desk` is a held-out test site built to be unlike the Credit Desk: server-rendered pages, no row
buttons (the invoice number is a plain link to an opaque URL), numbered paging, a three-way match across four pages, and a
decision form whose values are **computed** (a date, a reason, a typed note) ending in a confirm dialog. Task and
grader: its `README.md`, `grade.py`, `expected.json`.

Run #41, 10 records, `gemini-3.5-flash-lite`, 2 browsers, headless, empty cache:

| | Result |
|---|---|
| Correct | **1 of 10** (the record the live agent did itself); the other 9 failed |
| Time | 3 min 59 s (live record 64 s; the others 26 to 56 s) |
| Modes | 1 `cold`, **9 `replay`**, **0 `recipe`** (a recipe was never even attempted) |
| Agent steps (LLM calls) | 67 in total: 16 for the live record, 51 spent by the 9 failed replays |
| Why they failed | `The site refused the save: POST /invoices/e57b1a67/decision status 422`, the same invoice nine times |

Neither fast path fitted this task at the time, and the **v2 replay path misfired** (both problems are fixed; see doc 06):

1. **The replay gate was fooled.** `_replayable_steps` keeps the cache when *any* step is tied to a data field. One click
   (the "Invoices" menu link) was tagged as tied to `invoice_no`, so the whole cache counted as replayable and the recipe
   path was never reached.
2. **Replay is not data-aware.** The cached link locator is `href="/invoices/e57b1a67"`, which is record 1's invoice, and
   the instruction text kept for the live fallback names record 1's number. The fallback agent for record 2 searched for
   record 1's invoice, and the replayed clicks then submitted record 1's page again.
3. **Computed values are never cached.** The dropdown choices, the date and the note are not copies of record fields, so
   they were not recorded, and the replayed form was incomplete (hence the 422).
4. The recipe would not have fit anyway: the invoice link's own attributes do not contain the record's value (only its
   text does), so `compile_single` finds no label to template.

Two lucky things kept this safe, and neither is a design guarantee:

- the site validates its form and refused the incomplete post, and the change tracker (doc 07) turned that into a failed
  record. A site that accepted the post would have recorded record 1's decision nine times and reported nine successes;
- verification (`grade.py` here) shows the truth. **Without a check, this run would have looked like 9 fast successes.**

Even with the replay path fixed, a task like this one had no fast path: the live agent for every record, about 16 steps and
about 64 s per record, so 100 records would be roughly 1,600 calls and 100 minutes, more than a free-tier day allows. That
is what the plan engine (doc 06) was built to fix; its results on the same site are in doc 09.

### How to check a new site safely

1. Run a **small batch first** (the Run button takes "first N rows"), headless or visible.
2. Read the log: *Learned a recipe...* or *Learned a plan...* followed by *The plan is trusted* means a fast path fits;
   *Could not learn a recipe or a plan (...)*, *The plan disagreed with the live agent...* or mostly `live` records means v3
   is acting like v1 on this site.
3. Always set up **verification** (an answer view or a table) so a wrong-but-successful run shows up as a lower accuracy
   number instead of a green tick.
4. Compare a few recipe decisions against what a human would pick before trusting a big batch.

### Still unproven

The recipe has been proven on **one** read-and-decide site. A second, differently built one is the real test, and a
"shadow check" (run the recipe and the live agent on a few records and compare) is not built yet. Both are listed in
`FUTURE_PLANS.md` and in doc 09.
