# Teaching an AI to do repetitive web work without paying for the AI every time

*A self-contained account of the project: what it is, what was tried, what worked, what failed, what was learned, and what comes
next. No prior knowledge of the project is assumed.*

---

## 1. The problem, in plain words

Imagine a company that gets 100 customer return requests a day. For each one, a person opens a website, finds the request,
looks at a few other pages (the order, the product, the return policy), decides what to do (refund, replace, reject), works out
a refund amount, types it into a form, and presses Save. It's boring, it follows written rules, and it's the same every time.

An AI that can use a web browser, clicking and typing like a person, can do this. These are called **browser agents**. You
give them instructions in plain English ("Find the return, read the order, decide using these rules, fill the form") and they
work through the website step by step.

There is a catch, and the whole project is about it: **cost and speed**. A browser agent looks at the page, thinks, picks one
click, looks again, thinks again. Doing one record takes about 15 calls to an AI model and about a minute. For 100 records
that's roughly 1,500 AI calls and over an hour. For a free-tier AI service that allows about 500 calls a day per model, a single
batch of 100 records can use up the whole day's allowance.

But the 100 records are not 100 different problems. They're **the same job done 100 times** with different data. A human
employee doesn't re-learn the job for each request. They learn it once and get fast. So the question is:

> **How do you make an AI do the first record the slow, smart way, then do the other 99 cheaply, without ever getting them
> wrong?**

That last clause, "without ever getting them wrong", is where nearly all the difficulty lives. Making something fast is easy.
Making something fast **and trustworthy** is hard. Almost everything in this story is about that tension.

The project is a program called **terminautomator**, built as a personal project. It is written in Python, uses an AI
called Gemini (from Google) and a library called browser-use that lets an AI control a Chrome browser. This is the story of
roughly the last two weeks of work on it, including what went right, what went badly wrong, and an honest look at how far it
is from a "general purpose AI employee".

---

## 2. Where things stood: "version 2"

The previous version, v2, already had two ideas.

**The live agent.** The AI looks at the page and decides each click. This works on almost any website, but it's slow and
expensive. Think of it as an expert contractor who is brilliant but charges by the minute.

**The click cache.** After the AI does the first record, the program saves the exact clicks and typed values. For the next
records it replays them without asking the AI at all. If a click doesn't match, it falls back to the AI for that step. This is
wonderful for simple forms. For example, "type this person's name, email and phone into these three boxes" can be replayed
perfectly for every person in a list, at zero cost.

The click cache has a fatal limit: it only works when every record needs **the same clicks with only simple data swapped in**.
The moment a record requires **reading something and deciding**, the cache can't help. If one return needs the "Refund"
option and the next needs "Reject", replaying the first record's clicks does the wrong thing. Worse, as we'll see, it can do
the wrong thing *while appearing to succeed*.

So the work in version 3 is the answer to: what do you do for tasks that need reading and deciding?

---

## 3. Version 3 starts: first, make it a real application

Before the clever parts, version 3 turned the tool from a set of scripts into a proper web application, because a tool you
can only run from a terminal is hard to trust and hard to share.

- A **web interface** (React, served by a Python server called FastAPI). You create a "task" by writing instructions in plain
  English, entering the website address, pasting the list of records, and choosing settings.
- A **Logs page** where you watch a run live: each record, each step the AI takes, its reasoning, and at the end an accuracy
  score.
- A **database** (PostgreSQL, hosted on Supabase) that stores everything: tasks, records, runs, logs, the AI keys (encrypted),
  and everything the system learns. Nothing about a task lives in code or config files.
- A **key pool**. The AI service limits how many calls you can make per minute and per day. The program takes several API keys
  and rotates between them. If one key hits a limit, the call goes to another key. If a model is used up for the day, it falls
  back to a second, slightly slower model.
- **Verification.** A run can be graded against a database that knows the correct answers, or by a separate grading script, so
  accuracy is a measured number, not a feeling.
- A **headless mode** so runs don't pop up browser windows.

This is background plumbing, but it matters for the story: because runs are graded against known correct answers, every
claim below ("100 out of 100") is something that was actually measured.

---

## 4. The first test site, and the first big idea: the "recipe"

To develop and measure the system, the developer uses **fake websites built for testing**. They behave like real business
websites but come with an answer key. The first is the **Credit Desk**.

**The Credit Desk task.** 100 credit requests. For each one: open the request, note the shop's name and the amount, go to a
"Ledger" page to look up that shop's history (limits, overdue amounts), apply a set of rules, and click **Approve**,
**Reject** or **Hold**. The site's own database holds the correct answer for each request, so every run can be scored.

**First, the live agent alone.** It got 35 of 36 right (97%). That sounds good, but it used about 9 AI calls per record.
Scaled to 100 records that's about 900 calls, and it took 21 minutes to do just 36 records. Too slow and too expensive.

**The recipe idea.** Look at what's the same across all requests. The *route* is identical: open the request, read it, open the
ledger, read it, decide, click one button. Only the *decision* differs. So after the AI does the first record, the program
learns a "recipe":

1. Visit these pages and read them. (Done by ordinary code, no AI.)
2. Ask the AI **one small question**: "Here's the request, here's the ledger, here are the rules. Which button?"
3. Click that button. (The buttons are labelled with the request number, so code can find the right one.)

And the questions for several records are bundled, so one AI call can decide five requests at once.

### The long climb from 80% to 100%

The recipe was cheap, but its accuracy at first was mediocre. This is the part of the story that sets up the main lesson of the
whole project. Each time accuracy was short of perfect, the developer found the *exact kind of mistake* and then **took that
job away from the AI and gave it to ordinary code**:

| Problem seen | Fix |
|---|---|
| The AI picked a button before it had reasoned about the facts | Make it write its facts and arithmetic first, and choose the button last |
| The AI matched a shop to a ledger entry with a *similar* name and got the wrong shop | Code checks that the details the AI copied appear **word for word** on the page |
| The AI made small arithmetic slips near boundaries ("is 30 days over 30?") | The AI no longer does arithmetic. It writes each rule as a **formula**, and code evaluates the formulas, in the order the rules were written, and picks the first one that's true |
| Sometimes the AI attached the wrong button to a correct rule | Take the button from the rule's own text instead of asking the AI to choose |
| Random occasional slips | **Voting**: ask twice at once; if the two answers differ, ask a third time and take the majority |

After all this, the Credit Desk scored **100 out of 100**, repeatedly, using about **66 AI calls for 100 records instead of
about 900**. It took about 6 minutes.

Two principles were born here, and they come back again and again:

> **Never ask an AI to do arithmetic or to decide which rule comes first. Let it read and translate; let code compute.**
>
> **"Similar" is a dangerous word. Whenever the AI is allowed to match things loosely, it invents things. Make it point to
> exact text.**

But the recipe had a hidden limit: it only fits one *shape* of task, "read pages, decide, click one button labelled with the
record's value". Real tasks come in many shapes.

---

## 5. The second test site, and the first disaster: 1 out of 10

A 100% score on the site you built the system around proves very little. You may simply have tuned the system to that site. So
the developer built a **second site on purpose to be different**: the **AP Desk**, an accounts-payable desk.

**The AP Desk task.** For each invoice, do a "three-way match": check the invoice against the purchase order and the goods
that were actually received, looking at about four pages. Then fill in a form: pick a status from a dropdown (approve, hold,
reject), choose a reason, type a note that contains computed numbers ("Invoiced 40, received 35"), enter a payment date, and
confirm in a popup. The pages are different in style too: normal server-rendered pages, opaque web addresses, numbered paging,
a search box.

**The first attempt scored 1 out of 10.**

What went wrong is the most important story in the project. The older click cache looked at the first record and decided its
clicks could be replayed. It then replayed *the first record's exact clicks for the other records*, including a click on a link
to the first record's own invoice. Nine records "succeeded". Nothing crashed. The program reported success nine times. Every
one of them was wrong: the same invoice processed over and over.

Only two things caught it: the website refused some of the saves, and the outside grading script compared the result against
the answer key.

> **A cache that is wrong without failing is the most dangerous kind of cache.** A slow system that fails loudly is better
> than a fast one that quietly does the wrong thing. This is why everything that follows has a "prove it first" step.

### Fix one: a safety check on replay

After the first record, the program now judges whether replaying its clicks is *safe*. It's unsafe if any typed value was read
from a page or worked out (not just copied from the record), or if any click names something that belongs to the first record
(like a link to its page). If unsafe, the replay is switched off for that task.

### Fix two: the plan engine

This is the central invention of the project, so it's worth understanding well.

After the live AI does the first record, the program studies the recording and builds a **plan**. For every value that was typed
or clicked, it asks: **where did this value come from?** There are only three answers:

1. **From the record.** Like the invoice number typed into the search box. Next time, copy it from the new record.
2. **Read off an earlier page.** Like a vendor's name seen on page two and typed into a search box on page four. Next time, find
   it again on the new record's page. (A small AI call can do this, and the answer must appear **word for word on the page**,
   which stops the AI from inventing things.)
3. **Worked out.** The decision, the reason, the note, the date. These are decided fresh for every record.

The plan is the **route** (a list of steps with the right places to click, found by name or role rather than by any one
record's specific identifiers), with each value marked as one of those three kinds.

Running the plan on a new record:

1. **Walk the route** by code. At each step check that you landed on the right *type* of page, and that the page actually
   mentions this record. If you land on another record's page, stop.
2. **Fetch the "read off a page" values**, with one small AI call for all of them.
3. **Decide the form values**: one AI question with the instructions, the pages read so far and the form's fields. The AI
   writes each rule as a formula; the code evaluates them, takes the first true rule and uses its values. The numbers come from
   code, not from the AI's head.
4. **Fill in the form and press Save once.**

Questions for several records running at the same time are combined into a single AI call.

### Fix three: prove it before trusting it

One recording of one record can't tell you which parts of the route are fixed and which vary. So a brand new plan is
**untrusted**. On the next records it does a **dry run**: it goes through everything except pressing Save, in a second browser
tab, and writes down what it *would* have entered. Then the live AI does that record normally. The program compares what the
plan would have done with what the AI actually did.

- If they agree on two records, the plan is **trusted**, and the remaining records use it.
- If they disagree, the plan is **thrown away**.

The plan can even correct itself during this proof stage. If a label the AI clicked turns out not to exist for the next record
(it was record data after all, like a vendor's name), the plan turns it into a "read it off the page" value and tries again.

### The result

The AP Desk went from 1 out of 10 to **100 out of 100**: about 126 AI calls for 100 records, about 10 minutes. The live agent
alone would have needed about 1,500 calls and 70 minutes. The cost per record fell to well under one call.

---

## 6. The third test site: designed to break it

If the second site exposed a problem, a third, harder site would expose more. The **Returns Desk** was built to be
deliberately nasty:

- A list of returns spread over **ten pages with no search box**. The only way to find a record is to page through.
- Links whose text is an **ID** ("ORD-10128", "SKU-8649"), so the order and item pages are reached by clicking text that belongs
  to the record.
- The pick-up date is chosen in a **custom calendar** made of buttons: open it, press "next month" until the right month, click
  a day number. Not a normal date field.
- A **very long policy page**, about 20,000 characters, where the restocking-fee table is thousands of characters down.
- A **branching route**. A "wrong item" return only needs the order. A "defective" return needs the item's warranty. A "changed
  mind" return needs the item, its category and the policy's fee table. One recording only sees one branch.

Each of these broke the engine, and each fix was made *general*, not specific to this site:

- **Paging.** Clicks that only move through a list are removed from the route. At run time the plan pages through the list
  until the record's link appears.
- **Links with ID text.** The ID is treated as a "read it off the page" value.
- **Custom widgets.** A run of clicks that fills one field is treated as one control. The AI decides the *value* (a date), and
  ordinary code drives a normal calendar itself. Anything stranger goes to a tiny scoped AI step.
- **Long pages.** Table rows are presented with their column headings attached ("Category: Footwear | Opened: 10% | Damaged:
  25%"), because the heading is useless 60 rows away from the row that matters. Long pages are cut down to the lines related
  to this record.
- **Branches.** The AI must say when the pages in front of it do *not* contain what a rule needs, and then the record goes to the
  live AI instead of getting a guess. When the live AI follows a longer route than the plan knew, the plan is **extended** with
  that route and must be proven again.

### The quota wall

On the first day of this, the main AI model's daily quota ran out, and the fallback model was too slow to finish even one
record. The developer also verified a surprising fact: Google's daily quota resets at **midnight Pacific time**, which is
12:30 pm in India, not at midnight local. The key pool was fixed to compute that correctly.

### The first real runs: reality is messier than a script

Up to this point, the Returns Desk had only been tested with a *scripted* imitation of the AI's recording. When the real AI
finally ran it, the program's learning step broke in five ways at once, because **real AI behaviour is much messier than a tidy
script**:

| What the real AI did | Why it broke the plan | Fix |
|---|---|---|
| Clicked a table **cell** instead of the link inside it | A cell has no name or id, so the plan could find no way to click it again | Find plain elements by their visible text |
| Typed **web addresses it guessed**, got "Page not found" | The dead ends became part of the route | Drop steps that only reach an error page |
| Typed a list page's address directly (`?page=7`) | Every record would be sent to page 7 | Treat typed page addresses as paging |
| Went back to the list and found the same record again | The plan replayed the whole wandering detour | Cut detours: when the route returns to an address it already reached, jump straight back |
| A typed address lost its destination (a plain bug) | Every dry run went to the wrong page | Fix it |

After these fixes, a fresh run scored **8 out of 8**: the plan learned from the AI's own messy recording, proved itself on
three records, ran alone on the rest, and even **extended itself** when a record needed a page it hadn't seen.

---

## 7. The fourth site: a modern web app

The Expense Desk was built to look like a modern application rather than an old-fashioned website:

- Everything on **one single web page**. Navigation changes only the part of the address after a `#`.
- Content that **loads late** from a background service, with a "Loading..." placeholder.
- A search box that filters **as you type**, with no search button, and results as clickable table rows, not links.
- **Tabs** (Summary / Receipts / Activity) that show different content without changing the address.
- **Collapsed sections** in the policy page. The text isn't there until a section is opened.
- Money in six currencies, requiring conversion before comparing against a limit.
- The decision made in a **three-step wizard in a popup window**: choose an outcome with radio buttons, then fill in fields
  that depend on the choice (numbers, a group of checkboxes, a dropdown), then tick a confirmation box to enable Submit.
- Form controls get a **new random id every time they are drawn**. Only their names stay the same.

The engine knew nothing about wizards, radio buttons or checkboxes, and these were the fixes:

- Page "types" now understand the `#` part of addresses and ids made of random letters.
- Radio buttons and checkboxes are treated as form controls, found by their stable **name**, never by their random id.
- "Next" ends a step of the form. The plan fills a wizard step by step, and checks the form really moved on.
- **Records that prove a plan teach it.** A proof record that took the "deduction" branch teaches the plan the extra fields that
  branch needs. Without this, every new branch would cost a slow live record.
- Every page read opens all collapsed sections first.

The Expense Desk runs scored 8/8, 16/16, 8/8, 8/8.

---

## 8. The scariest bug: "right by luck"

This is the finding the developer should remember longest.

On the Returns Desk, a plan that had been proven on three simple "wrong item" returns (which need only the order page) was
trusted. It then met a "changed mind" return and decided a refund by **guessing a 15% restocking fee**. The true fee, in the
policy page's table, was 20%. The plan had never opened the policy page. When asked "can you see everything you need to
decide?", the AI said yes. It was wrong, and it saved a wrong refund.

Two lessons:

1. **An AI's opinion of itself is weak evidence.** Asking "are you sure?" is not a safety check.
2. **"Proven" has to be specific.** A plan proven on one kind of case proves nothing about a different kind of case.

The fix is **trust per kind of record**. A "kind" is defined by which rule applied and which choices were made ("refund, by the
changed-mind rule"). A trusted plan decides alone only the kinds that have already agreed with the live AI at least once. When
it meets a new kind, it states what it *would* enter, **stops before typing anything**, lets the live AI do the record, and
compares the two. If they agree, that kind is trusted from then on. If they differ, that kind stays with the live AI forever.

This run scored 14 out of 14, and caught **two guesses before anything was saved**.

---

## 9. The most ambitious round: making it truly general

At this point the developer asked for something bigger: not a system that works on a few test sites, but a **general-purpose
agent** that caches as much as possible, calls the AI only when truly necessary, and handles messy real-world websites.

Even with a trusted plan, every record still cost roughly one AI call, the one that makes the decision. The ambition was to
get that to **zero**: make even the *decision* a cached, checked piece of code.

Seven pieces were designed and built **in parallel by a team of AI helper agents**, each one then reviewed by a separate AI
agent whose only job was to try to break it. Their combined work added about 6,000 lines of code and about 900 automated test
checks.

**1. Decision programs (the big idea).** After a few records, ask the AI to write the *rules* as a small program: a list of
**facts** to read off the pages (a number next to a label, a cell in a table) and an ordered list of **rules** over those
facts. Then code runs the program for each record with **no AI call at all**. The program must reproduce every recorded example
exactly before it's accepted, and is trusted per kind of record only after agreeing with the AI a couple of times.
Occasionally it's double-checked against the AI.

**2. Grounding.** Every number the AI uses in a decision must actually appear on the pages (or be derivable in one simple step
from numbers that do). A guessed 15% fee would be caught here.

**3. Pacing.** Instead of reacting to "too many requests" errors, the key pool counts calls in a sliding one-minute window and
waits when needed, staying under 14 calls a minute per key.

**4. Real-world page handling.** Forms inside **iframes**, lists that load more as you scroll, **cookie banners and "what's
new" popups** (dismissed, but never a button that confirms something real), custom searchable dropdowns, accessible-role
radio buttons and checkboxes, rich text boxes, error pages with a "Retry" button, and **login pages with sessions that expire**.

**5. Read-back.** After saving, reopen the record and check that what was saved is what was meant to be saved. A success is
only a claim until confirmed.

**6. Coverage and pruning.** One AI call reads the instructions and finds pages they mention that the recorded route never
visited (like the policy page) so the plan reads them *before* a record needs them. And pages that never turn out to supply a
fact get skipped to save time.

**7. A fifth test site** full of those hazards: an insurance claims desk with a login that expires, a cookie banner, an endless
scrolling list, random errors with a Retry button, and a decision form hidden inside an iframe with custom dropdowns.

Each piece was written, then attacked by a reviewer. The reviewers found real, serious bugs before anything was connected. A
few examples:

- A table lookup that treated "1 Year" and "1 Month" as the same row, because both start with the number 1. It would have typed
  the wrong amount.
- A table cell containing a line break was split into two rows, corrupting totals.
- A check that accepted a program that got dates and text wrong.
- A popup dismisser that could have clicked "OK" on a genuine confirmation.
- A read-back comparison that ignored the word "not", so "not approved" looked the same as "approved".

All were fixed, with a test for each. The pieces were then wired into the engine one step at a time, with another independent
review at each step and a final four-way safety review: could it ever save a guess, press Save twice, break the old sites, or
waste AI calls?

After all of that, about **900 automated checks passed** across 12 test files.

---

## 10. The humbling part: it got worse on real runs

Then the real test: fresh runs of 16 records on the Expense Desk. The old engine had scored 16/16 and 8/8 on it. The new engine
scored **15, 15, 15, 12 and 14 out of 16**. It was **worse**.

Every run exposed a new bug that no test had caught. A few examples:

- **Facts hidden on another tab.** The code that looks for a fact on a page of a given type only checked the *latest* view. A
  fact on the Receipts tab was invisible after the plan moved back to the Summary tab.
- **A "Back" button learned as a form field.** Once, the live AI pressed Back inside the wizard to change something. The plan
  recorded "Back" as a field to be filled in. On later records it pressed Next, then couldn't "set" the Back field, and failed
  halfway. Three records were lost, because pressing Next counts as having already acted, so they couldn't be safely retried.
- **Incomplete examples.** The examples used to teach decision programs were missing the wizard's later steps, so correct
  programs were rejected for "wrongly" filling fields.
- **A confirmation tick on a later step** was treated as a disagreement between the plan and the live AI.
- Small gaps in the program language itself: no way to say "if there's no exchange-rate row, the rate is 1".

Each was fixed. But the biggest disappointment was this: **across five live runs, a decision program was never successfully
learned and used.** Offline, from six recorded records, the AI did write a correct program on its first try, twice. But the
real live pipeline kept feeding it imperfect examples.

And there was a second, quieter problem: several of the misses in those runs were **the live AI's own mistakes**. The live AI is
the "referee" the plan is judged against. When the plan said "Approve" and the referee said "Send back", the plan was right,
but the system trusted the referee. In the final check, the one miss was exactly this: the plan had the right answer and the
live AI was wrong.

The honest diagnosis: **too much new code was built before any of it had been tested live.** Six thousand lines, written
quickly, with great test coverage against *fake* AI answers, and then exposed to the real, messy AI for the first time.

---

## 11. The decision: switch off what isn't proven

Rather than keep patching, the developer chose a safer path (called "option B"). The pieces that hadn't proven themselves live
are **switched off by default**: decision programs, coverage, pruning, read-back, reading values by code, and extending plans
when answers agree. They can be turned on with a setting for experiments.

What stays on: **pacing**, the **grounding check**, the **real-world page handling**, and **all the fixes** found during the
live runs.

A final check with those settings scored 7 out of 8 (the one miss being the live AI's mistake, not the plan's). The engine is
back to its reliable behaviour, with the new safety improvements added.

---

## 12. The lessons, in plain English

1. **Don't let the AI do arithmetic or decide which rule comes first.** It reads and translates. Code computes.
2. **A cache must prove itself before it is trusted**, and the proof must be specific to the kind of case, not general.
3. **A wrong cache that looks fine is worse than a slow one that fails loudly.** Always measure against something independent.
4. **Every new kind of website breaks something.** Test on sites you did not tune the system to, and expect to be surprised.
5. **The AI's opinion of itself is weak evidence.** "Are you sure?" is not a check. Checking numbers against the page is.
6. **A scripted test is tidier than reality.** The first run with the real AI broke the system five ways that scripted tests
   never showed.
7. **The referee can be wrong too.** If you grade a cheap method against an expensive AI, remember the expensive AI has bad days.
8. **Pressing "save" is a one-way door.** Decide everything first, then act once. Never retry something that may have saved.
9. **When unsure, refuse.** A refusal costs one slow record. A wrong save costs trust.
10. **Test small, live, and early.** Sixteen records revealed every problem. Nine hundred automated checks against fake answers
    did not. Build one new thing, prove it live, then build the next.
11. **Quota is a design constraint**, not an afterthought. Pace the calls, count them, and test with small batches.
12. **The AI never does the same thing the same way twice.** A plan learned from one recording must survive the other ways the AI
    might do the same task: clicking a cell versus the row, guessing an address versus following a link.

---

## 13. Some numbers worth remembering

| Site | Type of task | Live agent alone | With the engine |
|---|---|---|---|
| Credit Desk | Read, decide, click one button | ~900 AI calls per 100 | ~66 calls, **100/100** |
| AP Desk | Match across pages, fill a form | ~1,500 calls, ~70 min | ~126 calls, ~10 min, **100/100** |
| Returns Desk | Paging, calendar, branching, long page | far too costly to finish a run | **8/8** and **14/14** small runs |
| Expense Desk | Single-page app, wizard | ~8–19 calls per record | 8/8, 16/16, 8/8, 8/8 in small runs |

A plan record costs roughly **0.3 to 1 AI call**, against about 15 for the live agent, a saving of roughly **15 to 50 times**.
The expensive part is always the *teaching*: the first few records plus the proof records, about 15 to 25 calls each.

The free AI tier allows about 500 calls a day per model per Google project and 15 a minute. With two keys from two different
projects, there are about 1,000 calls a day on the main model, then a fallback to a second model. That's why the developer
tests with **small batches of 8 to 16 records** and only runs 100 once a small batch is clean.

By the end the main code was about **13,800 lines** of Python, with about **5,500 lines** of tests, plus five test websites.

---

## 14. So how close is this to a general-purpose AI employee?

Honestly: **not close. Perhaps 15 to 20 percent of the way.**

What exists is a **repeat-task engine**: give it a hundred similar records and a website, it learns the route once and then
does the rest cheaply and carefully. That is genuinely useful. But a human employee can do much more:

- **One-off tasks.** The savings only appear when records repeat the same route. A brand-new task still costs a full live run.
- **Real company software.** Everything is proven only on five test sites that the developer built. Real systems have logins,
  slow pages, odd widgets and surprises. Some of this was built for but none of it is proven.
- **Judgment.** It follows written rules. It can't weigh trade-offs or handle truly ambiguous cases.
- **Other kinds of work.** Email, documents, spreadsheets, phone calls, talking to people. It only handles web forms.
- **Reliability.** A human colleague doesn't need every answer re-checked. This system is still sometimes wrong, and so is the
  AI that judges it.

A fair comparison: it's like a very good intern for **one boring, repeating job**, not yet a general employee. But the central
trick, **teach once, prove it, then reuse cheaply, and refuse when unsure**, is the right foundation. The distance to a
general-purpose employee is mostly about reliability on unfamiliar real-world sites and handling tasks that aren't repeats.
That is a long road.

---

## 15. What comes next: rebuilding it from scratch, on purpose

The current code grew by patches. Each new site added a fix. It's now big, and the developer admitted it feels too complex to
understand. Reading 14,000 lines written by someone else gives the *feeling* of understanding without much of the substance.

So the plan is to **rebuild it from scratch in a new folder, one function at a time, by hand**, with an AI assistant guiding
each step. The reasons:

- **Understanding.** Writing something piece by piece is the surest way to really know it.
- **A cleaner design.** With every lesson known up front, the rebuild can be roughly half the size and far simpler.
- **A ready-made exam.** The five test websites with answer keys and the roughly 900 existing checks serve as a specification.
  When the new version passes the same tests, it is known to handle paging, calendars, wizards and the rest, without
  rediscovering each failure. And a written list of every bug ever found is available as a checklist.

The plan has **ten phases** and about **75 small steps**:

- **A. Foundations.** The project skeleton, settings, database, encrypted secrets, the key pool with pacing, a single gateway
  that every AI call passes through (counting and pacing them), a minimal web server and web page.
- **B. The live agent.** Run one record with the AI and record everything it does; a run loop for many records; a logs page.
- **C. Grading.** Score every run against the answer key. From here, every change is measured.
- **D. Learning a plan.** Turn a recording into a plan: classify each value, find elements by name or text rather than by one
  record's ids, handle paging, detours and dead ends.
- **E. Running a plan.** Walk the route, read the values, decide with formulas the code evaluates, fill the form and save once.
- **F. Proof and trust.** Dry runs next to the live agent, trust per kind of record, extending plans, and a better referee.
- **G. Hard shapes.** Calendars, long pages, wizards, radio buttons and checkboxes, single-page apps, iframes, popups, logins,
  endless lists and custom dropdowns.
- **H. Safety checks.** Grounding (numbers must be on the pages) and read-back (look at what was saved).
- **I. Scaling.** Bigger runs, concurrency, cost reports and a review screen in the app.
- **J. Research.** Decision programs, only after everything else works, built **offline first** on recorded examples, and
  switched on only when they reproduce recorded runs exactly.

A few design decisions already made for the rebuild, each answering a specific failure:

**A better referee.** The live AI does the record but **stops before saving**. Its answer is compared with a second,
independent answer: the plan's, or a separate decision over the same pages. If they differ, a stronger model gives a third
answer. Two out of three must agree. If not, the record goes to a **human review queue** and nothing is saved. This fixes the
problem where a wrong referee overruled a right plan.

**Wizards.** Everything about a multi-step form is decided and checked *before* the first irreversible click. Whether a click
is irreversible is learned from whether it actually sent a save request to the website. After that click, nothing is ever
redone from scratch.

**Never save twice.** A record in the database notes "already acted" before every irreversible click. After a crash, the record
is marked "unknown", never silently redone.

**Fewer moving parts.** The old click-replay is dropped (the plan engine covers it), and the Credit Desk's special recipe is
folded into the plan engine, so there is one general mechanism instead of three.

**Process discipline.** Build one new thing at a time and prove it live before adding the next; test with small batches;
keep the unproven research behind switches that are off by default.

---

## 16. The whole story in one paragraph

Browser AIs can do repetitive web work but are slow and expensive when they redo every record from scratch. The project
learned to watch the AI do the first record, turn that into a route plus a few small AI questions, make the plan **prove itself
against the live AI before being trusted**, and refuse to save anything it can't back up with the record, the pages or the
instructions. That brought costs down by a factor of 15 to 50 and reached perfect scores on several test sites. Each new,
harder test site broke something, and each fix was made general. A final, very ambitious attempt to remove the AI from the
decision entirely was built quickly, tested heavily against fake answers, and then performed worse on real runs, which taught
the most important lesson of all: **build one thing, prove it live, then build the next**. The unproven parts were switched
off, the reliable core kept, and the next step is a patient, hand-built rebuild, designed with every lesson in mind, to make the
whole thing understood as well as working.
