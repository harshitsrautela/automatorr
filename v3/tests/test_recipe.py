"""Offline checks for recipe learning (no browser, no model, no database):

    python tests/test_recipe.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

import recipe.execute as ex
from recipe.formula import evaluate
from recipe.network import MutationTracker
from keypool import KeyPool, _seconds_to_quota_reset, models_for
from run_context import RunContext
from recipe.compile import compile_pair, compile_recipe, compile_single, family_pattern, fill, templatize


def btn(label, row_id, url="http://x/"):
    return {"kind": "click", "goal": "g", "url": url, "el": {"node": "button", "attrs": {"aria-label": f"{label} request {row_id}", "title": label}, "text": f"{label} request {row_id}", "xpath": "/b"}}


def link(href, text, url):
    return {"kind": "click", "goal": "g", "url": url, "el": {"node": "a", "attrs": {"href": href}, "text": text, "xpath": "/a"}}


checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


check("values become blanks only as whole tokens", templatize("Approve request 12", {"id": 1}) == "Approve request 12" and templatize("Approve request 1", {"id": 1}) == "Approve request {id}")
check("blanks are filled back", fill("Reject request {id}", {"id": 7}) == "Reject request 7")
check("family pattern leaves the differing word open", family_pattern("Approve request {id}", "Reject request {id}") == r"^\S+\s+request\s+{id}$")

# Two records where the AI took different routes (one searched, one did not) still give one recipe
a = [link("/ledger", "Ledger", "http://x/"), link("/", "Requests", "http://x/ledger"), btn("Approve", 1)]
b = [link("/ledger", "Ledger", "http://x/"), link("/", "Requests", "http://x/ledger"), link("/ledger", "Ledger", "http://x/"), link("/", "Requests", "http://x/ledger"), btn("Hold", 2)]
recipe, why = compile_pair({"id": 1}, a, {"id": 2}, b)
check("different routes still compile", recipe is not None and recipe["read"] == ["http://x/", "http://x/ledger"])
check("the final button becomes a choice among its siblings", recipe["act"]["choose"]["attr"] == "aria-label" and recipe["act"]["page"] == "http://x/")

recipe, why = compile_pair({"id": 1}, [link("/ledger", "Ledger", "http://x/")], {"id": 2}, [link("/ledger", "Ledger", "http://x/")])
check("no record-specific click means no recipe", recipe is None)

recipe, why = compile_single({"id": 5}, [link("/ledger", "Ledger", "http://x/"), btn("Reject", 5)])
check("one record is enough to learn a recipe", recipe is not None and recipe["act"]["siblings"] == "auto" and recipe["act"]["locator"]["value"] == "Reject request {id}")
check("a lone trace goes through compile_recipe too", compile_recipe([("5", {"id": 5}, [btn("Reject", 5)])])[0] is not None)
check("a trace with no button of the record's own gives no recipe", compile_single({"id": 5}, [link("/ledger", "Ledger", "http://x/")])[0] is None)

pool = KeyPool()
keys = ["k1", "k2"]
first, second = pool.pick(keys, "m"), pool.pick(keys, "m")
check("keys are handed out in turn", {first, second} == {"k1", "k2"})
pool.cool("k1", "m", 60)
check("a rate-limited key is skipped", all(pool.pick(keys, "m") == "k2" for _ in range(4)))
pool.exhaust_today("k2", "m")
check("nothing is handed out when every key is set aside", pool.pick(keys, "m") is None)
check("a rate-limited key can come back, an exhausted one cannot", pool.wait_seconds(keys, "m") is not None and not pool.all_exhausted(keys, "m"))
pool.exhaust_today("k1", "m")
check("all keys out for the day is recognised", pool.all_exhausted(keys, "m") and pool.wait_seconds(keys, "m") is None)
check("the fallback model is only second", models_for("gemini-3.5-flash-lite") == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
p2 = KeyPool()
p2.exhaust_today("k1", "big")
check("the first model stays in use while one key has quota", p2.first_open_model(["k1", "k2"], ["big", "small"]) == "big")
p2.exhaust_today("k2", "big")
check("the fallback is used once both keys are out for the first model", p2.first_open_model(["k1", "k2"], ["big", "small"]) == "small")
p2.exhaust_today("k1", "small"); p2.exhaust_today("k2", "small")
check("no model is left when everything is used up", p2.first_open_model(["k1", "k2"], ["big", "small"]) is None)
ctx = RunContext(target_url="x", workflow_name="w", match_key_field="id", model_name="m", gemini_api_key="k", task_instructions="rules")
items = [("1", {"id": 1}, ["Approve request 1", "Reject request 1"]), ("2", {"id": 2}, ["Approve request 2", "Reject request 2"])]


async def fake_answer(_ctx, _prompt, schema=None, temperature=0.0):
    return {"decisions": [{"record": "1", "numbers": "n1", "checks": [{"rule": "a", "expr": "1 > 2", "button": "Approve request 1"}, {"rule": "b", "expr": "true", "button": "Reject request 1"}], "button": "Approve request 1", "rule": "r1"}, {"record": "2", "numbers": "n2", "checks": [{"rule": "a", "expr": "3 <= 2", "button": "Reject request 2"}, {"rule": "b", "expr": "3 > 2", "button": "Approve request 2"}], "button": "Approve request 2", "rule": "r2"}, {"record": "9", "button": "x"}]}


ex.ask_json = fake_answer
got = asyncio.run(ex._decide(ctx, items, [("http://x/", "page text")]))
check("the formulas decide, even when the model's own button disagrees", got["1"][0] == "Reject request 1" and got["2"][0] == "Approve request 2")
check("an answer for an unknown record is ignored", set(got) == {"1", "2"})
prompt = ex._decision_prompt(ctx, items, [("http://x/", "page text")])
check("every record and its buttons are in the question", 'record "1"' in prompt and "Approve request 2 | Reject request 2" in prompt and "rules" in prompt)
book = ex.DecisionBook({"1": {}, "2": {}}, ["1", "2"])
book.decisions["1"] = ("a", "b")
book.finish("1")
async def no_reasoning(_ctx, _prompt, schema=None, temperature=0.0):
    return {"decisions": [{"record": "1", "button": "Reject request 1"}, {"record": "2", "button": "Approve request 2"}]}


ex.ask_json = no_reasoning
check("a button with no written reasoning is not accepted", asyncio.run(ex._decide(ctx, items, [("http://x/", "page text")])) == {})
ex.ask_json = fake_answer
class _Ctx:
    def on(self, *_a):
        pass


tracker = MutationTracker(_Ctx())
check("no change request means nothing failed", tracker.last_failure() is None)
tracker.results.append(("PATCH", "http://x/api?id=1", "ok"))
check("a saved change is not a failure", tracker.last_failure() is None)
tracker.results.append(("PATCH", "http://x/api?id=2", "status 500"))
check("a refused save is reported with its address", tracker.last_failure() == "PATCH http://x/api status 500")
check("a record with nothing in flight settles at once", asyncio.run(tracker.settle(1.0)))
check("a boundary comparison is true when both sides are equal", evaluate("935000 - 850000 <= 850000 * 0.10") is True and evaluate("650001 - 650000 <= 65000") is True)
check("rule order and plain arithmetic are done in code", evaluate("520000 + 195000 <= 650000") is False and evaluate("53 > 30") is True)
check("anything that is not a plain formula is refused", evaluate("__import__('os').system('x')") is None and evaluate("open('x')") is None and evaluate("sum([1]) > 0") is None)
check("abs, min and max are the only calls, and commas inside them separate arguments", evaluate("abs(3) > 1") is True and evaluate("max(1,2) == 2") is True and evaluate("min(10,200) == 10") is True and evaluate("650,000 >= 600,000") is True)
opts = ["Approve request 83", "Reject request 83", "Hold request 83"]
check("a rule that names its button decides which one", ex._option_named_in(opts, "Retailer is not in the ledger: Hold") == 2 and ex._option_named_in(opts, "Otherwise: Reject") == 1)
instr = "Open the Retailer Ledger page and find the retailer with exactly the same name; if it is not in the ledger, the retailer is not found, so note that.\nDecide:\n- Retailer is not in the ledger: Hold\n- Retailer is Blocked: Reject\n- Outstanding + amount requested is at or under the credit limit: Approve"
line = ex._instruction_line(instr, "Retailer is not in the ledger")
check("the user's own line is found even when the model shortens the rule", line.endswith("Hold") and ex._option_named_in(opts, line) == 2)
check("a rule that names no button, or two, decides nothing", ex._option_named_in(opts, "Days since last payment is greater than the credit term") is None and ex._option_named_in(opts, "Approve or Reject") is None)


async def chunk_flow():
    book = ex.DecisionBook({"1": {}, "2": {}}, ["1", "2"])
    loop = asyncio.get_running_loop()
    book.inflight["1"], book.inflight["2"] = loop.create_future(), loop.create_future()

    async def fake_decide(_ctx, items, _pages, _retrying=False):
        return {k: (f"Approve request {k}", "why") for k, _f, _o in items}

    original = ex._decide
    ex._decide = fake_decide
    try:
        await book.decide_chunk(ctx, [("1", {}, ["a"]), ("2", {}, ["a"])], [], True)
    finally:
        ex._decide = original
    return book


booked = asyncio.run(chunk_flow())
check("a decided chunk releases every record in it", booked.decisions["1"][0] == "Approve request 1" and booked.inflight["2"].done())
votes = {"n": 0}


async def disagreeing(_ctx, prompt, schema=None, temperature=0.0):
    # the first pass (temperature 0) says Reject, the second (0.4) says Approve, the tie-break (0 again, one record) says Approve
    votes["n"] += 1
    if schema is ex._Decisions:
        button = "Reject request 1" if (temperature == 0.0 and votes["n"] <= 2) else "Approve request 1"
        return {"decisions": [{"record": "1", "numbers": "n", "checks": [{"rule": "x", "expr": "true", "button": button}], "button": button, "rule": "x"}]}
    return {"records": []}


ex.ask_json = disagreeing
one = [("1", {"id": 1}, ["Approve request 1", "Reject request 1"])]
voted = asyncio.run(ex._decide(ctx, one, [("http://x/", "a")]))
check("two passes that disagree are settled by a third", voted["1"][0] == "Approve request 1" and votes["n"] == 3)
ex.ask_json = fake_answer
check("a finished record leaves the book", book.pending == ["2"] and "1" not in book.decisions)
from datetime import datetime, timezone

# Google resets the daily quota at midnight Pacific time: 07:00 UTC in summer, 08:00 UTC in winter
check("the quota reset is midnight Pacific (07:00 UTC in summer), not midnight UTC", round(_seconds_to_quota_reset(datetime(2026, 10, 8, 6, 15, tzinfo=timezone.utc))) == 45 * 60 + 60)
check("a key marked just after the reset is held for nearly a day", round(_seconds_to_quota_reset(datetime(2026, 10, 8, 7, 1, tzinfo=timezone.utc))) == 24 * 3600 - 60 + 60)
check("in winter the reset is 08:00 UTC", round(_seconds_to_quota_reset(datetime(2026, 12, 1, 7, 30, tzinfo=timezone.utc))) == 30 * 60 + 60)
check("the day the clocks go back is 25 hours long", round(_seconds_to_quota_reset(datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc))) == int(24.5 * 3600) + 60)
print(f"\n{checks} checks passed")
