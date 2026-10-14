# Future plans — not implemented yet, written down so we don't lose them

This is a running list of things discussed but deliberately **not built**
yet — either because they need a decision only made later, or because
building them now would be guessing without a way to actually test the
guess. Nothing in this file describes the current system; for that, read
`00_FULL_OVERVIEW.md` / `06_CODE_WALKTHROUGH.md`. This file is the
opposite of those — it's the "known gaps, on purpose" list.

Current focus (as of this writing): **make the automation engine itself
general-purpose — not tied to DataDesk's specific form — while explicitly
NOT worrying yet about ERP-scale concerns** (direct DB access for
verification, multi-client deployment, etc.). Those come later, once this
gets built into something bigger and sold to clients — see "The bigger
picture" at the bottom.

---

## 1. Broader action-type coverage

**Where:** `cache/instrumentation.py` (deciding what's cacheable) and
`replay/engine.py` (replaying it).

**Gap:** only 4 action shapes are cacheable today — plain click, text
input, native `<select>` dropdown, and checkbox. A form with radio button
groups, a date-picker widget, a multi-select list, a JS-built toggle
switch, drag-and-drop, or a file upload would fall through to always
live-fallback for those fields — correct, but never fast, since nothing
about them ever gets cached.

**Shape of the fix, when we get to it:** each new type needs two small,
additive pieces — teach `StepRecorder.callback` to recognize the new
browser-use action name and extract a value/state from it, and add one
more `elif` branch to `ReplayEngine.perform()` with the matching
Playwright call. Not a redesign — the existing structure was built to make
this exactly this contained.

**Priority reasoning:** ranked highest of the "safe to build now" items,
since it directly determines how many fields on ANY new form can be
cached at all, and it's low-risk (purely additive, doesn't change
behavior for existing action types).

---

## 2. A second, differently-shaped test target

**Gap:** this codebase has only ever been run against DataDesk. Every
claim in `05_PORTABILITY.md` about "the caching engine would work
unchanged against a different form" is reasoned from reading the code,
not proven by an actual second run against an actual different site.

**Decision (as of this conversation):** not building this right now —
just writing it down as the thing that needs to happen before trusting
portability claims for real. When we do:
- Should be deliberately DIFFERENT in shape from DataDesk — different
  widget types (see #1), maybe a different frontend framework, to
  actually stress the "closest matching label" prompt logic and the
  xpath/attribute locate strategy against something that isn't
  suspiciously well-behaved semantic HTML.
- Cheapest version: a second small practice React/Supabase app, same
  spirit as DataDesk itself (a safe, disposable rehearsal target) — not a
  real client site.

---

## 3. Element-locating robustness for messier real-world UIs

**Where:** `replay/engine.py`'s `_locate()` (currently: xpath, then an
attribute selector).

**Gap:** DataDesk is unusually clean — semantic HTML, stable ids,
accessible labels, plain `<select>` elements. Real internal tools are
often built with heavier JS frameworks: custom-styled dropdowns that
aren't real `<select>` tags, virtualized lists that only render visible
rows, content inside an iframe or shadow DOM. The current 2-tier locate
strategy assumes a fairly ordinary DOM and hasn't been tested against
anything else.

**Shape of the fix:** additional locate tiers — visible-text matching,
ARIA-role-based lookup, shadow-DOM piercing, iframe-aware locators — added
alongside (not replacing) the existing xpath/attribute tiers.

**Why this waits:** hard to build correctly without a real messy target to
test against (see #2) — building speculative locate strategies for
problems we haven't actually hit yet risks solving the wrong problem.
Revisit once #2 exists, or once we're pointed at an actual second site.

---

## 4. Reconciliation prompt genericity

**Where:** `agent/prompts.py`'s `build_reconciliation_prompt` —
currently hardcodes "You are on DataDesk's People page... use the search
box... click that row's edit action."

**Gap:** this assumes a specific navigation pattern (searchable list +
edit action per row + pre-filled edit form). Common for admin/CRUD tools,
but not universal — a wizard-style edit flow, or a target with no search
capability, would need this rewritten.

**Shape of the fix:** make the edit-flow description a per-target,
configurable piece of text (or a small structured description) instead of
a hardcoded string — same spirit as `TARGET_FORM_URL`/`MATCH_KEY_FIELD`
already being config-driven.

**Priority reasoning:** small, contained, low-risk — but only matters once
there's a second target whose edit flow actually differs from DataDesk's,
so it's tied to #2 rather than urgent on its own.

---

## 5. Multi-step / multi-page / wizard forms

**Gap:** the entire current design assumes one page load → fill every
field → one submit click. A wizard ("next → next → submit") or a form
that conditionally reveals fields based on an earlier answer doesn't fit
this model — the cache's flat list-of-steps and the "steps_before_submit,
then submit" ordering rule both assume everything happens on one page in
one pass.

**Shape of the fix:** not scoped yet — this is a genuine design question,
not a small addition like #1/#4. Would likely need the cache to track
something like a "phase" or "page" per step, and the replay loop to
navigate between phases rather than assuming one flat sequence. Needs real
thought before touching code, not just a quick branch add.

**Priority reasoning:** lowest priority of this list — DataDesk doesn't
need it, no concrete second target needs it yet either, and it's the
biggest unknown in terms of how much would actually need to change.

---

## 6. Compound (multi-attribute) selector as a deeper tier-2 fallback

**Where:** `replay/engine.py`'s `_attribute_selector()` — currently picks
exactly ONE attribute (whichever is first present in the priority list:
`data-testid` > `data-test` > `data-cy` > `id` > `name` > `aria-label`) and
builds a single-attribute CSS selector from it. If that one attribute
matches more than one element on the page, `_locate()` gives up entirely
(logs a warning, returns `no_match`) rather than trying anything else — it
never combines two attributes together to narrow the match further.

**Gap:** a single attribute — especially a lower-priority one like `name`
or `aria-label` — can legitimately collide across multiple elements (e.g. a
radio group sharing one `name`). In that case a second, otherwise-lower-
priority attribute might combine with the first to uniquely identify the
right element, without ever needing to fall back to the LLM at all.

**Shape of the fix, when we get to it:** if the single best available
attribute is ambiguous (count > 1), try adding the *next*-best available
attribute as an additional constraint on the same selector (e.g.
`[name="x"][id="y"]`), one attribute at a time, rather than jumping straight
to "give up, call the LLM." Keep it incremental/tiered rather than always
building one big compound selector from every available attribute up
front — a compound selector is only as reliable as its least-stable
ingredient, so combining more attributes than necessary raises the risk of
the whole locator breaking when an unrelated, non-identity attribute
changes (see the tier-1/tier-2 discussion — this is the same "more precise
but more fragile" trade-off, just one level deeper).

**Priority reasoning:** small, additive, testable against DataDesk right
now (doesn't need a second test target like #2/#3) — but not urgent, since
`id`/`data-testid` collisions are rare by spec/convention and DataDesk
hasn't actually hit this failure mode yet.

---

## 7. Reconciliation's "expected empty" skip is one-directional — misses stray wrong data

**Where:** `orchestrator.py`'s `run_reconciliation_pass()`, the todo-building
loop (`if fr.expected_value in (None, ""): continue`).

**Gap:** this line only runs after `fr.passed` is already `False` (i.e. a
real mismatch was already found), so it does NOT mean "source and DB agree
this is empty" — that case would already have `passed=True` and never reach
here. What it actually catches is the *other* direction: the source record
says a field should be empty/`None`, but the DB has some non-empty value
sitting there (written by a bug, or an incorrect live-fallback). The comment
"correctly empty, not a real gap" is wrong for this case — it's actually
"incorrectly NOT empty," and this filter silently skips fixing it forever,
since reconciliation only gets one pass.

**Shape of the fix:** don't treat `expected_value in (None, "")` as an
automatic skip. If `actual_value` is non-empty while `expected_value` is
empty, that's a real gap (stray/wrong data) and should be queued for
reconciliation the same as any other mismatch — the field's fix in that case
being "clear it," not "set it."

**Priority reasoning:** correctness gap, silent (never surfaces as an error,
just quietly never gets cleaned up) — worth fixing directly rather than
deferring, since it's small and contained to one filter condition.

---

## 8. Reconciliation never independently re-verifies its own fixes

**Where:** `api/routes.py` — `verify_batch()` (the real, independent
Supabase-read check) is called exactly twice in the whole codebase, both
BEFORE `run_reconciliation_pass` ever starts. Nothing calls it again after
reconciliation finishes.

**Gap:** reconciliation's own `fixed`/`skipped` tally comes entirely from
`RunResult.success` — the live agent's OWN self-reported opinion of whether
its edit worked ([orchestrator.py:1161]). This directly contradicts this
project's own core design principle, stated explicitly in
`evaluation/evaluator.py`'s docstring: "the agent's own self-reported
outcome is not evidence." Reconciliation is the one place in the current
codebase that quietly relies on exactly that self-report anyway. The
dashboard's Reconciliation panel can report "N fixed" while one of those N
still has the wrong value sitting in Supabase, and nothing currently catches
that.

**Shape of the fix:** after `run_reconciliation_pass` finishes, run the
evaluator ONE more time — but scoped only to the specific (record_id,
field_name) pairs reconciliation actually attempted, not a full re-verify of
every row in the batch (that would be wasteful; the rest of the batch's
fields haven't changed). Concretely: re-fetch just those records' rows from
Supabase and re-run `values_match` on just the touched fields, then emit a
final "reconciliation_verified" event (or fold the real outcome into the
existing `reconciliation_done` counts) so "fixed" actually means
DB-confirmed fixed, not agent-claimed fixed.

**Priority reasoning:** real trust gap in the one part of the pipeline that
was supposed to be the "closes the loop, make sure everything ends up
correct" step — worth doing before relying on reconciliation numbers in a
real demo or client-facing report.

---

## 9. Single source of truth for `headless`

**Where:** `orchestrator.py` — `headless=False` is hardcoded as a literal in
TWO separate places: the main batch's browser launch ([orchestrator.py:710])
and reconciliation's own browser launch ([orchestrator.py:1128]). Not wired
to `config.py` at all right now.

**Gap:** flipping between headed (current — lets you watch Chrome windows
live during a demo) and headless (needed for eventual unattended/server-side
deployment with no display, and generally lower RAM/CPU per Chrome instance)
currently means editing two separate literals in two different functions and
keeping them in sync by hand — easy to accidentally change one and forget
the other.

**Shape of the fix:** add one `HEADLESS: bool` setting to `config.py` (same
pattern as `MAX_CONCURRENT_REPLAYS`/`CDP_DEBUG_PORT` — env-var-overridable,
sensible default), and have both launch call sites read `config.HEADLESS`
instead of a hardcoded `False`. One flip in one place then changes it
everywhere. Purely mechanical — nothing about the caching/replay/CDP
mechanics themselves changes based on headed vs. headless (see the
headless discussion this came from: CDP, screenshots, and element location
all work identically either way).

**Priority reasoning:** small, low-risk, no behavior change today (default
stays `False`/headed) — worth doing whenever headless is actually needed
(server deployment, or wanting more concurrent windows than RAM allows
headed), not urgent before then.

---

## 10. Prompt/context caching for the constant part of live LLM calls

**Where:** `agent/runner.py`'s `_get_llm()` — currently `ChatGoogle(model=MODEL_NAME,
api_key=GEMINI_API_KEY)`, no cache configuration at all.

**Gap:** every live LLM call (cold run's per-step calls, live single-field
fallback, reconciliation) resends the full system prompt + task/record
description on every single call, even though that part is byte-for-byte
identical across every step of one record's run — only the DOM snapshot
genuinely changes step to step. Gemini already supports prompt/context
caching (confirmed: browser-use's own code already reads back
`response.usage_metadata.cached_content_token_count`), which would let the
server skip re-processing that constant block on every step instead of
paying full cost for it each time.

**Shape of the fix:** mark the constant prefix (system prompt + task/record
description — NOT the DOM snapshot, which is genuinely not constant across
steps) as the cacheable block when configuring `ChatGoogle`, per Gemini's
context-caching API.

**Priority reasoning:** a real but SMALL optimization — v2's caching layer
already eliminated the big cost (N records x 10-15 calls collapsed to ~1
cold run per batch), so this only shrinks the cost of the few live calls
that still happen (one cold run's own internal steps, occasional
fallback/reconciliation calls), not a second version of that same win. Worth
doing once cost/latency on those remaining live calls actually matters,
not urgent before then.

---

## 11. Deliberate, controlled thinking-effort tuning per LLM call

**Where:** `agent/runner.py`'s `_build_llm()` — currently `ChatGoogle(model=MODEL_NAME,
api_key=GEMINI_API_KEY)`, no `thinking_level`/`thinking_budget` set at all.
Separately, `config.py:35`'s `THINKING_LEVEL = os.getenv("THINKING_LEVEL", "low")`
exists but is never read by anything — dead config, by its own comment
("wired up later if needed").

**Gap, confirmed by reading browser-use's actual model-detection code**
(`venv/.../browser_use/llm/google/chat.py:291-338`): for this project's
configured model, `gemini-3.5-flash-lite`, none of that file's hardcoded
model-name checks (`is_gemini_3_pro`, `is_gemini_3_flash`, the `gemini-2.5`/
`gemini-flash` fallback) match the literal string `"gemini-3.5-flash-lite"` —
so `thinking_level`/`thinking_budget` never get set in the API request at
all. Checked directly against Google's own docs
(ai.google.dev/gemini-api/docs/thinking): `gemini-3.5-flash-lite`'s real
server-side default when unset is **"minimal"** — the cheapest tier that
model supports, not "high" as might be assumed. So right now this project
is accidentally running at the lowest-cost tier, by a version-mismatch
accident, not by deliberate choice. That's fine today, but fragile: a
future Gemini API default change, or switching to a different model whose
default tier is `medium`/`high` (several are, per that same docs table),
would silently change cost/latency with zero code change to notice it by.

**Shape of the fix, discussed and agreed on (2026-10-01):**
- Wire `THINKING_LEVEL` (or a proper per-model-aware setting) into
  `_build_llm()` for real, so the thinking tier is always a deliberate,
  known choice — never left to an accidental library/model-name mismatch.
- Give different call sites different STARTING levels based on how
  consequential a mistake there is, at zero extra cost: `_run_cold` (seeds
  the whole cache for every future record in the batch — mistakes compound)
  starts at a higher tier than `_run_live_fallback_step`/reconciliation
  (scoped to one field on one record — mistakes don't compound).
- Rejected approach: having the LLM itself classify "how hard is this
  task" via a separate upfront call before the real one. That pays for an
  extra API call on every single attempt (works against the whole reason
  caching exists) and isn't a well-proven signal (a model predicting its
  own future difficulty before attempting the task).
- Preferred approach instead: ESCALATE on proven failure, the exact same
  pattern already built for vision fallback (`orchestrator.py`'s three
  live-call sites) — try the starting tier, and only if that attempt
  doesn't self-report success, retry once at a higher tier. "It already
  failed" is a real, free difficulty signal discovered as a side effect of
  the attempt itself, not predicted in advance. Could combine naturally
  with the existing vision retry (escalate thinking AND vision together on
  the same retry) rather than needing a third separate mechanism.
- Worth remembering Google's own docs note: "Gemini models engage in
  dynamic thinking by default, automatically adjusting... based on the
  complexity of the request" — some of what per-call effort tuning is
  trying to achieve already happens automatically within whichever tier is
  selected; explicit tiers are about the outer bound, not replacing that.

**Priority reasoning:** not urgent (current accidental default is already
the cheap one) — but worth doing before switching models or relying on
Gemini's current default behavior staying put, since the current state is
correct by luck, not by design.

---

## The bigger picture (context, not a near-term task)

Longer-term intent (from conversation, not a spec): eventually this grows
into something bigger — pointed at a real company ERP, with real DB (or
equivalent) access for verification, and eventually packaged as something
sellable to other clients, not just an internal tool. That's explicitly
**out of scope for right now** — the current focus is purely making the
automation/caching engine itself general-purpose (items 1-5 above), not
solving verification-without-DB-access, multi-tenant deployment, or
client-facing UX. Revisit `05_PORTABILITY.md`'s "one real structural
assumption" section (direct-DB-read grading) when that phase actually
starts — don't design for it speculatively before then.
