# Problems we faced

Everything that went wrong while testing on several websites, with many AI agents running in the background. About a 10-minute
read, grouped by where each problem came from. Each item says what happened and what was done about it.

---

## 1. Problems from running many background agents

- **The AI assistant's own usage limit stopped the work 5+ times.** The build and wiring workflows died mid-run each time the session limit hit.
  The limit resets at odd times (7:30 pm, 12:30 am, 5 pm, 5:40 am, 10:30 pm), so each hit cost hours.
  - *Fix:* the workflow tool can resume. Finished steps replay from cache and only the failed ones rerun.
  - *Lesson:* long multi-agent jobs need to be restartable, and a result like `[null, null, null...]` doesn't mean "nothing
    happened", it means "died".
- **Half-written files.** When a builder agent was cut off, its files were left partly written. The next try had to read them,
  keep what was right, and finish the rest, not start over.
- **A workflow script couldn't be reloaded** from its saved path. It had to be copied into the scratchpad folder first.
- **Many agents editing one folder.** Seven packages were built at once, so each agent could only touch its own files. That
  worked, but the shared spec was a guess: some function names in the finished code differed from the spec. *Fix:* later steps
  were told "the code is the truth, not the spec".
- **Reviewers found real bugs the builders missed.** Examples: a table lookup that treated "1 Year" and "1 Month" as the same
  row; a cell with a line break split into two rows; a popup-closer that could press a real "OK"; a read-back that ignored the
  word "not". This is the best argument for having a separate agent try to break each piece.
- **Reports can be wrong.** One helper said the "Back" bug lost 2 records, but the run log showed 3. Always check agent claims
  against logs.
- **Dozens of "background tasks" piled up** in the app's task list, mostly finished test runs. Harmless, but it made the whole
  thing look scarier than it was.
- **The spawned "100-record runs" session never ran anything.** It hit the quota, then the session limit, and sat idle for a day.
- **Too many things changed at once.** With 7 pieces landing together, it was hard to know which one caused a live failure.

## 2. Problems with servers, sites and tests colliding

- **Tests that reset a test site wiped a live run.** A test that resets the Returns Desk was run while a real run was using it.
  Two decisions were lost.
  - *Fix:* every browser test takes `--offline`, and the docs say never run one during a live run.
- **The server loads code only at start.** More than once a fix was made and a test run without restarting, so the run used
  the *old* code (run #54 did exactly this). Also stale `__pycache__` folders. *Fix:* every fresh test now starts with a restart
  and a cache clean.
- **Aborting didn't stop everything.** Records already queued behind the "learning lock" ran live after an abort. Fixed.
- **Servers died** after breaks, so five sites and the app had to be restarted by hand.
- **The Credit Desk can't be reset by the agent.** Its reset button is a shared database action, which the safety system
  blocked, so it has to be clicked by hand. Same for reading its database with the service key.

## 3. Problems with the Gemini quota

- **Daily limits on both keys.** About 500 calls a day per model per Google project. The two keys turned out to be in
  different projects, so about 1,000 a day, but that still ran out during run #61.
- **The quota was reported as exhausted when it was not.** The conclusion came from the assistant's own call count instead of the daily-limit
  error, and those counts also undercounted.
- **Per-minute limit (15 a minute).** Eight browsers at once produced dozens of "429" errors. *Fix:* a pacing layer that counts
  calls per minute per key and waits.
- **The fallback model was slow and flaky.** 12 seconds to answer "OK", 75-second timeouts, 503 errors. Run #48 was aborted
  after 7 minutes.
- **Wrong reset time.** The key pool assumed midnight UTC. Google resets at midnight Pacific (12:30 pm India). Marked-out keys
  stayed "out" after the real reset until a restart.
- **Every live run cost 100 to 430 calls**, so each bug found cost a chunk of the day.

## 4. Problems on the AP Desk (the second site)

- **First run: 1 out of 10.** The old cache replayed the first record's clicks for everyone and reported nine false successes.
  Only the site's refusals and the outside grader caught it.
- **A vendor link stored as a fixed label.** It was really record data, so the plan failed its first proof.
- **A formula using `abs()` wasn't allowed.**
- **A `{= ... }` calculation written inside a formula** wasn't understood.
- **An empty `values` list** made the model's answer useless.
- **`max(1,2)` turned into `max(12)`** because the cleanup stripped the comma meant to separate arguments.
- **A prompt typo** doubled the braces, so the AI wrote `{{= ...}}` and refund amounts came out as the text `'{6400}'`.
- **A number box rejected `₹6,400`.** Nothing saved; the live agent redid that record.
- **A README line claimed "100% offline"** before it was measured. Caught and checked afterwards.

## 5. Problems on the Returns Desk (the hard site)

- **The real agent is messier than the scripted test.** The first real run broke the plan compiler five ways:
  - it clicked a table cell with no name;
  - it typed addresses it had guessed and got "Page not found";
  - it typed a page number directly (`?page=7`);
  - it went back and found the same record again, which the plan replayed as a detour;
  - a typed address lost its destination (a plain bug).
- **The guessed fee.** A plan proven only on "wrong item" returns refunded a "changed mind" return with a made-up 15% fee
  (right: 20%) and never opened the policy page. The AI had said it could see what it needed.
  - *Fix:* trust per kind of record.
- **A healthy plan was dropped.** Its correct refusals ("the pages lack what this rule needs") counted as failures, so "3 in a
  row" switched the plan off, and 11 records ran live (427 calls).
- **The plan answered before reading the policy table**, which is a different case from the one above. Fixed by extending the
  plan instead of throwing it away.
- **Two refunds were right by luck.** Decided without the item page, they happened to match the answer key.
- **A calendar needed an agent step** at first, which cost about 124 extra steps over 31 runs.

## 6. Problems on the Expense Desk (the modern web app)

- **Wizard form.** Radio buttons, checkboxes and "Next" weren't understood, so the plan replayed them as fixed clicks.
- **Random ids on every draw.** The plan looked fields up by id and missed them. *Fix:* use the stable name.
- **Hash addresses** (`/#/claims/...`) were all seen as the same page. Random ids with no digits weren't recognised as ids.
- **A tab was found as a "button"** and not as a tab.
- **Collapsed sections** hid text until opened.
- **A clickable table row** had no name, so the plan couldn't find it. A regular-expression escape the browser rejects hid
  the fix for a while.
- **A bug in a test site:** an old toast timer hid a newer message.
- **A detour cutter ate the "Review claim" click.** Caught by tests on the real trace.
- **Facts on a second tab were invisible.** The code only searched the latest view of a page, so the Receipts tab's rows were
  lost after going back to Summary.
- **"Back" was learned as a form field.** Once, the live agent pressed Back inside the wizard, and later records failed trying
  to "set" it. Three records were lost, because pressing Next counts as acting and can't be safely retried.
- **A confirmation tick on a later wizard step** counted as a disagreement between the plan and the live agent.
- **Grounding sometimes refused a right answer.** It's meant to catch invented numbers, but it did so on a correct one, and
  only some of the time.

## 7. Problems with the live agent itself (the "referee")

- **It's sometimes wrong, and the plan is judged against it.** In the final check the plan had the right answer and the live
  agent had the wrong one, so the system trusted the wrong one.
- **It reports success when it hasn't succeeded.** One record was marked done but was never saved.
- **It does things differently every time:** a cell one day, a row the next; guessing addresses; pressing Back.
- **It learns from its own mistakes.** In one run the first record's answer was wrong, and the plan was built from it.

## 8. Problems with the big "general-purpose" round

- **About 900 tests passed against fake answers, but live runs scored 12 to 15 out of 16.** The old engine had scored 16/16.
  Tests that use a fake AI can't find the problems real AI output causes.
- **Decision programs never worked live.** Across five live runs, no program was learned and used. Causes found:
  - the examples were incomplete (missing the wizard's later steps);
  - stray fields like the search box leaked into the field list;
  - the language was missing small features: `if()`, defaults like "no exchange-rate row means 1", and empty lists.

  Offline, the AI wrote a correct program on its first try twice, so the idea is sound; the live pipeline wasn't ready.
- **The new test site (hazards: login, popups, iframe form) was built and tested offline only.** It never ran live.
- **Too much was built before testing live.** About 6,000 lines landed before any real run. That is the main lesson.

## 9. Process problems

- **Each live test found a new bug.** Fix one, run again, and something else breaks. Costly in quota and time.
- **Test counts grew faster than confidence.** Five rounds of fixes without a clean 16/16 was the signal to stop.
- **The docs grew faster than they could be read.** By the end there were 13 files. *Fix:* four new docs
  (two short, two long) and the old ones moved to `docs_old`.
- **Complexity.** 13,800 lines of engine grown by patches is too big to hold in one head, which is the reason for the
  rebuild plan.
