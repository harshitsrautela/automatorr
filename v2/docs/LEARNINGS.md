# LEARNINGS.md — deep-dive doubts cleared, with the full "why"

This file is a record of a long Q&A pass through v2's actual internals — not
a design doc, not a spec. Each entry below started as a specific question
("how does X actually work," "why does the code do X and not Y"), and the
answer here is written the same way it was worked out: grounded in the real
code (file/line references included), with the reasoning for why the current
approach was chosen over an obvious-sounding alternative ("plan B"), not just
a description of what the code does. If a reference file:line ever drifts
from what's written here, trust the code — this file is a snapshot of
understanding, not a live spec (same caveat as `FUTURE_PLANS.md`).

Organized by topic area, not chronologically.

---

## 1. Caching internals

### 1.1 The circuit breaker

**The doubt:** what is the circuit breaker, and why does it exist on top of
the seed lock?

**The answer:** `orchestrator.py`'s circuit breaker (`_CIRCUIT_FAILURE_THRESHOLD
= 2`, `_CIRCUIT_COOLDOWN_SECONDS = 30`, around line 246) exists because the
seed lock alone made one real failure mode WORSE, not better. A live run
(2026-09-28) hit Gemini returning `503 UNAVAILABLE` for one specific field
(`notes`) on every attempt. Without the circuit breaker, every record
needing that field queued up single-file behind the missing-field lock, and
each one still burned a full doomed live-agent attempt (retries included,
5-30s) before the next one even got a turn — a 100-record batch spent most
of its wall-clock time serially losing to an API that was never going to
say yes. The circuit breaker fixes this by tracking consecutive failures
per `(workflow, path_variant, target_key)`; after 2 in a row, it "opens" —
every later record targeting that same field skips the live fallback
entirely (no lock wait, no LLM call) for 30 seconds, then the next record
gets a real retry, self-recovering once the API comes back. **Plan B we
didn't take:** retry forever with backoff — rejected because it still
wastes real wall-clock time per record on a dead end that's already known;
skipping fast and retrying later is strictly better once 2 failures have
already proven the point.

### 1.2 Seed lock vs. missing-field lock — two different locks, two different jobs

**The doubt:** when 10 windows are replaying and one hits a cache miss on
an already-known field, does it block the other 9? Does it share the same
lock as the initial "who cold-runs this form" decision?

**The answer:** no — these are genuinely two separate locks with different
scopes. The **seed lock** (`_seed_locks`, keyed by `(workflow_name,
path_variant)` only, `orchestrator.py:184`) is a one-time, whole-form thing:
only one record ever claims "I'm cold-running this form" at a time; everyone
else waits, and once seeded, this lock is never touched again for that
form. The **missing-field lock** (`_missing_field_locks`, keyed by
`(workflow, variant, field_name)`, line 152) is per-field and only used when
a field has genuinely never been cached by anyone — it ensures only one
window pays the LLM cost to learn a brand-new field, others wait then reuse
what got learned. Critically: a replay MISS on an *already-cached* field
(stale locator, timing issue) is **not locked at all** — `_replay_or_fallback`
calls the live fallback directly (line 484), so multiple windows can
independently self-heal the same field concurrently. **Plan B rejected:**
locking every fallback call would have been simpler to write, but would
serialize unrelated per-tab glitches that have nothing to share — the
lock is reserved specifically for genuine shared-knowledge gaps.

### 1.3 Why a cached step actually fails on replay — `no_match` vs. `execution_failed`

**The doubt:** given a field IS in the cache, what specifically makes
replaying it fail?

**The answer:** `replay/engine.py`'s `ReplayResult.reason` is one of two
things. `"no_match"` (line 141) means the 2-tier locator (XPath, then
attribute selector) found zero or more-than-one matching element — DOM
structure shifted (a conditional field appeared/disappeared above it,
shifting XPath indices), the element isn't rendered yet, or something
regenerates IDs per render. `"execution_failed"` (line 169) means the
element WAS found uniquely, but the actual `.click()`/`.fill()`/
`.select_option()` threw — usually a `PlaywrightTimeoutError` because
something's blocking interaction (an overlay, the element not yet
interactable) or the DOM changed between locate and act. A third-looking
reason, `"field_missing_in_record"`, isn't a replay failure at all — it's
checked before `perform()` is even called, just meaning this record's JSON
has no value for that field. **Plan B not taken:** silently swallowing a
`no_match`/`execution_failed` into one generic "miss" — rejected because the
distinct reasons are genuinely useful for diagnosing *why* a cache is
degrading, visible in `miss_reasons` on every `record_done` event.

---

## 2. Element location — how a cached step gets re-found on a new page

### 2.1 XPath (tier 1)

**The doubt:** what is XPath, actually?

**The answer:** a query language for addressing one exact node in a tree —
built for XML, works on HTML/DOM too, since the DOM is also a tree. An
expression like `/html/body/div[2]/form/div[3]/select` reads as "walk down
from the root through these exact child positions" and resolves to one
specific element. It's the primary locator here because browser-use's own
live agent already computes it internally for every element it touches
(`element.x_path` on `DOMInteractedElement`) — it's handed to this project
for free, not derived. **Why tier 1 and not tried "equally" alongside tier
2:** XPath is the one locator that's *always* computable for any element
(every element has a tree position), where the attribute selector only
works if the element happens to carry one of a handful of specific
attributes. Position-based addressing is fragile to structural DOM drift
but universal; that's why it's tried first, not because it's "better" in
an absolute sense.

### 2.2 Attribute selector (tier 2) — and its real, confirmed limitation

**The doubt:** if the best attribute is ambiguous (matches >1 element),
does it fall back to combining attributes for more specificity?

**The answer, confirmed from the actual code (`engine.py:105`):** no — it
tries exactly ONE attribute, whichever is first present in this priority
order: `data-testid > data-test > data-cy > id > name > aria-label`. It
stops at the first one that EXISTS, not the first one that's unique — if
that single attribute's selector matches more than one element, `_locate()`
just gives up (returns `None` → `no_match`), it never falls through to try
the next attribute or combine two attributes into one compound selector.
This is a real, acknowledged limitation — not a missing feature I
imagined, confirmed by reading the exact loop. The priority order itself is
ranked "hardest to accidentally duplicate" (test-specific attributes,
`id`, by-spec page-unique) down to "easiest to duplicate" (`name`, which a
radio group can legitimately share; `aria-label`, least standardized).
**Plan B, written down, not yet built** (`FUTURE_PLANS.md #6`): if the
single best attribute is ambiguous, try adding the next-best as an
ADDITIONAL constraint, one at a time — not jump straight to giving up, and
not build one giant compound selector from everything available up front
(a compound selector is only as reliable as its least-stable ingredient,
so over-combining trades a *different* kind of fragility for the one it
fixes).

### 2.3 `aria-label` — what it actually is

**The doubt:** what is `aria-label`, and why is it in the fallback list at
all?

**The answer:** an HTML accessibility attribute, meant to give a screen
reader a spoken name for an element with no visible text of its own. It's
useful as a locator specifically because it tends to be hand-written and
rarely churns, unlike an auto-generated `id` some frameworks regenerate per
render — stable and semantically meaningful, just less universally present
than `id`/`name`, which is why it sits last in the priority list rather
than higher.

### 2.4 `stable_hash` vs. `fingerprint` — genuinely two different things

**The doubt:** are these the same value under two names, or actually
different?

**The answer:** different, deliberately. `stable_hash` is browser-use's OWN
internal value, computed from private internals (its own DOM-tree node
objects + a live accessibility-tree snapshot) that a plain Playwright
script has no access to — it's stored in the cache row purely as a
diagnostic string, explicitly "never used as a lookup key"
(`fingerprint.py:22`). `fingerprint` is THIS project's own independent hash
(`compute_fingerprint()`, SHA-256 over a filtered, sorted, normalized
string of `node_name` + a static-attribute allowlist), computed by the
SAME function on both the cache-write side and the replay side — so the
two fingerprints are comparable to each other even though neither matches
browser-use's `stable_hash` bit-for-bit. **Why not just reuse
`stable_hash` directly:** recomputing it at replay time would require
reconstructing browser-use's private DOM-snapshot internals inside
`replay/engine.py`, which is deliberately built with zero `browser_use`
import to keep cache-hit replay genuinely LLM-free and cheap.

### 2.5 Fingerprint mismatch — the "canary metric" idea

**The doubt:** if a fingerprint mismatch doesn't block anything, why
compute and compare it at all?

**The answer:** it's checked ONLY after a locator (tier 1 or 2) already
found exactly one unique element (`engine.py:73`/`:92`) — never part of the
decision whether a locator succeeds. On mismatch, it just logs a warning
(`engine.py:126`) and the replay proceeds exactly as it would have anyway.
This is deliberate: "the locator matched one element uniquely" is already
strong evidence it's correct; treating a fingerprint drift as a hard
failure on top of that would risk forcing expensive LLM fallbacks for
things that are probably still fine (a class picked up one unrelated
utility class). The term for this pattern is a **canary metric** — named
after miners carrying a caged canary that would keel over from toxic gas
before a human could notice; the bird didn't fix anything, it was pure
early warning. Same idea here: a rising rate of fingerprint-mismatch
warnings for one field is a signal worth investigating BEFORE it degrades
into a real `no_match`, without ever blocking anything on its own.

### 2.6 What's actually stored in the SQLite cache — one row, explained column by column

**The doubt:** what does one cache row actually look like? (can't open
`.sqlite3` in a text editor)

**The answer, a concrete example row** (schema at `cache/store.py:34`):
- `workflow_name`/`path_variant` — which form and which branch of it (only
  `"default"` exists today; DataDesk's form never branches).
- `target_key` — the real primary-key identity (with workflow+variant): a
  field name for value-carrying steps, or `"<action_type>::<x_path>"` for a
  fieldless click (submit). This is what makes the cache a true
  dictionary — one current answer per field, no duplicates.
- `step_index`/`action_offset` — ordering hints ONLY, not part of row
  identity. This distinction is itself the fix for a real bug: a stubborn
  dropdown that took 5 attempts in one cold run would, if keyed by
  step_index, store 5 separate rows (4 dead ends + 1 real answer), and
  replay would retry every dead end first. Keying by `target_key` instead
  means the LAST successful attempt simply overwrites the earlier ones.
- `action_type` — `click`/`input`/`select_dropdown`/`checkbox`, tells
  `perform()` which Playwright call to make.
- `field_name` — which record field this step's value maps to (`NULL` for
  a fieldless click).
- `instruction` — diagnostic only: the agent's own stated goal text at
  capture time, never read by replay logic.
- `x_path`/`node_name`/`attributes_json` — the actual locator data (tiers
  1 and 2 both read from here).
- `stable_hash`/`fingerprint` — see 2.4 above.
- `updated_at` — last write/overwrite time.

---

## 3. Reconciliation — closing the "eventually everything's correct" loop

### 3.1 How it actually works — search, edit, one field, always live

**The doubt:** after verification flags wrong/missing fields on records
that DID land, how does the fix actually happen — does it search, edit,
handle pagination?

**The answer:** `run_reconciliation_pass()` (`orchestrator.py:1166`) builds
a to-do list of `(record_id, field_name, expected_value)` from
`report.per_record_results`, filtered to: the record landed in the DB at
all (`matched_in_db`), the field actually failed, the expected value isn't
empty, and the field's circuit isn't currently open. Each surviving entry
gets ONE live LLM call via `build_reconciliation_prompt()` — never cache
replay, `auto_persist=False` always — that navigates to DataDesk's LIST
page (not the blank "Add Person" form), tells the agent to search for the
row by its match key (email), open its edit view, change ONLY the one
broken field, and save. Pagination is handled with zero scripted logic —
the prompt just says "use the search box if the list is long," and since
this is always a live agent (never replay), it can use judgment the same
way v1's original agent always could.

### 3.2 Reconciliation's concurrency, and why it's safe to share the SAME SQLite connection

**The doubt:** reconciliation runs on its own background thread while a
brand-new batch can start — don't they collide on the same cache file?

**The answer:** they genuinely share ONE SQLite connection (a module-level
singleton via `orchestrator.get_store()`), and this was a REAL bug, found
and fixed (`cache/store.py:95`): `check_same_thread=False` makes
cross-thread USE legal but not automatically SAFE — the caller has to
serialize it. `CacheStore` wraps every read/write in its own
`threading.Lock()` (`store.py:106`), so even if two different threads hit
the cache at the exact same instant, they're serialized through that lock
rather than racing. Separately, reconciliation uses its OWN CDP port range
(`RECONCILE_PORT_OFFSET_BASE = 100`, so ports 9433-9435 vs. the main
batch's 9333-9342) specifically so two unrelated batches' BROWSER sessions
can never collide, even while their cache WRITES are safely serialized
through the shared lock.

### 3.3 Does reconciliation's own circuit-breaker interaction risk an infinite loop?

**The doubt:** if reconciliation hits a circuit-open field, does that
spiral since we're already inside the "fix things" logic?

**The answer:** no infinite loop — each `(record, field)` in the to-do list
gets exactly ONE attempt, period; nothing retries within a pass. The
circuit check (`_circuit_is_open`, `orchestrator.py:1195`) only gates
whether a fix is even ATTEMPTED, checked once, up front, before any
browser opens — not re-checked per-item during the pass. One real subtlety
this surfaced: if field X's circuit opens MID-pass (two failures during
this same reconciliation run), an already-queued record #7 needing that
same field still runs anyway — the circuit only protects the NEXT thing
that checks it (a future pass), not other items already in THIS pass's
to-do list. Separately: by the time reconciliation even gets to check a
circuit, the whole batch PLUS a full Supabase read has already happened —
often well past the 30-second cooldown — so most of the time a field's
circuit has already self-closed by the time reconciliation looks at it,
giving it a natural retry without any special-casing needed. The edge
case where this DOESN'T hold is a genuinely fast batch (small, mostly warm
cache) that finishes in well under 30 seconds.

### 3.4 Two real gaps reconciliation has, both written down, not yet fixed

**The doubt-turned-finding #1:** the `if fr.expected_value in (None, ""):
continue` skip (meant to mean "correctly empty, not a real gap") only
fires AFTER `fr.passed` is already False — meaning it actually catches the
OPPOSITE case: source says empty, but the DB has some wrong non-empty
value sitting there (a bug, a stray write). The filter silently treats
"there's junk data that shouldn't be there" as "nothing to fix," forever,
since reconciliation is one-shot. Written up as `FUTURE_PLANS.md #7`.

**The doubt-turned-finding #2:** `verify_batch()` — the real, independent
Supabase-read check — is called exactly twice in the whole codebase, both
BEFORE reconciliation ever starts. Reconciliation's own `fixed`/`skipped`
tally comes entirely from the live agent's OWN self-reported
`RunResult.success` — exactly the kind of self-report
`evaluation/evaluator.py`'s own docstring says should never be trusted
("the agent's own self-reported outcome is not evidence"). Reconciliation
is the one place in the current codebase that quietly relies on it anyway.
Written up as `FUTURE_PLANS.md #8` (re-verify just the touched fields, not
a full re-verify of the whole batch).

---

## 4. Browser automation internals — CDP, ports, and what talks to what

### 4.1 CDP — what it is, and that it's a spec, not a small file

**The doubt:** is CDP "just a file" with the rules in it?

**The answer:** CDP (Chrome DevTools Protocol) is a protocol SPECIFICATION
— a large, versioned definition of every command/event Chrome exposes for
remote control, organized into domains (`Page`, `DOM`, `Network`, `Input`,
dozens of them). It IS published as structured, machine-readable JSON
schema files (which generator tools parse to build typed clients), but
it's not one small reference file — it spans essentially everything a
browser can be remotely asked to do. Chrome's own C++ code is what
actually implements/enforces it at runtime once you connect to its debug
port.

### 4.2 `cdp-use` — browser-use's own library, NOT shared with Playwright

**The doubt:** is `cdp-use` the general tool everyone (including
Playwright) uses to speak CDP?

**The answer, confirmed from actual installed package metadata:**
`browser-use==0.13.10` depends on `cdp-use==1.4.5` — its OWN dedicated,
type-safe CDP client, built by the same `browser-use` GitHub org, depending
only on `httpx`+`websockets`. Playwright's own Python package depends on
NEITHER Playwright-specific CDP code in Python nor `cdp-use` — its real
engine is a bundled Node.js/TypeScript program (visible as a `driver/`
folder in the installed package), with its OWN, separately-written CDP
implementation inside that Node.js driver. So: two fully independent,
parallel implementations, each talking directly to CDP, neither depending
on the other. `cdp-use` is also much thinner than Playwright overall — just
raw typed CDP bindings, no browser-launching logic, no auto-wait/retry
heuristics, no multi-browser-engine support; browser-use's own
"action watchdog" code (`default_action_watchdog.py`) is what adds that
layer on top, playing a role similar to what Playwright's internals do,
but independently built.

### 4.3 Is browser-use "just Playwright 2.0"? No — different layers, not an upgrade chain

**The doubt:** since browser-use talks CDP and Playwright talks CDP, isn't
browser-use basically an improved/smarter Playwright?

**The answer:** no, and the original mental model of a CHAIN
(browser-use → Playwright → CDP) was wrong too — they're PEERS sitting
directly on CDP, not a chain. More importantly, they solve different
problems at different layers regardless of dependencies: Playwright is
pure, deterministic EXECUTION — zero judgment, does exactly what the code
says, every time. browser-use is a DECISION-MAKING layer — an LLM looks at
the page and decides what should happen next, and only then does something
execute that decision at the low level. The analogy that fits: a
self-driving car isn't "Car 2.0" — the actual car parts (steering, brakes)
aren't what got upgraded; an entirely different KIND of thing (a
perception+decision system) got layered on top of ordinary actuators. This
project's `replay/engine.py` IS the "Playwright, deterministic execution"
role; browser-use (via `cdp-use`) plays the "decide what to do" role,
independently.

### 4.4 Ports, and whether 8000/5173/9333 are "reserved"

**The doubt:** is 9333 an OS-level default for CDP? Are ports like
8000/5173/9333 reserved, or can anything claim them?

**The answer:** 9333 isn't an OS or Chrome standard — the commonly-known
convention is actually 9222, and 9333 here (`config.py:70`) looks like an
arbitrary number picked specifically to avoid that more common default.
More generally: only ports 0-1023 are genuinely OS-reserved ("well-known"
ports — 80, 443, 22). Ports 1024-49151 (where 3000/5000/8000/5173 all
live) are NOT OS-reserved for React/Flask/FastAPI/Vite at all — those are
pure TOOLING CONVENTIONS, free real estate otherwise, first-come
first-served. "Is a port taken" only ever means "is something on THIS
machine bound to it right now" — there's no global registry beyond
sub-1024. Stacking `CDP_DEBUG_PORT + port_offset` for up to 10 concurrent
windows (9333-9342) plus reconciliation's own offset block
(`RECONCILE_PORT_OFFSET_BASE = 100` → 9433-9435) is safe specifically
because it's an obscure, uncommonly-used range — and even if it DID
collide with something, Playwright's `launch_persistent_context` would
fail loudly with an "address already in use" error immediately, not
silently misbehave.

### 4.5 "JSON over WebSocket," and correcting my own confusing earlier phrasing

**The doubt:** why describe CDP as "JSON over WebSocket"? And does
Playwright actually use a DIFFERENT protocol (HTTP) than browser-use
(WebSocket) to control the same browser window?

**The answer:** WebSocket is a protocol that opens ONE persistent,
two-way connection (unlike ordinary HTTP's one request → one response →
close) — either side can push a message at any time without being asked.
CDP sends JSON-formatted commands/events over that open connection. The
genuinely important correction here: Playwright does NOT use HTTP to
control pages — `.click()`/`.fill()` etc. are themselves implemented via
CDP over WebSocket internally, same transport browser-use's own driver
uses, sometimes literally the SAME open connection (that's exactly what
`BrowserProfile(cdp_url=...)` achieves — browser-use attaching to the
browser Playwright already launched, rather than opening a second one).
The ONE place HTTP genuinely appears: a single one-time discovery GET to
`http://127.0.0.1:<port>/json/version`, which returns the actual WebSocket
URL to connect to — after that handshake, everything real (from both
libraries) flows over the WebSocket. My own earlier sentence contrasting
"Playwright polling over HTTP" vs. CDP's WebSocket was a hypothetical
illustration of protocol design in the abstract, badly worded as if it
described what Playwright itself does — it doesn't; Playwright has never
used HTTP polling for control, only CDP/WebSocket, same as browser-use.

### 4.6 `headless=False` — how coupled is the code to it?

**The doubt:** if we switch to headless later, is that a huge rewrite, or
small?

**The answer:** genuinely small — `headless=False` appears as a hardcoded
literal in exactly two places (`orchestrator.py:710` and `:1128`), not
wired to config at all, and nothing else in the codebase branches on
browser visibility. CDP, screenshots/vision, and element location all work
identically headed or headless — headless mode exists specifically FOR
automation use cases like this one. The real trade-offs aren't code
complexity, they're PURPOSE ones: headed mode is what makes a live demo
watchable and lets issues get caught visually (exactly how the country-
dropdown bug got caught); headless is what's actually needed to ever run
this on a real server with no display, and it uses less RAM/CPU per Chrome
instance. Written up as `FUTURE_PLANS.md #9` (one `HEADLESS` config value
instead of two literals to keep in sync by hand) rather than flipped now.

---

## 5. The LLM call itself — what's actually happening under the hood

### 5.1 The agent's step loop — NOT the LLM "prompting itself"

**The doubt:** does browser-use make a prompt, feed it to the LLM, which
then reads/writes its own prompt in a loop — is it giving itself orders?

**The answer, traced through the real code** (`agent/service.py:1035`
`step()` → `_get_next_action()` → `self.llm.ainvoke(input_messages)`): no
self-dialogue anywhere. Browser-use's own plain Python code (not the LLM)
builds a prompt from the current DOM snapshot + task + history. That ONE
prompt goes out as a real network call to Gemini's actual API — a
genuinely separate, external system, same as any other API call. Gemini
sends back ONE decision. Browser-use's Python code parses it and executes
real actions via `cdp-use`. The page changes; browser-use's code builds a
NEW prompt from the NEW state and sends THAT for the next decision. The
"loop" is real, but it's "code repeatedly asks an external system a fresh
question based on new reality," not the model reading its own output back
to itself.

### 5.2 How a text decision becomes a deterministic click — structured output, not NLP

**The doubt:** if browser-use's Python code "parses" the LLM's decision
into a real action, isn't that NLP? How can NLP be deterministic?

**The answer, confirmed in code** (`agent/service.py:1950`:
`kwargs = {'output_format': self.AgentOutput, ...}`): it's NOT NLP on the
output side — `AgentOutput` is a Pydantic model with a FIXED schema, and
`output_format=self.AgentOutput` triggers the provider's actual
**structured output / constrained decoding** feature. This is mechanically
different from "ask nicely for JSON, parse it afterward and hope": the
schema is converted into a grammar, and at EVERY token-generation step,
tokens that would violate that grammar are masked out (probability forced
to zero) BEFORE the model's choice is finalized — the model can only ever
pick from whatever's left valid. So the result is valid JSON "by
construction," not by luck. The NLP/reasoning happens once, entirely
inside deciding WHICH action to take; turning the resulting guaranteed-
valid JSON into a real CDP call afterward is ordinary, deterministic
dictionary dispatch — zero interpretation needed.

### 5.3 `ainvoke` — what the "a" means

**The doubt:** what is `ainvoke`, what's the "a"?

**The answer:** "a" = async. `ainvoke` is the `async def`, awaitable
version of a plain blocking `invoke` call (naming convention from
LangChain-style interfaces). Needed because this whole system runs on
`asyncio` — while one record's `await ainvoke(...)` is waiting on Gemini's
network round-trip, the shared event loop is free to do OTHER useful work
(another record's Playwright replay) instead of freezing the whole
program. Same underlying reason `asyncio.sleep` (not `time.sleep`) was
used for the rate-limit cooldown elsewhere — a blocking call would stall
every concurrently-running record, not just one.

### 5.4 Field-name "guessing," and the one real bug it led to (now fixed)

**The doubt:** `infer_field_type()`'s own docstring says it "guesses" —
what does that mean, and is it reliable?

**The answer:** not ML, just keyword matching on the field NAME string
(never its value) — `infer_field_type()` checks a fixed override dict
first (`FIELD_NAME_OVERRIDES`, currently empty, an escape hatch for a
future field name the keyword lists get wrong), then keyword lists for
date/phone/money/email/etc. It's called a "guess" because it's a
heuristic, not a certainty — a field genuinely named something like
`"birth_certificate_number"` would be wrongly inferred as a date purely
because it contains "birth."

**The real bug this doubt led to finding, now FIXED:** `_match_field()`
reverse-matches a typed value back to a field name by normalized equality,
with NO memory of which fields were already claimed in this same run. For
a record where `first_name == last_name == "John"`, both actions would
match the SAME first field in iteration order, silently overwriting the
correct cache entry for whichever field came second (since `target_key` is
the primary key). Fixed in `cache/instrumentation.py` by adding
`self.matched_fields: set[str]`, excluded from future matches once a field
is claimed within one record's capture — a duplicate value now correctly
falls through to the next still-unclaimed field instead of re-claiming the
first one.

### 5.5 Vision fallback — added at every live LLM call site, not in replay

**The decision:** add a vision retry (`use_vision=True`) at every point
this project calls a live LLM, triggered only when the text-only attempt
didn't self-report success and it isn't already a known quota dead end —
then fall through to existing failure handling unchanged if vision also
fails. Implemented at all three real call sites: `_run_cold` (whole-record
cold run), `_run_live_fallback_step` (scoped single-field self-heal), and
`_reconcile_one_field_async` (reconciliation edit) — never in
`replay/engine.py`, which makes no LLM calls at all by design. Each retry
reuses the SAME browser tab (same `cdp_url`) so nothing already filled in
is lost. **Plan B explicitly rejected:** copying v1's old 60-second
cooldown sleep before the retry — that sleep was written for v1's SERIAL
batch loop, where it only delayed one record; reused inside `_run_cold`
here, it would hold the seed lock for a full minute, blocking every other
waiting record too. Left out deliberately rather than inherited by
accident; a cooldown can be added later if actually needed, on purpose.

---

## 6. Prompt/context caching — why resending everything is normal, and what changes it

### 6.1 Why a "chat" feels continuous when every API call is stateless

**The doubt:** humans chatting with Gemini seems like one ongoing
conversation — how does that square with "every call is independent"?

**The answer:** the continuity is an illusion built entirely by the CLIENT
APP, not by Gemini remembering anything. Every new message you send
secretly includes the ENTIRE conversation so far, resent in full, as part
of one new independent call — Gemini reads it fresh each time as if for
the first time. There is no session object on Gemini's side linking your
calls together; your API key is pure authentication, carrying zero
conversational identity. This project's own `MessageManager`/`history`
inside browser-use does exactly this same job already — building up and
resending the growing transcript (or a compacted summary of older parts)
on every step.

### 6.2 Implicit vs. explicit caching — genuinely different mechanisms

**The doubt:** does explicit caching mean I never send the text again?
Does implicit caching require a byte-perfect match, and is checking that
practical at Google's scale?

**The answer — explicit caching:** you send the big constant block ONCE,
to a dedicated cache-creation call, and get back a short reference ID.
Every call AFTER that sends only the ID + whatever's new — the original
text is genuinely NOT resent. This is the mode that reduces what's
transmitted, not just what's computed, and it's the one worth deliberately
using. **Implicit/automatic caching:** no explicit object at all — you DO
keep sending the full text every call, but if an identical prefix was seen
recently, the server silently skips recomputing it, saving compute/cost
but not transmission. **On the practicality doubt:** yes, exact match
required — it's hash-based (any single-character change produces a
completely different hash, no partial credit), but it's a PREFIX match,
not whole-message — only the leading, unchanging chunk (system prompt +
task) needs to be byte-identical; the DOM snapshot after it is free to
change every call without breaking the cache hit on the leading part.
That's also why it's feasible at scale: it's scoped to your own
account/project (not checked against the world), matched via a cheap hash
lookup (not content understanding), against a short, constantly-expiring
time window (not all history) — the same category of infrastructure CDNs
and database indexes have run cheaply for decades.

**Confirmed in this project's own code:** browser-use's `ChatGoogle`
wrapper already reads `response.usage_metadata.cached_content_token_count`
back from Gemini (`google/chat.py:231`) — meaning implicit caching's effect
would already show up for free, at zero code changes, if it's happening.
It does NOT currently expose a way to explicitly create/attach a cache,
though — getting real control over explicit caching would mean going
around browser-use's own LLM wrapper, not just flipping a config value.
Written up as `FUTURE_PLANS.md #10` — a real but SMALL optimization, since
v2's caching layer already eliminated the big cost (N records × 10-15
calls collapsed to ~1 cold run per batch); this only shrinks the cost of
the few live calls that still happen.
