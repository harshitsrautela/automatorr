"""Checks for the plan engine (the general recipe):

    python tests/test_plan.py              # add --offline to skip part 2 (it resets the site's decisions)

Part 1 needs nothing (no browser, no model, no database): compiling a plan from a real trace, telling when the old click
replay is unsafe, and comparing a plan with the live agent.
Part 2 runs the plan in a real headless browser against the AP Desk test site (testsites/ap-desk, `node server.js`)
with a fake model that answers from the site's own data, so it checks the navigation, slot copying, form filling, dry run
and saving without any model calls. It is skipped when the site is not running. It resets the site's decisions."""

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
from recipe.formula import number, render_inline
from recipe.plan import PROOF_MAX, PROOF_NEEDED, committed_values, compile_plan, needs_proof, replay_verdict, url_shape, values_agree
from run_context import RunContext

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "ap_trace.json")))
FIELDS, TRACE = FIXTURE["fields"], FIXTURE["trace"]

checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ part 1: no browser needed

check("page types ignore ids and query values", url_shape("http://x/invoices/e57b1a67") == url_shape("http://x/invoices/0a1b2c3d") == "/invoices/*")
check("query names, not values, count", url_shape("http://x/invoices?status=&q=A") == url_shape("http://x/invoices?q=B&status=") == "/invoices?q&status")
check("vendor ids are ids too", url_shape("http://x/vendors/V-108") == "/vendors/*" and url_shape("http://x/purchase-orders/po012") == "/purchase-orders/*")
check("a formula inside a value is computed", render_inline("Invoiced 740, received {=700+38}") == "Invoiced 740, received 738" and number("0.1*850000") == "85000")
from recipe.formula import evaluate
check("abs, min and max work in formulas; other calls do not", evaluate("abs(440 - 450) > 0.02 * 450") is True and evaluate("abs(450 - 450) > 9") is False and evaluate("min(3, 5) == 3") is True and evaluate("open(1) == 1") is None and evaluate("__import__(1)") is None)
check("a {= } written inside a rule's formula is read as a bracket", evaluate("590 > {=469+121}") is False and evaluate("591 > {=469+121}") is True)
check("a doubled-brace slip leaves no stray braces in the value", render_inline("{{=4*1600}}") == "6400" and render_inline("received {{=20+15+5}}") == "received 40")
check("an uncomputable formula refuses the whole value", render_inline("x {=1/0}") is None and render_inline("x {=abc}") is None)

plan, why = compile_plan(FIELDS, TRACE)
check("a plan is learned from the real AP trace", plan is not None and why == "ok")
check("the vendor name and PO number read from a page become slots", set(plan["slots"]) == {"s1", "s2"} and plan["slots"]["s1"]["example"] == "Royal Hide Traders" and plan["slots"]["s2"]["example"] == "PO-4314")
check("the same PO number typed twice is one slot", [o["value"] for o in plan["gather"] if o["op"] == "fill"].count("{s2}") == 2)
link_ops = [o for o in plan["gather"] if o["op"] == "click" and any(c.get("role") == "link" and "{" in c["name"] for c in o["by"])]
check("the first record's invoice and vendor links are blanks, not its own text", [c["name"] for o in link_ops for c in o["by"] if c.get("role") == "link"] == ["{s1}", "{invoice_no}"])
check("no way of finding an element points at the first record's own id", not any("e57b1a67" in json.dumps(o) or "V-108" in json.dumps(o) for o in plan["gather"] + plan["commit"]["submit"]))
check("the invoice link is checked against the record (wrong-record guard)", plan["key_fields"] == ["invoice_no"] and link_ops[-1]["bound"] == ["invoice_no"])
check("worked-out values are the form fields to decide", [c["key"] for c in plan["commit"]["controls"]] == ["status", "reason", "note"] and all(c["value"] == {"computed": True} for c in plan["commit"]["controls"]))
check("saving is the trailing clicks (Save, then Confirm)", [o["by"][0].get("value") or o["by"][0].get("name") for o in plan["commit"]["submit"]] == ["save", "confirm-yes"])
check("every replayed click knows what page it should land on", all(o.get("expect") for o in plan["gather"] if o["op"] == "click"))
check("plans are version 4, and only the step found by the record's invoice number is marked as its own page",
      plan["version"] == 4 and [o for o in plan["gather"] if o.get("own")] == [link_ops[-1]])
with_ins, _w = compile_plan(FIELDS, TRACE, "Match the invoice against its PO (for example PO-4314) and the vendor Royal Hide Traders.")
check("a value read off an earlier page stays a slot even when the instructions mention it", with_ins["slots"] == plan["slots"] and with_ins["gather"] == plan["gather"])

check("the old click replay is refused for this trace", replay_verdict(FIELDS, TRACE, unmatched_inputs=3)[0] is False)
check("it is refused even if only a click is bound to the record", replay_verdict(FIELDS, TRACE, unmatched_inputs=0)[0] is False)
form_trace = [
    {"kind": "input", "url": "http://x/new", "value": "Ada", "el": {"node": "input", "attrs": {"name": "first_name"}, "text": "First name"}},
    {"kind": "click", "url": "http://x/new", "el": {"node": "button", "attrs": {"type": "submit"}, "text": "Save"}},
]
check("a plain form fill with static clicks stays replayable", replay_verdict({"first_name": "Ada"}, form_trace, 0) == (True, "ok"))
check("a button labelled with the record's id is not replayable", replay_verdict({"id": 7}, [{"kind": "click", "url": "http://x/", "el": {"node": "button", "attrs": {"aria-label": "Approve request 7"}, "text": "Approve request 7"}}], 0)[0] is False)

check("a plan needs proof, then trust", needs_proof(plan) and not needs_proof({"version": 2, "read": [], "act": {}}))
plan["trust"] = {"ok": PROOF_NEEDED, "bad": 0, "signatures": ["a", "a"]}
check("two records that chose the same thing earn one more look", needs_proof(plan))
plan["trust"] = {"ok": PROOF_NEEDED, "bad": 0, "signatures": ["a", "b"]}
check("two records that chose differently are enough", not needs_proof(plan))
plan["trust"] = {"ok": PROOF_MAX, "bad": 0, "signatures": ["a", "a", "a"]}
check("but never more than the maximum", not needs_proof(plan))

kinds = {"status": "select", "reason": "select", "note": "text", "payment_date": "other"}
check("same choices and the same numbers in a differently worded note agree", values_agree({"status": "Place on hold", "note": "Invoiced 740, received 738"}, {"status": "Place on hold", "note": "Invoiced 740 and received 738."}, kinds)[0])
check("different numbers in the note disagree", not values_agree({"note": "Invoiced 740, received 738"}, {"note": "Invoiced 740, received 700"}, kinds)[0])
check("a missing field disagrees", not values_agree({"status": "Reject", "reason": "No open PO"}, {"status": "Reject"}, kinds)[0])
check("the agent's entries are read from its trace", committed_values(TRACE, {"status", "reason", "note"}) == {"status": "Place on hold", "reason": "Short receipt", "note": "Invoiced 740, received 738"})

# ---- the question batcher
from recipe.batcher import Batcher


async def _batch_tests():
    seen = []

    async def handler(group):
        seen.append([k for k, _p in group])
        return {k: (f"answer {k}", 1 if n == 0 else 0) for n, (k, _p) in enumerate(group)}

    # everyone who is on the way asks: one group
    b = Batcher(handler, size=5, wait=5.0)
    for _ in range(3):
        b.enter()
    answers = await asyncio.gather(b.ask("a", 1), b.ask("b", 2), b.ask("c", 3))
    check("records that are all waiting are answered by one call", seen == [["a", "b", "c"]] and [x[0] for x in answers] == ["answer a", "answer b", "answer c"])
    check("only the first record of a group is charged for the call", [x[1] for x in answers] == [1, 0, 0])

    # one record leaves without asking: the others are not kept waiting for it
    seen.clear()
    b = Batcher(handler, size=5, wait=5.0)
    for _ in range(3):
        b.enter()
    t = [asyncio.ensure_future(b.ask("a", 1)), asyncio.ensure_future(b.ask("b", 2))]
    await asyncio.sleep(0.05)
    check("two of three waiting is not enough while the third may still ask", seen == [])
    b.leave()
    await asyncio.gather(*t)
    check("a record that ends without asking releases the others", seen == [["a", "b"]])

    # nobody else is coming but the time runs out
    seen.clear()
    b = Batcher(handler, size=5, wait=0.2)
    for _ in range(3):
        b.enter()
    await b.ask("a", 1)
    check("a lone question is sent after the wait", seen == [["a"]])

    # a full group goes at once and the rest follow
    seen.clear()
    b = Batcher(handler, size=5, wait=0.2)
    for _ in range(7):
        b.enter()
    await asyncio.gather(*(b.ask(str(i), i) for i in range(7)))
    check("a group never holds more than its size", sorted(len(g) for g in seen) == [2, 5])

    # a failure reaches every record in the group
    async def broken(group):
        raise RuntimeError("boom")

    b = Batcher(broken, size=5, wait=5.0)
    for _ in range(2):
        b.enter()
    results = await asyncio.gather(b.ask("a", 1), b.ask("b", 2), return_exceptions=True)
    check("a failed call is passed to every record that asked", all(isinstance(r, RuntimeError) for r in results))


asyncio.run(_batch_tests())

# ---- 15 Oct review: a value written in the instructions is a constant only on a page the route leaves before any form
B15 = "http://x.test"
INS15 = "Open the order. If the amount is under 500, set Status to Approved; otherwise set Status to Rejected. Save."


def t15(kind, url, el=None, value=None, text=""):
    return {"step": 0, "kind": kind, "url": B15 + url, "el": el, "value": value, "goal": "", "page_text": text}


LIST15 = "Orders\nORD-10001\t₹300\nORD-10002\t₹900"
PAGE15 = "Order ORD-10001\nAmount\n₹300\nStatus"
OPEN15 = t15("click", "/orders", {"node": "a", "attrs": {"href": "/orders/ORD-10001"}, "text": "ORD-10001", "xpath": "a1"}, text=LIST15)
SAVE15 = t15("click", "/orders/ORD-10001", {"node": "button", "attrs": {"type": "submit"}, "text": "Save", "xpath": "b1"}, text=PAGE15)
BACK15 = t15("click", "/orders", {"node": "a", "attrs": {"href": "/orders"}, "text": "Orders", "xpath": "a2"}, text=LIST15)
typed15 = [OPEN15, t15("input", "/orders/ORD-10001", {"node": "input", "attrs": {"name": "status", "type": "text"}, "text": "Status", "xpath": "i1"}, "Approved", PAGE15), SAVE15, BACK15]
combo15 = [OPEN15, t15("click", "/orders/ORD-10001", {"node": "input", "attrs": {"name": "status", "role": "combobox"}, "text": "Status", "xpath": "c1"}, text=PAGE15),
           t15("click", "/orders/ORD-10001", {"node": "div", "attrs": {"role": "option"}, "text": "Approved", "xpath": "o1"}, text=PAGE15), SAVE15, BACK15]
for name, trace15 in (("typed", typed15), ("combobox", combo15)):
    p15, why15 = compile_plan({"order_no": "ORD-10001"}, trace15, INS15)
    check(f"a value written in the instructions, {name} on a page whose next button saves, is decided for every record (not a constant), even when saving leaves the page",
          why15 == "ok" and [c["key"] for c in p15["commit"]["controls"]] == ["status"] and p15["commit"]["controls"][0]["value"] == {"computed": True}
          and not any(o.get("saves") or o.get("value") == "Approved" for o in p15["gather"]) and p15["commit"]["submit"][0]["by"][0]["name"] == "Save")
FILTER15 = [t15("input", "/orders", {"node": "input", "attrs": {"name": "q", "type": "search"}, "text": "Search", "xpath": "q1"}, "Overdue", LIST15),
            t15("click", "/orders", {"node": "button", "attrs": {"type": "submit"}, "text": "Search", "xpath": "s1"}, text=LIST15),
            t15("click", "/orders?q=Overdue", {"node": "a", "attrs": {"href": "/orders/ORD-10001"}, "text": "ORD-10001", "xpath": "a1"}, text=LIST15),
            t15("input", "/orders/ORD-10001", {"node": "input", "attrs": {"name": "note", "type": "text"}, "text": "Note", "xpath": "n1"}, "checked by the desk", PAGE15), SAVE15]
p15, why15 = compile_plan({"order_no": "ORD-10001"}, FILTER15, "List the Overdue orders and open each.")
check("  a fixed filter written in the instructions and searched with (not saved) stays a constant of the route",
      why15 == "ok" and p15["gather"][0]["op"] == "fill" and p15["gather"][0]["value"] == "Overdue" and not p15["gather"][1].get("saves"))

# ---- a task with no form: the button that saves is the plan's saving step, never a step of the route
NOFORM = [{"kind": "click", "url": "http://x/claims", "el": {"node": "a", "attrs": {"href": "/claims/C-1"}, "text": "C-1"}, "page_text": "Claims C-1 C-2"},
          {"kind": "click", "url": "http://x/claims/C-1", "el": {"node": "button", "attrs": {"type": "button"}, "text": "Approve"}, "page_text": "Claim C-1 Approve"},
          {"kind": "click", "url": "http://x/claims/C-1/done", "el": {"node": "a", "attrs": {"href": "/claims"}, "text": "Back to claims"}, "page_text": "Approved"}]
p15, why15 = compile_plan({"no": "C-1"}, NOFORM, "Approve the claim.")
check("a no-form task's Approve becomes its saving step (pressed once, never in a dry run), the way back after it is left out",
      why15 == "ok" and [o["by"][-1]["name"] for o in p15["gather"]] == ["{no}"] and [o["by"][0]["name"] for o in p15["commit"]["submit"]] == ["Approve"]
      and p15["gather"][0].get("expect") == "/claims/*")
p15, why15 = compile_plan({"no": "C-1"}, NOFORM, "Approve the claim if its amount is under 500; otherwise reject it.")
check("  but when the instructions choose between outcomes, one record's button cannot stand for all: no plan", p15 is None and "choose between" in why15)
START15 = [{"kind": "click", "url": "http://x/claims", "el": {"node": "a", "attrs": {"href": "/claims/C-1"}, "text": "C-1"}, "page_text": "Claims C-1"},
           {"kind": "click", "url": "http://x/claims/C-1", "el": {"node": "button", "attrs": {"type": "button"}, "text": "Start review"}, "page_text": "Claim C-1"},
           {"kind": "select", "url": "http://x/claims/C-1/form", "el": {"node": "select", "attrs": {"name": "status"}, "text": "Status"}, "value": "Approve", "page_text": "Claim C-1 form"},
           {"kind": "click", "url": "http://x/claims/C-1/form", "el": {"node": "button", "attrs": {"type": "submit"}, "text": "Save"}, "page_text": "Claim C-1 form"}]
p15, why15 = compile_plan({"no": "C-1"}, START15, "Start a review of the claim and approve it.")
check("a route's click that may change something on the site ('Start review') is marked as saving; opening the record is not",
      why15 == "ok" and [bool(o.get("saves")) for o in p15["gather"]] == [False, True])


# ------------------------------------------------------------------ part 2: the plan in a real browser (fake model)

SITE = "http://localhost:5180"
try:
    urllib.request.urlopen(SITE + "/admin/state", timeout=2)
    site_up = "--offline" not in sys.argv  # part 2 resets the site: never run it while a real run is using the site
except Exception:
    site_up = False

if not site_up:
    print(f"\nSKIPPED part 2 (--offline, or the AP Desk site is not running on {SITE})")
    print(f"\n{checks} checks passed")
    sys.exit(0)

SEED = json.load(open(site_path("ap-desk", "data/seed.json")))
EXPECTED = json.load(open(site_path("ap-desk", "expected.json")))
VENDOR = {v["id"]: v["name"] for v in SEED["vendors"]}
INVOICE = {i["invoice_no"]: i for i in SEED["invoices"]}
STATUS_LABEL = {"Approved": "Approve for payment", "On hold": "Place on hold", "Rejected": "Reject"}
calls = {"slots": 0, "form": 0, "slot_records": 0, "form_records": 0}
corrupt = {"slots": False}


# A stand-in for the model that answers from the site's own data (so the test costs no quota and is exact). It understands
# questions about several records at once.
async def fake_ask_json(ctx, prompt, schema=None, temperature=0.0):
    blocks = re.split(r'^### record "', prompt, flags=re.M)[1:]
    if schema is px._SlotAnswers:
        calls["slots"] += 1
        records = []
        for block in blocks:
            key = block.split('"', 1)[0]
            inv = INVOICE[re.search(r"invoice_no = (INV-26-\d+)", block).group(1)]
            values = []
            for n in re.findall(r"^  - (s\d+):", block, flags=re.M):
                ex = re.search(rf'^  - {n}: for a different record this was "([^"]+)"', block, flags=re.M).group(1)
                values.append({"name": n, "value": inv["po_number"] if ex.startswith("PO-") else VENDOR[inv["vendor_id"]]})
            if corrupt["slots"]:
                values = [{"name": v["name"], "value": "Somebody Else Ltd"} for v in values]
            records.append({"record": key, "values": values})
        calls["slot_records"] += len(records)
        return {"records": records}
    if schema is px._FormDecisions:
        calls["form"] += 1
        decisions = []
        for block in blocks:
            key = block.split('"', 1)[0]
            invoice_no = re.search(r"invoice_no = (INV-26-\d+)", block).group(1)
            e = EXPECTED[invoice_no]
            values = [{"field": "status", "value": STATUS_LABEL[e["status"]]}]
            if e["status"] == "Approved":
                values.append({"field": "payment_date", "value": e["payment_date"]})
            else:
                values.append({"field": "reason", "value": e["reason"]})
            if e["status"] == "On hold":
                a, b = e["note_numbers"]
                values.append({"field": "note", "value": f"Invoiced {a}, received {{={b}}}"})
            decisions.append({"record": key, "facts": "from the pages", "missing": "", "checks": [{"rule": "otherwise: the one that applies", "expr": "true", "values": values}]})
        calls["form_records"] += len(decisions)
        return {"decisions": decisions}
    raise AssertionError("unexpected question")


px.ask_json = fake_ask_json
ctx = RunContext(target_url=SITE, workflow_name="test", match_key_field="invoice_no", model_name="m", gemini_api_key="x", task_instructions="the task")


def state():
    return json.load(urllib.request.urlopen(SITE + "/admin/state"))


def reset():
    urllib.request.urlopen(urllib.request.Request(SITE + "/admin/reset", method="POST"))


from playwright.async_api import async_playwright


async def run(record_no, *, dry, page_factory):
    page = await page_factory()
    events = []
    out = await px.run_plan(plan_for_run, {"invoice_no": record_no}, ctx=ctx, page=page, emit=events.append, dry=dry)
    return out, events


plan_for_run, _ = compile_plan(FIELDS, TRACE)
pick = {}
for no, e in EXPECTED.items():
    pick.setdefault((e["status"], e.get("reason")), no)


async def part2():
    reset()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        async def fresh():
            return await context.new_page()

        # a dry run decides but saves nothing
        no = pick[("On hold", "Short receipt")]
        out, _ev = await run(no, dry=True, page_factory=fresh)
        check("a dry run returns the values it would enter", out.ok and out.planned["status"] == "Place on hold" and out.planned["reason"] == "Short receipt" and not out.acted)
        check("a dry run computes the {= } numbers", re.findall(r"\d+", out.planned["note"]) == [str(n) for n in EXPECTED[no]["note_numbers"]])
        check("a dry run saves nothing", state()[no] is None)
        check("the plan reports each control's kind (a textarea is a note: compared by its numbers, skipped when one side leaves it empty)", out.kinds.get("status") == "select" and out.kinds.get("note") == "note")
        check("a dry run made 1 slot call and 2 form calls", calls["slots"] == 1 and calls["form"] == 2)

        # real runs, one of each kind of outcome
        for key, (status, reason) in {"short": ("On hold", "Short receipt"), "approve": ("Approved", None), "blocked": ("Rejected", "Vendor blocked"), "closed": ("Rejected", "No open PO"), "price": ("On hold", "Price variance")}.items():
            no = pick[(status, reason)]
            out, _ev = await run(no, dry=False, page_factory=fresh)
            e = EXPECTED[no]
            got = state()[no]
            good = out.ok and out.acted and got is not None and got["status"] == e["status"] and got["reason"] == e.get("reason") and (got["payment_date"] == e.get("payment_date"))
            if status == "On hold":
                good = good and all(str(n) in (got["note"] or "") for n in e["note_numbers"])
            check(f"{key}: the plan fills the form and saves the right decision ({no})", good)

        # four records at once share their model calls, and each still gets its own correct answer
        reset()
        for k in calls:
            calls[k] = 0
        batchers = px.PlanBatchers(ctx, size=5, wait=3.0)
        four = [pick[("Approved", None)], pick[("On hold", "Short receipt")], pick[("Rejected", "Vendor blocked")], pick[("On hold", "Price variance")]]

        async def one(no):
            page = await context.new_page()
            out = await px.run_plan(compile_plan(FIELDS, TRACE)[0], {"invoice_no": no}, ctx=ctx, page=page, emit=lambda e: None, key=no, batchers=batchers)
            await page.close()
            return out

        outs = await asyncio.gather(*(one(no) for no in four))
        got_all = state()
        right_all = all(o.ok and o.acted and got_all[no] is not None and got_all[no]["status"] == EXPECTED[no]["status"] and got_all[no]["reason"] == EXPECTED[no].get("reason") for o, no in zip(outs, four))
        check("four records run at once and each saves its own right decision", right_all)
        check("their questions were shared: fewer calls than 4 records alone would need", calls["slots"] < 4 and calls["form"] < 8 and calls["slot_records"] == 4 and calls["form_records"] >= 4)
        check("each call is charged to exactly one record", sum(o.llm_calls for o in outs) == calls["slots"] + calls["form"])
        await batchers.close()
        reset()

        # a fixed label that is really record data is corrected during the check, never on a trusted plan
        stale, _why = compile_plan(FIELDS, TRACE)
        vendor_click = next(o for o in stale["gather"] if o["op"] == "click" and any(c.get("name") == "{s1}" for c in o["by"]))
        vendor_click["by"] = [{"role": "link", "name": "Royal Hide Traders"}]
        other_vendor = next(no for no in EXPECTED if VENDOR[INVOICE[no]["vendor_id"]] != "Royal Hide Traders" and EXPECTED[no]["status"] == "On hold")
        global plan_for_run
        keep, plan_for_run = plan_for_run, stale
        out, _ev = await run(other_vendor, dry=True, page_factory=fresh)
        check("a fixed label that was record data is turned into a slot during a dry run", out.ok and out.healed and any(v["example"] == "Royal Hide Traders" for v in stale["slots"].values()) and out.planned["status"] == "Place on hold")
        check("the corrected plan no longer names that vendor", not any(c.get("name") == "Royal Hide Traders" for o in stale["gather"] for c in o.get("by", [])))
        stale2, _why = compile_plan(FIELDS, TRACE)
        next(o for o in stale2["gather"] if o["op"] == "click" and any(c.get("name") == "{s1}" for c in o["by"]))["by"] = [{"role": "link", "name": "Royal Hide Traders"}]
        plan_for_run = stale2
        before = state()[other_vendor]
        out, _ev = await run(other_vendor, dry=False, page_factory=fresh)
        check("a trusted plan is never corrected on the fly: it fails before saving", (not out.ok) and (not out.acted) and not out.healed and state()[other_vendor] == before)
        plan_for_run = keep

        # the slots really come from the record's own pages: a wrong answer from the model is refused
        reset()
        corrupt["slots"] = True
        out, _ev = await run(pick[("Approved", None)], dry=False, page_factory=fresh)
        check("a slot value that is not on the pages is refused before anything is saved", (not out.ok) and (not out.acted) and "could not find a value" in (out.error or "") and all(v is None for v in state().values()))
        corrupt["slots"] = False

        # the wrong-record guard: a plan whose record-bound link opens another invoice is refused
        other = "INV-26-0000"
        out, _ev = await run(other, dry=False, page_factory=fresh)
        check("a record the site does not have fails before saving", (not out.ok) and (not out.acted))

        await browser.close()
    reset()


asyncio.run(part2())
print(f"\n{checks} checks passed")
