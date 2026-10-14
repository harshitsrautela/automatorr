# 06. The plan engine (the general cache)

Doc 05 explained the recipe: a fast path for one task shape (read pages, decide, click one record-labelled button). This
doc explains what was added on 8 Oct 2026 after the **AP Desk** test site showed that the recipe, and the old v2 click
replay, both fail on a differently built site. The plan engine is the first fast path that does not assume a shape.

Code: `recipe/plan.py` (learning), `recipe/plan_exec.py` (running), hooks in `orchestrator.py`. Since 10 Oct also
`recipe/program.py` + `recipe/facts.py` (decision programs), `recipe/evidence.py` (grounding), `recipe/readback.py`,
`recipe/coverage.py`, `recipe/webkit.py` (real-world pages) and pacing in `keypool.py`.
Tests: `tests/test_plan.py`, `tests/test_plan_returns.py`, `tests/test_plan_expense.py`, `tests/test_plan_hazards.py`,
`tests/score_plan.py`, and the 10 Oct module tests (doc 12).

It has been tested on three sites we built to differ from each other: the **AP Desk** (server pages, search box, a form with a
confirm dialog), the **Returns Desk** (paging with no search, id links, a calendar, a very long page, a branching route) and the
**Expense Desk** (a single-page app with hash routes, tabs, collapsed sections, and a three-step wizard with radio buttons and
checkboxes whose ids change on every render). Each new site broke something; each fix was made general, not site-specific. The
9 Oct round is in "Generality round" below. The 10 Oct round ("General-purpose round" below, full story in doc 12) adds a fifth
site, the **Claims Desk** (sign-in, pop-ups, an endless list, a form in an iframe built from ARIA widgets), decision programs
that decide with **no model call**, and checks of every cached answer against the pages. **Nothing of the 10 Oct round has run
live yet**: it is tested with fake models only.

## The problem it solves

On AP Desk (a three-way invoice match: invoice, vendor, purchase order, goods receipts, then a form with a dropdown, a
reason, a typed note and a confirm dialog) the first attempt got **1 of 10 right** (doc 05 has the autopsy). Three things
broke the older fast paths:

1. The values in the route do not all come from the record. The agent read a vendor name and a PO number off one page
   and typed them into another page's search box, and the values in the form were **worked out**.
2. The cached clicks named the first record's own items (a link to *its* invoice), so replaying them repeated record 1.
3. A route that depends on what was read cannot be replayed from selectors at all.

## The one idea: every value has a source

A plan is the live agent's route, with each value that varies marked by where it comes from:

| Source | Example | At run time |
|---|---|---|
| **field** | the invoice number typed in the search box | copied from the record (`{invoice_no}`) |
| **slot** | the vendor name, the PO number | read off an earlier page by one small model call (`{s1}`) |
| **computed** | the decision, the reason, the date, the note | decided by the model, with **code** doing the arithmetic |

Everything the model would otherwise do step by step becomes either a replayed step (free) or one of three small, focused
calls.

## Learning: from one trace to a plan (`compile_plan`)

After the first record (done by the live agent), its trace (doc 05) is compiled:

1. **Page text is captured** per page address in the trace (`recipe/trace.py`), so a typed value can be traced back to where
   it was read.
2. **Each typed or chosen value is classified**:
   - equals a record field, so it becomes `{field}`;
   - typed, not a field, and appears in an **earlier page's text**, so it becomes a **slot** (`s1`, `s2`, ...). The same
     text typed twice is one slot (the PO number was typed in two search boxes);
   - anything else is **computed**.
3. **The first computed value starts the commit phase.** Before it is **gather** (replayed by code); from it on is the
   form: its controls (found by name, else id; radio buttons and checkboxes clicked count as controls too) and the **trailing
   clicks** that save (Save, then Confirm).
4. **Every click gets locators**, best first, with blanks: a stable attribute (`id`, `name`, `aria-label`, ...), then
   role + text (`link` named `{invoice_no}`; an element's own `role` attribute wins over its tag), and for a plain element (a table
   cell) its exact visible text. Any locator that names one particular thing (an id with 3+ digits, like
   `href="/invoices/e57b1a67"`) is **dropped**, so the plan never points at record 1's own item.
5. **Every click records the page type it must land on** (`url_shape`: the path with ids replaced by `*` and only the names
   of query parameters, so `/invoices/e57b1a67` and `/invoices/0a1b2c3d` are the same shape), and which record fields its
   locator used (checked after the click).

A plan is refused (the live agent keeps doing everything) when: the AI pressed keys or changed page in the middle of the form,
no button saves the form, the form has no ordinary field to find it by, an element has no stable way to be found, or nothing
in the route depends on the record. (A click between form fields used to be a refusal; since 8 Oct it becomes a *widget*,
below.)

## Harder shapes (added 8 Oct 2026, from the Returns Desk test site)

The Returns Desk (`testsites/returns-desk`, port 5181) was built to be hard on purpose: a list that must be **paged through with
no search box**, links whose text is an **id**, a custom **calendar** instead of a date input, a **very long** policy page, and a
route that **branches** on the return's reason. Five additions:

1. **Paging.** Clicks that only move through a list (a page number, Next, Prev, a link to `?page=3`) are dropped from the route.
   The following record-specific click is marked `paged`; at run time the plan walks the list (the Next control, else the
   smallest page number not visited yet, up to 40 pages) until that record's link appears. Starting from page 1 each time means a
   return on page 9 is found as reliably as one on page 1.
2. **Links whose text is an id** (an order number, an item code). They belong to the record, so their text is a **slot**, read
   off the page by the same small call as other slots; even an `href` like `/orders/ORD-10128` is turned into `/orders/{s1}`.
3. **Widgets.** A run of clicks that fills a field (open the calendar, next month, click a day) is a `widget` control. The model
   decides its value like any other field (a date as `YYYY-MM-DD`). The plan then sets it **itself** when it is a calendar laid
   out the ordinary way (`_set_calendar`): it presses the button that opened it in the trace (the widget's `opener`), reads the
   month label ("October 2026"), presses the previous/next button next to that label until the month matches, and clicks the
   enabled day button. No model call. Anything else (a picker laid out differently, or one it cannot open) goes to a **scoped
   live-agent step** that sets just that control (`orchestrator._widget_filler`, prompt `build_widget_prompt`), a few agent
   steps instead of the whole route, which never presses save. Without either, the plan stops **before** saving and the record
   goes to the live agent. The proof stage cannot compare a widget (the live agent's trace holds clicks, not a value), so
   widgets are left out of that comparison and rely on verification.
   *Which clicks save?* After the last ordinary field, the clicks up to the first button that saves (`type=submit`, or named
   Save / Submit / Confirm / Apply / ...) belong to the widget; that button and what follows are the saving clicks.
4. **Long pages.** Page text now also carries every table row **with its headings** (`Category: Footwear - Casual | Opened: 10% |
   Damaged: 25%`), because a header 60 rows above the row that matters is lost in a long table. A page longer than 5,000 characters
   is cut down to its first lines plus the lines that mention something the record's *other* pages mention (a category, an
   item number), with their neighbours (`_trim`). Short pages are untouched. On the Returns Desk's 19,600-character policy page the
   record's fee row survives and the block is under 12,000 characters.
5. **Branches.** The plan is learned from one record, so it only knows that record's route (a "wrong item" return never reads the
   policy page). Three defences:
   - for **every rule**, the model says whether the pages show what that rule's condition needs (`known`, added 9 Oct). A rule it
     cannot settle that comes **before** the rule that applies is an error, so the record goes to the live agent instead of
     getting a guess. (On 9 Oct two refunds were decided without their item page, the one that says "final sale" and the
     warranty; they were right only by luck. The older free-text `missing` field had not caught it. It is still checked too.)
   - when a record falls back to the live agent and its route reached something the plan never does, the plan is **extended**:
     replaced by one learned from that richer route, **untrusted**, and proven again (`_extend_plan`). "Reached" counts page types
     **and** clicks that show something without leaving the page (a tab, a section), by what they are called. The plan is only
     replaced when the new route reaches **everything** the old one did; otherwise it is kept (the log says what was skipped).
   - the fields another branch fills on the form are learned too (next section).
   What is *not* covered: the first plan can be trusted on a short branch and the first record of another branch pays the price
   (one live record, then the extension, then two proof records).

## Generality round (added 9 Oct 2026, from real live traces and the Expense Desk)

On 9 Oct the live agent ran the Returns Desk for the first time (earlier tests there used a scripted trace), and a fourth site was
built: the **Expense Desk** (`testsites/expense-desk`, port 5182). Real traces are messier than scripted ones, and a modern
single-page app breaks assumptions the server-rendered sites never tested. Every fix below is general; none mentions a site.

**Messy routes (from the live agent's first real Returns Desk traces)**

| What the agent did | What went wrong | Fix |
|---|---|---|
| Clicked the table **cell** around a link (`<td>RMA-71683</td>`, no id, no role, no name) | "an element has no stable way to find it again": no plan at all | A plain element is found by its **exact visible text** (`{"text": "{rma}"}`), blanks included |
| Typed **guessed addresses** (`/items/SKU-5621`, `/returns/RMA-71683`) and got "Page not found" | the dead ends were part of the route | A step whose landing page is short and says "not found" / 404 is dropped (`_dead_end`) |
| Typed an address (`navigate`) | the plan stored the **page it was on** as the target, so it replayed record 1's order page | The target is kept separately (`target`); this was a plain bug |
| Typed a list page's address (`/returns?page=7`) | record 1's page number would be replayed for every record | A typed address with a page parameter is **paging**, like a click on "Next" |
| Went back to the list, paged again and opened the same return | the plan replayed the detour (paging twice) | **Detours are cut**: when the route returns to the **exact address** it reached earlier, through pages it had already seen, those steps become one `revisit` step that reopens the address the plan itself reached at that point for this record. Only a step that moves to another address can start this (a tab click that stays put never does) |
| The first trace failed to compile | nothing was tried again | Every saved trace is tried in turn (newest first), and the reason each fails is logged. In run #59 this rescued the run: record 1's trace could not be compiled, record 2's could |
| Clicked a table **row** (`<tr>`: no text of its own, only random attributes) | no way to find it again | A nameless element is found as **the one of its kind that contains the record's value** (`{"css": "tr", "contains": "{claim_id}"}`). The value comes from the text inside it (the recorder keeps that since 9 Oct) or, for older traces, from the agent's own goal for the step ("click the row for EXP-41142") when the element is a container. It matches a row with a part whose whole text is the value, or text where the value is not followed by another digit (a row's text runs its cells together); more than one match is never clicked |
| Clicked a `<details>` box itself (no text, id or role) | the trace could not be compiled | Clicks that open a section are dropped (every read opens them all); a click that cannot be found again and did not change the page is skipped instead of stopping the plan |

**A single-page app (the Expense Desk)**

| Shape | What went wrong | Fix |
|---|---|---|
| Hash routes (`/#/claims/e3lcxu`) | every page had the shape `/` (the fragment was ignored) | The page type includes a fragment route; ids inside it become `*` like path ids |
| Random ids without digits (`kxqfab`, `e3lcxu`) | not recognised as ids, so record 1's address was "expected" for every record | (a) ids with mixed letters and 2+ digits and UUIDs are recognised; (b) the page reached by clicking **the record's own link** has its last address part treated as the record's id, whatever it looks like; (c) any later step that lands on that same address (a tab, a revisit) expects that same page type; (d) a `*` matches any one part (`shape_matches`) |
| A tab is `<button role="tab">` | located as a *button*, which the accessibility tree does not report | An element's own `role` attribute wins over its tag |
| Collapsed sections (`<details>`) | the plan only opened the sections record 1 opened; a foreign trip needs "Exchange rates" | Every page read opens all collapsed `<details>` first (it only changes the view), so the whole page is read whichever sections the first record opened |
| A **wizard**: radio buttons, Next, fields that depend on the choice, Next, a confirmation checkbox, Submit | radio and checkbox clicks were replayed as fixed clicks; the Next buttons became a "widget" | See "Forms in several steps" below |
| Controls get a **new random id on every render** (`f50324`) | the form was looked up by record 1's ids | Controls are keyed by their **name** first (stable, and shared by a radio group), then their id |
| The form is in a modal without a `<form>` | the old reader read the whole page | Controls are read from the element's form, else its **dialog** (`role=dialog`, `<dialog>`, `aria-modal`), else the page |

**Forms in several steps (wizards)**

- *Learning.* A click on a radio button or a checkbox (native, or with `role=radio|checkbox|switch`) is a **form control** whose
  value is decided per record, like a typed field. Clicks named Next / Continue / Proceed between controls **end a step**
  (`commit.advance`); each control remembers its step (`stage`). Checkboxes that share a name are one control (a group).
- *Values.* A radio group's value is one option's label; a single checkbox is `yes` or `no`; a group of checkboxes is the
  labels to tick, comma separated. The model is told which of these each control is, and its answers are checked against the
  choices actually on the form.
- *Running.* For each step: find it (by a control the plan knows on that step, else by the button that ends it), read its
  controls, decide what is not decided yet, fill, press Next, and check that the form really moved on (otherwise: "the form did
  not move on from step 1", and the live agent takes over before anything is saved). Then the saving clicks.
- *One question where possible.* The first question also asks for the fields the plan knows come **later** (labelled as such), so
  a record whose branch the plan knows needs a single question. A field the plan has never seen (another branch) is asked on its
  own step. To make that rarer, a proof record that **agrees** with the plan, and a live fallback, teach the plan the fields its
  route filled (`learn_fields`): after a "with deduction" proof record the plan knows the deduction and the hotel tick.
- *Dry runs.* A one-step form is compared before anything is typed (as before). Since 10 Oct a wizard's dry run **never presses
  Next** either (a Next may save a draft): the first question also decides the later steps' fields the plan knows, and those are
  compared. Only values that really went into the form (or, in a dry run, would go) are compared (a value the model decided for a
  field that never appeared is dropped). A real run that fails after pressing Next counts as having acted (never redone).
- *Comparing with the live agent.* The agent's radio click gives its label, its checkbox clicks give the labels left ticked (a
  second click unticks), a single box counts as `yes` when ticked. An optional message one side left empty is not a difference.

## General-purpose round (added 10 Oct 2026): cache as code, check before trusting

The 9 Oct runs left four problems (doc 11): a trusted plan **guessed** a 15% restocking fee it could not see, and the model twice
said it could see what it needed when it could not; a trusted plan record still cost **model calls** every time (a slot call and
2 or 3 voting passes per group); a save was taken as **done** because the plan pressed Save; and nothing had been tried on what real
web apps put in the way (a sign-in, pop-ups, frames, endless lists, custom widgets, per-minute limits). The rule for everything
below: **a model call only where needed; anything cached is checked before it is trusted; when unsure, refuse and let the live agent
do the record; never press a saving button twice.** Doc 12 has the full story, the tests and what is still open. **None of this has
run live yet**: every check so far used a fake model.

### Decision programs: the decision itself becomes code (`recipe/program.py`, `recipe/facts.py`)

A **program** is a list of named **facts** and an ordered list of **rules**:

```
{"facts": [{"name": "fee_pct", "type": "number",
            "source": {"kind": "table", "page": "/policy", "where": [{"column": "Category", "op": "eq", "value": "{category}"}],
                       "column": "Opened", "aggregate": "first"}}, ...],
 "rules": [{"name": "final sale", "when": "final_sale", "set": {"disposition": "Reject", "reason": "Final sale"}}, ...,
           {"name": "otherwise refund", "when": "true", "set": {"disposition": "Refund", "refund_amount": "{= qty * price * (100 - fee_pct) / 100 }"}}]}
```

- A fact is read **by code** (`facts.py`): from a record field, the value next to a **label** on a page type, a **table** cell picked
  by other cells (`eq`, `contains`, `in`, or `lt/le/gt/ge` on numbers and dates; the column may be a template like `{grade}`; first,
  sum, count, list, max or min of the matching rows), or a fixed text. Numbers understand `₹1,25,000`, `20%`, `USD 120`, `45 days`;
  dates `19 Oct 2026`, `05 Sept 2026`, `2026-10-19`, and `10/19/2026` only when the day and month cannot be confused.
- A rule's `when` is a small expression language (exact decimals; `and or not`, comparisons, `in`, and only `abs min max round floor
  ceil len lower upper days_between add_days contains startswith join`); it is parsed with Python's `ast` and anything else is refused.
- **Running** (`run_program`): read every fact, then try the rules in order; the first true rule gives the values, each written the
  way its control takes it (an option's exact label, `yes`/`no`, sorted labels, digits, `YYYY-MM-DD`). A rule that needs a fact that
  is **not on the pages stops the program** for this record (it never skips to a later rule). Its kind of decision (its **branch**)
  is `p<rule number>: <the choices it made>`.
- **Learning** (orchestrator, `_maybe_learn_program`): the orchestrator keeps **verified examples** (fields, pages, values) per
  workflow and variant: proof records whose dry run agreed with the live agent, plan records the model decided that saved (and whose
  read-back did not doubt them), and new kinds of record the live agent confirmed; the newest 12 are kept. With **4 or more**, one
  record at a time (a lock) asks the model once to write a program from up to 6 varied examples. Code then checks it: every key is a
  field of the form; a fixed fact may not hold a number the instructions do not print (a rate the examples happened to share); and
  the program must give back **every example's values exactly**. If not, **one** retry with the differences listed; still wrong:
  no program (asked again only after 2 more examples, at most 3 times per batch).
- **Trust per branch**: at the decision point the program runs beside the model (`program_mode="shadow"` in proof dry runs,
  `"use"` in trusted plan records). While a branch is not trusted, the **model decides** and the two are compared
  (`outcome.program_check`). A branch is trusted after **2 agreements and no disagreement**; one disagreement marks that branch as
  never trusted; **2 disagreements drop the program** (it may be learned again from more examples). An agreement only counts when the
  record really saved and read-back did not doubt it; on a record the live agent did, it counts only if the live agent agrees.
- **Using it**: on a trusted branch the program decides with **no model call** (`out.decided_by = "program"`), and every **10th**
  trusted use is a **spot check** (the model decides it too and they are compared). Its answer is grounded like a model's (next
  section): a program whose numbers this record's pages do not show hands the record to the model. A dry run never lets the program
  decide. A program learned again keeps the trust of rules that did not change (`carry_trust`).

Measured with fakes only (`tests/test_plan_returns.py`, `tests/test_plan_expense.py`, part 2, against the real Returns and Expense
sites): **hand-written** programs decided all 8 kinds of return and 6 claims of every Expense outcome (through the 3-step wizard)
with **zero model calls**, and the sites saved the right values. No program **written by the real model** has been tried yet.

### Grounding: an answer must rest on what the pages show (`recipe/evidence.py`)

Every model decision (and every program decision) is checked by code before anything is typed, for the rules up to and including
the one that applies (`evidence.check_answer`):

- every **number** in their formulas and in the values must be printed on the record's pages, in the record, or in the task
  instructions, or be **one step of arithmetic** from such numbers (`2480 = 4 x 620`); a **percentage or rate** must be printed
  itself (small numbers every page has, like a pager's 5 and 10, would put almost any guess one step away: 15 = 10 + 5); a `- 0`
  (a deductible of 0) must be printed too;
- every **date** in the values must be on the pages or in the record, as one clear day;
- the model now gives **evidence**: word-for-word quotes of the page text each rule's condition uses. Every quote must be on a page
  (line breaks aside, never joining a label to another row's value), and a rule decided as plain true or false (a status) must give
  at least one quote that is on a page.

Any problem refuses the answer with "the pages do not show what this answer uses: ...", which the orchestrator already treats as a
branch the plan has not learned: the live agent does the record and its route extends the plan. The 9 Oct guess (`4*620*(100-15)/100`
with no page showing 15) is a test case (`tests/test_evidence.py`, `tests/test_plan_returns.py`). This replaces trusting the
model's own `known` answer (still asked, still checked).

### Slots by code

A slot (a value copied from a page) the model answered gets a proposed **code**: the label it is printed next to on its page and
its shape (`ORD-99751` -> three letters, a dash, five digits). Each later model reading is compared with the code's reading; after
**3 agreements** the code reads it with **no model call** (and every 10th reading is given to the model again and compared). One
disagreement deletes the code. A code reading that is not the right shape, or is one of the page's labels, is not used: the model
is asked instead.

### Read-back: a success is a claim (`recipe/readback.py`)

After a real save, when the plan knows the record's own page (the step found by the record's own value, marked `own`) and the site is
known to show saved values, the plan opens that page again (afresh from the site, never the success screen) and looks for the values
it entered: numbers by value, dates by day, choices by word stems (`Approve with deduction` ~ `Approved with deduction`, but not
`Not approved`, and a form that still lists every option does not count), short text by its words. Whether the site shows saved
values at all is learned from **proof records**: after the live agent saved, its values are looked for on the dry run's record page
in a new tab; two checks that agree decide it, and values the site never shows (even for the live agent's own save) are kept as
hidden. A plan record whose page does **not** show a value read-back has seen confirmed before **fails** ("The plan saved, but the
record's page does not show ... (read-back)"); so does one whose page could not be read back at all. It was saved, so it is **never
retried**, and it does not become an example; 3 such records in a row drop the plan. A value never seen confirmed on this site (it
may be printed in other words) is reported as unchecked, not missing. Read-back costs no model call.

### Coverage and pruning (`recipe/coverage.py`)

- **Coverage**: a newly learned or extended plan is marked `coverage_pending`. On its next proof record, before the dry run, the
  model is asked **once**: which pages do the instructions say to read that this route does not open, and which link on a page the
  route reads leads there? Code checks each answer (the page is one the route reads, the link text is on it, a per-record link sits
  next to the label named) and adds up to 4 side trips: a click and a `revisit` back (later `revisit` steps are renumbered). A
  per-record link becomes a new slot. Note: "Added from the instructions: ...".
- **Pruning**: after each decided record the plan counts which page types supplied a fact (the program's facts, or the pages the
  model's evidence quotes were found on). A side trip (a click followed straight by the step back) to a page type that supplied
  nothing in **12 or more** decisions is skipped. If a record run with skips is refused for missing pages, the counts start again and
  the record runs the whole route once more, before any live fallback.
- **Proactive extension**: a proof record that agreed, or a new kind of record the live agent confirmed, whose live route read page
  types the plan lacks, now extends the plan too (before, only refusals and disagreements did).

### Real-world pages (`recipe/webkit.py`, built on the Claims Desk, doc 12)

| Hazard | Learning (`plan.py`, `trace.py`) | Running (`plan_exec.py`) |
|---|---|---|
| A **sign-in**, and sessions that run out | the steps on the page where a password was typed, up to the sign-in click, become `plan["login"]`; a typed value written word for word in the instructions (a username, a password) is a **constant**; a password the plan cannot type again (not typed from the instructions or a record field) refuses the plan | landing on the login page unexpectedly replays the login and goes back to where the plan was, at most 2 times per record |
| **Cookie banners, "What's new" pop-ups** | a click named like Accept / Got it / Close that did not change the address is `optional` | before every step and before the form, visible overlays with such a button are closed (on a cookie banner, "Reject non-essential" / "Only necessary" is preferred to Accept all; never a Save/Submit/Delete/Pay/Confirm button, never anything outside an overlay); an optional step whose element is not there within 1.5 s is skipped |
| **Error box with Retry** | a Retry click is optional | a step whose element is missing, or that landed on the wrong page type, presses Retry once and tries again |
| **Endless lists** (scroll, "Load more") | "Load more" clicks are paging, like Next; the record's click is `paged` | the list is grown (`load_more`: a Load more button, else scrolling the window and the biggest scroll box) until the record's row shows: up to 15 times while paging, 8 for a step found by the record's value |
| **Forms inside iframes** | | elements and form controls are looked for in every frame (main first); page reads add each frame's text |
| **ARIA radio groups and checkboxes** (no native input) | keyed `aria:` + the group's name (the recorder notes the group; a lone ARIA checkbox by its own name); a choice with no group and no name refuses the plan | read by `SCRAPE_CUSTOM`, set by clicking the option and checking `aria-checked`; compared like native radios and checkboxes |
| **Searchable comboboxes** (options in a portal, typing alone chooses nothing) | a click or typing on the box followed by a click on an option is **one** control (`combobox`) | type, wait for the options (anywhere in the frame), click the one that matches, check the box shows it; compared like a select |
| **Rich-text notes** (`contenteditable`) | a control keyed by id or `edit:` + label, type `richtext` | filled like a note; compared like a note |
| **Buttons on the route that change something** (Start review, Assign to me) | marked `saves` | never pressed in a dry run; pressed once in a real run (counts as acting, never pressed again after a re-login) |
| **No form, one button** (Approve on the record's page) | that button is the saving step, unless the instructions choose between outcomes | a dry run presses nothing |
| A **dialog that appears after the form was partly filled** | | nothing in it is pressed, and the record is not done again by anyone (it may already have saved) |

## Running a plan (`run_plan`)

For one record:

1. **Gather**: replay each step with the record's values filled in. After every click: read the page, check it is the
   expected page type, and check that the page **mentions the record's key** (`invoice_no`), so landing on another
   record's page stops the run.
2. **Slots**: before the first step that needs one, **one model call** copies all slot values from the pages read so far
   (examples from the first record are shown as examples only). The answer must appear **verbatim on a page**, which stops
   the model from answering with an example or inventing a value.
3. **Read the form**: every control in the form (or dialog) of the first field the plan knows, else of the button that ends
   the step (label, kind, options, whether it is visible). Radio buttons and same-name checkboxes are one control each.
4. **Decide the form** (below).
5. **Fill the form**: controls that only appear after an earlier choice are filled on a later round. A form in several steps
   is read, decided, filled and moved on one step at a time ("Forms in several steps" above). The form must pass its own
   validity check (`checkValidity`) before anything is pressed.
6. **Save**: the trailing clicks. From the first one the record counts as *acted*: a later failure is never retried.

Every page read (`_quick_read`) first opens collapsed `<details>` sections, waits for the text to stop changing and for any
"Loading" to go, and adds each table row with its headings.

### Deciding the form values (`_decide`)

This generalizes the recipe's decision prompt from "which button" to "which values":

- The prompt gets the task instructions, the record, the text of every page visited, and the form's fields.
- The model writes, for **every rule in the user's order**: a boolean `expr` formula, and the `values` to enter if that
  rule is the one that applies. Code evaluates the formulas (`recipe/formula.py`: exact decimals, plus `abs`, `min`, `max`,
  the only calls allowed; inside a call a comma separates arguments, elsewhere "6,50,000" is a number);
  **the first true rule wins**, never the model's own choice.
- Numbers the form needs are written as `{= formula }` inside a value, for example `received {=20+15+5}`, and computed by
  code. A value that cannot be computed refuses the whole answer rather than typing half of it.
- A choice must be one of the listed options exactly; fields copied from the record are never changed by the model.
- A **number field** gets digits only: currency markers (`₹`, `Rs`, `INR`, ...), commas and spaces are stripped and a pointless
  `.00` dropped, but an answer with any other words is **refused** (stripping them would be a guess). Found when the refund
  amount `₹6,400` could not be typed into an `<input type="number">` (nothing was saved; the live agent did that record).
- The model also reports `missing` (empty when the pages hold everything the rules need); anything else refuses the answer.
- For every rule it also says `known`: whether the pages show what that rule's condition needs. A rule marked not known that
  comes before the rule that applies refuses the answer (the record goes to the live agent). A not-known rule *after* the one
  that applies does not matter.
- On a form in several steps, values entered on earlier steps are shown as "already entered", and fields of later steps the
  plan knows are asked for in the same question.
- **Voting**: two passes at once (the second with a little randomness), a third only when they disagree; two must agree.
  Free-text fields (a note) agree when they hold the same numbers, so different wording is not a disagreement.

Cost for one record on its own: **3 model calls** (1 slot call + 2 voting passes), 4 when the third pass is needed, against
the live agent's 15 to 17 steps. With several records in flight the calls are **shared** (next section). Since 10 Oct a slot can
be read by code and a decision by a program, which brings a trusted kind of record to **0 calls** (below).

## Speed: cheap page reads and shared model calls (added 8 Oct 2026)

Two changes took a plan record from about 18 s and 3 calls to about 4 s and under 1 call.

**Cheap page reads.** After each step the plan reads the page with `_quick_read`: wait for `load`, then read until two reads 150 ms
apart agree (and the text no longer says "Loading"), capped at about 4 s. The general reader the recipe uses also waits for
the network to go quiet, which costs at least half a second on every page.

**Shared model calls (`recipe/batcher.py`).** Records run in parallel in their own browsers. When several reach the same
question (copy my slot values; decide my form) close together, one call answers all of them:

- Each batch of records has two `Batcher`s (slots and form decisions), made by `PlanBatchers` in `plan_exec.py`.
- A record **enters** a batcher when it starts and **leaves** when it has asked (or ends without asking). A group is sent as
  soon as it holds 5 questions, or **every record still on the way has asked**, or 4 seconds have passed.
- One prompt holds several records, each under its own heading with **its own pages**, and the answer is a list keyed by record.
  Every record is answered, voted (two passes, the second reversed, a third for disputes) and checked on its own: slot values
  must still appear verbatim on **that record's** pages; formulas are evaluated per record.
- A record the model left out, or answered unusably, **asks again alone** (a group of one); the first record of a group is
  charged for the call so the totals stay honest.
- Proof dry runs always run alone (one record at a time), through the same prompt code with one record in it.

Measured on AP Desk (doc 09): 100 of 100 correct at **0.62 calls per record** (20 groups of 5, for both questions) instead of
3.0. Because a group waits for its model call, **use more browsers than the group size** (8 to 10 for groups of 5) so one
group's call overlaps the next group's page loads: 5 browsers took 524 s for 100 records, 10 took 320 s.

## Trust: a plan must be proven before it is used

One trace cannot show which parts of a route are fixed and which vary. So a new plan is **untrusted**:

1. The next records are **proof records**, run one at a time. For each: the plan does a **dry run** (everything except the
   saving clicks) in a **second tab, before the live agent touches the record**, so it cannot copy an answer the agent
   already saved. Then the **live agent does the record normally**.
2. The two are compared: what the plan *would* have entered against what the agent *did* enter (`values_agree`: selections
   and dates exactly, free text by its numbers).
3. **Same values**: one agreement. After **2 agreements** (or 3 if both made the same choice, because two identical choices
   prove less than two different ones) the plan is **trusted** and the remaining records use it.
4. **Different values**: the plan is **thrown away** at once and every other record uses the live agent. The decision to
   throw away costs money, but a wrong plan costs correctness.
5. **A dry run that only failed to run** (model hiccup, unreadable formula) proves nothing either way, and the live agent
   does the record regardless, so up to 3 such failures are tolerated before the plan is dropped.

### Trust per kind of record (added 9 Oct, after run #61)

Being trusted is not enough. In run #61 the plan was proven on three "wrong item" returns (they only need the order page), then
decided a change-of-mind refund and **guessed** its restocking fee (15% instead of 20%) without ever opening the policy table, and the
model's own `known` answer did not catch it. So a trusted plan now also tracks **which kinds of record** it has been checked on:

- A **kind** is the rule that applied (its place in the instructions) plus the choices made: `rule 4: disposition=Refund`,
  `rule 1: outcome=Reject` (amounts and free text are not part of it). `PlanOutcome.branch`.
- A proof record that agrees marks its kind as checked (`trust.branches`).
- A trusted plan that meets an **unchecked kind** decides it, says what it would enter, and stops **before typing anything**; the
  live agent does the record, and the two are compared (`_check_branch`). Same values: that kind is trusted from now on. Different
  values: that kind is always left to the live agent; and if the agent's route read more pages, the plan is extended as usual.
- So each kind of record costs one live record the first time it is met, and a plan never decides a kind of record it has not
  been seen to get right. Such a refusal is not a failure of the plan (it does not count toward dropping the plan).

### Learning from proof records that the plan could not do

When a proof record's dry run refuses because its pages lack what a rule needs (a branch the plan has not learned), the live agent
does the record anyway; its route is used to **extend** the plan at once (the proof starts again), instead of counting an
inconclusive dry run. Four inconclusive dry runs still drop the plan; refusals for missing pages no longer add to that count, and
they do not count toward "the plan failed 3 records in a row" either (in run #61 that rule dropped a plan that was refusing exactly as
it should, and 11 records ran on the live agent: 427 calls). The same goes for a proof record where the plan **answered differently**
and the live agent's route read page types the plan does not: the plan answered without what it needed, so it is extended rather than
thrown away (run #62: a refund without the restocking fee, because the plan had never read the policy table).

**What trust per kind does not cover yet.** A kind is the rule plus the choices, so one kind can mix records that need different pages
(an unopened change-of-mind refund needs no fee; an opened one needs the policy table). Once the easy case is checked, the kind is
trusted, and a plan still lacking the policy page relies on the model's `known` answer, which said yes without the facts twice on
9 Oct. Keep verification on; candidate fixes are in doc 11.

### Self-correction during proof only

If a click's fixed label is not on the page for this record (for example a vendor's name that the agent clicked without ever
typing it), it was record data. During a dry run the plan turns that label into a new slot and tries again; its earlier
proof is wiped and starts over. A **trusted** plan is never edited on the fly: it fails that record before saving, and the
live agent does it.

### When a trusted plan fails on a record

Before any saving click: the record goes to the live agent on a fresh page (`plan+live`). After a saving click: the record is
marked failed, never retried (it could act twice). Three failures in a row drop the plan for the rest of the run.

## The safety fix to the old click replay

The cause of the 1-of-10 result was the v2 replay path misfiring. It now decides *per workflow* whether replaying the first
record's clicks is safe (`replay_verdict`), judged from the full trace right after the first record:

- **Unsafe** if any typed or chosen value was not a copy of a record field (it was read or worked out), or if any click's
  locator names something belonging to the first record (a link to its page, a button labelled with its id).
- If unsafe: the saved click steps are **deleted** and the workflow is marked "do not replay", so every later record goes to
  the recipe, the plan, or the live agent. A first-record trace that **is** safe (a plain form fill) replays exactly as
  before.
- Because the saved click steps used to double as the marker "this workflow has been learned", the first-record check is now
  `_already_learned`: replayable steps, **or** a recipe or plan, **or** the do-not-replay verdict.

## Order of choices for each record

```
replay (only if judged safe)  ->  recipe (Credit Desk shape)  ->  plan (anything else)  ->  live agent
```

Modes in the log: `cold` (first record), `live`, `replay`, `recipe`, `plan`, and `plan+live` (a plan step failed before any
save, so the live agent redid it). Records that prove a plan show as `live`, with notes saying how the comparison went.

Where it is stored: the `recipes` table holds one JSON row per workflow variant. Its `kind` is a plan (with its trust
counters), a legacy recipe (no `kind`), or **`live_only`** (nothing could be learned, or a plan was thrown away: the reason
is stored, and later runs skip learning). Bumping the workflow's cache epoch forgets all of it.

## Results (details in doc 09)

AP Desk, all 100 invoices from an empty cache, **100 correct**, 1 teaching record + 2 proof records + 97 plan records, 0
fallbacks:

| | One at a time (run #45) | Batched, 8 browsers (run #47) |
|---|---|---|
| Plan records: model calls | 291 (exactly 3 each) | **77** (0.8 each) |
| All AI calls in the run | about 343 | **126** |
| Time for the 97 plan records | about 14.5 min | **5 min 51 s** |

(The live agent: about 1,500 calls and about 70 minutes.) Run #47's total time, 18.5 min, is misleading: its two proof records took
about 10 minutes each because the primary model's daily quota was already spent and the live agent was on the slow fallback
model; normally a proof record takes 1 to 2 minutes.

## What it handles, and what it does not

| Handles | Does not (yet) |
|---|---|
| A list with no search that must be paged through, by clicks or by a typed `?page=` address | A route that **branches** is learned one branch at a time (the first record of another branch goes live, then extends the plan) |
| Links whose text is an id; plain clickable cells and rows (found by their text) | A fixed label that is record data and was never typed (corrected during proof only) |
| A calendar (driven by code), or another custom widget (a scoped agent step, never saving) | Comparing a widget's value with the live agent in the proof |
| Very long pages (labelled table rows, cut to the lines that matter to the record) | Pages where the lines that matter share nothing with the record's other pages |
| Values from the record, read from a page, or worked out | A form with typing or key presses in the middle of a widget |
| Search-then-open routes, including type-to-filter boxes with no button | Accordions that are not `<details>` (they are clicked only as the first record clicked them) |
| Single-page apps: hash routes, content that loads late, random ids with or without digits | Tabs the first record did not open (a record needing one goes live once, then the plan is extended) |
| Tabs and collapsed sections; detours and guessed dead-end addresses in the agent's route | |
| A conditional form, confirm dialogs, and **forms in several steps** (Next) with radio buttons, checkboxes and groups of checkboxes | |
| (10 Oct, fake model only) A sign-in that runs out, cookie banners and pop-ups, Retry boxes, endless lists, forms in iframes, ARIA radio groups and checkboxes, searchable comboboxes, rich-text notes | Not tested: sign-ins with a second factor or a CAPTCHA, pop-ups whose button is not named like Accept / Got it / Close, shadow DOM, frames from another site |
| (10 Oct, fake model only) Deciding with **no model call** once a decision program is trusted for a kind of record; refusing answers whose numbers, dates or quotes are not on the pages; checking a save on the record's page | A program written by the real model has not been tried; a kind of record that needs a fact the program does not read stops it (the model decides) |
| Controls whose ids change on every render (found by name) | Two unrelated controls sharing one name |
| Several partial values to add up (computed by code) | Pages too long for 3,500 characters each (the end is cut) |
| Detecting a plan that is wrong, by comparison with the live agent; refusing to decide when a rule's facts are not on the pages; checking each kind of record once against the live agent before deciding it | Kinds of record that are rare cost one live record each the first time |

Honest caveats:

- The proof stage compares two or three records. Since 9 Oct a kind of record none of them was is checked once against the live
  agent before the plan decides it (trust per kind, above), so an unproven branch costs a live record instead of a guess. Within a
  kind, a plan is trusted on what it got right before; verification (a grader or an answer view) should still be on.
- It is only proven on sites that we built ourselves (Credit Desk via the recipe; AP Desk, Returns Desk and Expense Desk via the
  plan; the Claims Desk only with a scripted trace and a fake model). On 9 Oct the Returns Desk and the Expense Desk were run end to end with the live agent from an empty cache, but only on
  8 to 16 records each, to save the day's model quota (doc 09).
- Each plan record replays the agent's own route. Detours that come back to the same address are cut (9 Oct), but a page the
  agent read and did not need is still visited.
- Since 10 Oct a wizard's dry run never presses Next, so a later step's field the plan has never seen cannot be compared in a
  dry run (it is decided on its own step in a real run).
- The 10 Oct additions (programs, grounding, read-back, coverage, pruning, pacing, real-world pages) have **not run live** yet.

## Tests and tools

- `python tests/test_plan.py`: 62 checks (54 before 10 Oct). Part 1 needs nothing (batcher behaviour, compiling the real AP trace, slot detection, no
  locator pointing at record 1's own id, the replay verdict, comparing values, formulas). Part 2 runs plans in a real headless
  browser against AP Desk with a **fake model** that answers from the site's own data (a dry run saves nothing, five outcome
  types saved correctly, four records at once share their model calls, a slot value not on the pages is refused, a wrong record
  fails before saving, the healing rule). It resets the site's decisions.
- `python tests/test_plan_returns.py`: 72 checks on the hard shapes (52 before 10 Oct). Part 1 compiles the recorded Returns Desk trace (pager clicks
  dropped, paged links, id-text links as slots, the ways back as `revisit` steps, the calendar as a widget, Save and Confirm as the
  saving clicks, plan extension, the `missing` and `known` refusals, trust per kind of record, learning from proof records), a messy route like the live agent's real ones (a typed page
  address, a guessed "Page not found" address, a typed address it read, the list typed again), and a clicked table cell. Part 2 runs
  plans against the Returns Desk with a fake model (finds a return on the last page, reads a 19,600-character page, sets the calendar
  by itself, saves; a picker it cannot open goes to the agent step; with neither it stops before saving).
- `python tests/test_plan_expense.py`: 88 checks on the single-page app and the wizard (42 before 10 Oct). Part 1 compiles the live agent's **real**
  Expense Desk trace (search box, clicked row, the record's page type with a digit-less id, a tab found by its role, the detour cut,
  the wizard's steps and controls keyed by name), a real trace where the agent clicked the table row, and the real traces of two
  proof records (a deduction and a rejection) to check that branch fields are learned (also through the orchestrator's proof step)
  and that their live values agree with the plan's. Part 2 runs plans against the site with a
  fake model: collapsed sections read, the row locator picking exactly the record's row, a dry run that walks the three steps and
  saves nothing, and one record of each outcome
  (approve, deduction, send back with one and with two boxes ticked, reject) saved correctly.
- `python tests/test_plan_hazards.py`: 58 checks on the Claims Desk (doc 12): the scripted trace compiled (login, optional
  dismissals, the row found by growing the list, the iframe's ARIA and combobox controls), made-up pages served from memory, then the
  real site with a fake model (sign-in and its expiry, banner, pop-up, Retry, a deep row, every outcome saved, read-back, a skipped
  side trip).
- Added on 10 Oct to the Returns file: grounding refuses the guessed fee, slots by code, a hand-written program decides all 8 kinds
  of return with 0 model calls; to the Expense file: a program through the wizard with 0 model calls, shadow mode in a dry run. The module tests of the 10 Oct round are listed in doc 12.
- Every test file with a part 2 takes `--offline` to skip it. Part 2 resets the site's decisions, so **never run it while a real run
  is using that site** (on 9 Oct that wiped two decisions of a live run).
- `python tests/score_plan.py 100 10 [returns]`: dry-runs the plan with the **real** model on N records, several at a time through
  the batchers, and scores the decisions against `expected.json`, with no live agent (about 0.6 calls per record). This is the
  cheap way to judge a prompt change, the same technique that took the Credit Desk to 100% (doc 09).
- `tests/fixtures/ap_trace.json`: a real trace of the live agent on AP Desk, with page text. `tests/fixtures/returns_trace.json`: a
  trace of a **scripted** agent on the Returns Desk (`python tests/make_returns_fixture.py` rebuilds it), in the recorder's
  format, made on 8 Oct when the live agent could not finish a record. `tests/fixtures/expense_trace.json`: the live agent's real
  trace on the Expense Desk (run #56) plus two real proof-record traces from run #57.
