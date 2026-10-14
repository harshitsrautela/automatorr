"""Checks for route coverage and pruning (recipe/coverage.py):

    python tests/test_coverage.py              # add --offline to skip part 2 (a headless browser, no site needed)

Part 1 needs nothing: a plan compiled from the recorded Returns Desk trace (tests/fixtures/returns_trace.json) and a short plan
from the same trace that never opens the order, the item or the policy page, a FAKE model answer (with good and bad reads),
the side trips added to the route, revisit steps renumbered, and pruning counted and picked.
Part 2 runs a plan in a real headless browser against a small site served by the test itself (Playwright request routing,
no server, no test site on 5180-5182) with a fake model: before coverage the record cannot be decided (its order and the
rates table were never read); after coverage the plan reads both and decides it. No real model is ever called."""

import asyncio
import copy
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recipe.coverage as cov
import recipe.plan_exec as px
from recipe.coverage import add_reads, coverage_prompt, cover_route, note_page_use, parse_coverage, reset_pruning, route_pages_from_trace, skippable
from recipe.llm import LlmHalt
from recipe.plan import compile_plan
from run_context import RunContext

HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures", "returns_trace.json")))
FIELDS, TRACE = FX["fields"], FX["trace"]
EXPENSE = json.load(open(os.path.join(HERE, "fixtures", "expense_trace.json")))
checks = 0


# Counts one check and prints it; stops the run on the first that fails.
def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


INSTRUCTIONS = """For each pending return, open it and decide.
1. If the reason is "Wrong item", choose Replace.
2. Open the order to read the unit price, and the item's page to see its category and whether it is final sale. A final-sale item is rejected.
3. Otherwise refund the unit price less the restocking fee for the item's category, from the Return policy (Opened or Damaged column).
Choose the next pick-up slot as the pick-up date, then save and confirm."""


# Takes a plan's gather steps; returns them as (op, the name a click finds its element by or a step's target, added?).
def route_of(gather):
    return [(o["op"], next((c["name"] for c in o.get("by", []) if "role" in c), None) if o["op"] == "click" else o.get("to", o.get("url")), bool(o.get("added"))) for o in gather]


# True when every revisit step points at a step that landed on the page type the revisit expects.
def revisits_consistent(gather):
    return all(0 <= o["to"] < k and gather[o["to"]].get("expect") == o.get("expect") for k, o in enumerate(gather) if o["op"] == "revisit")


# ------------------------------------------------------------------ part 1: no browser needed

full, why = compile_plan(FIELDS, TRACE)
short_trace = [e for e in TRACE if e["step"] in (1, 2, 3, 4, 5, 6, 17, 18, 19, 20, 21, 22, 23)]
short, short_why = compile_plan(FIELDS, short_trace)
check("the full and the short plan compile from the recorded trace", why == "ok" and short_why == "ok")
check("the short plan only reads the list and the return (a 'wrong item' route)", [o.get("expect") for o in short["gather"]] == ["/returns", "/returns/*"] and not short["slots"])

pages = route_pages_from_trace(short, TRACE)
check("the pages a plan reads are taken from its trace, typed as its steps expect them", [t for t, _x in pages] == ["/", "/returns", "/returns/*"] and "ORD-10128" in pages[2][1])
check("a page the plan does not read (the order, the item, the policy) is not among them", not any(t.startswith(("/orders", "/products", "/policy")) for t, _x in pages))
tab_trace = copy.deepcopy(EXPENSE["trace"])
tab_step = next(e for e in tab_trace if e["step"] == 8)
tab_step["page_text"] = "Receipts\nHotel tariff card"
tab_plan, _w = compile_plan(EXPENSE["fields"], EXPENSE["trace"])
tab_pages = route_pages_from_trace(tab_plan, tab_trace)
check("each page type keeps only the text the route lands on, not text shown after a tab was opened",
      [t for t, _x in tab_pages].count("/#/claims/*") == 1 and not any("Hotel tariff card" in x for _t, x in tab_pages)
      and parse_coverage({"reads": [{"from_page": "/#/claims/*", "link_text": "Hotel tariff card", "per_record": False, "label": "", "why": ""}]}, tab_pages) == [])

prompt = coverage_prompt(INSTRUCTIONS, pages, short["commit"]["controls"])
check("the coverage question holds the instructions, every page under its type and the form",
      "final sale" in prompt and "=== /returns/* ===" in prompt and "=== /returns ===" in prompt and "disposition" in prompt and "refund_amount" in prompt and '"reads"' in prompt)
dom_page = "Desk\n[30]<a />\n\tDashboard\n[36]<a />\n\tTravel rules\n*[40]<dt />\n\tVendor\n*[41]<dd />\n\t*[42]<a />\n\t\tRoyal Hide Traders"
dom_prompt = coverage_prompt(INSTRUCTIONS, [("/vendors/*", dom_page)], [])
check("a trace's page view is shown without its element marks", "[36]<a />" not in dom_prompt and "Travel rules" in dom_prompt and "Vendor\nRoyal Hide Traders" in dom_prompt)

ANSWER = {"reads": [
    {"from_page": "/returns/*", "link_text": "ORD-10128", "per_record": True, "label": "Order", "why": "the unit price"},
    {"from_page": "/returns/*", "link_text": "SKU-8649", "per_record": True, "label": "Item:", "why": "the category and whether it is final sale"},
    {"from_page": "/returns", "link_text": "return policy", "per_record": False, "label": "", "why": "the restocking fee for the category"},
    # every one below is wrong in its own way
    {"from_page": "/orders/*", "link_text": "Return policy", "per_record": False, "label": "", "why": "a page the route does not read"},
    {"from_page": "/returns/*", "link_text": "Warranty terms", "per_record": False, "label": "", "why": "not on the page"},
    {"from_page": "/returns/*", "link_text": "SKU-8649", "per_record": True, "label": "Order", "why": "not the value next to its label"},
    {"from_page": "/returns/*", "link_text": "INV-77", "per_record": True, "label": "Invoice", "why": "a label the page does not show"},
    {"from_page": "/returns/*", "link_text": "ORD-10128", "per_record": False, "label": "", "why": "a fixed link naming one record's order"},
    {"from_page": "/returns/*", "link_text": "Save decision", "per_record": False, "label": "", "why": "a button that saves"},
    {"from_page": "/returns", "link_text": "Next ›", "per_record": False, "label": "", "why": "paging"},
    {"from_page": "/returns", "link_text": "Return policy", "per_record": False, "label": "", "why": "the same link again"},
    {"from_page": "/returns/*", "link_text": "Krish Malhotra", "per_record": "maybe", "label": "Customer", "why": "not a yes or no"},
    {"from_page": "", "link_text": "Return policy", "per_record": False, "label": "", "why": "no page"},
    "Return policy",
]}
reads = parse_coverage(ANSWER, pages)
check("the three good reads are kept, in order", [(r["from"], r["link_text"], r["per_record"]) for r in reads] == [
    ("/returns/*", "ORD-10128", True), ("/returns/*", "SKU-8649", True), ("/returns", "Return policy", False)])
check("each read has exactly the agreed shape", all(set(r) == {"from", "link_text", "per_record", "label", "why"} for r in reads))
check("a link written in the wrong case is taken as the page writes it", reads[2]["link_text"] == "Return policy")
check("a label's trailing colon is dropped", reads[1]["label"] == "Item")
check("every bad read is dropped: other page, not shown, wrong label, unknown label, an item as a fixed link, an action, paging, a repeat, a non-bool, no page",
      len(reads) == 3)
check("a page given as an address is matched to the route's page type",
      parse_coverage({"reads": [{"from_page": "http://localhost:5181/returns/88637011", "link_text": "ORD-10128", "per_record": True, "label": "Order", "why": ""}]}, pages)[0]["from"] == "/returns/*")
check("an answer that is not a list of reads gives nothing", parse_coverage(None, pages) == [] and parse_coverage("Return policy", pages) == [] and parse_coverage({}, pages) == [] and parse_coverage({"reads": "x"}, pages) == [])
check("a bare list and the key 'from' are read too", [r["link_text"] for r in parse_coverage([{"from": "/returns", "link_text": "Dashboard", "per_record": "false", "label": "", "why": ""}], pages)] == ["Dashboard"])
many = [{"from_page": "/returns/*", "link_text": v, "per_record": True, "label": k, "why": ""} for k, v in (("Order", "ORD-10128"), ("Item", "SKU-8649"), ("Customer", "Krish Malhotra"), ("Status", "Pending"), ("Condition", "Damaged"), ("Reason", "Changed mind"))]
check("one answer adds at most four reads", len(parse_coverage({"reads": many}, pages)) == 4)

# "the value next to the label", as page text writes it: table columns, labelled table rows, a trace's page view, a line
table_page = "Lines\nItem\tQty\tPrice\nSKU-1001\t2\t₹1,200\nSKU-1002\t1\t₹640\n\n[table rows with their headings]\nItem: SKU-1001 | Qty: 2 | Price: ₹1,200\nItem: SKU-1002 | Qty: 1 | Price: ₹640"
inline_page = "Case CASE-9\nShipped as Order ORD-5512 (two parcels)\nOrder ORD-551201"


# Takes a page's text, a label and a link text; returns the link text a per-record read keeps (None when it is left out).
def per_record(page_text, label, link):
    got = parse_coverage({"reads": [{"from_page": "/x/*", "link_text": link, "per_record": True, "label": label, "why": ""}]}, [("/x/*", page_text)])
    return got[0]["link_text"] if got else None


one_row = "Lines\nItem\tQty\tPrice\nSKU-1001\t2\t₹1,200\n\n[table rows with their headings]\nItem: SKU-1001 | Qty: 2 | Price: ₹1,200"
check("a value in a table column under its heading is next to the label", per_record(one_row, "Item", "SKU-1001") == "SKU-1001")
check("a value in a labelled table row is next to the label", per_record(one_row.split("\n\n")[1], "Item", "SKU-1001") == "SKU-1001" and per_record(one_row.split("\n\n")[1], "Price", "SKU-1001") is None)
check("a table of many records is not one record's own link (nothing ties it to the record's row)",
      per_record(table_page, "Item", "SKU-1002") is None and per_record(table_page.split("\n\n")[1], "Item", "SKU-1001") is None
      and per_record("Case\tOrder\tCustomer\nCASE-1\tORD-5001\tA\nCASE-3\tORD-9003\tB", "Order", "ORD-9003") is None)
check("a row of a label and its value counts, and a key/value table is not taken as a heading row",
      per_record("Order\tORD-5512\nCustomer\tAsha Rao", "Order", "ORD-5512") == "ORD-5512" and per_record("Order\tORD-5512\nCustomer\tAsha Rao", "Customer", "Asha Rao") == "Asha Rao")
check("a label or value whose small letters are longer than it (a dotted capital I) does not break the check",
      per_record("Customer\nALİ", "Customer", "ALI") is None and per_record("Customer\nALİ KAYA", "Customer", "ALİ") == "ALİ" and per_record("İD 7\nCustomer\nALİ", "İD", "7x") is None)
check("a bare number is never taken as a link (it reads as a page number)", per_record(table_page, "Qty", "2") is None)
check("a value under its label in a trace's page view is next to the label", per_record(dom_page, "Vendor", "Royal Hide Traders") == "Royal Hide Traders")
check("a value that starts a line after the label counts; a longer id does not", per_record("Order ORD-5512 (two parcels)", "Order", "ORD-5512") == "ORD-5512" and per_record("Order ORD-551201", "Order", "ORD-5512") is None)
check("a value elsewhere on the page is not next to the label", per_record(inline_page, "Case", "ORD-5512") is None)
big = "\n".join(f"Order\tORD-{i:05d}" for i in range(6000))
started = time.monotonic()
slow = parse_coverage({"reads": [{"from_page": "/x/*", "link_text": f"ORD-9{i:04d}", "per_record": True, "label": "Order", "why": ""} for i in range(50)]}, [("/x/*", big)])
check("a long table of labels and values is read in one pass, not once per row", slow == [] and time.monotonic() - started < 5)


# Takes a page's text and a link text (and, when given, the route's other pages); returns the link text a fixed read keeps (None
# when it is left out).
def fixed(page_text, link, others=()):
    got = parse_coverage({"reads": [{"from_page": "/x/*", "link_text": link, "per_record": False, "label": "", "why": ""}]}, [("/x/*", page_text), *others])
    return got[0]["link_text"] if got else None


actions = ["Issue refund", "Process refund", "Pay now", "Escalate", "Refund", "Void", "Reopen", "Withdraw", "Disable account", "Place order",
           "Ship now", "Delete this return for good", "Approve and close this case"]
check("links that do something are never added, however many words follow the verb", all(fixed(f"Desk\n{a}\nMore", a) is None for a in actions)
      and fixed("DeskRefundsIssues", "Refunds") == "Refunds" and fixed("DeskRefundsIssues", "Issues") == "Issues")
check("a fixed link must stand on its own: not inside a word, and never a case-changed part of a sentence",
      fixed("This separates the fees", "Rates") is None and fixed("Helpdesk tickets", "Help") is None
      and fixed("Fees follow the return policy shown at checkout.", "Return policy") is None
      and fixed("HomeCasesRates and fees", "Rates and fees") == "Rates and fees" and fixed("See the Return policy here", "return policy") == "Return policy")
check("a record's own value next to a label is not a fixed link (a category, a customer, a value in a table or after 'Label:')",
      fixed("Dashboard\nItem SKU-8649\nCategory\nFootwear", "Footwear") is None and fixed("Customer\nKrish Malhotra\nStatus\nPending", "Krish Malhotra") is None
      and fixed("Category: Footwear", "Footwear") is None and fixed("Item\tCategory\nX-1\tFootwear", "Footwear") is None and fixed("Category\tFootwear", "Footwear") is None)
check("a menu link with one link a line is still kept when the route's other pages show it too",
      fixed("Dashboard\nCases\nRates\nCase CASE-1", "Rates", [("/cases", "Dashboard\nCases\nRates\nCases")]) == "Rates"
      and fixed("Dashboard\nCases\nRates\nCase CASE-1", "Rates") is None)
marked = "Desk\n[30]<a />\n\tDashboard\n[33]<a />\n\tRates and fees\nPolicy text mentions Help\n*[40]<button />\n\tReview claim\n*[41]<a />\n\t*[42]<span />\n\t\tTravel policy"
check("in a trace's page view a fixed link must be the text of a link there", fixed(marked, "Rates and fees") == "Rates and fees"
      and fixed(marked, "Travel policy") == "Travel policy" and fixed(marked, "Help") is None and fixed(marked, "Review claim") is None)

# ---- adding the reads to the short plan
plan = copy.deepcopy(short)
before = copy.deepcopy(plan["gather"])
added = add_reads(plan, reads)
gather = plan["gather"]
check("each read becomes a side trip: a click and a step back", len(added) == 3 and len(gather) == len(before) + 6)
check("the side trips sit right after the first step that lands on their page, in the order given", route_of(gather) == [
    ("click", "Returns", False), ("click", "Return policy", True), ("revisit", 0, True),
    ("click", "{rma}", False), ("click", "{s1}", True), ("revisit", 3, True), ("click", "{s2}", True), ("revisit", 3, True)])
check("every way back reopens the page the trip started from", revisits_consistent(gather) and [o["expect"] for o in gather if o["op"] == "revisit"] == ["/returns", "/returns/*", "/returns/*"])
check("a record's own link becomes a new slot, read off the page it is shown on", plan["slots"] == {
    "s1": {"example": "ORD-10128", "seen_on": "/returns/*", "hint": "the link next to Order"},
    "s2": {"example": "SKU-8649", "seen_on": "/returns/*", "hint": "the link next to Item"}})
s1_click = gather[4]
check("its click finds the link by the slot, and says it uses the slot", s1_click["by"][0] == {"role": "link", "name": "{s1}"} and s1_click["uses"] == ["s1"] and s1_click["bound"] == [])
check("a fixed link is clicked by its own text and uses no slot", gather[1]["by"][0] == {"role": "link", "name": "Return policy"} and gather[1]["uses"] == [])
check("an added click finds only a link (never a button with the same text, which may do something)",
      all(o["by"] == [{"role": "link", "name": o["by"][0]["name"]}] for o in gather if o.get("added") and o["op"] == "click"))
check("added steps are marked and say where they came from", all(o.get("added") is True and o["goal"].endswith("(added from the instructions)") for o in gather if o not in before)
      and all(len(o["goal"]) <= 160 for o in gather))
check("the original steps are unchanged", [o for o in gather if not o.get("added")] == before)
check("the descriptions say what was added", "Return policy" in added[2] and "s1" in added[0] and "ORD-10128" in added[0])
check("the plan is still plain JSON (it is stored as JSON)", json.loads(json.dumps(plan)) == plan)
check("adding the same reads again adds nothing", add_reads(plan, reads) == [] and len(plan["gather"]) == 8)
later = [{"from": "/returns/*", "link_text": "ORD-20417", "per_record": True, "label": "Order", "why": ""}]
check("a later record's value next to the same label is the same link, not a new one", add_reads(plan, later) == [] and len(plan["slots"]) == 2)

# ---- the full plan: renumbering every later revisit
plan = copy.deepcopy(full)
old = plan["gather"][:]
old_targets = [(o, old[o["to"]]) for o in old if o["op"] == "revisit"]
check("the full plan's ways back point at steps 1 and 3 before anything is added", [o["to"] for o, _t in old_targets] == [1, 3])
extra = [{"from": "/returns/*", "link_text": "Krish Malhotra", "per_record": True, "label": "Customer", "why": "the customer's history"},
         {"from": "/returns", "link_text": "Dashboard", "per_record": False, "label": "", "why": "open counts"},
         {"from": "/products/*", "link_text": "Return policy", "per_record": False, "label": "", "why": "already read"}]
added = add_reads(plan, extra)
gather = plan["gather"]
check("a link the route already clicks is not added again", len(added) == 2 and sum(1 for o in gather if any(c.get("name") == "Return policy" for c in o.get("by", []))) == 1)
check("every later revisit is renumbered to the very step it pointed at before", all(gather[o["to"]] is target for o, target in old_targets) and [o["to"] for o, _t in old_targets] == [3, 7])
check("the new ways back point at their own pages", revisits_consistent(gather) and [o["to"] for o in gather if o["op"] == "revisit" and o.get("added")] == [0, 3])
check("a new slot takes the next free name", plan["slots"]["s3"]["example"] == "Krish Malhotra" and set(plan["slots"]) == {"s1", "s2", "s3"})

# ---- reads on the start page, on a page the route never reaches, and malformed reads
plan = copy.deepcopy(short)
start_read = [{"from": "/", "link_text": "Return policy", "per_record": False, "label": "", "why": "fees"}]
added = add_reads(plan, start_read)
check("a read on the start page (no step lands there) goes first, then back to the start's address",
      len(added) == 1 and [o["op"] for o in plan["gather"][:3]] == ["click", "goto", "click"] and plan["gather"][1]["url"] == short["start"] and plan["gather"][1]["added"])
plan = copy.deepcopy(short)
bad = [{"from": "/orders/*", "link_text": "ORD-10128", "per_record": True, "label": "Order"},
       {"from": "/returns/*", "link_text": "ORD-10128", "per_record": True, "label": ""},
       {"from": "/returns/*", "link_text": "", "per_record": False, "label": ""},
       {"from": "/returns/*", "link_text": "Return policy", "per_record": "no", "label": ""},
       {"link_text": "Return policy", "per_record": False}, "Return policy", None]
check("a read whose page the route never reaches, or that is malformed, is skipped", add_reads(plan, bad) == [] and plan["gather"] == short["gather"] and not plan["slots"])

# ---- the one model call, with a fake model
asked = []


# A fake model that gives the coverage answer above (good and bad reads), noting each question it was asked.
async def fake_coverage(ctx, prompt, schema=None, temperature=0.0):
    asked.append((prompt, schema))
    return ANSWER


# A fake model answer naming a customer written with a dotted capital I (its small letters are one character longer).
async def dotted(ctx, prompt, schema=None, temperature=0.0):
    return {"reads": [{"from_page": "/returns/*", "link_text": "ALI", "per_record": True, "label": "Customer", "why": ""}]}


# A fake model that has run out of quota.
async def halted(ctx, prompt, schema=None, temperature=0.0):
    raise LlmHalt("quota", quota=True)


# A fake model whose call fails.
async def broken(ctx, prompt, schema=None, temperature=0.0):
    raise RuntimeError("the model call failed: 503")


ctx = RunContext(target_url="http://localhost", workflow_name="test", match_key_field="rma", model_name="m", gemini_api_key="x", task_instructions=INSTRUCTIONS)
real_ask = cov.ask_json
cov.ask_json = fake_coverage
plan = copy.deepcopy(short)
added = asyncio.run(cover_route(ctx, plan, pages))
check("cover_route asks once, with the plan's own form, and adds what holds up", len(asked) == 1 and len(added) == 3 and "refund_amount" in asked[0][0] and asked[0][1] is cov._CoverageAnswer and len(plan["gather"]) == 8)
check("without instructions or pages it asks nothing", asyncio.run(cover_route(RunContext(target_url="x", workflow_name="w", match_key_field="rma", model_name="m", gemini_api_key="x"), copy.deepcopy(short), pages)) == []
      and asyncio.run(cover_route(ctx, copy.deepcopy(short), [])) == [] and len(asked) == 1)
cov.ask_json = broken
plan = copy.deepcopy(short)
check("a failed question leaves the plan as it was", asyncio.run(cover_route(ctx, plan, pages)) == [] and plan == short)
cov.ask_json = dotted
plan = copy.deepcopy(short)
check("an answer whose text has a dotted capital I is checked without an error", asyncio.run(cover_route(ctx, plan, [("/returns/*", "Customer\nALİ")])) == [] and plan == short)
cov.ask_json, real_add = fake_coverage, cov.add_reads


# Takes a plan and reads; changes the plan part way, then fails (an unexpected error while adding).
def add_then_fail(plan, reads):
    plan["gather"].insert(0, {"op": "click", "by": [], "added": True})
    raise ValueError("broken part way")


cov.add_reads = add_then_fail
plan = copy.deepcopy(short)
check("an error while adding the reads leaves the plan as it was", asyncio.run(cover_route(ctx, plan, pages)) == [] and plan == short)
cov.add_reads = real_add
cov.ask_json = halted
try:
    asyncio.run(cover_route(ctx, copy.deepcopy(short), pages))
    check("a halted model (quota, bad key) stops the caller", False)
except LlmHalt:
    check("a halted model (quota, bad key) stops the caller", True)
cov.ask_json = real_ask

# ---- pruning
plan = copy.deepcopy(full)
for _ in range(11):
    note_page_use(plan, ["/returns/*", "http://localhost:5181/products/SKU-1001", "/returns/*"])
check("decisions and the page types that supplied facts are counted (an address counts under its page type)",
      plan["page_use"] == {"decisions": 11, "used": {"/returns/*": 11, "/products/*": 11}})
check("nothing is skipped before enough decisions", skippable(plan) == set())
note_page_use(plan, ["/returns/*", "/products/*"])
check("after 12 decisions, side trips to pages that never supplied a fact are skipped (the order, the policy)", skippable(plan) == {2, 5})
check("a smaller threshold can be asked for", skippable(plan, min_decisions=13) == set() and skippable(plan, min_decisions=5) == {2, 5})
lean = copy.deepcopy(full)
for _ in range(12):
    note_page_use(lean, "/returns/*")
check("a trip of two clicks (item, then policy, then back) is skipped whole when neither page supplied a fact", skippable(lean) == {2, 4, 5})
blind = copy.deepcopy(full)
for _ in range(12):
    note_page_use(blind, [])
check("even when no page supplied a fact: the step that finds the record, the list and the ways back are never skipped",
      blind["page_use"] == {"decisions": 12, "used": {}} and skippable(blind) == {2, 4, 5})
policy_only = copy.deepcopy(full)
for _ in range(12):
    note_page_use(policy_only, ["/returns/*", "/policy"])
check("the item page on the way to a policy page that is used is kept", skippable(policy_only) == {2})
reset_pruning(lean)
check("reset_pruning forgets the counts", lean["page_use"] == {"decisions": 0, "used": {}} and skippable(lean) == set())

expense, _w = compile_plan(EXPENSE["fields"], EXPENSE["trace"])
tab = next(k for k, o in enumerate(expense["gather"]) if o.get("stays") and o["op"] == "click")
for _ in range(12):
    note_page_use(expense, ["/#/claims/*", "http://localhost:5182/#/claims/kxqfab"])
check("a single-page app's id-less record address counts under the plan's own page type", expense["page_use"]["used"] == {"/#/claims/*": 12})
check("on a single-page app the policy side trip is skipped, never the tab it returns to", skippable(expense) == {tab + 1} and expense["gather"][tab + 1]["expect"] == "/#/policy")
extended = copy.deepcopy(short)
add_reads(extended, reads)
for _ in range(12):
    note_page_use(extended, [])
check("a side trip added from the instructions is never skipped (where it lands is not known)", skippable(extended) == set())
synthetic = {"start": "http://x/", "gather": [
    {"op": "click", "by": [{"role": "link", "name": "List"}], "expect": "/list"},
    {"op": "click", "by": [{"role": "link", "name": "X"}], "expect": "/x"},
    {"op": "revisit", "to": 0, "expect": "/list"},
    {"op": "click", "by": [{"role": "link", "name": "Y"}], "expect": "/y"},
    {"op": "revisit", "to": 1, "expect": "/x"}]}
for _ in range(12):
    note_page_use(synthetic, ["/list"])
check("a click whose page a later step goes back to is never skipped", skippable(synthetic) == {3})
sourced = {"start": "http://x/", "slots": {"s1": {"example": "ORD-1", "seen_on": "/x/*"}, "s2": {"example": "Gold", "seen_on": "/orders/*"}},
           "commit": {"controls": [{"key": "tier", "value": {"template": "{s2}"}}]},
           "gather": [{"op": "click", "by": [{"role": "link", "name": "X"}], "expect": "/x/*"},
                      {"op": "click", "by": [{"role": "link", "name": "Y"}], "expect": "/x/*", "stays": True},
                      {"op": "click", "by": [{"role": "link", "name": "{s1}"}], "expect": "/orders/*"},
                      {"op": "revisit", "to": 1, "expect": "/x/*"}]}
for _ in range(12):
    note_page_use(sourced, ["/x/*"])
check("a side trip to the page a value in the form is copied from is never skipped", skippable(sourced) == set())
unused = copy.deepcopy(sourced)
unused["commit"]["controls"][0]["value"] = {"computed": True}
check("once no form value or step uses that page's value, the side trip may be skipped", skippable(unused) == {2})


# ------------------------------------------------------------------ part 2: the side trips really run (headless browser)

SITE = "http://desk.test"
NAV = '<nav><a href="/">Dashboard</a> <a href="/cases">Cases</a> <a href="/rates">Rates</a></nav>'
CASES = {"CASE-101": ("40101", "Asha Rao", "ORD-5001", "North"), "CASE-202": ("40202", "Vikram Iyer", "ORD-7002", "South")}


# Takes a page title and body; returns the small site's whole page around them.
def html(title, body):
    return f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head><body>{NAV}<main>{body}</main></body></html>'


SITE_PAGES = {
    "/": html("Dashboard", "<h1>Dashboard</h1><p>2 cases waiting.</p>"),
    "/cases": html("Cases", "<h1>Cases</h1><table><thead><tr><th>Case</th><th>Customer</th></tr></thead><tbody>"
                   + "".join(f'<tr><td><a href="/cases/{n}">{c}</a></td><td>{who}</td></tr>' for c, (n, who, _o, _r) in CASES.items()) + "</tbody></table>"),
    "/rates": html("Rates", "<h1>Rates</h1><table><thead><tr><th>Region</th><th>Decision</th></tr></thead>"
                   "<tbody><tr><td>North</td><td>Accept</td></tr><tr><td>South</td><td>Decline</td></tr></tbody></table>"),
}
for case, (num, who, order, region) in CASES.items():
    SITE_PAGES[f"/cases/{num}"] = html(case, f'<h1>Case {case}</h1><dl><dt>Customer</dt><dd>{who}</dd><dt>Order</dt><dd><a href="/orders/{order}">{order}</a></dd></dl>'
                                       '<form><label for="outcome">Outcome</label><select id="outcome" name="outcome"><option value="">Choose…</option>'
                                       '<option>Accept</option><option>Decline</option></select> <button id="save" type="submit">Save</button></form>')
    SITE_PAGES[f"/orders/{order}"] = html(order, f"<h1>Order {order}</h1><dl><dt>Region</dt><dd>{region}</dd></dl>")

NAV_TEXT = "Dashboard Cases Rates"
CASE_TEXT = f"{NAV_TEXT}\nCase CASE-101\nCustomer\nAsha Rao\nOrder\nORD-5001\nOutcome\nChoose…\nAccept\nDecline\nSave"
SITE_TRACE = [
    {"step": 1, "kind": "click", "url": SITE + "/", "goal": "Open the cases list.", "el": {"node": "a", "attrs": {"href": "/cases"}, "text": "Cases"},
     "page_text": f"{NAV_TEXT}\nDashboard\n2 cases waiting."},
    {"step": 2, "kind": "click", "url": SITE + "/cases", "goal": "Open case CASE-101.", "el": {"node": "a", "attrs": {"href": "/cases/40101"}, "text": "CASE-101"},
     "page_text": f"{NAV_TEXT}\nCases\nCase\tCustomer\nCASE-101\tAsha Rao\nCASE-202\tVikram Iyer"},
    {"step": 3, "kind": "select", "url": SITE + "/cases/40101", "goal": "Choose the outcome.", "value": "Accept",
     "el": {"node": "select", "attrs": {"id": "outcome", "name": "outcome"}, "text": "Choose… Accept Decline"}, "page_text": CASE_TEXT},
    {"step": 4, "kind": "click", "url": SITE + "/cases/40101", "goal": "Save.", "el": {"node": "button", "attrs": {"id": "save", "type": "submit"}, "text": "Save"}},
]
SITE_INSTRUCTIONS = "For each case: open the case's order and read its region. Look the region up on the Rates page; its decision is the outcome. Choose that outcome and save."
SITE_ANSWER = {"reads": [
    {"from_page": "/cases/*", "link_text": "ORD-5001", "per_record": True, "label": "Order", "why": "the order's region"},
    {"from_page": "/cases", "link_text": "Rates", "per_record": False, "label": "", "why": "the decision for each region"},
]}
prompts = {"slots": [], "form": []}


# A fake model for the plan: copies the slot from the record's own page, and decides from the order's region and the rates
# table when both are among the pages (otherwise it says what is missing, as the real prompt asks).
async def fake_plan_model(ctx, prompt, schema=None, temperature=0.0):
    record = re.search(r'### record "([^"]+)"', prompt).group(1)
    if schema is px._SlotAnswers:
        prompts["slots"].append(prompt)
        names = re.findall(r"^  - (s\d+): for a different record", prompt, re.M)
        order = re.search(r"\nOrder\n(ORD-\d+)", prompt)
        return {"records": [{"record": record, "values": [{"name": n, "value": order.group(1) if order else ""} for n in names]}]}
    prompts["form"].append(prompt)
    region = re.search(r"\nRegion\n(\w+)", prompt)
    rates = dict(re.findall(r"Region: (\w+) \| Decision: (\w+)", prompt))
    if not region or region.group(1) not in rates:
        return {"decisions": [{"record": record, "facts": "", "missing": "the order's region and the rates table", "checks": []}]}
    outcome = rates[region.group(1)]
    return {"decisions": [{"record": record, "facts": f"region {region.group(1)}", "missing": "", "checks": [
        {"rule": "the outcome is the region's decision", "known": True, "expr": "true", "values": [{"field": "outcome", "value": outcome}],
         "evidence": [f"Region: {region.group(1)} | Decision: {outcome}"]}]}]}


# Part 2: learns a plan on the small site, adds coverage, and runs it before and after in a headless browser.
async def part2():
    from playwright.async_api import async_playwright

    site_plan, site_why = compile_plan({"case_id": "CASE-101"}, SITE_TRACE)
    check("part 2: a plan is learned from the small site's trace", site_why == "ok" and [o.get("expect") for o in site_plan["gather"]] == ["/cases", "/cases/*"])
    site_pages = route_pages_from_trace(site_plan, SITE_TRACE)
    cov.ask_json = fake_coverage_site
    try:
        extended = copy.deepcopy(site_plan)
        added = await cover_route(RunContext(target_url=SITE, workflow_name="test", match_key_field="case_id", model_name="m", gemini_api_key="x",
                                             task_instructions=SITE_INSTRUCTIONS), extended, site_pages)
    finally:
        cov.ask_json = real_ask
    check("part 2: coverage adds the order (a record's own link) and the rates page", len(added) == 2 and route_of(extended["gather"]) == [
        ("click", "Cases", False), ("click", "Rates", True), ("revisit", 0, True), ("click", "{case_id}", False), ("click", "{s1}", True), ("revisit", 3, True)])

    requested: list[str] = []

    # Serves the small site's pages from memory (404 for anything else), noting each path asked for.
    async def serve(route):
        path = urlsplit(route.request.url).path
        requested.append(path)
        body = SITE_PAGES.get(path)
        await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8", body=body or "<p>Page not found</p>")

    ctx2 = RunContext(target_url=SITE, workflow_name="test", match_key_field="case_id", model_name="m", gemini_api_key="x", task_instructions=SITE_INSTRUCTIONS)
    px.ask_json = fake_plan_model
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                await context.route(re.compile(r"^http://desk\.test/"), serve)
                events: list[dict] = []

                page = await context.new_page()
                before = await px.run_plan(site_plan, {"case_id": "CASE-202"}, ctx=ctx2, page=page, emit=events.append, dry=True, key="CASE-202")
                check("part 2: before coverage the record cannot be decided (its order and the rates were never read)",
                      not before.ok and "do not hold what the rules need" in (before.error or "") and "/orders/ORD-7002" not in requested and "/rates" not in requested)

                requested.clear()
                events.clear()
                page = await context.new_page()
                after = await px.run_plan(extended, {"case_id": "CASE-202"}, ctx=ctx2, page=page, emit=events.append, dry=True, key="CASE-202")
                check("part 2: after coverage the plan opens this record's own order and the rates page", after.ok and "/orders/ORD-7002" in requested and "/rates" in requested
                      and "/orders/ORD-5001" not in requested)
                check("part 2: the order number is read off the record's page by the slot question", len(prompts["slots"]) == 1 and 'this was "ORD-5001"' in prompts["slots"][0]
                      and "the link next to Order" in prompts["slots"][0])
                check("part 2: the decision sees both pages and decides from them", after.planned == {"outcome": "Decline"} and "Region: South | Decision: Decline" in prompts["form"][-1]
                      and "Order ORD-7002" in prompts["form"][-1])
                check("part 2: each way back returns to the page the trip left, so the route goes on as learned",
                      requested.count("/cases") == 2 and requested.count("/cases/40202") == 2 and page.url == SITE + "/cases/40202")
                check("part 2: the added steps show up in the run's events", sum(1 for e in events if "(added from the instructions)" in str(e.get("field", ""))) == 4)
                check("part 2: nothing was saved in the dry run", not after.acted)
            finally:
                await browser.close()
    finally:
        px.ask_json = real_plan_ask


# A fake model that gives the small site's coverage answer.
async def fake_coverage_site(ctx, prompt, schema=None, temperature=0.0):
    return SITE_ANSWER


real_plan_ask = px.ask_json
if "--offline" in sys.argv:
    print("\nSKIPPED part 2 (--offline)")
else:
    asyncio.run(part2())

print(f"\n{checks} checks passed")
