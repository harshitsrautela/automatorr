# terminautomator v2: The Orchestrator, In Depth

## Part 0: What this document is, and what the orchestrator is

This document is a complete, plain-language tour of a single file: `orchestrator.py`. It is the largest file in the project, over thirteen hundred lines, which is roughly a third of all the real code in version two. It is written to be read aloud or turned into a long audio episode, so it uses prose, short lists, and almost no code. The goal is that after reading or hearing it, you could stand in front of a whiteboard and explain exactly how a batch of records gets from a JSON file into a database, who decides what happens at every moment, and why the file is built the way it is.

The previous guide, the one called In Depth, covered the six supporting files: the cache store, the instrumentation watcher, the replay engine, the prompt writers, the runner that talks to the language model, and the evaluator that grades the result. This guide assumes you have met those, but it re-explains each of them briefly whenever the orchestrator uses them, so it should stand on its own.

### The conductor

The easiest way to think about the orchestrator is as the conductor of an orchestra. Every other file in the project is an instrument. The store remembers things. The recorder watches and takes notes. The replay engine plays back remembered moves. The runner calls the language model. The evaluator grades. None of those files knows about any of the others' schedules. They do not know when to start, when to wait, when to give up, or what to do when one of them fails. The orchestrator is the only file that knows all of that. It imports from essentially every other module in the project, and that is exactly why it is so large and why it is read last.

It has one overriding job. Given a list of records, it must get each record into the target form as cheaply and reliably as possible, using the language model as little as possible, and it must never let one record's trouble damage any other record's work.

### Who calls it, and who it calls

It is called from the web server layer, in the file `api/routes.py`. The web server calls a function named `run_batch_cached` to run a batch, a function named `run_reconciliation_pass_sync` to run the cleanup pass afterward, and a function named `get_store` to read cache statistics. The orchestrator itself knows nothing about the web, nothing about HTTP, and nothing about the live dashboard. It reports what is happening through a plain callback function that the caller hands in. That separation matters: you could call the orchestrator from a command-line script, a test, or a scheduled job, and it would behave identically.

Going the other way, the orchestrator calls the cache store to read and write learned steps, creates the instrumentation recorder to learn new steps, creates the replay engine to replay known steps, calls the runner to use the language model when it has to, and uses the prompt builders to tell the model what to do. It also uses Playwright directly, to open and close Chrome windows. It uses the browser-use library only in one tiny way: to build a profile object that says "attach to the Chrome window that is already open on this port."

### A short refresher on the vocabulary

A record is one row of input data, such as one person. A field is one piece of a record, such as the email. A workflow is one target form, named `datadesk_people_form`. A path variant is a label for which branch of the form a record follows; it is always the word default for this project.

A cold run is the very first run for a workflow, when nothing is remembered and the language model does everything while the recorder takes notes. A replay is a later run that follows the remembered steps with no language model at all. A cache miss is when a remembered step cannot be carried out. A live fallback is a small, narrow language model call that fixes just that one step. Self-healing is what happens right after a successful live fallback: the corrected knowledge is written back into the cache, so the next record gets a clean hit.

Three newer terms appear in this file. The seed lock is a rule that only one record at a time may be teaching the cache. The circuit breaker is a rule that stops the system from repeatedly attempting something that keeps failing. And reconciliation is a cleanup pass that runs after grading and tries to fix fields that ended up wrong.

---

## Part 1: The shape of the file, so you never get lost

If you open this file cold, it can feel like a wall. It helps enormously to know that it is built in five layers, from small to large, and that each layer only uses the ones before it.

The first layer is a set of small shared devices at the top of the file. These include the event-emitting helper, the shared cache store, the daily-quota tripwire, the per-field lock for missing fields, the seed lock, the circuit breaker, and a tiny function that decides the path variant.

The second layer is three routines that each carry out part of the work for a single record. One is the cold run, one is the live fallback for a single step, and one is the replay. These are the intellectual heart of the file.

The third layer is the routine that wraps a single record from start to finish. It opens a private Chrome window, decides whether the record is a cold run or a replay, runs it, waits for the database write to be confirmed, closes the window, and records the statistics.

The fourth layer is the batch layer. It runs many of those per-record routines at once, within a limit, and reports progress for the whole batch.

The fifth and last layer is reconciliation, a separate cleanup pass that fixes leftover wrong fields after the batch has been graded.

The recommended reading order, which matches the project's own reading guide, is: the path variant function, then the cold run, then the live fallback, then the replay, then the per-record wrapper, then the batch layer, then the lock and circuit breaker section, and last the reconciliation section. In this document, we go through the shared devices first, because every later section refers to them, and then follow the order above.

One more thing to know before diving in: the module comment at the top states a rule described as non-negotiable in the project's design. The live language model is never removed from the path. Even in the worst case, where every single cached step misses, every record can still fall all the way back to being filled in by the live agent. The cache is an optimization layered on top of a system that always still works without it.

---

## Part 2: The plumbing at the top of the file

### The event callback and the helper that protects it

Near the top, the file defines what it calls an event callback. It is simply an optional function that takes a dictionary and returns nothing. Throughout the whole file, every important function accepts such a callback. When something noteworthy happens, the function builds a small dictionary describing it and passes it to the callback. In the real application, the web server passes in a function that does two things at once: it sends the event to the live dashboard over a streaming connection, and it appends the event to a plain-text run log file.

The events carry a type. The ones you will meet in this file include batch start and batch done, which bracket the whole batch; record start and record done, which bracket each record; field hit and field miss, which say how each step of a replay went; a generic error event; and, for the cleanup pass, reconciliation start, reconciliation fix and reconciliation done. A step-level event also comes from the recorder, to show what the live agent is doing right now.

The file never calls the callback directly. It goes through a tiny wrapper that catches any exception the callback raises, logs it, and carries on. The reason is a design rule that appears all over the project: a broken dashboard or a broken log writer must never be able to stop the actual automation. Monitoring is allowed to fail. Work is not.

### The shared cache store

Next is a function called `get_store`. It keeps one shared instance of the cache store at module level and creates it the first time anyone asks. Every part of the program, including multiple batch threads and the reconciliation thread, shares that one instance and its single database connection. The comment explains why the store has to be internally thread-safe: this function guarantees the sharing, so the store has to guarantee that sharing is safe, which it does with a lock around every database operation.

---

## Part 3: The daily-quota tripwire

This section is a good example of how this file was shaped by real incidents. In a demo run, the free tier of the Gemini service ran out of its daily allowance of five hundred requests. This is different from the per-minute limit of fifteen requests, which recovers within a minute. The daily limit does not recover until the next day.

What went wrong, and why, is instructive. The system already had protections for short-lived failures. In particular, the seed lock was designed so that if the record teaching the cache failed, the next waiting record would automatically take over and try again. That is exactly the right behavior for a temporary problem. But for a permanent problem, it is a trap: every waiting record, one after another, took its turn at the same doomed task, each wasting around fifteen to twenty seconds on internal retries before failing. A batch that should have stopped immediately instead ground through every record, one painful failure at a time.

The fix is a tripwire: a single shared flag that the whole batch can see. It is a threading event, not a plain true-or-false variable. The reason is the same one the web server uses for its own abort flag: the reconciliation pass runs on a separate thread from the main batch, and both need to see the flag reliably.

There are three small functions around it. The first returns whether the tripwire is currently set. The second is called right after any run result that might carry the quota-exhausted flag, which the runner sets when it spots the daily-limit error. The first caller to notice flips the flag and sends one clear error event: the daily quota is exhausted, the remaining batch is aborting, and this will not recover until the quota resets. Every later caller does nothing, so the dashboard and the log show one clear message instead of fifty copies of it.

The tripwire is then checked in many places, which is where its value comes from. It is checked at the start of the cold run, before wasting a language model call. It is checked at the start of a live fallback. It is checked when the batch pool picks up a record, before opening a Chrome window for it. It is checked again inside the per-record wrapper, so records queued behind the seed lock when the quota dies do not each sit through a long dead wait. And it is checked at the start of each reconciliation fix. In every one of those places, the response is the same: skip the work, and say so.

One more detail: at the start of every batch, the tripwire is cleared. A fresh batch deserves a fresh chance. Maybe the user swapped in a new API key, or maybe enough time has passed for the daily quota to reset. Without this reset, the flag would stay set for the entire lifetime of the server process, and every later batch would refuse to start.

---

## Part 4: The per-field lock for missing fields

The next device solves a different problem: a thundering herd, meaning many records all rushing at the same thing at the same moment.

Imagine a batch of a hundred records. The first record, which taught the cache, happened to have an empty notes field. Because the recorder only caches fields that were actually typed, the cache has no step at all for notes. Now ten records start replaying at about the same time, and each one has real notes text. Each of them independently discovers that notes is missing from the cache. Without any coordination, each of the ten would call the language model to learn the same field, paying ten times for one answer.

The fix is a lock for each field. The file keeps a dictionary of locks, keyed by the workflow, the path variant, and the field name. A small function returns the lock for a given key, creating it the first time. The lock is held only around the short sequence of: check the cache again, maybe call the language model, and write the result back. The key trick is the double-check after getting the lock. A record that had to wait does not blindly call the model once it gets its turn. It looks at the cache again first, and usually finds that whichever record went first has already learned the field. It then simply replays that, for free. Only the first record to discover a gap ever pays for it.

---

## Part 5: The seed lock

This is one of the most important ideas in the entire file, and it has an interesting history.

### The problem

When a batch begins against an empty cache, every record starts at essentially the same instant, and every one of them looks at the cache and sees nothing. If each of them reacts by running a full language model session to fill in the form from scratch, you get ten or fifteen full cold runs at once. That defeats the entire purpose of the project, which is that only the first record should pay for the language model, and it would also hit the free tier's rate limit almost instantly.

### The first design and why it failed

The first design computed a list of seed records up front, before anything ran: record number one goes first, and everyone else starts a moment later. That works as long as the first record's cold run succeeds. But in a real run, the first record's cold run failed outright, because it never got even one successful reply from Gemini. Every other record, having started a moment later, looked at the cache, found it still empty, and each started its own full blind cold run. Fifteen simultaneous attempts, exactly what the design was supposed to prevent. A fixed plan made in advance cannot react to a failure at runtime.

### The final design: a lock at runtime

The seed lock is a real lock, held at runtime. The file keeps a dictionary of seed locks, one for each combination of workflow and path variant, with a small function to fetch or create the right one. Only one record at a time may be cold-seeding a given workflow and variant. Every other record that finds the cache empty simply waits on this same lock, and, importantly, it has not opened a browser yet, so waiting costs nothing. If the seeding attempt fails, the lock is released, the next waiting record wakes up, and it becomes the new seed attempt, one at a time, rather than a herd. Once any attempt succeeds, every later record finds a populated cache straight away and never touches the lock at all.

### The function that claims the lock, step by step

The function is called `_claim_seed_responsibility`. It returns true if the caller is now responsible for seeding, in which case it is holding the lock and must release it when its cold run finishes, whether that run succeeded or not. It returns false if the cache already has steps, in which case nothing is held and the caller should simply replay.

It works in four steps. First is a quick check with no lock at all: if the store already returns any steps, return false immediately. This is the fast path that almost every record takes in a normal batch. Second, if the cache looks empty, it waits to acquire the lock. This is where the herd queues up. Third, once it holds the lock, it checks the cache again, because while it waited, the previous seeder may have just filled it. Fourth, if the cache is now populated, it releases the lock and returns false. If it is still empty, it returns true, still holding the lock.

There is a subtle safety detail in step three. The re-check is wrapped so that if reading the cache fails for some unrelated reason, such as a disk error, the lock is released before the error is allowed to propagate. This came from a code review and is recorded as bug number twelve. Without it, a freakish failure at exactly that moment would leave the lock held forever, silently disabling the system's ability to ever seed that workflow again for the rest of the process's life. It would be an extremely quiet and extremely severe failure for something so narrow.

A companion function releases the lock, but only if it is currently held, so releasing is always safe.

### What it looks like in practice

In a batch of a hundred records with ten concurrent slots, the first ten records each take a slot. One of them wins the seed lock and starts a cold run. The other nine are waiting on the lock, each holding a slot but not yet running a browser. When the winner finishes and the cache is populated, the lock releases, the nine wake up one at a time, each re-checks the cache, finds it full, and goes straight into a fast replay. If the winner had failed, the next of the nine would take over as the seeder. The cost of the whole herd problem is reduced to, at most, one language-model session at a time, retried one by one until one succeeds.

---

## Part 6: The circuit breaker

The circuit breaker is named after the device in a house that cuts the power when something is drawing dangerously too much current. In this file it protects against a more subtle failure that the locks alone made worse.

### The incident

During a live run, Gemini returned a "service unavailable, high demand" error for every attempt at one particular field, the notes field. Because of the lock described earlier, every record that needed that field queued up single file behind the lock. Each one took its turn and burned a full, doomed live-agent attempt, with multiple internal retries lasting five to thirty seconds, before the next record got its turn to fail in exactly the same way. A hundred-record batch spent most of its time losing, one record at a time, to an API that was never going to say yes. The lock turned a fast, parallel failure into a slow, serial one.

### The mechanism

The breaker is controlled by two constants. A field must fail two times in a row before the circuit opens. When it opens, it stays open for thirty seconds. While it is open, any record that misses on that field skips the live fallback entirely: no lock wait, no model call, no browser action. It just moves on, leaving that field blank or wrong on that one record, for the cleanup pass to deal with later.

The state is kept in a dictionary, keyed the same way the cache itself is keyed: by workflow, path variant and a target, which is the field name or, for a click with no field, the action type joined to the element's address. For each key, it stores a count of consecutive failures and, once opened, the time until which it stays open.

Four small functions manage it. One builds the key. One checks whether a given circuit is open, by comparing the current time against the stored open-until time. One records a failure: it adds one to the count, and when the count reaches the threshold, it sets the open-until time thirty seconds into the future and logs a warning. And one records a success, which resets the count to zero and clears the open time.

### Self-recovery

What makes this a good breaker, rather than a permanent switch, is that it recovers by itself. After the thirty seconds pass, the circuit reports itself as closed again, even though the failure count is still at or above the threshold. The next record that misses on that field gets a genuine attempt. If the problem has cleared up, say the API recovered, that attempt succeeds, the success handler resets everything, and normal service resumes with no human involved. If the problem is still there, that attempt fails, the count goes up again, the threshold test fires again, and the circuit re-opens for another thirty seconds.

### Where it is consulted

The breaker is checked at several points. In the replay, it is checked when a step misses, but only for non-critical steps. The submit step is never skipped, because without a successful submit the record did not land at all, so submit always gets a real attempt. It is also checked in the missing-fields path, both before waiting for the field's lock and again after getting it. And it is checked by the reconciliation pass when it builds its to-do list, so that cleanup does not waste attempts on something already known to be broken.

### A practical lesson baked into this section

A field that is repeatedly circuit-skipped can look, from outside, exactly like a caching bug, a field that is mysteriously never learned, run after run. This happened once. The real cause was that the test data was asking for dropdown values that did not exist as real options on the form, so every attempt was correctly and permanently doomed. When one field keeps failing, check the target form first before suspecting the cache.

The breaker state lives only in memory. If the server restarts, every circuit starts closed again, with no memory of what was failing before. That is a reasonable default, since whatever was wrong may well have changed.

---

## Part 7: Deciding the path variant

This is the smallest function in the file but conceptually worth a moment. A form can sometimes branch. For instance, a retailer form might ask different questions for an individual than for a company. The system needs a name for which branch a record follows, because the cache is organized per branch. The function that decides this is deliberately not an artificial intelligence call. It simply looks in the record for a field named path variant, and if there is one it returns its value as text; otherwise it returns the word default.

DataDesk's People form has no branching, so in practice it always returns default. But the design is ready for a branching form: put a path variant field in the source data, and it is picked up with no code change.

---

## Part 8: The cold run

The cold run is what happens when a record finds the cache empty and wins the seed lock. This is the one place in the normal flow where the language model fills in an entire form from scratch. The function is called `_run_cold`.

### Setup

It starts by creating a metrics dictionary with the mode set to cold and zeros for hits, misses and language model steps, plus an empty list for miss reasons. This dictionary is how the per-record wrapper later records statistics.

### The quota check comes first

Before doing anything expensive, it checks the daily-quota tripwire. If an earlier record already tripped it, the function does not waste another language model call to rediscover the same dead end. It returns immediately with a failed run result whose message says the run was skipped because the daily quota is already known to be exhausted, and whose quota flag is set.

### Running the live agent with the recorder attached

Otherwise, it creates a recorder from the instrumentation file. The recorder is told the workflow, the path variant, and the fields of this record, and it is set to save steps automatically as it learns them. Then it calls the runner's core function, passing the record, text-only mode with no screenshots, a browser profile that simply says "attach to the Chrome window at this debugging address," and the recorder's callback.

That attach step is worth pausing on. The window Playwright has already opened and navigated to the form is the window the language model's agent attaches to, rather than opening its own. This was flagged as an unverified risk early in the project, since it was not certain the agent would see the already-open, already-navigated tab rather than a fresh blank one. A real run confirmed that it does attach to the exact tab, and correctly continued working on the open form. This matters because it is the foundation for the whole hand-off between replay and live model.

After the run, it passes the result to the tripwire helper, so that a daily-quota error is noticed.

### The vision retry

The first attempt is text-only, which is cheaper, and has proved sufficient for DataDesk because of its well-labeled, semantic markup. If that whole cold run does not report success, and it did not hit the quota wall, and the tripwire is not set, the function tries again once with screenshots turned on. Critically, the retry happens on the same browser window, using the same recorder. Anything the first pass already got right is not re-learned, because the recorder keeps track of which fields it has already claimed. And the retry does not open a second browser.

There is a deliberate absence here, explained in a comment. Version one had a sixty-second pause before this kind of retry. Here there is none, on purpose. The cold run is happening while holding the seed lock. A sixty-second sleep would hold that lock for a minute, blocking every other waiting record. If a cooldown is ever needed, it should be added deliberately, not by copying the old constant.

### What it returns

At the end, it records how many language model steps happened, using the recorder's step counter, and returns the run result together with the metrics.

---

## Part 9: The live fallback for a single step

This function, `_run_live_fallback_step`, is the self-healing engine. It stands in for exactly one cached step that could not be replayed. It returns two things: whether it succeeded, and how many language model steps it took.

### Narrow by design

It starts with the same quota check. Then it builds a recorder, but with two differences from the cold run. The recorder is told about only one field, the one this step belongs to, with the value for this record. And saving is switched off, because the little agent counts its own steps starting from one, which has nothing to do with where the original step sat in the sequence. Instead, the function will do the saving itself, carefully.

It then builds a narrow prompt using the single-step prompt builder. That prompt includes the instruction text saved with the cached step, describing what the step did the last time, and it tells the agent to find exactly that one field, enter exactly that value, touch nothing else, and stop. The agent shares the browser window with replay, in the middle of a half-filled form, so being narrow is not just cheaper, it is safer.

Because the runner expects a record, the function creates a stub record whose identifier is made from the workflow and the step's position. The record contents are irrelevant, since the prompt override replaces the whole-record prompt.

### Text-only first, vision second

It runs the agent in text-only mode. As with the cold run, if that does not succeed, and there is no quota wall, it retries once with screenshots on, on the same window. A subtle point in the comments: the circuit breaker only sees the final result this function returns. So a field that fails both the text pass and the vision pass counts as exactly one failure toward opening the circuit, not two. Bundling the retry inside here prevents double-counting.

If anything inside throws an exception, it is caught, logged, and the function returns failure along with however many steps it observed. This is the isolation principle again.

### The self-healing write

This is the most interesting part. If the recorder captured anything, the function takes the first captured step and relabels it. The step's index and offset are overwritten with the original step's, and its field name is set to the original step's field name when there is one. Then it saves that into the store. The effect is that the corrected knowledge replaces the stale entry under the same key, so the next record that needs this field gets a clean hit.

One detail: the type of action that gets saved is whatever the live agent actually did, not necessarily what the original step was. If a missing field was assumed to be a typed input but the agent chose a dropdown selection, the saved step will correctly say dropdown.

If the recorder captured nothing, perhaps because the agent never interacted with an element it could map, the function logs a warning that the cache entry is left stale, and returns anyway.

The success value returned is the agent's own self-report. That is the best the function can do at this point, and it is the reason the final independent grading exists.

---

## Part 10: The replay, the heart of version two

The function `_run_replay` is the fast path. It is where almost all records spend their time. It takes a record and the list of cached steps, plus the open Playwright page, and replays everything without a language model, calling it only when something misses.

### Setup

It creates a replay engine around the page, with the timeout taken from the configuration. It gets the record's fields, and reads the workflow and path variant from the first cached step. It prepares counters for hits, misses and language model steps, and a list of miss reasons. And it creates a flag named submit failed. That flag is the key to understanding what success means here.

### What "success" means for a replayed record

A replayed record counts as successful if and only if the final submit step worked. Every other field is, in a precise sense, optional. A record that submitted correctly but has one wrong or blank optional field still landed in the target system. That is a data-quality gap for the independent grader and the cleanup pass to deal with, not proof that the automation failed. This definition was reached the hard way: in one real run, a missing optional field wrongly marked whole records as failed even though every one of them had landed correctly.

### The inner helper that handles each step

Inside the replay is a small helper, which tries one step and handles every outcome. It works out a display name and a target key for the step, then asks the replay engine to perform it.

If the step works, it counts as a cache hit, sends a field-hit event, and, if the step belongs to a field, tells the circuit breaker about a success.

If it does not work, it counts as a miss. If the step is not critical and its circuit is open, it skips instantly: it notes the reason as circuit open skipped, sends a field-miss event, and returns without calling the model. Otherwise, it records the replay engine's reason, logs the miss, sends a field-miss event, and calls the live fallback. If the fallback succeeds, the circuit is told of a success. If it fails, the circuit is told of a failure, an error event is sent, and, if the step was critical, the submit-failed flag is set.

### Finding the submit step

Before replaying, the function must work out which cached step is the submit button. This is not labeled anywhere in the cache. The function identifies the submit step as the last cached click that has no associated field. The reasoning is structural. Submit buttons are plain clicks. Every other cacheable action, whether typing, a dropdown or a checkbox, always carries a field name, because the recorder refuses to cache them without one.

The history here is the project's bug number thirteen. The original approach was "whichever step sorts last." In a live run, the language model agent clicked submit and then, perhaps double-checking itself, re-selected two dropdown values after the submit. Those redundant actions sorted after the real submit click and were mistaken for the submit step. Since the submit step is always replayed with no value, every replay then tried to set a dropdown to nothing, which could never work, either by replay or by the live agent that was asked to do something nonsensical. That wrongly marked every genuinely successful record as failed, even though the real submit click, now an ordinary earlier step, had already worked. Identifying the submit step by what it structurally is, rather than where it sorts, makes the system immune to a trailing redundant action.

If, against expectations, no fieldless click exists, the function falls back to the old heuristic with a warning in the log.

### Replaying everything before submit

For every step except submit, in order, the function decides the value. If the step belongs to a field, the function looks up that field in the current record. If the record does not have that field at all, it counts a miss with the reason field missing in record and moves on without calling the model. If the step has no field, such as clicking the button that opens the form, the value is simply nothing. Then it hands the step to the inner helper described above.

### Fields the cache has never seen

After the cached steps, there is a second phase. The function works out which of the record's fields have no cached step at all and have a real value. This catches the case from earlier, where the seed record's own notes was empty, so no notes step was ever learned.

For each such field, it first checks the circuit. If open, it counts a miss and skips. Otherwise, it gets the per-field lock from Part 4. After acquiring it, it checks the circuit again, because another record may have opened it while this one waited. Then it looks in the store for a step for that field. If one exists now, because another record healed it while this one waited, it simply replays that step with this record's value, and moves on. This is the double-check described earlier.

If there is still no step, it counts a miss with the reason field missing from cache, and builds a synthetic step to hand to the live fallback. The synthetic step is a placeholder: it is a typed input, it has an empty address, and its position is set to just before the submit step's. That position matters, because when the fallback self-heals, it saves under those exact numbers, so on a future run the learned step will sort correctly before submit. The fallback is then run, and the circuit breaker is updated based on the result.

### Finally, submit

After everything else, the function runs the submit step through the inner helper as critical. The run result's success is then set to the opposite of the submit-failed flag, and the metrics dictionary is filled in with the mode set to replay, the hits, misses, language model steps and miss reasons. Both are returned.

---

## Part 11: The per-record wrapper

The function `_run_record_cached_async` wraps one record from start to finish. It is the longest and most delicate function in the file, and almost every line of it exists because of a real bug.

### Parameters and the idea of a port offset

It takes the record, the store, the workflow name, and a few extra values. The port offset deserves explanation. Every record runs in its own Chrome window, and every Chrome window needs its own remote-control debugging port. If two windows tried to share a port, they would either collide or, worse, one would quietly attach to the other's browser. So each concurrent record is assigned a small number, and the port used is the configured base port plus that number. The index and total values are only for display in the live dashboard.

### Deciding cold or replay

The function works out the path variant, notes the start time, and computes its port. Then it calls the seed lock's claim function. If it returns true, this record is the seeder and will do a cold run, with no cached steps. Otherwise, it reads the cached steps from the store and will replay them. Importantly, this happens before any browser is opened, so records waiting on the seed lock cost nothing.

It then builds a display name from the first and last name if the record has them, and sends a record-start event that includes whether the mode is cold or replay.

### Safe defaults

Before the risky part, the function creates a default run result saying the record did not start, and a default metrics dictionary. The reason is bug number seven, one of the most important in the project. In a real incident, the target form was briefly unreachable, and an error was raised deep inside one record's work. Because the batch uses a Python mechanism that, by default, cancels every other running task the moment any one of them raises an error, that single blip cancelled every other record in flight. A one-record network hiccup turned into a whole-batch failure, with zero records completed. The defaults exist so that, whatever happens, the record has valid values to report.

### The browser, opened and closed with care

The structure is a try block with a finally, with another try and a broad catch inside it. Inside, the function creates a temporary directory for a disposable Chrome profile, starts Playwright, and launches Chrome as a persistent context using that directory. It is launched visibly, not in the background, and with the remote debugging port set. It takes the first page, navigates to the target form, and builds the debugging address.

### Skipping when the quota is dead

Next comes a check that was added after a code review. If this record was going to be the seeder, but the daily quota is already known to be exhausted, the cold run would return instantly. But the next section, which waits for the database write, has a thirty-second timeout measured from the moment the block begins, not from how long the body takes. A skipped cold run submits nothing, so no write would ever arrive, and every record queued behind the seed lock at the moment the quota died would each sit through a full thirty-second dead wait, one after another. So when the quota is dead, the function skips that whole section and just records a skipped result.

### Waiting for the database write to be confirmed

This is the part with the longest comment in the file, and it tells a three-chapter story about one of the most dangerous kinds of failure: the silent kind.

Chapter one. In an early ten-record run, every replayed record reported complete success, all cache hits and no errors, but none of them actually appeared in the database. Only the cold-run record did. The cause is that the form's submit handler starts an asynchronous database write and returns immediately. A Playwright click returns the instant the click event fires, not when whatever it triggered has finished. The function's next action was to close the browser, which aborts any in-flight request. The write was being cut off.

The first fix was a fixed two-second wait. Chapter two: in a larger run with fifteen windows hammering the same local server and database at once, fourteen records reported success but never reached the database. A fixed delay can never be right under variable load. The only thing that scales is waiting for the actual network response that proves the write happened.

So the function waits for a response whose address contains the table's path and whose method is a POST, with a thirty-second budget, using Playwright's expect-response mechanism. The cold run or the replay happens inside that waiting block.

Chapter three. The first version of that fix had a misunderstanding of when the timeout fires. The waiting, and the timeout error, happen when the block exits, not when the response value is later read. So a slow but real success threw an error before reaching the code meant to catch it, and the error overwrote an already-correct, already-successful result with a failure. In a two-hundred-record run, a hundred and forty records were marked failed even though every one of them had landed correctly.

The final form catches the timeout right around the block itself. A timeout is now purely a diagnostic warning, and only if the record had otherwise succeeded. It is never a verdict that overwrites a correct result. If the block finishes normally and the record succeeded, the function reads the response, and if its status code is four hundred or higher, logs a warning that the record likely did not land. After that, it waits a short three hundred milliseconds to let any user-interface state settle. That buffer is no longer load-bearing for correctness, unlike the old two-second guess.

Finally, whatever happens, the browser context is closed in a finally block.

### The outer safety nets

Around all of that is a broad catch that converts any exception into a failed run result with the exception's type and message. This is the second half of bug number seven. The metrics stay at their zeroed defaults, because the record never got far enough to produce real counts.

Around that, in an outer finally, if this record was the seeder, it releases the seed lock. This is the guarantee behind the whole seed lock design. Because the release is outside everything else and in a finally, it happens on success, on failure, and on any exception. And because it happens after the browser has closed, the next waiting record only wakes up once the seeder's window is gone and the cache is populated.

### Closing the books

After all that, the function records the run in the store's metrics table, with the mode, the start and finish times, and the hit, miss and step counts. It sends a record-done event with the same figures plus the success flag and any error message, and, if the record failed, an additional error event. Then it returns the run result and metrics.

There is also a small synchronous wrapper that runs this function for a single record in its own event loop, mirroring the equivalent helper from version one. It is a convenience for running one record on its own.

---

## Part 12: The batch layer

### The pool of slots and ports

The function `_run_many_concurrent_async` runs all of the records, limited to a maximum number in flight at once. The limit comes from the configuration, currently ten. The mechanism is neat. It builds a queue of small numbers, from zero up to one less than the limit. Each of those numbers is both a slot and a port offset.

Each record gets its own small routine. The first thing it does is wait for a number from the queue. So only as many records as there are numbers can actually be running at once; the rest are waiting for a free slot, with no browser open. The number it receives becomes its port offset, so no two concurrent records can ever use the same port. When the record finishes, in a finally block, the number is put back on the queue for the next record to use.

### Abort and quota checks

After getting a slot, but before doing any real work, the routine checks two things: whether the caller has asked to abort, and whether the daily-quota tripwire is set. If either is true, the record is skipped, returning a failed result with a reason of aborted before starting or quota exhausted before starting. This design is deliberate. Records already in flight are left to finish naturally, rather than being yanked out from under a live browser session. Only records that have not yet started are skipped. That means an abort stops new work promptly without leaving Chrome windows or model calls half-done.

### Defense in depth

The per-record routine also has its own broad catch. This looks redundant, since the per-record wrapper already catches everything, and the comment says so. But it is there for defense in depth. The batch is run with Python's gather, which, by default, cancels all other running tasks the moment any one raises an exception. A single unforeseen exception, from any cause nobody thought of, must never take the whole batch down.

### The batch entry point

The function `run_batch_cached` is the one the web server calls. Its docstring explains the core design in a nutshell. Pure cache replay makes no language model calls at all, so it does not share the Gemini free tier's per-minute cap that kept version one from running concurrently. Only a cold run still calls the model, and running two of those at once would reintroduce the old problem. Every record is dispatched into the same pool at once, up to the limit. What keeps only one cold run in flight is the seed lock, not any upfront split of the list. And the limit exists because the limiting resource here is memory, since each window is a real Chrome process, not the model's quota. The limit was lowered from fifteen to ten after fifteen windows made the live event log badly interleaved during a demo and added enough load to contribute to some submit timeouts.

The function itself is short. If the list is empty, it returns an empty list. It clears the quota tripwire, computes the total, and builds a lookup from record identifier to its one-based position for display. It sends a batch-start event, gets the store, and runs the batch in a fresh event loop. Afterward, it sends a batch-done event with the totals of successes and failures, whether the batch was aborted, and whether the quota was exhausted, and returns the list of results.

---

## Part 13: Reconciliation, the cleanup pass

### Why it exists

The circuit breaker makes a deliberate trade-off. During a batch, if a field's live fallback is failing hard, say because Gemini is returning errors, the right move is to keep going and leave that field blank, rather than let one broken field stall a hundred records. But nobody actually wants a hundred records with some blank fields as the end state. The goal, in the words of the comment, is that in the end, all hundred records should be fully correct. Reconciliation is the other half of that bargain.

It runs after the batch has been graded. It looks at the grader's report, finds every field that was wrong or missing on a record that did land, and, only for fields whose circuit has since closed, retries just that one field through the application's edit flow. Editing an existing row is structurally different from filling a blank form. It starts by searching for the right person, opens the pre-filled edit form, changes one field, and saves. That is why it has its own prompt.

It is deliberately decoupled from whether a new batch is running. It runs on its own thread, started by the web server, so a brand-new batch can start immediately while the previous batch's cleanup continues in the background. To make sure the two cannot collide, reconciliation uses its own range of debugging ports, starting a hundred above the base, far clear of the main batch's range. Its pool is also smaller: at most three fixes at a time.

### Fixing one field

The function `_reconcile_one_field_async` fixes one field on one already-submitted record. Like everything else, it first checks the quota tripwire and returns failure immediately if it is set, before opening a browser.

Otherwise, it opens its own Chrome window on its own port, in a temporary profile, goes to the form's address, and builds a recorder. The recorder is named for a workflow called reconciliation and a variant called edit, and, importantly, saving is off: editing is not part of the create-record cache, and nothing about an edit is ever cached or replayed. It runs live, every time. The volume is small, usually a handful of fields, so building a second cache just for it would not pay off.

It builds the reconciliation prompt from the match key field and value, which is typically the email, plus the field to fix and its correct value. It creates a stub record, and runs the runner in text-only mode, attached to the open window, with the recorder attached. As in the other live paths, if that does not succeed and there is no quota wall, it retries once with screenshots on. Then it waits two seconds, closes the window in a finally block, and returns whether the agent reported success.

### Planning the pass

The function `run_reconciliation_pass` takes the batch's records and the grader's report. If there are no records, it returns zeros. It builds a lookup of each record's fields, and works out the path variant from the first record.

Then it builds a to-do list by walking through the report. It skips any record that never landed at all, because a record that is missing entirely needs the full create flow again, not an edit, which is out of scope here. It skips fields that passed. It skips fields whose expected value is empty, because a correctly empty field is not a real gap. And it skips fields whose circuit is still open, since there is no point retrying something already known to be broken right now. Everything left goes on the list as a record identifier, a field name, and the expected value.

It sends a reconciliation-start event with the size of the list, and if the list is empty, a reconciliation-done event, and stops. Otherwise, it builds its own small pool of three slots, and runs each fix through a small routine. That routine waits for a slot, runs the one-field fix, catches any exception, and puts the slot back. If the fix worked, it counts it as fixed and tells the circuit breaker about a success. If not, it counts it as skipped and tells the circuit breaker about a failure. Either way, it sends a reconciliation-fix event. All of them run together, and at the end a reconciliation-done event reports the fixed and skipped counts.

A synchronous wrapper at the bottom runs the whole pass in its own event loop, meant to be the target of its own thread.

---

## Part 14: How concurrency and thread safety fit together

It is worth stepping back, because this file mixes several kinds of concurrency, and each is protected in a different way.

Within a batch, records run as asynchronous tasks sharing one event loop. They take turns whenever one is waiting, for example for a page to load or for a model to answer. For these, protection comes from the asynchronous locks: the seed lock, so only one record seeds the cache at a time, and the per-field locks, so only one record fixes a given missing field at a time. Because everything shares one thread, ordinary dictionary updates between tasks, like the circuit breaker's state, are safe.

Across threads, there are two. The main batch runs on one thread. The reconciliation pass runs on another, and may overlap with a new batch. For these, protection comes from two other tools. The cache store has an ordinary thread lock around every database operation. And the daily-quota tripwire is a thread-safe event, so both threads see it reliably.

There is also a third kind of protection, not a lock at all but a structural habit: containment. Every layer that could throw an exception converts it into an ordinary failed result for just that record, so that no failure can propagate sideways into other work. This appears in the per-record wrapper, in the batch routine, in the live fallback, in the reconciliation fix, and in the event helper. It is the single most important architectural rule in the file.

---

## Part 15: The life of a batch, narrated

Here is what happens, in order, for a batch of a hundred records on an empty cache.

The web server calls the batch function. The tripwire is cleared, a batch-start event goes out, and the batch is launched. All hundred records are scheduled at once. A pool of ten slots means ten records get a slot immediately, while ninety wait, with no browser open.

The ten records each work out their path variant and ask to claim the seed responsibility. Nine of them find the cache empty, queue on the seed lock, and wait. One wins. It opens Chrome on its own port, navigates to the form, and enters the response-waiting block. The cold run begins. The recorder attaches, the language model fills in the form step by step, and the recorder writes each learned step into the cache. The agent submits, and the write is confirmed by a network response. The window closes. The seed lock is released.

The next waiting record wakes, re-checks the cache, finds it full, and returns false. It never launches a cold run. It reads the cached steps, opens its own Chrome window on its own port, and starts a replay. The others follow. As each record finishes, its slot is returned and another of the waiting ninety takes it. From this point on, nearly every record is a fast replay: each step is located by its address or its attributes, performed by Playwright, and counted as a hit, with no language model involved.

Now suppose a few records have a field the seed record did not have. They go through the missing-fields phase. The first to arrive takes the field's lock, finds nothing cached, and runs a live fallback. The others queue on the lock. When the first finishes and self-heals the cache, the others wake, re-check, find the step, and replay it for free. One live call for the whole batch.

Suppose the page changed slightly and one step misses on every record. The first record to miss calls the live fallback, heals the step, and the rest hit. Or suppose Gemini is returning errors for one field. The first two attempts fail, the circuit opens, and for thirty seconds every record skips that field instantly. Later, one gets a real attempt, and normal service resumes if the problem has cleared.

Suppose, worst of all, the daily quota runs out. The first record to notice sets the tripwire and sends one clear error. Every record not yet started is skipped. Records waiting on locks short-circuit. The batch ends quickly, flagged as quota exhausted, and reconciliation is skipped too, because it would only hit the same wall.

When the last record finishes, a batch-done event goes out. The web server then asks the evaluator to read the real database and grade the result, saves the report, and starts reconciliation in a background thread, which fixes what it can through the edit flow.

---

## Part 16: The design principles, and the bug history behind them

If you remember only a handful of ideas about this file, they are these.

First, contain failures. One record's bad luck must never reach another. This came from the incident where a single network blip cancelled a whole batch. It is the reason for the multiple layers of broad exception handling, and the reason for the safe defaults at the start of each record.

Second, never trust the automation's own opinion of itself. A click returning does not mean a database write finished. An agent saying done does not mean the record landed. That is why the system waits for the actual network response, treats timeouts as warnings rather than verdicts, and relies on the independent grader for the real answer.

Third, replace guesses with real signals. A fixed two-second wait was a guess. Waiting for the actual response is a signal. A pre-computed list of seed records was a guess. A runtime lock is a signal.

Fourth, stop feeding certain failure. The quota tripwire and the circuit breaker both exist to stop the system from repeatedly attempting something that cannot succeed right now, and the breaker also recovers on its own when things improve.

Fifth, spend the language model only where it is needed. The cache means a hundred records cost roughly one cold run plus a few repairs. The locks make sure those repairs are paid for once, not once per record.

Each safety device in the file maps to a real incident in the project's bug log. The cache key collision is bug one. The browser closing before the write landed is bug two. The checkbox toggling blindly is bug three. The missing-field gap is bug four. The thundering herd on a shared missing field is bug six, and the circuit breaker is its follow-up, six b. One failure cancelling a whole batch is bug seven. The unsafe database access across threads is bug eight. The check-then-set race on starting a batch is bug nine. The lock that could be held forever is bug twelve. The submit step identified by position is bug thirteen. The fixed wait that was not long enough is bug fourteen, and the fix that turned slow successes into false failures is bug fifteen. And the missing fast-stop on the daily quota is bug sixteen.

---

## Part 17: Things worth knowing, and things to watch

These are observations from reading the code closely. Some were checked directly, and some are inferences, and the difference is stated for each.

First, a latent issue with the locks. The seed lock and the per-field locks are stored in dictionaries at module level, so they persist for the life of the server process. But each batch runs in its own fresh event loop. An asynchronous lock in recent versions of Python becomes tied to the event loop in which it was first contended, and raises an error if it is later contended in a different loop. I tested this in isolation: a single lock contended in one run and then again in a second, separate run raised an error saying it was bound to a different event loop. In the orchestrator, this would show up only if the same lock were contended in two different batches within one server process, for example a field that stays missing from the cache across two batches. If it happened, the affected record would be caught and marked failed with that message. I did not see this in the project's runs, and the conditions are narrow, but it is a real edge case worth knowing, and a simple fix is to create the locks fresh at the start of each batch.

Second, a possible stale-entry case in the self-healing write. A cached click that has no field, such as the submit button, is keyed in the cache by its element address. If the live fallback heals such a step and the element's address has changed, the healed row gets a different key, so it is inserted next to the old row rather than replacing it. The stale row would then remain. By my reading of the code this is possible, though the circuit breaker would limit the damage, and I have not seen it happen. Field-based steps are not affected, because they are keyed by field name.

Third, the reconciliation fix waits a fixed two seconds after the edit before closing the window. That is the same kind of fixed guess that the project replaced with a real network confirmation for the create flow. It may be fine in practice, but it is the same class of risk, and worth watching if cleanup fixes ever seem to not stick.

Fourth, reconciliation reports the agent's own sense of success. Nothing inside this file re-reads the database after the cleanup to confirm that the fixes really landed.

Fifth, the browsers are launched visibly, not in the background. That is helpful for demos and debugging, but it means the machine needs a display, and ten windows at once is a real memory load. That is why the concurrency limit exists.

Sixth, several pieces are specific to the current target. The network address pattern used to confirm a write is specific to the Supabase table interface. The reconciliation prompt talks about DataDesk's People page. The path variant logic assumes a single branch. Pointing the system at a different application, such as a real ERP, would mean changing those, along with the comparison rules for the grader.

Seventh, all of the protective state, meaning the circuit breaker, the quota tripwire and the locks, lives only in memory. A server restart resets all of it. That is intentional for the breaker and the tripwire, and consistent with the design that an in-progress batch is not resumed after a restart; it is started again.

---

## Part 18: Questions a manager might ask, with short answers

How much does it cost to run a batch compared with the first version? In the first version, every record cost roughly a dozen language model calls. In this version, a batch costs roughly one cold run, plus a small number of repairs, plus the cleanup pass if there are wrong fields. A cache hit costs no model calls at all.

What stops it from damaging data if something goes wrong? A failure in one record is contained to that record. The success of a record is judged by whether the final submit worked, and the real outcome is judged by a separate grader that reads the database directly. Cleanup only edits fields that the grader found wrong, on records that already landed, and it never creates new rows.

What happens if the target form changes? Replay steps start to miss. The live fallback repairs them one field at a time, and writes the repair back into the cache. The hit rate statistics in the store would show a sudden drop, which is the early warning that something changed.

How does it scale? The limit today is memory, because each concurrent record is a real Chrome window, and the configured limit is ten. The language model is not a limit during replay, since replay uses none. The model matters only for the cold run, the repairs, and the cleanup, and those are what the free tier's quotas constrain.

What would it take to point this at a different system? The structure carries over. The pieces that are specific to the current target are the prompts, the write-confirmation address pattern, the path variant logic, and the field comparison rules. And a system with a real programming interface would be better served by calling that interface directly for writing, using this approach only where there is no such interface.

What are the known risks? The ones in the previous section: a narrow edge case with locks reused across batches, a possible stale entry for a field-less click, a fixed wait in the cleanup pass, and cleanup results that are not re-verified.

In one sentence, what is the orchestrator? It is the conductor that decides, for every record, whether to teach the cache or replay from it, keeps ten browsers from stepping on each other, protects the language model quota with a tripwire and a circuit breaker, makes sure one record's failure never harms another, and hands the final judgment to an independent grader.
