"""Checks for the harder shapes the plan engine learned for the Returns Desk (testsites/returns-desk, `node server.js`, port 5181):
a list that has to be paged through (no search), links whose text is an id, a custom calendar widget, a very long policy page.

    python tests/test_plan_returns.py              # add --offline to skip part 2 (it resets the site's decisions)

Part 1 needs nothing but the recorded trace (tests/fixtures/returns_trace.json, rebuilt by make_returns_fixture.py).
Part 2 runs plans in a real headless browser against the site with a FAKE model that answers from the site's own data and a
scripted calendar standing in for the scoped agent step, so it costs no model calls. It is skipped when the site is not
running, and it resets the site's decisions."""

import asyncio
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sites import site_path  # noqa: E402

import recipe.plan_exec as px
from recipe.plan import compile_plan, replay_verdict, values_agree
from run_context import RunContext

HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures", "returns_trace.json")))
FIELDS, TRACE = FX["fields"], FX["trace"]
checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ part 1: compiling the recorded trace

plan, why = compile_plan(FIELDS, TRACE)
check("a plan is learned from the Returns Desk trace", plan is not None and why == "ok")
gather = plan["gather"]
check("the clicks that only move through the list are not part of the route", not any(c.get("name", "").startswith("Next") for o in gather for c in o.get("by", [])) and len(gather) == 7)
check("the return's own link is found by paging, once", [o.get("paged") for o in gather if o["op"] == "click" and any(c.get("name") == "{rma}" for c in o["by"])] == [True])
check("the ways back to the return (Back, then the list paged again) become steps that reopen the return's own address", [(o["op"], o.get("to")) for o in gather if o["op"] == "revisit"] == [("revisit", 1), ("revisit", 3)] and all(o["expect"] == "/returns/*" for o in gather if o["op"] == "revisit"))
check("a link whose text is an id becomes a value read off the page", set(plan["slots"]) == {"s1", "s2"} and plan["slots"]["s1"]["example"].startswith("ORD-") and plan["slots"]["s2"]["example"].startswith("SKU-"))
check("such a link has no locator that points at the first record's own order or item", not any("ORD-10128" in json.dumps(o.get("by", [])) or "SKU-8649" in json.dumps(o.get("by", [])) for o in gather))
check("every step knows the page it should land on, from the original route", all(o.get("expect") for o in gather) and [o["expect"] for o in gather][:3] == ["/returns", "/returns/*", "/orders/*"])
controls = plan["commit"]["controls"]
check("the calendar is a control the model decides a value for", [(c["key"], c["type"]) for c in controls] == [("disposition", ""), ("refund_amount", "number"), ("widget1", "widget")])
check("the buttons that save are only Save and Confirm, not the calendar's day buttons", [o["by"][0].get("value") for o in plan["commit"]["submit"]] == ["save", "confirm-yes"])
check("the old click replay is refused for this trace", replay_verdict(FIELDS, TRACE, 2)[0] is False)
check("the step found by the return's own value is marked as landing on the return's own page (for read-back), and only it",
      [(o["op"], o["by"]) for o in gather if o.get("own")] == [("click", [{"role": "link", "name": "{rma}"}])])
check("a route with no sign-in and no pop-ups has no login and no optional steps", "login" not in plan and not any(o.get("optional") for o in gather))
# The live agent sometimes clicks the table cell around a link (a <td> with no id, role or name), as it did on 9 Oct
from recipe.plan import element_locators
check("a plain cell the agent clicked is found again by its exact visible text, as the record's value", element_locators({"node": "td", "attrs": {}, "text": FX["rma"]}, FIELDS, {}) == [{"text": "{rma}"}])
check("a plain cell whose text names some other particular item gives no way to find it", element_locators({"node": "td", "attrs": {}, "text": "INV-2026-1188"}, FIELDS, {}) == [])
# What the live agent did on 9 Oct: typed a list page's address, guessed an address that does not exist ("Page not found"),
# typed the policy page's address, then typed the list's address and found the return again
S = {e["step"]: e for e in TRACE}
BASE = "http://localhost:5181"


def nav(at, to, text=None):
    e = {"step": 0, "kind": "navigate", "url": BASE + at, "el": None, "value": BASE + to, "goal": "go to " + to}
    return dict(e, page_text=text) if text else e


messy = [S[1], nav("/returns", "/returns?page=5", "Returns\n100 returns, 10 per page."), S[6], S[7],
         nav("/orders/ORD-10128", "/items/SKU-8649", "Order ORD-10128\nOrdered SKU\nSKU-8649"),
         nav("/items/SKU-8649", "/policy", "Returns Desk\n[12]<a />\n\tReturns\nPage not found"),
         nav("/policy", "/returns", "Return policy\nRestocking fees\nCategory Opened Damaged"),
         dict(S[16], url=BASE + "/returns"), *[S[k] for k in range(17, 24)]]
messy_plan, messy_why = compile_plan(FIELDS, messy)
route = [(o["op"], o.get("url") or (o.get("by") or [{}])[-1].get("name"), o.get("paged", False), o.get("to")) for o in messy_plan["gather"]] if messy_plan else None
check("a messy live route compiles to a clean one", messy_why == "ok")
check("  a typed list-page address is paging (the record is found by paging), not a fixed page to go to", route[1] == ("click", "{rma}", True, None))
check("  a guessed address that only reached 'Page not found' is left out", not any("/items/" in str(r[1]) for r in route))
check("  a typed address the agent read (the policy) is kept, with its own target", route[3] == ("goto", BASE + "/policy", False, None))
check("  typing the list's address and finding the return again becomes one step back to the return's own address", route[4] == ("revisit", None, False, 1) and len(route) == 5)
cell_trace = [dict(e, el={"node": "td", "attrs": {}, "text": FX["rma"]}) if e["step"] == 6 else e for e in TRACE]
cell_plan, cell_why = compile_plan(FIELDS, cell_trace)
check("a route through such a cell compiles, and the cell is found by paging", cell_plan is not None and cell_plan["gather"][1]["by"] == [{"text": "{rma}"}] and cell_plan["gather"][1].get("paged"))
check("a widget is left out of the comparison with the live agent", values_agree({"disposition": "Refund", "widget1": "2026-11-14"}, {"disposition": "Refund"}, {"disposition": "select", "widget1": "widget"})[0])
# the same list grown with a "Load more" button instead of pages: the button is paging too
grown_list = [S[1]] + [dict(S[2], url=BASE + "/returns", el={"node": "button", "attrs": {"type": "button"}, "text": "Load more", "xpath": "m"})] * 3 + [dict(S[6], url=BASE + "/returns")] + [S[k] for k in range(17, 24)]
more_plan, more_why = compile_plan(FIELDS, grown_list)
check("'Load more' clicks are left out of the route and the return's link after them is found by paging",
      more_why == "ok" and [(o["op"], o["by"][-1].get("name"), o.get("paged")) for o in more_plan["gather"]] == [("click", "Returns", None), ("click", "{rma}", True)])
check("  a div with role=button named 'Show more results' is paging too", compile_plan(FIELDS, [S[1], dict(grown_list[1], el={"node": "div", "attrs": {"role": "button"}, "text": "Show more results", "xpath": "m"}), *grown_list[4:]])[0]["gather"][1].get("paged") is True)
ins_plan = compile_plan(FIELDS, TRACE, "Refund 850 when the item is defective; write the date as 27 Nov.")[0]
check("values typed into the form are decided for every record even when the instructions happen to hold them (only route values become constants)",
      [c["value"] for c in ins_plan["commit"]["controls"]] == [{"computed": True}] * 3)

# ---- a branch the first record did not take
import orchestrator as orch
from data.models import Record

short_trace = [e for e in TRACE if e["step"] in (1, 2, 3, 4, 5, 6, 17, 18, 19, 20, 21, 22, 23)]
# the route of a return that never needed the order, the item or the policy page: list, return, form
short_plan, short_why = compile_plan(FIELDS, short_trace)
check("a route with no detours still compiles (a 'wrong item' return needs no policy page)", short_plan is not None and orch._plan_pages(short_plan) < orch._plan_pages(plan))

notes, saved = [], []
orch.recipe_store.save_trace = lambda *a, **k: saved.append("trace")
orch.recipe_store.save_recipe = lambda *a, **k: saved.append("recipe")
orch._recipes.clear()
orch._recipes[("wf", "default")] = short_plan
orch._extend_plan(short_plan, Record(record_id=FX["rma"], rma=FX["rma"]), TRACE, "wf", "default", notes.append)
extended = orch._recipes[("wf", "default")]
check("a record the short plan could not do, done live over a longer route, extends the plan", extended is not short_plan and orch._plan_pages(extended) > orch._plan_pages(short_plan) and notes and "extended" in notes[0]["message"])
check("the extended plan must be proven again", orch.needs_proof(extended) and extended["trust"]["ok"] == 0)
# 9 Oct, run #61: a proof record's dry run refused (the item page was not among the plan's pages), and the live agent did the
# record over a route that read the item page: the proof step extends the plan instead of counting a failure
import copy

import recipe.plan_exec as _px

orch._recipes.clear()
proving = copy.deepcopy(short_plan)
orch._recipes[("wf", "default")] = proving
refusal = _px.PlanOutcome(ok=False, error="the model's answers did not agree (the pages do not show what this rule needs: Reason is \"Defective\")")
said = []
orch._judge_plan(proving, refusal, TRACE, FX["rma"], "wf", "default", said.append, {"rma": FX["rma"]})
grown = orch._recipes[("wf", "default")]
check("a proof record whose dry run lacked a page extends the plan from the live route instead of counting a failure",
      grown is not proving and orch._plan_pages(grown) > orch._plan_pages(proving) and int(proving["trust"].get("bad", 0)) == 0 and "extended" in said[-1]["message"])
other = copy.deepcopy(short_plan)
orch._recipes[("wf", "default")] = other
orch._judge_plan(other, _px.PlanOutcome(ok=False, error="could not find the element for: Save"), TRACE, FX["rma"], "wf", "default", said.append, {"rma": FX["rma"]})
check("  any other failed dry run still only counts as inconclusive", orch._recipes[("wf", "default")] is other and other["trust"]["bad"] == 1)
wrong = copy.deepcopy(short_plan)
orch._recipes[("wf", "default")] = wrong
live_vals = orch.committed_values(TRACE, {"disposition", "refund_amount"})
guess = _px.PlanOutcome(ok=True, planned={"disposition": "Refund", "refund_amount": "15240"}, kinds={"disposition": "select", "refund_amount": "other"})
orch._judge_plan(wrong, guess, TRACE, FX["rma"], "wf", "default", said.append, {"rma": FX["rma"]})
check("a proof record where the plan answered differently, and the live route read pages the plan does not, extends the plan instead of throwing it away (run #62)",
      orch._recipes.get(("wf", "default")) is not wrong and orch._recipes.get(("wf", "default")) is not None and ("wf", "default") not in orch._recipe_given_up and "extended instead of thrown away" in said[-1]["message"])
# ---- trust per kind of decision (run #61: a plan proven on 3 "wrong item" returns guessed a change-of-mind refund's fee)
kinds_plan = copy.deepcopy(plan)
kinds_plan["trust"] = {"ok": 3, "bad": 0, "signatures": [], "branches": {"rule 1: disposition=Replace": 3}}
check("a trusted plan decides alone only the kinds of record that agreed with the live agent", orch._trusted_branches(kinds_plan) == {"rule 1: disposition=Replace"})
said = []
orch.recipe_store.save_recipe = lambda *a, **k: None
live_values = orch.committed_values(TRACE, {"disposition", "refund_amount", "widget1"})
seen = _px.PlanOutcome(ok=False, error="this kind of record (rule 4: disposition=Refund) has not been checked against the live agent yet", branch="rule 4: disposition=Refund",
                       planned={"disposition": "Refund", "refund_amount": live_values["refund_amount"]}, kinds={"disposition": "select", "refund_amount": "other", "widget1": "widget"})
orch._check_branch(kinds_plan, seen, TRACE, FX["rma"], "wf", "default", said.append)
check("a new kind whose stated answer matches what the live agent then entered becomes trusted", "rule 4: disposition=Refund" in orch._trusted_branches(kinds_plan) and "now decides this kind" in said[-1]["message"])
guessed = _px.PlanOutcome(ok=False, error=seen.error, branch="rule 5: disposition=Refund", planned={"disposition": "Refund", "refund_amount": "2108"}, kinds=seen.kinds)
orch._check_branch(kinds_plan, guessed, TRACE, FX["rma"], "wf", "default", said.append)
check("  one whose answer differs (a guessed fee) is never decided by the plan, even if it agrees later",
      kinds_plan["trust"]["branches"]["rule 5: disposition=Refund"] == -1 and "keeps doing this kind" in said[-1]["message"]
      and (orch._note_branch(kinds_plan, "rule 5: disposition=Refund", True) or "rule 5: disposition=Refund" not in orch._trusted_branches(kinds_plan)))
check("  and such refusals are not plan failures either", orch._unchecked_branch(seen.error) and not orch._unchecked_branch("could not find the element for: Save decision"))
check("a refusal for missing pages is not a plan failure (run #61 dropped a good plan after 3 such refusals in a row)",
      orch._lacks_pages("the model's answers did not agree (the pages do not hold what the rules need: the item page)") and orch._lacks_pages(refusal.error)
      and not orch._lacks_pages("could not find the element for: Save decision") and not orch._lacks_pages("the form did not move on from step 1"))
orch._recipes[("wf", "default")] = plan
before = orch._recipes[("wf", "default")]
orch._extend_plan(plan, Record(record_id=FX["rma"], rma=FX["rma"]), TRACE, "wf", "default", notes.append)
check("a route that visits nothing new changes nothing", orch._recipes[("wf", "default")] is before)
orch._recipes.clear()

# ---- the model must say when the pages do not hold what the rules need
ctl = [{"key": "disposition", "tag": "select", "type": "", "label": "Decision", "visible": True, "options": ["Refund", "Reject"]}]
answer = {"facts": "no fee table", "missing": "the policy page with the restocking fees", "checks": [{"rule": "r", "expr": "true", "values": [{"field": "disposition", "value": "Refund"}]}]}
try:
    px._values_from_answer(answer, ctl, {})
    refused = False
except px.PlanError as exc:
    refused = "do not hold what the rules need" in str(exc)
check("a record whose pages lack something the rules need is refused, not guessed", refused)
answer["missing"] = ""
num = [{"key": "refund_amount", "tag": "input", "type": "number", "label": "Refund", "visible": True, "options": []}]
def number_answer(v):
    return {"facts": "", "missing": "", "checks": [{"rule": "r", "expr": "true", "values": [{"field": "refund_amount", "value": v}]}]}
check("a number field gets digits only, whatever the model wrote", all(px._values_from_answer(number_answer(v), num, {})[0] == {"refund_amount": "6400"} for v in ("₹6,400", "6400.00", " 6400 ", "Rs 6,400.0")))
try:
    px._values_from_answer(number_answer("about 6400 rupees or so"), num, {})
    refused_number = False
except px.PlanError:
    refused_number = True
check("something that is not a number is refused for a number field", refused_number)
check("with nothing missing the same answer is used", px._values_from_answer(answer, ctl, {})[0] == {"disposition": "Refund"})
# 9 Oct: a defective return was decided without its item page (final sale? warranty?) and was right only by luck
unsure = {"facts": "", "missing": "", "checks": [
    {"rule": "final sale: Reject", "known": False, "expr": "false", "values": [{"field": "disposition", "value": "Reject"}]},
    {"rule": "otherwise: Refund", "known": True, "expr": "true", "values": [{"field": "disposition", "value": "Refund"}]}]}
try:
    px._values_from_answer(unsure, ctl, {})
    refused_unknown = False
except px.PlanError as exc:
    refused_unknown = "final sale" in str(exc)
check("a rule the pages cannot settle, ahead of the one that applies, sends the record to the live agent", refused_unknown)
late_unknown = {"facts": "", "missing": "", "checks": [
    {"rule": "wrong item: Replace", "known": True, "expr": "true", "values": [{"field": "disposition", "value": "Reject"}]},
    {"rule": "final sale: Reject", "known": False, "expr": "false", "values": [{"field": "disposition", "value": "Refund"}]}]}
check("a rule the pages cannot settle that comes after the one that applies does not matter", px._values_from_answer(late_unknown, ctl, {})[0] == {"disposition": "Reject"})

# ---- 15 Oct review: an answer about a page that was never read is refused, even when its rule has no numbers
claim_ctl = [{"key": "aria:Outcome", "tag": "select", "type": "", "label": "Outcome", "visible": True, "options": ["Approve", "Decline"]},
             {"key": "reason", "tag": "textarea", "type": "", "label": "Reason", "visible": True, "options": []},
             {"key": "payout", "tag": "input", "type": "number", "label": "Payout", "visible": True, "options": []}]
claim_pages = [("http://localhost:5183/claims/CL-1", "Claim CL-1\nPolicy: POL-77\nLoss date: 2026-03-02\nClaimed amount: 48000")]
claim_ins = ("Open the claim, then open its policy page. (1) If the policy status is not Active on the loss date, Decline with reason 'No cover on loss date'. "
             "(5) Otherwise Approve and set payout to the claimed amount minus the policy deductible.")
not_active = {"facts": "", "missing": "", "checks": [{"rule": "(1) policy not Active", "known": True, "expr": "true", "evidence": [],
                                                      "values": [{"field": "aria:Outcome", "value": "Decline"}, {"field": "reason", "value": "No cover on loss date"}]}]}
zero_deductible = {"facts": "", "missing": "", "checks": [
    {"rule": "(1) policy not Active", "known": True, "expr": "false", "evidence": [], "values": [{"field": "aria:Outcome", "value": "Decline"}]},
    {"rule": "(5) otherwise", "known": True, "expr": "true", "evidence": [], "values": [{"field": "aria:Outcome", "value": "Approve"}, {"field": "payout", "value": "{= 48000 - 0 }"}]}]}
refusals = []
for unread in (not_active, zero_deductible):
    try:
        px._values_from_answer(unread, claim_ctl, {}, ground=(claim_pages, {"claim": "CL-1"}, claim_ins))
        refusals.append("")
    except px.PlanError as exc:
        refusals.append(str(exc))
check("a status rule decided without a quote of the page it rests on (the policy page was never read) is refused, and so is a guessed deductible of 0",
      all("pages do not show what" in r for r in refusals) and "without a quote" in refusals[0])

# ---- a value read by code that is really the next label (the label's own value was empty) is never used
from recipe.facts import value_shape  # noqa: E402
vendor_code = {"label": "Vendor", "shape": value_shape("Acme Traders"), "agree": 3, "page_lines": ["Invoice", "Vendor", "Due date", "Amount"]}
vendor = {"seen_on": "/invoices/*", "code": vendor_code}
check("a slot read by code: the value next to its label is used",
      px._slot_by_code(vendor, [("http://x/invoices/7", "Invoice\nVendor\nAcme Traders\nDue date\n02 Nov 2026")]) == "Acme Traders")
check("  but when the label's value is empty, the next label ('Due date') is not taken for it (the model reads it instead)",
      px._slot_by_code(vendor, [("http://x/invoices/7", "Invoice\nVendor\n\nDue date\n02 Nov 2026")]) is None
      and px._slot_by_code({"seen_on": "/invoices/*", "code": dict(vendor_code, page_lines=None)}, [("http://x/invoices/7", "Vendor\nVendor:")]) is None)
check("  the page's fixed lines are kept when a code is proposed (the value itself left out)",
      px._page_lines("Invoice\nVendor\nAcme Traders\nDue date\t02 Nov 2026", "Acme Traders") == ["Invoice", "Vendor", "Due date", "02 Nov 2026"])

# ---- pruning: the page a value's number came from counts as used, not only the pages the evidence quotes
order_pages = [("http://x/returns/r1", "Return r1\nReason\nChanged mind\nQuantity\n5"), ("http://x/orders/ORD-1", "Order ORD-1\nUnit price\n₹2,880"),
               ("http://x/policy", "Policy\nFinal sale items cannot be returned")]
refund_ctl = [{"key": "disposition", "tag": "select", "type": "", "label": "Decision", "visible": True, "options": ["Refund", "Reject"]},
              {"key": "refund_amount", "tag": "input", "type": "number", "label": "Refund", "visible": True, "options": []}]
refund = {"facts": "", "missing": "", "checks": [{"rule": "otherwise refund", "known": True, "expr": "true", "evidence": ["Reason Changed mind"],
                                                  "values": [{"field": "disposition", "value": "Refund"}, {"field": "refund_amount", "value": "{= 5*2880 }"}]}]}
got = px._values_from_answer(refund, refund_ctl, {}, ground=(order_pages, {}, "Refund quantity x unit price."))
check("a decision's pages include the one its refund amount's unit price came from (the order page), so pruning never drops it",
      got[0]["refund_amount"] == "14400" and px._quote_page_types(got[4], order_pages, ["/returns/*", "/orders/*", "/policy"]) == {"/returns/*", "/orders/*"})

# ------------------------------------------------------------------ part 2: in a real browser (fake model)

SITE = "http://localhost:5181"
try:
    if "--offline" in sys.argv:
        raise RuntimeError("asked to stay offline")  # part 2 resets the site: never run it while a real run is using the site
    urllib.request.urlopen(SITE + "/admin/state", timeout=2)
except Exception:
    print(f"\nSKIPPED part 2 (--offline, or the Returns Desk site is not running on {SITE})")
    print(f"\n{checks} checks passed")
    sys.exit(0)

SEED = json.load(open(site_path("returns-desk", "data/seed.json")))
EXPECTED = json.load(open(site_path("returns-desk", "expected.json")))
RET = {r["rma"]: r for r in SEED["returns"]}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
calls = {"slots": 0, "form": 0}
ORDERS = {o["order_no"]: o for o in SEED["orders"]}
PRODUCTS = {p["sku"]: p for p in SEED["products"]}
FEES = {f["category"]: f for f in SEED["policy_fees"]}
guess = {"fee": None}  # a fee the fake model makes up instead of reading the policy page


# A refund as a careful model writes it: a formula of the numbers printed on the order and the policy pages (grounding refuses
# a number the model worked out itself). Takes the return's number; returns the formula.
def refund_formula(rma):
    r = RET[rma]
    o, cat = ORDERS[r["order_no"]], PRODUCTS[r["sku"]]["category"]
    fee = 0 if r["reason"] == "Defective" or r["condition"] == "Unopened" else FEES[cat]["opened" if r["condition"] == "Opened" else "damaged"]
    if guess["fee"] is not None:
        fee = guess["fee"]
    return f"{{= {o['qty']} * {o['unit_price']} * (100 - {fee}) / 100 }}" if fee else f"{{= {o['qty']} * {o['unit_price']} }}"


async def fake_ask_json(ctx, prompt, schema=None, temperature=0.0):
    blocks = re.split(r'^### record "', prompt, flags=re.M)[1:]
    if schema is px._SlotAnswers:
        calls["slots"] += 1
        records = []
        for block in blocks:
            key = block.split('"', 1)[0]
            r = RET[re.search(r"rma = (RMA-\d+)", block).group(1)]
            values = []
            for n in re.findall(r"^  - (s\d+):", block, flags=re.M):
                ex = re.search(rf'^  - {n}: for a different record this was "([^"]+)"', block, flags=re.M).group(1)
                values.append({"name": n, "value": r["order_no"] if ex.startswith("ORD-") else r["sku"]})
            records.append({"record": key, "values": values})
        return {"records": records}
    if schema is px._FormDecisions:
        calls["form"] += 1
        decisions = []
        for block in blocks:
            key = block.split('"', 1)[0]
            r_no = re.search(r"rma = (RMA-\d+)", block).group(1)
            e = EXPECTED[r_no]
            values = [{"field": "disposition", "value": e["disposition"]}]
            if e["disposition"] == "Refund":
                values += [{"field": "refund_amount", "value": refund_formula(r_no)}, {"field": "widget1", "value": e["pickup_date"]}]
            if e["disposition"] == "Replace":
                values += [{"field": "replacement_sku", "value": e["replacement_sku"]}, {"field": "widget1", "value": e["pickup_date"]}]
            if e["disposition"] == "Reject":
                values.append({"field": "reason", "value": e["reason"]})
            decisions.append({"record": key, "facts": "from the pages", "missing": "", "checks": [{"rule": "otherwise: the one that applies", "expr": "true", "values": values}]})
        return {"decisions": decisions}
    raise AssertionError("unexpected question")


px.ask_json = fake_ask_json
ctx = RunContext(target_url=SITE, workflow_name="test", match_key_field="rma", model_name="m", gemini_api_key="x", task_instructions="the task")


def state():
    return json.load(urllib.request.urlopen(SITE + "/admin/state"))


def reset():
    urllib.request.urlopen(urllib.request.Request(SITE + "/admin/reset", method="POST"))


# A scripted stand-in for the scoped agent step that sets the calendar: opens it, goes to the month, clicks the day.
def calendar_filler(page, log):
    async def fill(label, value, hint):
        log.append((label, value))
        y, m, d = map(int, value.split("-"))
        await page.click("#pickup-open")
        for _ in range(24):
            if await page.inner_text("#cal-title") == f"{MONTHS[m - 1]} {y}":
                break
            await page.click("#cal-next")
        await page.click(f"#cal-grid button:text-is('{d}')")
        return True

    return fill


import copy

from playwright.async_api import async_playwright

from recipe.facts import value_shape

pick = {}
for rma, e in EXPECTED.items():
    pick.setdefault((e["disposition"], e.get("reason")), rma)
# a return that is far down the list, to show paging
ordered = sorted(EXPECTED)
last_page = ordered[-1]


async def run(rma, context, *, dry=False, filler=True, break_opener=False, trusted=None, plan=None, **kw):
    page = await context.new_page()
    log = []
    the_plan = plan if plan is not None else compile_plan(FIELDS, TRACE)[0]
    if break_opener:  # a picker the plan cannot open by itself
        for c in the_plan["commit"]["controls"]:
            if c["type"] == "widget":
                c["opener"] = [{"role": "button", "name": "No such button"}]
    out = await px.run_plan(the_plan, {"rma": rma}, ctx=ctx, page=page, emit=lambda e: None, dry=dry, key=rma, widget_fill=calendar_filler(page, log) if filler else None, trusted_branches=trusted, **kw)
    await page.close()
    return out, log


async def part2():
    reset()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        # the long policy page is cut down to what matters, and its table rows carry their headings
        page = await context.new_page()
        await page.goto(SITE + "/policy")
        text = await px._quick_read(page)
        await page.close()
        check("a long page is read together with its table rows labelled by their headings", "Category: Footwear - Running | Opened: " in text and len(text) > 15000)
        r = RET[pick[("Refund", None)]]
        product = next(p for p in SEED["products"] if p["sku"] == r["sku"])
        pages = [(SITE + "/returns/x", f"Return\nRMA\t{r['rma']}\nItem\t{r['sku']}"), (SITE + "/products/y", f"{product['name']}\nCategory\t{product['category']}\nFinal sale\tNo"), (SITE + "/policy", text)]
        block = px._pages_block(pages, {"rma": r["rma"]})
        fee = next(f for f in SEED["policy_fees"] if f["category"] == product["category"])
        check("the fee row of this record's category survives the cut", f"Category: {product['category']} | Opened: {fee['opened']}% | Damaged: {fee['damaged']}%" in block)
        check("the cut page is much shorter than the page", len(block) < 12000 < len(text))

        # a dry run decides everything without saving, including the calendar's value
        no = pick[("Refund", None)]
        out, log = await run(no, context, dry=True)
        check("a dry run finds the return by paging and reads its order and item through the page text", out.ok and not out.acted, ) if out.ok else check(f"dry run failed: {out.error}", False)
        check("a dry run decides the calendar's value too, and does not touch the calendar", out.planned.get("widget1") == EXPECTED[no]["pickup_date"] and log == [])
        check("a dry run saves nothing", state()[no] is None)

        # real runs of each kind of outcome, including a return on the last page of the list
        for name, rma in {"refund": no, "replace": pick[("Replace", None)], "final sale": pick[("Reject", "Final sale")], "out of warranty": pick[("Reject", "Out of warranty")], "the last page": last_page}.items():
            out, log = await run(rma, context, filler=False)  # the plan drives the calendar itself: no agent
            e, got = EXPECTED[rma], state()[rma]
            good = out.ok and out.acted and got is not None and got["disposition"] == e["disposition"] and got.get("reason") == e.get("reason") and got.get("refund_amount") == e.get("refund_amount") and got.get("replacement_sku") == e.get("replacement_sku") and got.get("pickup_date") == e.get("pickup_date")
            check(f"{name}: the plan pages to the return, decides, sets the calendar itself and saves ({rma})" if good else f"{name} FAILED: {out.error} {got}", good)

        # a trusted plan meeting a kind of record it was never checked on states its answer and stops before typing anything
        reset()
        rep = pick[("Replace", None)]
        out, log = await run(rep, context, filler=False, trusted={"rule 1: disposition=Refund"})
        check("an unchecked kind of record: the plan states what it would enter, and stops before typing or saving anything",
              not out.ok and not out.acted and out.branch == "rule 1: disposition=Replace" and out.planned.get("replacement_sku") == EXPECTED[rep]["replacement_sku"] and state()[rep] is None)
        out, log = await run(rep, context, filler=False, trusted={"rule 1: disposition=Replace"})
        check("  once that kind is trusted, the plan does it", out.ok and state()[rep] is not None)

        # a picker the plan cannot drive by itself goes to the agent step; with no agent step the plan stops BEFORE saving
        reset()
        rep = pick[("Replace", None)]
        out, log = await run(rep, context, filler=True, break_opener=True)
        got = state()[rep]
        check("a picker the plan cannot open is set by the agent step instead", out.ok and len(log) == 1 and got is not None and got["pickup_date"] == EXPECTED[rep]["pickup_date"])
        reset()
        out, log = await run(rep, context, filler=False, break_opener=True)
        check("a picker the plan cannot open, with no agent step, stops the plan before anything is saved", (not out.ok) and (not out.acted) and all(v is None for v in state().values()))

        # ---- grounding: a fee the model did not read is refused (9 Oct: a guessed 15% fee on a plan that never read the policy)
        opened = next(n for n in ordered if RET[n]["reason"] == "Changed mind" and RET[n]["condition"] == "Opened" and EXPECTED[n]["disposition"] == "Refund"
                      and FEES[PRODUCTS[RET[n]["sku"]]["category"]]["opened"] != 15)
        policy_step = next(i for i, o in enumerate(compile_plan(FIELDS, TRACE)[0]["gather"]) if o.get("expect") == "/policy")
        out, _log = await run(opened, context, dry=True, skip={policy_step})
        check("a dry run that left out the policy page still reaches the decision (the step back from the item page is still made)",
              out.error is not None and "pages do not show" in out.error)
        guess["fee"] = 15
        out, _log = await run(opened, context, dry=True, skip={policy_step})
        check("a refund worked out with a fee the pages do not show (15%, the policy page was not read) is refused by grounding",
              not out.ok and "pages do not show what this answer uses" in (out.error or "") and "15" in out.error)
        guess["fee"] = None
        out, _log = await run(opened, context, dry=True)
        check("  the honest answer (the category's fee, read on the policy page) is used", out.ok and out.planned.get("refund_amount") == str(EXPECTED[opened]["refund_amount"]))
        check("  and the dry run reports the return's own page address (for reading the saved values back)", out.record_url and "/returns/" in out.record_url)

        # ---- slots read by code: the order and the item numbers are printed next to "Order" and "Item" on the return's page
        for k in calls:
            calls[k] = 0
        out, _log = await run(no, context, dry=True)
        check("a slot the model copied gets a code proposed: the label it is printed next to and its shape",
              out.slot_labels == {"s1": ("Order", value_shape(RET[no]["order_no"])), "s2": ("Item", value_shape(RET[no]["sku"]))} and calls["slots"] >= 1)
        coded = compile_plan(FIELDS, TRACE)[0]
        for name, label in (("s1", "Order"), ("s2", "Item")):
            coded["slots"][name]["code"] = {"label": label, "shape": value_shape(coded["slots"][name]["example"]), "agree": 2}
        for k in calls:
            calls[k] = 0
        out, _log = await run(no, context, dry=True, plan=coded)
        check("  a code that has agreed fewer than 3 times is checked against the model's answer (and agrees)", out.ok and calls["slots"] >= 1 and out.slot_checks == {"s1": True, "s2": True})
        for name in ("s1", "s2"):
            coded["slots"][name]["code"]["agree"] = 3
        for k in calls:
            calls[k] = 0
        out, _log = await run(no, context, dry=True, plan=coded)
        check("  after 3 agreements the code reads the values: no slot question to the model", out.ok and calls["slots"] == 0 and out.planned.get("refund_amount") == str(EXPECTED[no]["refund_amount"]))

        # ---- a decision program decides every kind of return with no model call at all
        reset()
        programmed = copy.deepcopy(coded)
        programmed["program"] = returns_program()
        kinds = {}
        for n in ordered:
            r, e = RET[n], EXPECTED[n]
            kinds.setdefault((e["disposition"], e.get("reason"), r["reason"] if e["disposition"] == "Refund" else "", r["condition"] if r["reason"] == "Changed mind" and e["disposition"] == "Refund" else ""), n)
        for k in calls:
            calls[k] = 0
        decided = []
        for kind, n in sorted(kinds.items()):
            out, _log = await run(n, context, filler=False, plan=programmed, program_mode="use")
            decided.append((kind, n, out.ok, out.decided_by, out.error))
        got = state()
        right = [n for _k, n, ok, by, _e in decided if ok and by == "program" and got[n] and all(got[n].get(f) == EXPECTED[n].get(f) for f in ("disposition", "reason", "refund_amount", "replacement_sku", "pickup_date"))]
        check(f"a decision program decides all {len(kinds)} kinds of return (replace, three rejections, refunds with and without a fee) and the plan saves them"
              if len(right) == len(kinds) else f"program FAILED: {[d for d in decided if d[1] not in right]}", len(right) == len(kinds) == 8)
        check("  with zero model calls (the slots are read by code too)", calls == {"slots": 0, "form": 0})
        check("  each record counted as one trusted use of the program", programmed["program"]["trust"]["since_spot"] == len(kinds))
        await browser.close()
    reset()


# A hand-written decision program for the Returns Desk's rules: facts read by label from the return, order and item pages and
# from the policy's fee table, and the four rules in order, trusted for every kind of decision (as if each had agreed twice).
def returns_program():
    label = lambda name, page, text, kind="text": {"name": name, "type": kind, "source": {"kind": "label", "page": page, "label": text}}
    fee = lambda name, column: {"name": name, "type": "number", "source": {"kind": "table", "page": "/policy", "where": [{"column": "Category", "op": "eq", "value": "{category}"}], "column": column, "aggregate": "first"}}
    program = {"version": 1, "facts": [
        label("reason", "/returns/*", "Reason"), label("condition", "/returns/*", "Condition"), label("days", "/returns/*", "Days since delivery", "number"),
        label("slot", "/returns/*", "Next pick-up slot", "date"), label("ordered_sku", "/orders/*", "Ordered SKU"), label("unit_price", "/orders/*", "Unit price", "number"),
        label("qty", "/orders/*", "Quantity ordered", "number"), label("final_sale", "/products/*", "Final sale", "bool"),
        label("warranty", "/products/*", "Warranty period (days)", "number"), label("category", "/products/*", "Category"),
        fee("fee_opened", "Opened"), fee("fee_damaged", "Damaged")],
        "rules": [
            {"name": "wrong item: replace", "when": "reason == 'Wrong item'", "set": {"disposition": "Replace", "replacement_sku": "{ordered_sku}", "widget1": "{slot}"}},
            {"name": "final sale: reject", "when": "final_sale", "set": {"disposition": "Reject", "reason": "Final sale"}},
            {"name": "defective after the warranty: reject", "when": "reason == 'Defective' and days > warranty", "set": {"disposition": "Reject", "reason": "Out of warranty"}},
            {"name": "defective: refund in full", "when": "reason == 'Defective'", "set": {"disposition": "Refund", "refund_amount": "{= qty * unit_price }", "widget1": "{slot}"}},
            {"name": "changed mind after 30 days: reject", "when": "days > 30", "set": {"disposition": "Reject", "reason": "Outside window"}},
            {"name": "changed mind, unopened: refund in full", "when": "condition == 'Unopened'", "set": {"disposition": "Refund", "refund_amount": "{= qty * unit_price }", "widget1": "{slot}"}},
            {"name": "changed mind, opened: refund less the opened fee", "when": "condition == 'Opened'", "set": {"disposition": "Refund", "refund_amount": "{= qty * unit_price * (100 - fee_opened) / 100 }", "widget1": "{slot}"}},
            {"name": "changed mind, damaged: refund less the damaged fee", "when": "true", "set": {"disposition": "Refund", "refund_amount": "{= qty * unit_price * (100 - fee_damaged) / 100 }", "widget1": "{slot}"}},
        ],
        "trust": {"branches": {}, "since_spot": 0}}
    for i, rule in enumerate(program["rules"]):
        program["trust"]["branches"]["p%d: " % (i + 1) + " | ".join(f"{k}={v}" for k, v in sorted(rule["set"].items()) if k in ("disposition", "reason"))] = 2
    return program


asyncio.run(part2())
print(f"\n{checks} checks passed")
