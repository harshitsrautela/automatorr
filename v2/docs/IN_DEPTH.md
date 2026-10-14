# terminautomator v2: An In-Depth Guide to Layers 2 to 5

## Part 0: Before you start

This document explains six files of the terminautomator v2 codebase in plain language, one file at a time, in the order you would want to learn them. It is written to be read aloud or turned into audio, so there are very few tables and no big blocks of code. Each section covers what the file is for, how it is built function by function, which other files it depends on, which files depend on it, and what to say if you have to explain it to someone else in a meeting.

The six files, in the order we will cover them, are these. First, `cache/store.py`, which is the memory of the system, a small database that remembers what the system has learned about a web form. Second, `cache/instrumentation.py`, which is the watcher that decides what is worth remembering. Third, `replay/engine.py`, which is the part that uses that memory to fill in a form quickly, with no artificial intelligence involved. Fourth, `agent/prompts.py`, which writes the instructions given to the language model. Fifth, `agent/runner.py`, which is the one place that actually calls the language model and drives a browser with it. And sixth, `evaluation/evaluator.py`, which is the independent grader that checks whether the data really landed in the database.

### The story in one minute

The goal of the whole project is to take a batch of records, say a hundred people with names, emails, departments and so on, and enter them into a web form automatically. In the first version of the project, version one, a language model called Gemini looked at the form and decided what to click for every single record. That worked, but it was slow and expensive, because every record cost around a dozen or more model calls, and the free tier of the Gemini service only allows a small number of calls per minute and per day.

Version two has one big idea: only the first record needs to be figured out by the language model. While the model fills in the first record, a watcher records where each field lives on the page. Every later record then replays those remembered locations with an ordinary browser automation tool, with no language model at all. If a replay step cannot find its element, for example because the page changed slightly, the language model is called for just that one field, it fixes the problem, and the fix is written back into the memory so nobody has to fix it again. And at the very end, a separate grader reads the real database and checks the result, without trusting anything the automation claims about itself.

### A few words you will hear again and again

A record is one row of input data, for example one person. A field is one piece of a record, such as email or department. A workflow is one target form; here it is the People form of an app called DataDesk, and it is identified by a name, `datadesk_people_form`. A path variant is a label for which branch of a form a record follows, and for DataDesk it is always just the word default. A step is one action in the browser, such as a click, typing into a box, or choosing from a dropdown.

A cold run is the very first run for a workflow, when nothing is remembered yet and the language model does everything. A replay is a later run that uses the remembered steps. A cache miss is when a replay step fails, because the element could not be found or the action did not work. Self-healing is what happens next: a small, scoped language model call fixes just that one step and writes the corrected knowledge into the cache.

Three tools show up repeatedly. Playwright is a popular browser automation library that can click and type in a real browser without any artificial intelligence. A library called browser-use is the layer that lets a language model drive a browser. And Chrome DevTools Protocol, usually shortened to CDP, is the remote-control channel that lets different programs control the same running Chrome window. Playwright and browser-use are both pointed at the same Chrome window through a CDP port, and that is how replay and the live model can take turns on the same half-filled form.

### How the six files fit together

Here is the whole picture in one pass. When the first record arrives, the runner file starts a browser-use agent with the instructions written by the prompts file. While that agent works, the instrumentation file watches every step and writes what it learns into the store file. When later records arrive, the engine file reads those stored steps and replays them. When a replay step fails, the runner is called again, this time with a narrow instruction from the prompts file, the instrumentation watches again, and the corrected step is written back into the store. At the end of the batch, the evaluator reads the real database and produces the grade. A seventh file, the orchestrator, is the conductor that decides who does what and when. It is the biggest file in the project, and it is not covered here. Whenever this guide says "the orchestrator", it means that conductor.

---

## Part 1: `cache/store.py`, the memory of the system

### What it is and why it exists

This file is the cache itself. It is about two hundred and eighty lines, and it is a thin layer over SQLite, which is a small database that lives in a single file on disk. The file is `storage/cache.sqlite3`. The most important thing to understand about this module is how little it knows. It does not know about browsers, Playwright, browser-use or language models. It only knows about rows in two tables. That keeps it simple and easy to test, and it means everything else in the system can lean on it without worrying about how it is built.

Because the cache lives on disk, it survives restarts. That is deliberate: what the system has learned about a form should not be forgotten just because the server was restarted.

### The two tables

The first table is called `cached_steps`, and each row is one learned action. A row says which workflow and which path variant it belongs to, what type of action it is, where the element is on the page, and how to double-check it. The action type is one of four things: a plain click, an input where text is typed, a select-dropdown where an option is chosen, or a checkbox. The location is stored in two forms. One is the XPath, which is a precise address of the element in the page structure, a little like a street address for a button. The other is a dictionary of backup attributes, such as the element's id and name, stored as text in JSON format. There is also a fingerprint, the identity hash from the fingerprint file, and some diagnostic columns: the instruction text the language model had in mind when the step was learned, a raw hash from browser-use that is kept only for debugging, and a timestamp.

The first table also has a column for the field name, which says which field of the record this step is tied to. A step for typing the email has the field name email. A step like clicking the Add Person button or the final submit button is not tied to any field, so that column is empty for it.

An important design decision is what the cache never stores: the value that was typed. When the first record types Marketing into the department dropdown, the cache does not remember "department equals Marketing". It remembers "the department field lives at this spot, and it is a dropdown". When the forty-seventh record replays that step, it types its own department into that same spot. If the cache stored literal values, every later record would get the first record's answers, which would make the whole idea useless.

The second table is `run_metrics`. It gets one row for every finished record, saying whether the run was cold or a replay, when it started and finished, how many steps were cache hits, how many were misses, how many language model steps were needed, and a list of the reasons for the misses. This table is pure history. Nothing ever reads it back to make a decision during a run. It exists only so that the system can report statistics.

### How a row is identified: the lesson of the five dropdown attempts

The primary key of the first table is the combination of workflow name, path variant and something called the target key. The target key is the field name if the step has one, and otherwise it is made from the action type and the XPath, for example "click::" followed by the address. This detail comes from a real bug. During a live run against a record with a stubborn country dropdown, the model needed five attempts to get it right: four failed select attempts and then a workaround using JavaScript. In the first design, rows were identified by the model's own step counter, which is unique per attempt, so all five attempts were saved as five separate rows. Every future replay would then retry all four dead-end attempts before reaching the one that worked. Keying rows by field instead means a later attempt simply overwrites an earlier one, so only the latest attempt at a given field survives. The step index and action offset are still stored, but only as hints for ordering the replay roughly the way the original run did things.

### The code, function by function

There is a small data class called `CachedStep`. It is just a convenient bundle of the same fields as the table columns, with the attributes held as a normal dictionary instead of JSON text. This bundle is what the other files pass around when they talk about a step.

The `CacheStore` class is created with a path to the database file. When it starts, it makes sure the folder exists, opens the connection, creates the two tables if they are not there yet, and sets up a lock. It tells SQLite to return rows that can be read by column name. It also allows the connection to be used from more than one thread, and the next paragraph explains why that matters.

Every database operation goes through a small helper that takes the lock, opens a cursor, runs the work, commits the change and closes the cursor. This is the thread-safety mechanism. The reason it exists is another real finding from a code review. The same connection object is shared across the whole program through a single shared instance. That was safe as long as everyone used it from the same thread. But the cleanup pass that runs after a batch, called reconciliation, runs on its own separate thread, and it can run at the same time as a new batch. SQLite allows a connection to be used from another thread if you ask for it, but it does not make simultaneous use safe, so the program has to take turns itself. The lock does that. The project's own architecture notes describe a stress test with eight threads each making fifty writes, with no errors and no deadlock.

The function `upsert_step` writes one learned step. The word upsert means "insert, or update if it already exists". It fills in a timestamp if there is none, works out the target key as described above, and runs a single insert that, on a conflict with an existing key, updates the row instead. It is called from two places: by the instrumentation file during a cold run, and by the orchestrator directly when a live fallback self-heals a step.

The function `get_steps` returns everything known for a given workflow and path variant, in roughly the original order, as a list of `CachedStep` bundles, turning the stored attribute text back into a dictionary. An empty list has a special meaning: nothing has been learned yet. The orchestrator uses exactly that to decide whether a record needs a cold run, and the seed-claiming logic in the orchestrator checks it twice, once before and once after acquiring a lock.

The function `record_run` adds one row to the metrics table. And `hit_rate_stats` summarizes the metrics table for each workflow and path variant: how many runs there were, the total hits, total misses, the hit rate as a fraction, the total number of language model steps, and when the last run finished. This is what the statistics endpoint of the web server returns. The permanent early-warning signal built on it is simple: if the hit rate for a workflow suddenly drops, it usually means the target web page changed.

### Who depends on it and what it depends on

It depends on nothing from this project, only on Python's standard library. In the other direction, the instrumentation file imports it to write steps, the replay engine imports its step bundle type, and the orchestrator creates the one shared instance through a function called `get_store` and uses it all over. The web server's statistics endpoint calls `hit_rate_stats` through that same shared instance.

### How to explain it in one breath

"It is a small database that remembers, for each form, where every field lives on the page, but never what was typed. Rows are keyed by field, so relearning something just overwrites the old answer, and a lock makes it safe to use from the batch thread and the cleanup thread at the same time."

---

## Part 2: `cache/instrumentation.py`, the watcher that decides what to remember

### What it is and why it exists

The store is only a place to put things. Something has to decide what goes in it, and that something is this file. It is a class called `StepRecorder`, and it watches the language model work and quietly takes notes, without changing the model's behavior at all.

It is the one file in the cache layer that is closely tied to browser-use. The comments in the file say it was verified against the installed version, 0.13.10. It uses a hook that browser-use offers, a callback that is called at every step of the agent, just before that step's actions are carried out. That makes this the most version-sensitive file in the cache layer: if browser-use changes how that callback or its related helper works, this is where things would break first.

### What it deliberately does and does not cache

Out of everything the model can do, such as waiting, scrolling, extracting text or declaring that it is done, only three action types are remembered: clicks, typing into an input, and selecting from a dropdown. These are the only ones that have both a reusable address on the page and a direct, simple equivalent in Playwright. Everything else is deliberately left out. If a replay reaches a point where something was not cached, it simply asks the language model for that one step, instead of guessing.

For the typing and dropdown actions, the recorder faces a puzzle. The model just typed some text, for example a name, but the cache must not store literal text, because later records will have different text. So the recorder works backwards: it takes the text that was typed and compares it, using the same normalizing comparison that the grader uses, with each field of the current record. Whichever field's expected value matches is the field that this step is tied to, and that field name is what gets stored. If nothing matches, the step is not cached at all, because an unexplained guess is worse than a live call later.

### The code, function by function

The constructor takes the store to write into, the workflow name, the path variant, and a dictionary of the current record's fields. It also takes a few optional settings: whether to save steps automatically, a record identifier, and an event callback. Inside, it keeps a list of captured steps, a counter for the highest step number seen, and a set of field names already claimed during this run. That set solves a subtle problem. Imagine a record where the first name and the last name are both "John". The first typing action matches the first field in the list whose value is John. Without any memory, the second typing action would also match that same first field, and it would overwrite the correct entry. Because already-claimed fields are skipped, the repeated value correctly falls through to the next field with the same value.

The main method is `callback`. It receives the browser's current state, the model's output for this step, and the step number. First, it updates the step counter. Then it sends a small "step" event to the dashboard, carrying the record identifier, the step number and the model's stated next goal, so a person can watch live what the agent is doing. If that event hook throws an error, the error is caught and ignored, because a broken dashboard must never break the automation itself.

Next, it asks browser-use which page element each of the step's actions targeted, using the same helper browser-use itself uses to build its history. It then goes through the actions together with their elements. An action with no element, such as waiting or scrolling, is skipped. For each remaining action, it reads the action's name and its parameters, and works out what to do depending on the action type.

For typing and dropdown actions, it calls `_match_field` with the text that was typed or selected. If there is no match, it moves on without caching. If there is, it records the field name, and normalizes the action type, so that the two ways of typing text that browser-use has are both stored simply as "input".

For clicks, there is a fork. If the clicked element is a checkbox, it calls `_match_checkbox_field`, and if that finds a field, the action is stored as a "checkbox" instead of a plain click. Any other click is stored as a plain click with no field. These field-less clicks are things like opening the form and, most importantly, the final submit button.

Then it builds the step bundle: the workflow, variant, step number, action offset, type, field name, the model's instruction text, the XPath, tag name, the attributes, browser-use's raw hash, a freshly computed fingerprint, and a timestamp. If automatic saving is on, the step goes into the store straight away. Either way, it is added to the list of captured steps.

The method `_match_field` goes through the record's fields. It skips fields that are empty or already claimed. For each remaining field, it looks up the normalizer for that field's type, based on the field's name, normalizes both the expected value and the typed text, and compares. On the first match, it claims the field and returns its name. Because the comparison is the same one the grader uses, a date typed in a different format still matches its field.

The method `_match_checkbox_field` works by name instead of by value, because a click has no typed text. It looks at the element's name and id attributes. DataDesk's convention is to give each control an id like "field-newsletter", so the method strips a leading "field-" and compares in lower case against the record's field names.

### Why checkboxes get special treatment

This too came from a real bug. In a ten-record live test, a checkbox click was first saved as an ordinary click. On replay, a click on a checkbox toggles whatever state it is in. So the replay was correct only for records that happened to want the same value as the record that taught the cache. Records that wanted newsletter to be false silently ended up true. The fix is the special "checkbox" action type, which the replay engine handles by looking at the box's current state and clicking only if the state is wrong.

### A flag that matters: automatic saving

The recorder is used in two situations. In a cold run, saving is automatic, because the whole record is being learned, and the step numbers from the agent make sense. In a live fallback for a single step, saving is turned off, because that little agent starts counting from one again, which has nothing to do with the original step's position. In that case, the orchestrator takes the first captured step, relabels it with the original step's numbers and field name, and saves it itself. That is the self-healing write.

### Who depends on it and what it depends on

It imports the fingerprint function, the store and its step bundle, the normalizer helpers from the evaluation package, and one helper from browser-use. The orchestrator creates it in the cold run, with the whole record's fields, and in the live fallback, with just the one field and its value. The same recorder object is reused if the cold run is retried with vision turned on, which is why already-claimed fields are not re-learned.

### How to explain it in one breath

"It watches the language model work, and for each click, typing or dropdown choice it works out which field that action belonged to, by matching what was typed against the record. It saves the location of the element, never the typed value, so later records can reuse the knowledge."

---

## Part 3: `replay/engine.py`, replaying from memory with no artificial intelligence

### What it is and why it exists

This is the fast path of the whole system. It is about one hundred and seventy lines, and it takes one remembered step and performs it in a browser with plain Playwright. There is not a single language model call in this file, and, deliberately, it does not even import browser-use. That is what makes a cache hit cost nothing in model quota and run in a fraction of a second.

Two design choices are worth knowing. First, the code uses Playwright's asynchronous interface, not its synchronous one. The orchestrator runs each record as one asynchronous routine that switches between replay steps and the occasional awaited call to the language model. Mixing a synchronous Playwright call into that same routine is not supported by Playwright, so the asynchronous interface is the only one that fits. Second, the file does not reinvent how to click, scroll or wait. Playwright already knows how to do those things reliably, so the file just delegates to it.

### The pieces

There is a small result type, `ReplayResult`, which says whether the step worked and, if not, a short reason. The reasons are "no_match", meaning the element could not be uniquely found, "execution_failed", meaning it was found but the action failed or timed out, and "field_missing_in_record". The last one is used by the orchestrator when a record simply has no value for a cached field.

The `ReplayEngine` class is created with a Playwright page, which is the browser tab that is already open on the form, and a timeout in milliseconds, five thousand by default, which the orchestrator takes from the configuration. It has four methods.

### Finding the element: `_locate`

The locating works in two tiers, plus a sanity check. In tier one, it tries the saved XPath. It asks the page how many elements match that address. If exactly one matches, that is the answer. If more than one matches, it logs a warning that the address is ambiguous and moves on to tier two. If none match, or the count itself fails, it also moves on.

In tier two, it builds a simple attribute selector from the saved backup attributes, using the helper `_attribute_selector`. That helper checks a priority list, in order: a test id, two other test attribute names, then id, name and aria-label. It takes the first one the element had and builds a selector such as id equals some value, with quotes escaped. The idea is that stable, purpose-made attributes beat generic ones. Again, exactly one match is a success, and anything else is not. If both tiers fail to find exactly one element, the method returns nothing, and that is a clean cache miss. There is no partial credit and no guessing among several candidates.

### The sanity check: `_check_fingerprint`

When tier one or tier two finds exactly one element, the engine runs a quick check before trusting it. It asks the page for the element's tag name and its attributes, recomputes the fingerprint with the same function that was used when the step was saved, and compares. A mismatch does not stop anything. It only logs a warning saying that the element no longer looks like what was cached. The reasoning is that a unique XPath or attribute match is already real evidence, so the fingerprint is treated as a signal to watch in trends, for example a rising number of mismatches might mean the page is slowly drifting. If the check itself fails to run, it quietly gives up and does not interfere.

### Doing the action: `perform`

This is the method the orchestrator calls. It takes a remembered step and the value to use, which comes from the record currently being processed, and it returns a result. First it locates the element. If nothing is found, it returns "no_match" right away. Otherwise, it first scrolls the element into view if needed, because on a fresh page load an element may be below the visible area. Then it does one action depending on the step's type.

For a click, it clicks. For an input, it fills the box with the value as text, using an empty string if the value is missing. For a dropdown, it selects the option whose visible label equals the value as text. That detail matters: it chooses by the text the user sees, not by an internal value. For a checkbox, it works out whether the record wants the box ticked, reads whether the box is currently ticked, and clicks only if the two differ. If the action type is something unknown, it returns "no_match".

Errors are handled in two layers. A Playwright timeout, meaning the element did not become clickable or the option never appeared within the time limit, is logged as a warning and returned as "execution_failed". Any other exception is logged with its details and also returned as "execution_failed". The function never lets an exception escape. This is part of the system's isolation principle: one record's trouble must never be allowed to crash everyone else's work.

A practical consequence: if a record asks for a dropdown option that does not exist on the form, for example a country that is not in the list, the selection will wait out the timeout, fail, and be reported as "execution_failed". The orchestrator then tries a live model call, which will also fail, and a repeated failure of that kind is what eventually opens a protective circuit breaker for that field. So when one field keeps failing again and again, the first thing to check is whether the target form really offers that value.

### Who depends on it and what it depends on

It imports Playwright's asynchronous types, the fingerprint function and the step bundle from the store. Nothing else. The orchestrator creates one engine per record, around that record's page, and calls `perform` for each remembered step, counting a success as a cache hit and anything else as a miss that leads to the live fallback.

### How to explain it in one breath

"It takes a remembered step, finds the element first by its exact address and then by its id or name, double-checks it still looks right, and performs the click, typing or selection with plain Playwright. No language model is involved, which is why a cache hit is fast and free."

---

## Part 4: `agent/prompts.py`, the instructions given to the language model

### What it is and why it exists

This is a small file, about one hundred lines, and it contains no logic beyond building strings. It turns data into plain-English instructions for the language model. It is kept in its own file on purpose. When the agent behaves badly, the prompt is almost always the first thing you edit, and you should be able to edit it without risk of touching any of the execution logic.

The file imports only two things: the target form's address from the configuration and the record type. It has three functions, one for each way the language model is used in the system.

### The whole-record prompt: `build_task_prompt`

This one is used for a cold run, when the model must fill in an entire form. It takes a record, gets its fields without the internal record identifier, and writes them as a list of lines, each saying a field name equals a value. The surrounding text tells the agent it is filling out a web form at the configured address and to match each value to the form field with the closest label, because the fields might not appear on the page in the same order as in the list.

Then comes a short list of rules. Go to the form's address first if you are not already there. Some dropdowns render slowly, so wait for the options to actually appear before picking one. Enter every field listed and do not skip one just because you are unsure which input matches; pick the closest label. Do not invent or guess values that were not given. After all fields are entered, submit the form.

One thing worth knowing about this file's history: for a while, the very last rule was cut off in the middle of a word. The text ended with the two characters "Do" and nothing after them, and it had been that way since the first commit. The missing rule is the one that says the agent should not retry the submission repeatedly if it appears to fail, but should attempt it once, note what happened, and stop. That has now been fixed, so the prompt ends with that rule in full. It is a good small example of why it pays to read the actual prompt text now and then, because the cut-off went unnoticed for a long time.

### The single-step prompt: `build_step_prompt`

This is used when a replay step fails and the system needs a live model call to repair just that step. Its main design principle is narrowness. The model is attached to the very same browser tab that replay was already working on, in the middle of a half-filled form, so the instruction must make it do exactly one thing and then stop. Otherwise it might redo, or even undo, fields that replay already filled in correctly.

The function takes the field name, the value, and an instruction. The instruction is the text the model itself had noted the last time it learned this step, which is saved in the cache, and the prompt quotes it back as a hint. There are two shapes of prompt. If there is a field name, the prompt says: find the single form field for this field name and enter exactly this value, touch only this field, do not click submit, wait for dropdown options to appear, do not alter the value, and stop once it is set. If there is no field name, the step is something like opening a dropdown or submitting the form. In that case the prompt says to perform exactly that one action and nothing else, not to fill in or modify any field, not to retry repeatedly, and to stop after one attempt.

### The reconciliation prompt: `build_reconciliation_prompt`

Reconciliation is the cleanup pass that runs after a batch has been graded. When the grader finds that a record landed in the database but one field is wrong or missing, this prompt tells a model how to fix that one field by using the app's edit feature. It takes the name and value of the matching key field, usually the email, plus the field to fix and its correct value.

The task is structurally different from filling in a blank form, because it starts with a search. The prompt tells the agent that it is on the People page, a list of existing people. It should find the person whose key field equals the given value, using the search box if the list is long, then click that row's edit action to open a form that is already filled in, and make sure not to create a new person. In the edit form, it sets the one field to the exact value, changes nothing else, waits for dropdown options when needed, and saves. If it cannot find the person, or cannot find an edit action, it must stop and report what it saw, and must not create a new person or guess.

Notice that this prompt speaks directly about DataDesk's People page. That is one of the places where the project is tied to its test target. If you pointed the system at a different application, this is one of the files you would rewrite.

### Who depends on it and what it depends on

The runner file calls `build_task_prompt` whenever it is not given an override. The orchestrator imports the other two: the step prompt for the live fallback and the reconciliation prompt for the cleanup pass.

### How to explain it in one breath

"It is three prompt writers: one for filling a whole form, one for fixing a single field in a half-filled form, and one for editing an existing record. The narrower the task, the cheaper and safer the model call, which is why the single-field prompts are so strict about touching nothing else."

---

## Part 5: `agent/runner.py`, the only door to the language model

### What it is and why it exists

This is where the system actually calls Gemini through browser-use. The comment says it all: every live language model call in the whole project passes through one function in this file. That makes it the natural place to detect problems with the model service, such as running out of quota.

An important thing to understand is that this file has two layers of history. The part that matters for version two is small: a quota detector and the main function that runs one task through browser-use. The rest of the file is leftover code from version one: a batch runner that goes through records one at a time with a pause after failures, a concurrent batch runner, a retry with vision turned on, and a wrapper that turns the asynchronous code into a normal function. None of that is called by version two's pipeline. The orchestrator imports exactly one thing from this file, the main function. The top-of-file comment also still describes version one. So when you read the file, concentrate on the first half and treat the second half as legacy.

### The two kinds of "too many requests"

The file begins with a constant that identifies a particular error text. This comes from a real incident, and it is one of the most important pieces of operational knowledge in the project. Gemini's free tier has two different limits, and both produce an error that looks nearly identical. One is a per-minute limit of fifteen requests, which recovers within a minute. The other is a per-day limit of five hundred requests, which does not recover until the next day. The first kind can be absorbed with retries and short waits. The second kind is a wall: if every remaining record in a batch independently discovers it, each one wastes time retrying something that cannot succeed. So the file contains a detector that recognizes only the daily error, so that the orchestrator can stop the rest of the batch early.

### The detector: `_detect_quota_exhausted`

It takes the result of an agent run and asks it for its list of per-step errors, using a documented method of browser-use's result object rather than scraping log text, so it keeps working even if browser-use changes how it logs. It returns true if any step's error contains the signature of the daily limit. If the method is missing or raises an error, it quietly returns false. Per-minute errors do not contain that signature, so they are never confused with the daily wall.

### Building the model client: `_build_llm`

This tiny function creates the Gemini client from the configured model name and key. A new client is made for each call, so there is no shared state between calls.

### The main function: `_run_single_record_async`

This is the core primitive of the whole project, and it accepts several options. It takes a record, whether to use vision, an optional prepared browser profile, an optional callback for step watching, and an optional override task. The vision option controls whether browser-use sends a screenshot at each step; text-only is cheaper, and the orchestrator tries text-only first. The callback is where the instrumentation's watcher is plugged in. The override task lets the orchestrator replace the whole-record prompt with a narrow single-field one.

The function first decides on the task text: the override if there is one, otherwise the whole-record prompt for this record. Inside it defines a small helper that does the actual work for a given browser profile. The helper builds the model client, creates a browser-use agent with the task, the client, the profile, the vision setting and the callback, and runs it to completion. After the run, it reads the agent's own claim of success, and runs the quota detector. It then returns a run result containing the record identifier, the success claim as a true or false, an error message that says the daily quota was exhausted if so, a timestamp, and the quota flag.

It is important to understand what that success value is. It is the agent's own opinion, and the project never trusts it for grading. The independent grader never even looks at it. It is logged and used for decisions such as whether to try again, but the truth comes from the database.

The function then picks how to supply the browser. If a profile is given, which is how version two works, the caller owns the browser. In that case the profile contains a CDP address pointing at a Chrome window that Playwright already opened, and browser-use attaches to that same window, so the model sees whatever fields replay has already filled in. If no profile is given, the version one behavior applies. The function makes a unique temporary directory, builds a profile around it, and runs the agent with it. A fresh directory forces Chrome to start a brand new isolated window instead of taking over whichever Chrome you already have open, and the window is closed when the work finishes.

Finally, the whole thing is wrapped in a deliberately broad catch. If anything at all goes wrong, the function returns a failed run result with the exception's type and message, instead of raising. This is the isolation principle again. One record's bad luck, such as a network blip, must never cancel the others that are running at the same time.

### The version one leftovers, briefly

For completeness, here is what the legacy half contains. There is a wrapper that tries a record without vision first and, on failure, waits a minute and retries with vision. There is a sync wrapper that runs that inside its own event loop. There is a sequential batch function that runs records one by one and pauses for sixty seconds after a failure, with clear log messages saying that the pause is deliberate and not a hang. And there is a concurrent batch function that runs all records at once with isolated browsers. That last one was tried and found not to work on the free tier, because concurrent agents share one request quota. Version two does not use any of these. It reimplements the retry-with-vision pattern inside the orchestrator, deliberately without the sixty second sleep, because that sleep would hold a lock and block every other waiting record.

### Who depends on it and what it depends on

It imports browser-use's agent, browser profile and Gemini client, the whole-record prompt builder, the model name and key from the configuration, and the record and run result types. The orchestrator calls its main function in four situations: the cold run, the vision retry of the cold run, the single-step live fallback together with its vision retry, and the reconciliation edits.

### How to explain it in one breath

"It is the single door through which every language model call passes. It runs one task through browser-use, attaches either to a browser we already opened or to a fresh isolated one, catches every error so one record cannot take down the batch, and flags the special case where the daily Gemini quota is gone."

---

## Part 6: `evaluation/evaluator.py`, the independent grader

### What it is and why it exists

This file is the reason you can trust the numbers the system reports. It is about one hundred and sixty lines, and its whole job is to read the real database after a batch and compare what is there to what should be there. It never looks at the automation's own report of success. The file's own opening comment states that a record could be reported as successful by the agent and still not be in the database, and that mismatch is useful information, not a contradiction.

It imports the two configured names, the table and the matching field, the shared data types, and the value comparison function from the normalizers. It deliberately does not import anything from the agent or the orchestrator. That absence is the independence, built into the structure of the code.

### The helper functions

The function `_fetch_db_rows` reads the entire target table in one request and returns the rows as a list of dictionaries. It does this once per batch rather than once per record, because a batch's rows easily fit in memory, and it avoids one round trip to the database per record.

The function `_find_matching_row` is the link between what we meant to submit and what is in the table. Nothing else connects a record to a specific database row, because the database creates its own row numbers. So the function takes the record's value for the matching field, which is the email by default, and looks through the fetched rows for one with the same value. The comparison ignores case and surrounding spaces, because an email typed in a slightly different case should not count as "this record never landed". If the record has no value for that field, it returns nothing.

The function `_compare_record` produces a verdict for one record, and it has two very different outcomes. If no matching row was found, every field of the record is marked failed with the reason "record_not_found_in_db", the record is marked as not matched, and a message explains which key value had no row. This usually means the submission failed, or that the matching field itself was entered wrong. If a row was found, each field is compared one by one. The expected value comes from the record, the actual value comes from the database row, and the shared comparison function normalizes both before comparing, so differences in date format, phone punctuation, currency symbols or capital letters do not count as errors. A field that fails gets the reason "value_mismatch". Distinguishing "the whole row is missing" from "the row is there but one field is wrong" is very useful in practice. The first almost always points at an infrastructure or submission problem. The second points at a specific accuracy problem.

### The grading entry point: `verify_batch`

This is called once per batch, right after the batch finishes, with the same list of records that was just submitted. It fetches the rows once, then goes through the records. For each one, it finds a matching row, compares, and adds up the totals. The totals are counted per field, not per record. A record with seventeen right fields and one wrong one moves the overall accuracy only a little, and does not count as a whole failure. At the end, it computes the headline number, the fraction of all expected fields that were correct, guarding against dividing by zero, and returns a batch report that holds the totals, the number of records with no matching row, the full per-record detail, and a timestamp.

The web server calls this function in both of its run endpoints, right after the batch ends. It builds a fresh database client with the service-role key, which has unrestricted read access, passes it in, saves the report to disk, and sends it back to the caller. The reconciliation pass later reads this same report to find which fields are wrong on records that did land.

### The reset helper: `delete_all_records`

This function wipes every row from the target table and returns how many it deleted. Nothing in the normal flow calls it. It exists for a person to reset the database to zero before a test, and it has a real operational reason beyond tidiness. DataDesk's front end reloads the entire table after every single insert, so as the table grows over a long testing session, every record's post-submit reload gets slower. Under heavy concurrent inserts, that slowdown measurably contributed to confirmation timeouts in an earlier version. Keeping the table small between sessions keeps those reloads cheap.

There is a technical detail in how it deletes. The database interface used here rejects a delete with no condition at all, as a safety measure. So the function first reads the ids to count them, then deletes with a condition that says the id is not equal to minus one. The real ids are a large integer type and are never negative, so that condition matches every row without having to know how many rows there are.

### Two things worth knowing

First, the matching logic returns the first row it finds with the right key value. If the table contains old rows from earlier test runs with the same email addresses, the grader may match an old row instead of the new one. That is one more reason the reset helper exists, and it is something to keep in mind if grades look strangely good or strangely bad after repeated tests on the same data.

Second, the grader only checks fields that are in the record. Extra columns in the database, such as an id or a creation time, are ignored, and a field in the record that the table does not have would show up as a mismatch.

### How to explain it in one breath

"After the batch, it reads the real database, finds each record's row by its email, and compares every field after normalizing formatting. It reports accuracy at the field level, and it separates 'the row never landed' from 'the row landed with one wrong field'. It never trusts the automation's own claim of success."

---

## Part 7: Putting it all together

Here is the complete life of a batch, using only the six files above plus the conductor. A hundred records arrive. The orchestrator works out that nothing is remembered for this form yet, because the store returns an empty list. It lets exactly one record do the cold run. That record opens a browser through Playwright, and the runner starts a browser-use agent attached to it, giving it the whole-record prompt. At every step, the instrumentation watcher works out which field each click or typing action belonged to and writes the location into the store. When the first record is finished, the store holds a little map of the form.

The other ninety-nine records now replay. For each remembered step, the orchestrator hands the step and that record's own value to the replay engine, which finds the element by its address, or by its id or name, double-checks its fingerprint, and acts. Most steps are cache hits and cost nothing in model quota. If a step fails, the orchestrator uses the runner with a narrow prompt from the prompts file to fix just that field. The instrumentation watches that repair, and the corrected step goes back into the store, so the next record gets a clean hit. When the batch is over, the evaluator reads the real database and produces the grade, and a cleanup pass tries to fix any wrong fields with the edit prompt.

If you remember only four ideas from these six files, make them these. One: the cache remembers where, not what, so one learned run teaches the whole batch. Two: rows are keyed by field, so relearning overwrites the old answer instead of piling up. Three: every language model call goes through one door, which catches every error so one record can never take down the rest. And four: the grade comes only from the real database, never from the system's own claim of success.

### Small things worth knowing, to mention if asked

The whole-record prompt used to have its last rule cut off in the middle of a word, so the agent was never told to avoid repeated submit attempts; that has since been fixed. Because the cold-run prompt changed, the first batch after the fix will behave slightly differently from earlier ones. The runner file still contains the legacy version one batch code and an outdated top comment, which no part of version two calls. The reconciliation prompt and some comments are written specifically for DataDesk, so pointing the system at a different application means rewriting those. And the grader's first-match behavior means stale rows with the same email can distort a grade, which is why the table should be reset between test sessions.
