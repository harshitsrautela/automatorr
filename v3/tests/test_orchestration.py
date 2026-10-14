"""Checks for what the orchestrator learns as records finish (no browser, no model, no database):

    python tests/test_orchestration.py

Every outside piece is a fake: the plan runner, the live agent, the coverage question, the program learner, the read-back and
the database. It checks that the task's instructions reach the plan compiler, the coverage question on a new plan, when a
decision program is learned (and that only one record asks at a time), how a program earns trust per kind of record and is
dropped, slots read by code, a failed read-back, pruning with a retry of the whole route, proactive extension of a plan and
the note at the end of a batch."""

import asyncio
import copy
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import orchestrator as orch
orch.FEATURES.update({name: True for name in orch.FEATURE_NAMES})  # these checks are about the optional parts: switch them all on
import recipe.plan_exec as px
from data.models import Record, RunResult
from recipe.plan import committed_values, compile_plan

HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures", "returns_trace.json")))
FIELDS, TRACE = FX["fields"], FX["trace"]
KEY = ("wf", "default")
KINDS = {"disposition": "select", "refund_amount": "other"}
LIVE = committed_values(TRACE, set(KINDS))

checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ fakes

REAL_DRY_RUN = orch._dry_run_plan

saved: list = []
orch.recipe_store.save_recipe = lambda *a, **k: saved.append(("recipe", a))
orch.recipe_store.save_trace = lambda *a, **k: saved.append(("trace", a))
STORED_TRACES: list = []
orch.recipe_store.get_traces = lambda *a, **k: list(STORED_TRACES)


class FakeTab:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.tabs = []

    async def new_page(self):
        tab = FakeTab()
        self.tabs.append(tab)
        return tab


class FakePage:
    def __init__(self):
        self.context = FakeContext()
        self.visits = []
        self.url = "http://127.0.0.1:5181/"

    async def goto(self, url, **_k):
        self.visits.append(url)


def make_ctx(instructions="Refund a defective item in full; read the policy page for the restocking fee."):
    return SimpleNamespace(workflow_name="wf", task_instructions=instructions, target_url="http://127.0.0.1:5181/", max_concurrent_replays=2)


def reset():
    for d in (orch._recipes, orch._recipe_failures, orch._plan_traces, orch._examples, orch._example_kinds, orch._program_locks,
              orch._program_tried, orch._program_asks, orch._plan_batchers):
        d.clear()
    orch._recipe_given_up.clear()
    orch._program_gaps.clear()
    orch._batch_counts.update({"program": 0, "model_calls": 0})
    saved.clear()
    orch._plan_batchers[KEY] = SimpleNamespace()  # never used: the plan runner is a fake


def fresh_plan():
    plan, why = compile_plan(FIELDS, TRACE)
    assert plan is not None, why
    return plan


def outcome(**kw):
    base = dict(ok=True, planned=dict(LIVE), kinds=dict(KINDS))
    base.update(kw)
    return px.PlanOutcome(**base)


def example(n):
    return {"fields": {"rma": f"RMA-{n}"}, "pages": [("http://x/returns/RMA-%d" % n, f"Return RMA-{n}")], "values": {"disposition": "Refund", "refund_amount": str(100 + n)}}


def run(coro):
    return asyncio.run(coro)


def notes_of(events):
    return [e.get("message", "") for e in events if e.get("type") == "note"]


# ------------------------------------------------------------------ 1. instructions reach the plan compiler

reset()
seen_instructions = []
real_compile = orch.compile_plan


def spy_compile(fields, trace, instructions=""):
    seen_instructions.append(instructions)
    return real_compile(fields, trace, instructions)


orch.compile_plan = spy_compile
orch._no_replay.add(KEY)
STORED_TRACES[:] = [(FX["rma"], FIELDS, TRACE)]
events = []
orch._try_compile_recipe("wf", "default", events.append, "Sign in as desk.auditor")
check("a plan is compiled with the task's instructions", seen_instructions == ["Sign in as desk.auditor"])
learned = orch._recipes.get(KEY)
check("  a newly learned plan is marked for the coverage question", learned is not None and learned.get("coverage_pending") is True)
check("  and the trace it was learned from is kept for that question", orch._plan_traces.get(KEY) is TRACE)

short_trace = [e for e in TRACE if e["step"] in (1, 2, 3, 4, 5, 6, 17, 18, 19, 20, 21, 22, 23)]
short_plan, _w = compile_plan(FIELDS, short_trace)
short_plan["program"] = {"version": 1, "facts": [], "rules": [], "trust": {"branches": {"p1: disposition=Refund": 1}, "since_spot": 0}}
short_plan["readback"] = {"shows": True, "checked": 2, "results": [True, True]}
orch._recipes[KEY] = short_plan
seen_instructions.clear()
grew = orch._extend_plan(short_plan, Record(record_id=FX["rma"], **FIELDS), TRACE, "wf", "default", events.append, instructions="Refund defective items")
grown = orch._recipes[KEY]
check("_extend_plan compiles the live route with the instructions", grew and seen_instructions == ["Refund defective items"])
check("  the extended plan is marked for coverage and keeps the program and the read-back verdict",
      grown.get("coverage_pending") is True and grown.get("program") is short_plan["program"] and grown.get("readback", {}).get("shows") is True)
orch.compile_plan = real_compile
orch._no_replay.discard(KEY)

# ------------------------------------------------------------------ 2. coverage on the next proof record

reset()
plan = fresh_plan()
plan["coverage_pending"] = True
orch._recipes[KEY] = plan
orch._plan_traces[KEY] = TRACE
asked = []


async def fake_cover(ctx, the_plan, route_pages, controls=None):
    asked.append(route_pages)
    the_plan["gather"].append({"op": "click", "by": [{"role": "link", "name": "Policy"}], "added": True, "bound": [], "uses": []})
    return ["after the route reaches /returns/*: open \"Policy\", read it and come back"]


orch.coverage.cover_route = fake_cover
dry_calls = []


async def fake_dry(the_plan, record, ctx, page, on_event):
    dry_calls.append(the_plan.get("coverage_pending"))
    return outcome(llm_calls=2, record_url="http://127.0.0.1:5181/returns/" + FX["rma"], decided_by="model",
                   example={"fields": FIELDS, "pages": [("http://x/returns/1", "text")], "values": dict(LIVE)}, used_pages={"/returns/*"})


async def fake_cold(record, **kw):
    fake_cold.calls += 1
    return RunResult(record_id=record.record_id, success=True, timestamp=datetime.now(timezone.utc)), {"mode": "cold", "cache_hits": 0, "cache_misses": 0, "llm_steps": 5, "miss_reasons": [], "trace": TRACE}


fake_cold.calls = 0
orch._dry_run_plan = fake_dry
orch._run_cold = fake_cold
readbacks = []


async def fake_read_back(page, url, values, kinds, reader, **kw):
    readbacks.append((url, dict(values)))
    return {"seen": list(values), "missing": [], "unchecked": [], "text": ""}


orch.readback.read_back = fake_read_back
events = []
page = FakePage()
rec = Record(record_id=FX["rma"], **FIELDS)
result, metrics = run(orch._run_live_and_prove(rec, plan, ctx=make_ctx(), page=page, cdp_url="", store=None, path_variant="default", persist=False, on_event=events.append))
check("the coverage question is asked once, before the dry run, with the pages the plan's route reads",
      len(asked) == 1 and asked[0] and dry_calls == [None])
check("  its flag is cleared, the plan is saved and a note says what was added",
      "coverage_pending" not in plan and any(m.startswith("Added from the instructions:") for m in notes_of(events)) and saved)
check("  its call (and the dry run's) count in the record's metrics", metrics["llm_steps"] == 5 + 1 + 2)
asked.clear()
events.clear()
run(orch._run_live_and_prove(rec, orch._recipes[KEY], ctx=make_ctx(), page=page, cdp_url="", store=None, path_variant="default", persist=False, on_event=events.append))
check("  a plan already covered is not asked again", asked == [])

# ------------------------------------------------------------------ 3. read-back learning in proof records

check("a proof record reads the record's page back in a new tab with the live agent's values",
      readbacks and readbacks[0][0].endswith(FX["rma"]) and readbacks[0][1] == LIVE and all(t.closed for t in page.context.tabs))
check("  two agreeing checks decide that the site shows what was saved",
      {k: v for k, v in orch._recipes[KEY]["readback"].items() if k != "confirmed"} == {"shows": True, "checked": 2, "results": [True, True], "hidden": []})
check("  and the values they saw are noted as confirmed (a choice per option)",
      set(orch._recipes[KEY]["readback"]["confirmed"]) == {orch._readback_mark(k, v, KINDS.get(k)) for k, v in LIVE.items()})
n_before = len(readbacks)
run(orch._run_live_and_prove(rec, orch._recipes[KEY], ctx=make_ctx(), page=page, cdp_url="", store=None, path_variant="default", persist=False, on_event=events.append))
check("  once decided, no more read-back checks are made", len(readbacks) == n_before)
mixed = fresh_plan()
results_seq = [{"seen": ["disposition"], "missing": [], "unchecked": []}, {"seen": [], "missing": ["disposition", "refund_amount"], "unchecked": []}]


async def alternating(page, url, values, kinds, reader, **kw):
    return results_seq.pop(0)


orch.readback.read_back = alternating
d = outcome(record_url="http://x/returns/1")
run(orch._learn_readback(mixed, d, TRACE, FakePage()))
run(orch._learn_readback(mixed, d, TRACE, FakePage()))
check("  two checks that disagree leave it undecided (the plan does not read back)", mixed["readback"]["shows"] is None and mixed["readback"]["checked"] == 2)


async def errored(page, url, values, kinds, reader, **kw):
    return {"seen": [], "missing": [], "unchecked": list(values), "error": "landed on /login"}


orch.readback.read_back = errored
blank = fresh_plan()
check("  a read-back that could not be done teaches nothing", run(orch._learn_readback(blank, d, TRACE, FakePage())) is False and "readback" not in blank)


async def hides_amount(page, url, values, kinds, reader, **kw):
    return {"seen": ["disposition"], "missing": ["refund_amount"], "unchecked": []}


orch.readback.read_back = hides_amount
partial = fresh_plan()
run(orch._learn_readback(partial, d, TRACE, FakePage()))
run(orch._learn_readback(partial, d, TRACE, FakePage()))
check("  a value the site never shows, even for the live agent's own save, is kept as hidden",
      partial["readback"]["shows"] is True and partial["readback"]["hidden"] == ["refund_amount"])
orch.readback.read_back = fake_read_back

# ------------------------------------------------------------------ 4. learning a decision program: the trigger and the lock

reset()
plan = fresh_plan()
orch._recipes[KEY] = plan
learn_calls = []


async def fake_learn(ctx, controls, examples, kinds, page_types=None):
    learn_calls.append((len(examples), sorted(kinds), [c["key"] for c in controls], list(page_types or [])))
    await asyncio.sleep(0.05)
    return {"version": 1, "facts": [{"name": "a"}], "rules": [{"name": "r1"}, {"name": "r2"}], "trust": {"branches": {}, "since_spot": 0}}, "ok", 1


orch.programs.learn_program = fake_learn
for n in range(3):
    orch._add_example(KEY, f"R{n}", example(n), KINDS)
events = []
check("no program is asked for with fewer than 4 verified examples", run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append)) == 0 and not learn_calls)
orch._add_example(KEY, "R2", example(2), KINDS)
check("  an example of the same record replaces the older one", len(orch._examples[KEY]) == 3)
orch._add_example(KEY, "R3", example(3), KINDS)


async def two_at_once():
    return await asyncio.gather(orch._maybe_learn_program(plan, make_ctx(), "default", events.append),
                                orch._maybe_learn_program(plan, make_ctx(), "default", events.append))


got = run(two_at_once())
check("with 4 examples a program is learned once, even when two records finish together (the lock)", len(learn_calls) == 1 and sorted(got) == [0, 1])
check("  it is asked with the examples, the fields' kinds, the plan's controls and its page types",
      learn_calls[0][0] == 4 and learn_calls[0][1] == ["disposition", "refund_amount"] and "disposition" in learn_calls[0][2] and "/returns/*" in learn_calls[0][3])
check("  it is kept on the plan and a note tells about it", isinstance(plan.get("program"), dict)
      and any("Learned a decision program: 1 facts, 2 rules" in m and "agreed with the model 2 times" in m for m in notes_of(events)))
orch._add_example(KEY, "R4", example(4), KINDS)
orch._add_example(KEY, "R5", example(5), KINDS)
check("  a plan that has a program (covering every kind seen) does not ask again", run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append)) == 0 and len(learn_calls) == 1)
orch._book_program(plan, outcome(decided_by="model", program_check=None), KEY, "R6", events.append)
check("  the model deciding a record the program could not marks a gap", KEY in orch._program_gaps)
check("  a gap with more examples asks again", run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append)) == 1 and len(learn_calls) == 2
      and KEY not in orch._program_gaps and any("Learned a new decision program" in m for m in notes_of(events)))

# ------------------------------------------------------------------ 5. trust per kind of record, spot checks and dropping

reset()
plan = fresh_plan()
plan["program"] = {"version": 1, "facts": [], "rules": [{"name": "r"}], "trust": {"branches": {}, "since_spot": 0}}
orch._recipes[KEY] = plan
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
calls_seen = []
queue = []


async def fake_run_plan(the_plan, fields, **kw):
    calls_seen.append(kw)
    return queue.pop(0)


orch.run_plan = fake_run_plan
fake_cold.calls = 0
BR = "p1: disposition=Refund"
events = []
for i in range(2):
    queue.append(outcome(decided_by="model", branch="rule 1: disposition=Refund", program_check=(BR, True), example=example(i)))
    res, met = run(orch._run_plan_with_fallback(Record(record_id=f"T{i}", rma=f"T{i}"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("trusted plan records run the plan with program_mode='use'", all(k.get("program_mode") == "use" for k in calls_seen))
check("  two agreements make the program trusted for that kind, with a note", orch.programs.program_trusted_for(plan["program"], BR)
      and sum("now decides this kind alone" in m for m in notes_of(events)) == 1)
check("  model-decided plan records that saved become verified examples", [e["record_id"] for e in orch._examples[KEY]] == ["T0", "T1"])
queue.append(outcome(decided_by="program", branch=BR, used_pages={"/policy"}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="T2", rma="T2"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a record the program decided counts for the batch-end note and is not an example", res.success and orch._batch_counts["program"] == 1 and len(orch._examples[KEY]) == 2)
check("  decided records count which page types supplied facts (pruning)", plan["page_use"]["decisions"] == 3 and plan["page_use"]["used"].get("/policy") == 1)
queue.append(outcome(decided_by="model", branch="rule 1: disposition=Refund", program_check=(BR, True), program_spot=True, example=example(3)))
run(orch._run_plan_with_fallback(Record(record_id="T3", rma="T3"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a spot check that agreed restarts the count to the next spot check", plan["program"]["trust"]["since_spot"] == 0 and plan["program"]["trust"]["branches"][BR] == 3)
BR2 = "p2: disposition=Reject"
queue.append(outcome(decided_by="model", branch="rule 2: disposition=Reject", program_check=(BR2, False), example=example(4)))
run(orch._run_plan_with_fallback(Record(record_id="T4", rma="T4"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  one disagreement marks that kind as never trusted, and the program stays", plan["program"]["trust"]["branches"][BR2] == -1 and "program" in plan
      and any("disagreed with the model on T4" in m for m in notes_of(events)))
queue.append(outcome(decided_by="model", branch="rule 1: disposition=Refund", program_check=(BR, False), example=example(5)))
run(orch._run_plan_with_fallback(Record(record_id="T5", rma="T5"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a second disagreement drops the program, with a note", "program" not in plan and any("so it is dropped" in m for m in notes_of(events)))
check("  and the live agent was never needed for these records", fake_cold.calls == 0)
learn_calls.clear()
check("  a dropped program is not relearned from the same examples", run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append)) == 0 and not learn_calls)
for i in range(6, 8):
    orch._add_example(KEY, f"T{i}", example(i), KINDS)
check("  but is relearned once there are more", run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append)) == 1 and isinstance(plan.get("program"), dict))

# ------------------------------------------------------------------ 6. dry runs run the program in shadow

reset()
seen_kw = []


async def spy_run_plan(the_plan, fields, **kw):
    seen_kw.append(kw)
    return outcome()


orch.run_plan = spy_run_plan
got = run(REAL_DRY_RUN(fresh_plan(), Record(record_id="D1", rma="D1"), make_ctx(), FakePage(), None))
check("a proof record's dry run runs the plan dry, with its program in shadow (it never decides there)",
      got is not None and seen_kw and seen_kw[0].get("dry") is True and seen_kw[0].get("program_mode") == "shadow")

# 13 Oct: the record runner hands run_plan the browser's change-request counter, so a wizard's Next only counts as acting when a save went out
reset()
orch._recipes[KEY] = fresh_plan()
seen_kw.clear()
orch.run_plan = spy_run_plan
sent = {"n": 7}
run(orch._run_plan_with_fallback(Record(record_id="M1", rma="M1"), orch._recipes[KEY], ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default",
                                 on_event=lambda e: None, mutations=lambda: sent["n"]))
check("the change-request counter reaches the plan runner", seen_kw and callable(seen_kw[0].get("mutations")) and seen_kw[0]["mutations"]() == 7)

# ------------------------------------------------------------------ 7. slots read by code

reset()
plan = fresh_plan()
orch._recipes[KEY] = plan
events = []
orch.run_plan = fake_run_plan
queue.append(outcome(decided_by="model", slot_labels={"s1": ("Order", r"^ORD-\d{5}$"), "nope": ("X", "y")}))
run(orch._run_plan_with_fallback(Record(record_id="S0", rma="S0"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("a slot's proposed label becomes its code, starting at 0 agreements (an unknown slot is ignored)",
      plan["slots"]["s1"].get("code") == {"label": "Order", "shape": r"^ORD-\d{5}$", "agree": 0} and "nope" not in plan["slots"])
for i in range(3):
    queue.append(outcome(decided_by="model", slot_checks={"s1": True}, slot_labels={"s1": ("Other", "z")}))
    run(orch._run_plan_with_fallback(Record(record_id=f"S{i + 1}", rma="S"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  each agreement with the model counts; an existing code is not replaced by a new proposal",
      plan["slots"]["s1"]["code"]["agree"] == 3 and plan["slots"]["s1"]["code"]["label"] == "Order")
check("  reaching the agreements px needs gives a note that code now reads it", sum("is now read by code" in m for m in notes_of(events)) == 1 and px._code_trusted(plan["slots"]["s1"]))
queue.append(outcome(decided_by="model", slot_checks={"s1": False}))
run(orch._run_plan_with_fallback(Record(record_id="S9", rma="S9"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a disagreement deletes the code", "code" not in plan["slots"]["s1"])

# ------------------------------------------------------------------ 8. a read-back that does not show the saved values

reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
# 15 Oct review: the AP Desk shows the option "Place on hold" as "On hold"; proofs that only saw Approve and Reject must not make
# a correct Hold save fail. A value read-back never saw confirmed is unchecked, not missing
plan["readback"] = {"shows": True, "checked": 2, "results": [True, True], "hidden": [], "confirmed": ["status=approve for payment", "status=reject"]}
orch._recipes[KEY] = plan
fake_cold.calls = 0
events = []
queue.append(outcome(acted=True, decided_by="model", unverified=True, planned={"status": "Place on hold", "reason": "Short receipt"}, kinds={"status": "select", "reason": "select"},
                     readback={"seen": ["reason"], "missing": ["status"], "unchecked": []}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="V0", rma="V0"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("a read-back that misses a choice's option never seen confirmed on this site does not fail the record (nor count toward dropping the plan)",
      res.success and orch._recipe_failures.get(KEY, 0) == 0 and "reason=short receipt" in plan["readback"]["confirmed"])
plan["readback"]["confirmed"] = sorted({orch._readback_mark(k, v, KINDS.get(k)) for k, v in LIVE.items()} | {"refund_amount", "disposition=refund"})
queue.append(outcome(acted=True, decided_by="model", unverified=True, planned={"disposition": "Refund", "refund_amount": "2480"},
                     readback={"seen": ["disposition"], "missing": ["refund_amount"], "unchecked": []}, example=example(1)))
res, met = run(orch._run_plan_with_fallback(Record(record_id="V1", rma="V1"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("a plan record whose read-back misses a value is a failed record, with the value named",
      not res.success and "The plan saved, but the record's page does not show refund_amount='2480' (read-back)" == res.error_message)
check("  it is not retried by the live agent, and is not kept as an example", fake_cold.calls == 0 and not orch._examples.get(KEY))
queue.append(outcome(acted=True, decided_by="model", unverified=True, readback={"seen": [], "missing": [], "unchecked": ["disposition"], "error": "landed on /login"}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="V2", rma="V2"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a read-back that could not be done fails the record too, saying why", not res.success and "could not be read back (landed on /login)" in res.error_message and fake_cold.calls == 0)
queue.append(outcome(acted=True, decided_by="model", unverified=True, readback={"seen": [], "missing": ["disposition"], "unchecked": []}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="V3", rma="V3"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  saves that never show up drop the plan after 3 failed records (the rest use the live agent)",
      not res.success and KEY not in orch._recipes and KEY in orch._recipe_given_up and any("failed 3 records in a row" in m for m in notes_of(events)))

reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["readback"] = {"shows": True, "checked": 2, "results": [True, True], "hidden": ["refund_amount"],
                    "confirmed": sorted({orch._readback_mark(k, v, KINDS.get(k)) for k, v in LIVE.items()} | {"refund_amount"})}
orch._recipes[KEY] = plan
events = []
for i in range(4):
    queue.append(outcome(acted=True, decided_by="model", unverified=True, planned={"disposition": "Refund", "refund_amount": "2480"},
                         readback={"seen": ["disposition"], "missing": ["refund_amount"], "unchecked": []}, example=example(i)))
    res, met = run(orch._run_plan_with_fallback(Record(record_id=f"H{i}", rma=f"H{i}"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
    assert res.success, res.error_message
check("  a value the site never shows (hidden) does not fail a plan record; other missing values still do",
      orch._recipes.get(KEY) is plan and orch._recipe_failures.get(KEY, 0) == 0 and len(orch._examples[KEY]) == 4)
queue.append(outcome(acted=True, decided_by="model", unverified=True, readback={"seen": ["refund_amount"], "missing": ["disposition", "refund_amount"], "unchecked": []}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="H9", rma="H9"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("    (a missing value that is not hidden is named; the hidden one is not)",
      not res.success and "does not show disposition=" in res.error_message and "refund_amount" not in res.error_message)

# ------------------------------------------------------------------ 9. pruning, and the whole route once more when a skipped page was needed

reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["page_use"] = {"decisions": 20, "used": {"/returns/*": 20}}
orch._recipes[KEY] = plan
want_skip = orch.coverage.skippable(plan)
check("a plan whose side trips never supplied a fact in 12+ decisions has steps to skip", bool(want_skip))
calls_seen.clear()
fake_cold.calls = 0
events = []
queue.append(outcome(ok=False, error="the pages do not show what this rule needs: the restocking fee", llm_calls=1, steps=4))
queue.append(outcome(decided_by="model", llm_calls=1, steps=9, used_pages={"/policy"}))
res, met = run(orch._run_plan_with_fallback(Record(record_id="P1", rma="P1"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  trusted plan records run with those steps skipped", calls_seen[0].get("skip") == want_skip)
check("  a record refused for missing pages resets pruning and runs the whole route once more, before any live fallback",
      len(calls_seen) == 2 and calls_seen[1].get("skip") is None and res.success and fake_cold.calls == 0)
check("  the counts start again (with this record's decision) and both runs' calls are counted",
      plan["page_use"] == {"decisions": 1, "used": {"/policy": 1}} and met["llm_steps"] == 2 and met["cache_hits"] == 13
      and any("stopped reading" in m for m in notes_of(events)))
calls_seen.clear()
queue.append(outcome(ok=False, error="the pages do not show what this rule needs: x"))
fake_cold.calls = 0
res, met = run(orch._run_plan_with_fallback(Record(record_id="P2", rma="P2"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  without skips, such a refusal goes straight to the live agent", len(calls_seen) == 1 and calls_seen[0].get("skip") is None and fake_cold.calls == 1)

# ------------------------------------------------------------------ 10. proactive extension

reset()
short_plan, _w = compile_plan(FIELDS, short_trace)
orch._recipes[KEY] = short_plan
events = []
agreeing = outcome(planned=dict(LIVE))
said = orch._judge_plan(short_plan, agreeing, TRACE, FX["rma"], "wf", "default", events.append, FIELDS, instructions="")
grown = orch._recipes[KEY]
check("a proof record that agreed, whose live route read page types the plan lacks, extends the plan",
      said is True and grown is not short_plan and orch._plan_pages(grown) > orch._plan_pages(short_plan) and grown.get("coverage_pending") is True)
check("  the note says it agreed and must be proven again (not that it is trusted)",
      "the same values" in notes_of(events)[-1] and "extended and must be proven again" in notes_of(events)[-1])
full = fresh_plan()
orch._recipes[KEY] = full
events = []
check("  a plan that already reads those pages is not replaced", orch._judge_plan(full, agreeing, TRACE, FX["rma"], "wf", "default", events.append, FIELDS) and orch._recipes[KEY] is full)

reset()
short_plan, _w = compile_plan(FIELDS, short_trace)
short_plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
orch._recipes[KEY] = short_plan
events = []
fake_cold.calls = 0
queue.append(outcome(ok=False, error="this kind of record (rule 1: disposition=Refund) has not been checked against the live agent yet",
                     branch="rule 1: disposition=Refund", decided_by="model", example={"fields": FIELDS, "pages": [("http://x/returns/1", "t")], "values": dict(LIVE)}))
res, met = run(orch._run_plan_with_fallback(Record(record_id=FX["rma"], **FIELDS), short_plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("after a new kind of record agreed with the live agent, its route still extends the plan",
      res.success and fake_cold.calls == 1 and orch._recipes[KEY] is not short_plan and short_plan["trust"]["branches"]["rule 1: disposition=Refund"] == 1)
check("  and the agreeing record is a verified example", [e["record_id"] for e in orch._examples.get(KEY, [])] == [FX["rma"]])

# ------------------------------------------------------------------ 10b. a program's agreement counts only when the live agent confirms it

def new_program():
    return {"version": 1, "facts": [], "rules": [{"name": "r"}], "trust": {"branches": {}, "since_spot": 0}}


WRONG = {"disposition": "Reject", "refund_amount": "0"}  # the model's (and the program's) answer; the live agent enters LIVE
UNCHECKED = "this kind of record (rule 2: disposition=Reject) has not been checked against the live agent yet"
PBR = "p: disposition=Reject"
reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["program"] = new_program()
orch._recipes[KEY] = plan
orch.run_plan = fake_run_plan
fake_cold.calls = 0
events = []
queue.append(outcome(ok=False, error=UNCHECKED, branch="rule 2: disposition=Reject", decided_by="model", planned=dict(WRONG), program_check=(PBR, True)))
res, met = run(orch._run_plan_with_fallback(Record(record_id="U0", **FIELDS), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("a new kind of record where the program agreed with a model the live agent contradicts counts as a disagreement",
      res.success and fake_cold.calls == 1 and plan["program"]["trust"]["branches"].get(PBR) == -1
      and not orch.programs.program_trusted_for(plan["program"], PBR) and not any("now decides this kind alone" in m for m in notes_of(events)))
reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["program"] = new_program()
orch._recipes[KEY] = plan
GBR = "p: disposition=Refund"
queue.append(outcome(ok=False, error=UNCHECKED.replace("rule 2: disposition=Reject", "rule 1: disposition=Refund"), branch="rule 1: disposition=Refund",
                     decided_by="model", planned=dict(LIVE), program_check=(GBR, True)))
run(orch._run_plan_with_fallback(Record(record_id="U1", **FIELDS), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  one the live agent confirms counts as an agreement", plan["program"]["trust"]["branches"].get(GBR) == 1)
reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["program"] = new_program()
orch._recipes[KEY] = plan
fake_cold.calls = 0
queue.append(outcome(ok=False, acted=True, error="the save did not confirm", decided_by="model", planned=dict(WRONG), program_check=(PBR, True)))
run(orch._run_plan_with_fallback(Record(record_id="U2", **FIELDS), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=events.append))
check("  a record whose save could not be confirmed counts nothing toward the program", plan["program"]["trust"]["branches"] == {} and fake_cold.calls == 0)

# proof records: only a dry run that agreed with the live agent counts the program's agreement
reset()
shadow = fresh_plan()
shadow["program"] = new_program()
orch._recipes[KEY] = shadow
P1 = "p1: disposition=Reject"


async def failed_dry(the_plan, record, ctx, page, on_event):
    return outcome(ok=False, error="the form did not move on from step 1", decided_by="model", planned={}, program_check=(P1, True))


orch._dry_run_plan = failed_dry
for i in range(2):
    run(orch._run_live_and_prove(Record(record_id=f"D{i}", **FIELDS), shadow, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", persist=False, on_event=None))
check("a dry run that did not run through counts nothing toward the program", shadow["program"]["trust"]["branches"] == {})


async def wrong_dry(the_plan, record, ctx, page, on_event):
    return outcome(ok=True, decided_by="model", planned=dict(WRONG), program_check=(P1, True))


reset()
shadow = fresh_plan()
shadow["program"] = new_program()
shadow["trust"] = {"ok": 0, "bad": 0, "signatures": []}
orch._recipes[KEY] = shadow
orch._dry_run_plan = wrong_dry
program_obj = shadow["program"]
run(orch._run_live_and_prove(Record(record_id="D5", **FIELDS), shadow, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", persist=False, on_event=None))
check("  a dry run whose answer the live agent contradicted throws the plan (and its program) away, crediting nothing",
      KEY not in orch._recipes and program_obj["trust"]["branches"] == {})
check("  when such a plan is kept (extended), the program that agreed with the wrong answer counts a disagreement",
      orch._proof_program_check(outcome(ok=True, program_check=(P1, True)), False) == (P1, False)
      and orch._proof_program_check(outcome(ok=True, program_check=(P1, True)), True) == (P1, True)
      and orch._proof_program_check(outcome(ok=True, program_check=(P1, False)), True) == (P1, False)
      and orch._proof_program_check(outcome(ok=False, program_check=(P1, True)), False) is None
      and orch._proof_program_check(outcome(ok=False, program_check=(P1, False)), False) == (P1, False))
orch._dry_run_plan = fake_dry

# a check made by a program that was replaced while the record ran is not counted toward the new one
reset()
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["program"] = new_program()
orch._recipes[KEY] = plan


async def swap_then_answer(the_plan, fields, **kw):
    the_plan["program"] = {**new_program(), "id": "relearned"}  # another record relearned the program meanwhile
    return outcome(decided_by="model", program_check=(BR, True), example=example(1))


orch.run_plan = swap_then_answer
run(orch._run_plan_with_fallback(Record(record_id="R1", rma="R1"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=None))
check("a program check is only counted toward the program that made it", plan["program"]["trust"]["branches"] == {})
orch.run_plan = fake_run_plan
reset()
plan = fresh_plan()
for i in range(4):
    orch._add_example(KEY, f"L{i}", example(i), KINDS)
learned = []


async def learn_relearned(ctx, controls, examples, kinds, page_types=None):
    return new_program(), "ok", 1


orch.programs.learn_program = learn_relearned
plan["program"] = {**new_program(), "id": "old"}
orch._program_gaps.add(KEY)
run(orch._maybe_learn_program(plan, make_ctx(), "default", None))
check("  a relearned program gets a new id", plan["program"].get("id") not in (None, "old"))

# a quota stop while learning a program stops the batch
reset()
plan = fresh_plan()
for i in range(4):
    orch._add_example(KEY, f"Q{i}", example(i), KINDS)


async def out_of_quota(ctx, controls, examples, kinds, page_types=None):
    raise orch.LlmHalt("quota", quota=True)


orch.programs.learn_program = out_of_quota
orch._quota_exhausted_event.clear()
events = []
calls = run(orch._maybe_learn_program(plan, make_ctx(), "default", events.append, "Q9"))
check("a quota stop while learning a program sets the batch's quota flag (and counts no call)",
      orch._quota_exhausted() and calls == 0 and any(e.get("type") == "error" for e in events))
orch._quota_exhausted_event.clear()
orch._halt_reason["text"] = None
orch.programs.learn_program = fake_learn

# ------------------------------------------------------------------ 11. the note at the end of a batch

reset()
orch._plan_batchers.clear()
events = []


async def fake_record(record, **kw):
    orch._batch_counts["model_calls"] += 3
    if record.record_id != "B1":
        orch._batch_counts["program"] += 1
    return RunResult(record_id=record.record_id, success=True, timestamp=datetime.now(timezone.utc)), {}


real_record = orch._run_record_cached_async
orch._run_record_cached_async = fake_record
recs = [Record(record_id=f"B{i}") for i in range(1, 4)]
run(orch._run_many_concurrent_async(recs, ctx=make_ctx(), store=None, index_by_id={r.record_id: i for i, r in enumerate(recs)}, total=3, on_event=events.append))
orch._run_record_cached_async = real_record
check("the batch ends with how many records the program decided alone and the model calls made in total",
      any("decided 2 record(s) with no model call" in m and "9 model call(s) in total" in m for m in notes_of(events)))

# ------------------------------------------------------------------ 12. the 15 Oct review

# ---- a spot check claimed by a record that never booked it is handed back; one that was booked is not
reset()
orch.run_plan = fake_run_plan
plan = fresh_plan()
plan["trust"] = {"ok": 3, "bad": 0, "signatures": ["a", "b"]}
plan["program"] = {"version": 1, "id": "P1", "facts": [], "rules": [{"name": "r"}], "trust": {"branches": {BR: 3}, "since_spot": 0, "spot_claimed": True}}
orch._recipes[KEY] = plan
fake_cold.calls = 0
queue.append(outcome(ok=False, acted=True, error="could not find the button: save", decided_by="model", program_spot=True, program_check=(BR, True)))
res, met = run(orch._run_plan_with_fallback(Record(record_id="SP1", rma="SP1"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=[].append))
check("a spot check its record claimed but never booked (the record failed) is handed back: the next trusted record is the spot check",
      not res.success and orch.programs.spot_check_due(plan["program"]) and "spot_claimed" not in plan["program"]["trust"])
plan["program"]["trust"].update(since_spot=0, spot_claimed=True)
queue.append(outcome(decided_by="model", program_spot=True, program_check=(BR, True), example=example(1)))
run(orch._run_plan_with_fallback(Record(record_id="SP2", rma="SP2"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=[].append))
check("  one that was booked is not handed back", not orch.programs.spot_check_due(plan["program"]) and "spot_claimed" not in plan["program"]["trust"]
      and plan["program"]["trust"]["branches"][BR] == 4)
queue.append(outcome(decided_by="program", branch=BR, llm_calls=3))
run(orch._run_plan_with_fallback(Record(record_id="SP3", rma="SP3"), plan, ctx=make_ctx(), page=FakePage(), cdp_url="", store=None, path_variant="default", on_event=[].append))
check("a record the program decided but whose widget took model calls is not counted as decided with no model call", orch._batch_counts["program"] == 0)

# ---- side trips added by coverage count as pages the plan reads, so a live route through the same pages adds nothing new
reset()
from recipe.coverage import add_reads, parse_coverage, route_pages_from_trace  # noqa: E402
short_trace = [e for e in TRACE if e["step"] in (1, 2, 3, 4, 5, 6, 17, 18, 19, 20, 21, 22, 23)]
covered, _why = compile_plan(FIELDS, short_trace)
reads = parse_coverage({"reads": [
    {"from_page": "/returns/*", "link_text": "ORD-10128", "per_record": True, "label": "Order", "why": "unit price"},
    {"from_page": "/returns/*", "link_text": "SKU-8649", "per_record": True, "label": "Item", "why": "category"},
    {"from_page": "/returns", "link_text": "Return policy", "per_record": False, "label": "", "why": "fees"}]}, route_pages_from_trace(covered, TRACE))
added = add_reads(covered, reads)
covered["trust"] = {"ok": 1, "bad": 0, "signatures": ["x"]}
before_run = sorted(orch._plan_pages(covered))
covered["added_types"] = ["/orders/*", "/products/*", "/policy"]  # where run_plan noted the added side trips led
orch._recipes[KEY] = covered
replaced = orch._extend_plan(covered, Record(record_id="R2", **FIELDS), TRACE, "wf", "default", None, save_trace=False, instructions="")
check(f"pages reached by side trips added from the instructions count as read by the plan ({before_run} before they ran), so a live route through them does not replace it",
      len(added) == 3 and not replaced and orch._recipes[KEY] is covered and covered["trust"]["ok"] == 1 and {"/orders/*", "/policy"} <= orch._plan_pages(covered))

# ---- a program learned again keeps the trust of the rules it did not change
reset()
plan = fresh_plan()


def same_program():
    return {"version": 1, "facts": [{"name": "reason", "type": "text", "source": {"kind": "label", "page": "/returns/*", "label": "Reason"}}],
            "rules": [{"name": "wrong item", "when": "reason == 'Wrong item'", "set": {"disposition": "Replace"}},
                      {"name": "else", "when": "true", "set": {"disposition": "Refund"}}],
            "trust": {"branches": {}, "since_spot": 0}}


async def relearn_same(ctx, controls, examples, kinds, page_types=None):
    return same_program(), "ok", 1


orch.programs.learn_program = relearn_same
plan["program"] = same_program()
plan["program"]["trust"]["branches"] = {"p1: disposition=Replace": 7, "p2: disposition=Refund": 9}
orch._recipes[KEY] = plan
for n in range(6):
    orch._add_example(KEY, f"G{n}", example(n), KINDS)
orch._program_tried[KEY] = 4
orch._book_program(plan, outcome(decided_by="model", program_check=None), KEY, "G9", [].append)
calls = run(orch._maybe_learn_program(plan, make_ctx(), "default", [].append, "G9"))
check("a gap makes the program be learned again, and the new program keeps the trust its unchanged rules had earned",
      calls == 1 and plan["program"]["trust"]["branches"] == {"p1: disposition=Replace": 7, "p2: disposition=Refund": 9}
      and orch.programs.program_trusted_for(plan["program"], "p2: disposition=Refund"))
orch.programs.learn_program = fake_learn

print(f"\n{checks} checks passed")
