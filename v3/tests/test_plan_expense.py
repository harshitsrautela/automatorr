"""Checks for the shapes the plan engine met on the Expense Desk (testsites/expense-desk, `node server.js`, port 5182): a single-page
app with hash routes, a type-to-filter search box, clickable table rows, tabs that keep the address, collapsed policy sections, and a
decision made in a three-step wizard (radio buttons, then fields that depend on the choice, then a confirmation checkbox), whose
controls get a new random id every time they are drawn.

    python tests/test_plan_expense.py              # add --offline to skip part 2 (it resets the site's decisions)

Part 1 needs nothing but the live agent's real trace (tests/fixtures/expense_trace.json, run #56 on 9 Oct 2026).
Part 2 runs plans in a real headless browser against the site with a FAKE model that answers from the site's expected decisions,
so it costs no model calls. It is skipped when the site is not running, and it resets the site's decisions."""

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
from recipe.plan import committed_values, compile_plan, shape_matches, url_shape, values_agree
from run_context import RunContext

HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures", "expense_trace.json")))
FIELDS, TRACE = FX["fields"], FX["trace"]
checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ part 1: compiling the real trace

plan, why = compile_plan(FIELDS, TRACE)
check("a plan is learned from the Expense Desk trace", plan is not None and why == "ok")
gather = plan["gather"]
ops = [(o["op"], (o.get("by") or [{}])[-1].get("name") or (o.get("by") or [{}])[-1].get("text"), o.get("value"), o.get("expect")) for o in gather]
check("the search box gets the record's claim number", ("fill", "Search by claim number or employee", "{claim_id}", None) in ops)
check("the result row (a plain table cell, no link) is found by its text, the claim number", any(o["op"] == "click" and o["by"] == [{"text": "{claim_id}"}] for o in gather))
check("the claim's page type has the record's random id as '*', even though 'e3lcxu' does not look like an id", ops[3][3] == "/#/claims/*")
check("a tab on the claim's page expects the claim's page type, not the first record's own address", ("click", "Receipts", None, "/#/claims/*") in ops)
check("  and is found as a tab (its own role attribute), not as a button", any(o.get("by") == [{"role": "tab", "name": "Receipts"}] for o in gather))
check("clicks that open the policy's collapsed sections are left out of the route (every page read opens them all)", [o[3] for o in ops].count("/#/policy") == 1 and not any(o[1] and o[1][0].isdigit() for o in ops))
# run #58: the agent clicked the <details> element itself (no text, id or role); that used to stop the whole compile
odd = [dict(TRACE[i], el={"node": "details", "attrs": {}, "text": "", "xpath": "x"}) if (TRACE[i].get("el") or {}).get("node") == "summary" else TRACE[i] for i in range(len(TRACE))]
check("a click on a collapsed section's box (no text, id or role) does not stop the plan being learned", compile_plan(FIELDS, odd)[1] == "ok")
check("going back to the list and searching again becomes one step back to the claim's own address", [o for o in ops if o[0] == "revisit"] == [("revisit", None, None, "/#/claims/*")] and gather[[o[0] for o in ops].index("revisit")]["to"] == 4)
check("the button that opens the decision box stays in the route (it does not change the address)", ops[-1][:2] == ("click", "Review claim"))
controls = plan["commit"]["controls"]
check("the wizard's fields are found by their names, not their random ids, and each knows its step",
      [(c["key"], c["type"], c["stage"]) for c in controls] == [("outcome", "radio", 0), ("payable", "number", 1), ("attest", "checkbox", 2)])
check("the two Next buttons are the steps of the form, not a custom widget", [o["by"] for o in plan["commit"]["advance"]] == [[{"role": "button", "name": "Next"}]] * 2 and not any(c["type"] == "widget" for c in controls))
check("the button that saves is Submit decision", [o["by"] for o in plan["commit"]["submit"]] == [[{"role": "button", "name": "Submit decision"}]])

live = committed_values(TRACE, {"outcome", "payable", "attest"})
check("what the live agent entered is read from its clicks too: the radio's label, the ticked box", live == {"outcome": "Approve", "payable": "23900", "attest": "I have checked this claim against the travel policy"})
kinds = {"outcome": "select", "payable": "other", "attest": "check", "missing": "checks", "message": "note"}
check("the plan's answers agree with those (a ticked box is 'yes')", values_agree({"outcome": "Approve", "payable": "23900", "attest": "yes"}, live, kinds)[0])
check("a box the plan would leave empty and the agent never touched agrees", values_agree({"outcome": "Approve", "attest": "no"}, {"outcome": "Approve"}, kinds)[0])
check("ticked boxes of a group agree in any order", values_agree({"missing": "Meals, Taxi"}, {"missing": "Taxi, Meals"}, kinds)[0])
check("  but not when a different box is ticked", not values_agree({"missing": "Meals"}, {"missing": "Hotel"}, kinds)[0])
check("an optional message one side left empty does not count", values_agree({"outcome": "Send back to employee", "message": ""}, {"outcome": "Send back to employee", "message": "Please add receipts"}, kinds)[0])
check("hash routes have page types; an id without digits is '*' only on the record's own page", url_shape("http://h/#/claims/kxqfab") == "/#/claims/kxqfab" and url_shape("http://h/#/claims/kxqfab", own_page=True) == "/#/claims/*" and shape_matches("http://h/#/claims/kxqfab", "/#/claims/*"))

# ---- run #59: the agent clicked the table ROW (<tr>: no text of its own, only random attributes)
from recipe.plan import element_locators

row = FX["row_click"]
row_plan, row_why = compile_plan(row["fields"], row["trace"])
check("a clicked table row (no name) is found as the row that contains the record's value, from the agent's goal for the step",
      row_why == "ok" and any(o.get("by") == [{"css": "tr", "contains": "{claim_id}"}] for o in row_plan["gather"]))
check("  a row recorded with the text inside it uses that text", element_locators({"node": "tr", "attrs": {"data-r": "x8k2p1"}, "text": "", "inner": "EXP-41142 Om Kulkarni Hyderabad 05 Sep 2026 28,900 Pending"}, {"claim_id": "EXP-41142"}, {}) == [{"css": "tr", "contains": "{claim_id}"}])
check("  a nameless element that holds nothing of the record gives no way to find it", element_locators({"node": "tr", "attrs": {}, "text": "", "inner": "Claim Employee Destination"}, {"claim_id": "EXP-41142"}, {}) == [])

# ---- 11 Oct: the live agent pressed Back inside the wizard; the plan made "Back" a field and lost 2 records
back = FX["back_click"]
back_plan, back_why = compile_plan(back["fields"], back["trace"])
check("a Back press inside the wizard is not a field: the steps stay right",
      back_why == "ok" and [(c["key"], c["stage"]) for c in back_plan["commit"]["controls"]] == [("outcome", 0), ("payable", 1), ("attest", 2)]
      and len(back_plan["commit"]["advance"]) == 2 and not any(c["type"] == "widget" for c in back_plan["commit"]["controls"]))

# ---- the fields of other branches, from the real proof records of run #57 (a deduction, a rejection)
import copy
from recipe.plan import learn_fields

OTHER = FX["other_branches"]
deduct, reject = OTHER["EXP-80905"], OTHER["EXP-36367"]
grown_plan = copy.deepcopy(plan)
check("an agreeing record's route teaches the plan the fields of its branch (the deduction, the hotel tick), on their step",
      learn_fields(grown_plan, deduct["fields"], deduct["trace"]) == ["deduction", "hotel_over_limit"] and all(c["stage"] == 1 for c in grown_plan["commit"]["controls"] if c["key"] in ("deduction", "hotel_over_limit")))
check("  and a rejection's route adds the reason; nothing is added twice", learn_fields(grown_plan, reject["fields"], reject["trace"]) == ["reason"] and learn_fields(grown_plan, deduct["fields"], deduct["trace"]) == [])
# the same through the orchestrator's proof step: a proof record that agrees teaches the plan its branch's fields
import orchestrator as orch

judged = copy.deepcopy(plan)
said = []
orch.recipe_store.save_recipe = lambda *a, **k: None
agreeing = px.PlanOutcome(ok=True, planned={"outcome": "Approve with deduction", "deduction": "490", "payable": "16870", "hotel_over_limit": "yes", "attest": "yes"},
                          kinds={"outcome": "select", "deduction": "other", "payable": "other", "hotel_over_limit": "check", "attest": "check"})
orch._judge_plan(judged, agreeing, deduct["trace"], "EXP-80905", "wf", "default", said.append, deduct["fields"])
check("a proof record that agrees counts, and the plan learns its branch's fields (the note says so)",
      judged["trust"]["ok"] == 1 and {"deduction", "hotel_over_limit"} <= {c["key"] for c in judged["commit"]["controls"]} and "now knows" in said[-1]["message"])
deduct_kinds = {"outcome": "select", "deduction": "other", "payable": "other", "hotel_over_limit": "check", "attest": "check"}
deduct_live = committed_values(deduct["trace"], set(deduct_kinds))
check("the deduction record's real live values agree with what the plan would enter",
      values_agree({"outcome": "Approve with deduction", "deduction": "490", "payable": "16870", "hotel_over_limit": "yes", "attest": "yes"}, deduct_live, deduct_kinds)[0])
check("  and a plan that forgot the hotel tick does not", not values_agree({"outcome": "Approve with deduction", "deduction": "490", "payable": "16870", "hotel_over_limit": "no", "attest": "yes"}, deduct_live, deduct_kinds)[0])

# ---- extending the plan when a record needed something the plan does not open
import orchestrator as orch
from data.models import Record

i_rec = next(i for i, e in enumerate(TRACE) if (e.get("el") or {}).get("text") == "Receipts")
tab = dict(TRACE[i_rec], el={**TRACE[i_rec]["el"], "text": "Activity"}, goal="Open the Activity tab", page_text=None)
with_activity = TRACE[: i_rec + 1] + [tab] + TRACE[i_rec + 1:]
no_policy = [e for e in TRACE if "/policy" not in (e.get("url") or "") and (e.get("el") or {}).get("text") != "Travel policy"]
notes = []
orch.recipe_store.save_trace = lambda *a, **k: None
orch.recipe_store.save_recipe = lambda *a, **k: None
rec = Record(record_id=FX["claim_id"], claim_id=FX["claim_id"])
orch._recipes.clear()
orch._recipes[("wf", "default")] = plan
orch._extend_plan(plan, rec, with_activity, "wf", "default", notes.append)
grown = orch._recipes[("wf", "default")]
check("a live route that also opened a tab the plan never opens (same address) extends the plan", grown is not plan and "/#/claims/* > Activity" in orch._plan_pages(grown))
orch._recipes[("wf", "default")] = plan
orch._extend_plan(plan, rec, [*no_policy[:3], tab, *no_policy[3:]], "wf", "default", notes.append)
check("  but a route that reaches something new while skipping what the plan reads (the policy) does not replace it", orch._recipes[("wf", "default")] is plan)
orch._recipes.clear()

# the model's answers for radios and checkboxes are checked against the choices on the form
ctl = [{"key": "outcome", "tag": "input", "type": "radio", "label": "Outcome", "visible": True, "options": ["Approve", "Approve with deduction", "Send back to employee", "Reject"]},
       {"key": "missing", "tag": "input", "type": "checkbox", "label": "Receipts missing for", "visible": True, "options": ["Hotel", "Meals", "Taxi", "Flight", "Train"]},
       {"key": "attest", "tag": "input", "type": "checkbox", "label": "I have checked", "visible": False, "options": [], "later": True}]
ans = {"facts": "", "missing": "", "checks": [{"rule": "r", "known": True, "expr": "true", "values": [{"field": "outcome", "value": "send back to employee"}, {"field": "missing", "value": "taxi and Meals"}, {"field": "attest", "value": "true"}]}]}
check("a radio answer becomes the option's own label; checkbox answers become sorted labels and yes/no", px._values_from_answer(ans, ctl, {})[0] == {"outcome": "Send back to employee", "missing": "Meals, Taxi", "attest": "yes"})
bad = {"facts": "", "missing": "", "checks": [{"rule": "r", "known": True, "expr": "true", "values": [{"field": "missing", "value": "Hotel, Parking"}]}]}
try:
    px._values_from_answer(bad, ctl, {})
    refused = False
except px.PlanError:
    refused = True
check("a box that is not on the form is refused", refused)

# ---- real-world pages, from a small made-up trace in the recorder's format: a sign-in, a cookie banner, a pop-up, a list
# grown by "Load more", a clickable row, a side trip to a linked page, and a form of custom controls (an ARIA radio group, a
# searchable combobox, a contenteditable note) whose ids are random
from recipe.plan import PLAN_VERSION, trace_key

H = "http://127.0.0.1:5183"
LOGIN, LIST, REC, POL = H + "/login?next=%2Fclaims", H + "/claims", H + "/claims/CLM-26-04123", H + "/policies/POL-55102"
HF = {"claim_no": "CLM-26-04123"}
HINS = "Sign in as desk.auditor with the password audit-2026. Decline a claim whose policy is not Active (reason: No cover on loss date)."
RADIO = {"node": "div", "attrs": {"role": "radio", "id": "r-k3j9x2a", "aria-checked": "false"}, "text": "Decline", "xpath": "html/body/form/div[1]/div/div[3]", "group": "Outcome"}
COMBO = {"node": "input", "attrs": {"role": "combobox", "name": "reason", "id": "c-q8w2e7r", "aria-autocomplete": "list", "type": "text"}, "text": "Decline reason", "xpath": "html/body/form/div[2]/div/input"}
NOTE = {"node": "div", "attrs": {"contenteditable": "true", "role": "textbox", "id": "n-x8k2p1q"}, "text": "Internal note", "xpath": "html/body/form/div[3]/div"}


def ev(kind, url, el=None, value=None, text=None, goal=""):
    e = {"step": 0, "goal": goal, "kind": kind, "url": url, "el": el}
    if value is not None:
        e["value"] = value
    return dict(e, page_text=text) if text else e


def button(name, **attrs):
    return {"node": "button", "attrs": {"type": "button", **attrs}, "text": name, "xpath": "x/" + name}


def hazard_trace(radio=RADIO, combo_value="No cover"):
    return [
        ev("click", LOGIN, button("Accept all"), text="Sign in\nUsername\nPassword\nWe use cookies. Accept all"),
        ev("input", LOGIN, {"node": "input", "attrs": {"name": "username", "type": "text"}, "text": "Username", "xpath": "u"}, "desk.auditor"),
        ev("input", LOGIN, {"node": "input", "attrs": {"name": "password", "type": "password"}, "text": "Password", "xpath": "p"}, "audit-2026"),
        ev("click", LOGIN, button("Sign in", type="submit")),
        ev("click", LIST, button("Got it"), text="Claims\nCLM-26-04001 Fire 02 Sep 2026\nWhat's new"),
        ev("click", LIST, button("Retry")),
        ev("click", LIST, button("Load more")),
        ev("click", LIST, {"node": "tr", "attrs": {"data-r": "x8k2p1"}, "text": "", "inner": "CLM-26-04123 Theft 12 Sep 2026 ₹45,000", "xpath": "tr"}, goal="open claim CLM-26-04123"),
        ev("click", REC, {"node": "a", "attrs": {"href": "/policies/POL-55102"}, "text": "POL-55102", "xpath": "a"}, text="Claim CLM-26-04123\nPeril\nTheft\nPolicy\nPOL-55102"),
        ev("back", POL, text="Policy POL-55102\nStatus\nLapsed"),
        ev("click", REC, radio),
        ev("click", REC, COMBO),
        ev("input", REC, COMBO, combo_value),
        ev("click", REC, {"node": "div", "attrs": {"role": "option", "id": "o-z7y6x5w"}, "text": "No cover on loss date", "xpath": "html/body/div[9]/div/div[1]"}),
        ev("input", REC, NOTE, "Policy lapsed before the loss"),
        ev("click", REC, button("Submit decision", type="submit")),
    ]


HT = hazard_trace()
hz, hz_why = compile_plan(HF, HT, HINS)
check("a plan is learned from a route with a sign-in, pop-ups, a grown list and custom controls", hz is not None and hz_why == "ok" and hz["version"] == PLAN_VERSION == 4)
hg = hz["gather"]
names = [((o.get("by") or [{}])[-1].get("name") or (o.get("by") or [{}])[-1].get("text") or (o.get("by") or [{}])[-1].get("contains"), o["op"]) for o in hg]
check("typed values written word for word in the instructions are constants (the username and the password)",
      [o.get("value") for o in hg if o["op"] == "fill"] == ["desk.auditor", "audit-2026"] and not hz["slots"].get("s2"))
check("the sign-in is kept as the plan's login: its page type and its steps up to the click that left the page",
      hz["login"]["page"] == "/login?next" and [o["op"] for o in hz["login"]["steps"]] == ["fill", "fill", "click"]
      and hz["login"]["steps"][-1]["by"][-1].get("name") == "Sign in" and hz["login"]["steps"][-1]["expect"] == "/claims")
check("  the cookie banner's button is not part of the login (it shows only once)", not any(o.get("optional") for o in hz["login"]["steps"]))
check("clicks that close a cookie banner or a pop-up, and a Retry, are optional steps", [n for n, o in zip(names, hg) if o.get("optional")] == [("Accept all", "click"), ("Got it", "click"), ("Retry", "click")])
check("  and no other step is optional", sum(1 for o in hg if o.get("optional")) == 3)
check("'Load more' is paging: it is not a step, and the row found after it is found by paging", not any(n[0] == "Load more" for n in names)
      and [o.get("paged") for o in hg if o.get("by") == [{"css": "tr", "contains": "{claim_no}"}]] == [True])
check("the step found by the record's own value is marked as landing on the record's own page, and only that one",
      [o.get("by") for o in hg if o.get("own")] == [[{"css": "tr", "contains": "{claim_no}"}]])
check("the side trip to the policy comes back to the record's page", [o["op"] for o in hg][-2:] == ["click", "revisit"] and hg[-1]["to"] == [o.get("own") for o in hg].index(True))
hc = hz["commit"]["controls"]
check("the ARIA radio group is one control keyed by its group's name, not by the option's random id", hc[0] == {"key": "aria:Outcome", "node": "input", "type": "radio", "label": "Outcome", "value": {"computed": True}, "stage": 0})
check("the combobox with the option picked in it is ONE control (not a widget, not a typed field)", hc[1]["key"] == "reason" and hc[1]["type"] == "combobox" and hc[1]["label"] == "Decline reason" and hc[1]["node"] == "select")
check("the contenteditable note is a rich-text control keyed by its label (its id is random)", hc[2] == {"key": "edit:Internal note", "node": "textarea", "type": "richtext", "label": "Internal note", "value": {"computed": True}, "stage": 0})
check("  nothing else is a control, and the button that saves is Submit decision", len(hc) == 3 and [o["by"][-1].get("name") for o in hz["commit"]["submit"]] == ["Submit decision"])
hv = committed_values(HT, {c["key"] for c in hc})
check("the live agent's values use the same keys: the ARIA option's text, the option picked in the combobox, the note",
      hv == {"aria:Outcome": "Decline", "reason": "No cover on loss date", "edit:Internal note": "Policy lapsed before the loss"})
check("  and agree with what a plan would enter", values_agree({"aria:Outcome": "Decline", "reason": "No cover on loss date"}, hv, {"aria:Outcome": "select", "reason": "select", "edit:Internal note": "note"})[0])
no_ins, no_ins_why = compile_plan(HF, HT)
check("without the instructions a password typed from nowhere is refused, with a reason that says so", no_ins is None and "password" in no_ins_why)
nameless = compile_plan(HF, hazard_trace(radio={**RADIO, "group": None}), HINS)
check("an ARIA radio button in no named group is refused, with a reason that says so", nameless[0] is None and "no named group" in nameless[1])
stable = compile_plan(HF, hazard_trace(radio={**RADIO, "attrs": {"role": "radio", "id": "outcome-decline"}}), HINS)[0]
check("  an ARIA radio button is keyed by its group even when its own id looks stable", stable["commit"]["controls"][0]["key"] == "aria:Outcome")
searched_plan = compile_plan(HF, hazard_trace(combo_value="CLM-26-04123"), HINS)[0]
check("a combobox typed into with the record's own value (a search) is not a choice control", searched_plan is None or not any(c["type"] == "combobox" for c in searched_plan["commit"]["controls"]))
check("trace_key: a combobox without a name or a stable id is 'combo:' + its label; a rich-text box with a stable id keeps it",
      trace_key({**COMBO, "attrs": {"role": "combobox", "id": "c-q8w2e7r"}}) == "combo:Decline reason" and trace_key({**NOTE, "attrs": {"contenteditable": "", "id": "note"}}) == "note")
boxes = [ev("click", REC, {"node": "div", "attrs": {"role": "checkbox", "id": f"b-{t[:3].lower()}9x8k2"}, "text": t, "xpath": "b/" + t, "group": "Documents seen:", "group_of_boxes": True}) for t in ("Police report", "Photos")]
box_trace = HT[:10] + boxes + HT[-1:]
box_plan, box_why = compile_plan(HF, box_trace, HINS)
check("ARIA checkboxes of one group are one control keyed by the group's name (trailing ':' dropped)", box_why == "ok" and [(c["key"], c["type"]) for c in box_plan["commit"]["controls"]] == [("aria:Documents seen", "checkbox")])
check("  and the live agent's ticks are its sorted labels", committed_values(box_trace, {"aria:Documents seen"}) == {"aria:Documents seen": "Photos, Police report"})
check("instructions only make constants of typed values written there word for word", compile_plan(HF, HT, HINS.replace("audit-2026", "audit-2027"))[0] is None)

# the recorder names the group a clicked choice belongs to, from the page's own elements and their parents
from types import SimpleNamespace as NS

from recipe.trace import _group_name, _role_of

group_box = NS(attributes={"role": "radiogroup", "aria-labelledby": "lbl"}, ax_node=NS(role="radiogroup", name="Outcome"), parent_node=None)
plain = NS(attributes={"class": "radios"}, ax_node=None, parent_node=group_box)
choice = NS(attributes={"role": "radio"}, ax_node=NS(role="radio", name="Decline"), parent_node=plain)
check("the recorder finds a choice's group by walking up its parents (role radiogroup, its accessible name)", _role_of(choice) == "radio" and _group_name(choice) == "Outcome")
check("  an aria-label names a group the accessibility tree gives no name", _group_name(NS(parent_node=NS(attributes={"role": "group", "aria-label": "Documents"}, ax_node=None, parent_node=None))) == "Documents")
check("  a choice in no group has none", _group_name(NS(parent_node=NS(attributes={}, ax_node=NS(role="generic", name=""), parent_node=None))) is None)

# ---- review of step I1: values the instructions name on the form, rows named by another field, "View more" links, list
# filters in a combobox, a sign-in that keeps its address, lone switches
from recipe.plan import element_locators as _locs
from recipe.trace import _group_of_boxes

CL, CR = H + "/claims", H + "/claims/C-1001"
CF = {"claim_no": "C-1001"}
C_ROW = {"node": "a", "attrs": {"href": "/claims/C-1001"}, "text": "C-1001", "xpath": "a1"}
C_REASON = {"node": "textarea", "attrs": {"name": "reason"}, "text": "Reason", "xpath": "t"}
C_REJECT = {"node": "input", "attrs": {"type": "radio", "name": "outcome", "value": "reject"}, "text": "Reject", "xpath": "r2"}
C_SAVE = button("Save", type="submit")
C_INS = "Approve when the policy is active and enter reason 'Policy active'. Otherwise reject with reason 'Policy lapsed'."
reason_first = [ev("click", CL, C_ROW, text="Claims C-1001"), ev("input", CR, C_REASON, "Policy lapsed", text="Claim C-1001"), ev("click", CR, C_REJECT), ev("click", CR, C_SAVE)]
rf_plan, rf_why = compile_plan(CF, reason_first, C_INS)
check("a value the instructions name, typed first on the decision form, is decided for every record (not a constant of the route)",
      rf_why == "ok" and [o["op"] for o in rf_plan["gather"]] == ["click"] and [c["key"] for c in rf_plan["commit"]["controls"]] == ["reason", "outcome"])
check("  and the live agent's reason is compared with the plan's", committed_values(reason_first, {"reason", "outcome"}) == {"reason": "Policy lapsed", "outcome": "Reject"})
check("  a value the instructions name on a page the route leaves (the username) is still a constant",
      [o.get("value") for o in hz["gather"] if o["op"] == "fill"] == ["desk.auditor", "audit-2026"])
wizard = [ev("click", CL, C_ROW, text="Claims"), ev("input", CR, C_REASON, "Policy lapsed"), ev("click", CR, button("Next")),
          ev("click", CR + "/step2", C_REJECT), ev("click", CR + "/step2", C_SAVE)]
wz = compile_plan(CF, wizard, C_INS)[0]
check("  so is one on the first step of a form whose next step has its own address (the page is only left inside the form)",
      wz is None or not any(o["op"] == "fill" for o in wz["gather"]))

check("a clickable row is found by the record's value the agent's goal names, not by the first field printed in it",
      _locs({"node": "tr", "attrs": {}, "text": "", "inner": "Acme Corp INV-1001 12 Sep 2026 $500"}, {"vendor": "Acme Corp", "invoice_no": "INV-1001"}, {}, hint="open invoice INV-1001")
      == [{"css": "tr", "contains": "{invoice_no}"}])
check("  without a goal, by the value that looks like an id (a vendor's name is on many rows)",
      _locs({"node": "tr", "attrs": {}, "text": "", "inner": "Acme Corp INV-1001 12 Sep 2026 $500"}, {"vendor": "Acme Corp", "invoice_no": "INV-1001"}, {})
      == [{"css": "tr", "contains": "{invoice_no}"}])

DET = CR + "/details"
details = [ev("click", CL, C_ROW, text="Claims C-1001"), ev("click", CR, {"node": "a", "attrs": {"href": "/claims/C-1001/details"}, "text": "View more details", "xpath": "vm"}, text="Claim C-1001"),
           ev("click", DET, {**C_REJECT}, text="Details"), ev("click", DET, C_SAVE)]
dp = compile_plan(CF, details, "")[0]
check("a 'View more details' link that opens another page is a step of the route, not a pager",
      dp is not None and [(o["by"][-1].get("name"), o.get("expect")) for o in dp["gather"]] == [("{claim_no}", "/claims/*"), ("View more details", "/claims/*/*")])
check("  and 'Show more details' is not a list's Load more", not __import__("recipe.plan", fromlist=["_is_pager"])._is_pager(ev("click", CR, button("Show more details")), CR))

STATUS = {"node": "input", "attrs": {"role": "combobox", "name": "status", "aria-autocomplete": "list"}, "text": "Status", "xpath": "st"}
OPEN = {"node": "div", "attrs": {"role": "option"}, "text": "Open", "xpath": "op"}
filt = [ev("click", CL, STATUS, text="Claims C-1001"), ev("input", CL, STATUS, "Op"), ev("click", CL, OPEN), ev("click", CL, C_ROW), ev("click", CR, C_REJECT, text="Claim C-1001"), ev("click", CR, C_SAVE)]
fp, fwhy = compile_plan(CF, filt, "Filter the list by status Open.")
check("a combobox used as the list's filter, with the option the instructions name, is steps of the route (open it, click the option)",
      fwhy == "ok" and [(o["op"], o["by"][-1].get("name") or o["by"][-1].get("value")) for o in fp["gather"]] == [("click", "Status"), ("click", "Open"), ("click", "{claim_no}")]
      and [c["key"] for c in fp["commit"]["controls"]] == ["outcome"])
check("  a filter whose option the instructions do not name is never replayed as a fixed click", compile_plan(CF, filt, "")[0] is None or not any(
      o["by"][-1].get("name") == "Open" for o in compile_plan(CF, filt, "")[0]["gather"]))
on_form = [ev("click", CL, C_ROW, text="Claims"), ev("click", CR, COMBO), ev("click", CR, {"node": "div", "attrs": {"role": "option"}, "text": "No cover on loss date", "xpath": "o"}), ev("click", CR, C_SAVE)]
ofp = compile_plan(CF, on_form, HINS)[0]
check("  a combobox on the form whose option the instructions name stays a decided control", ofp is not None and [(c["key"], c["type"]) for c in ofp["commit"]["controls"]] == [("reason", "combobox")])

APP = H + "/app"
spa = [ev("input", APP, {"node": "input", "attrs": {"name": "user"}, "text": "User", "xpath": "u"}, "desk", text="Sign in"),
       ev("input", APP, {"node": "input", "attrs": {"name": "pw", "type": "password"}, "text": "Password", "xpath": "p"}, "pw-123"),
       ev("click", APP, button("Sign in", type="submit")),
       ev("input", APP, {"node": "input", "attrs": {"name": "q", "type": "search"}, "text": "Search", "xpath": "q"}, "C-1001"),
       ev("keys", APP, None, "Enter"), ev("click", APP, C_ROW), ev("click", CR, C_REJECT, text="Claim C-1001"), ev("click", CR, C_SAVE)]
sp, spwhy = compile_plan(CF, spa, "Sign in as desk with password pw-123")
check("a sign-in that keeps the address ends at its Sign in click; the record's search and row are not part of it",
      spwhy == "ok" and [(o["op"], o.get("value")) for o in sp["login"]["steps"]] == [("fill", "desk"), ("fill", "pw-123"), ("click", None)]
      and not any(o.get("bound") for o in sp["login"]["steps"]))

check("a lone ARIA switch is keyed by its own name, as the run-time reader keys it", trace_key({"node": "div", "attrs": {"role": "switch"}, "text": "Urgent", "group": "Decision"}) == "aria:Urgent")
check("  boxes of a group that holds only boxes are keyed by the group; a radio always by its group",
      trace_key({"node": "div", "attrs": {"role": "checkbox"}, "text": "Photos", "group": "Docs", "group_of_boxes": True}) == "aria:Docs"
      and trace_key({"node": "div", "attrs": {"role": "radio"}, "text": "Yes", "group": "Decision"}) == "aria:Decision")
switches = [ev("click", CL, C_ROW, text="Claims")] + [ev("click", CR, {"node": "div", "attrs": {"role": "switch"}, "text": t, "xpath": "s/" + t, "group": "Decision"}) for t in ("Urgent", "Escalate")] + [ev("click", CR, C_SAVE)]
swp = compile_plan(CF, switches, "")[0]
check("  two lone switches in one mixed group are two controls", swp is not None and [(c["key"], c["label"]) for c in swp["commit"]["controls"]] == [("aria:Urgent", "Urgent"), ("aria:Escalate", "Escalate")])
check("  and the live agent's ticks use those keys", committed_values(switches, {"aria:Urgent", "aria:Escalate"}) == {"aria:Urgent": "Urgent", "aria:Escalate": "Escalate"})
_box = lambda t: NS(node_name="DIV", attributes={"role": "checkbox"}, ax_node=None, children_nodes=[NS(node_name="#text", attributes={}, ax_node=None, children_nodes=None)])
_only = NS(node_name="DIV", attributes={"role": "group", "aria-label": "Docs"}, ax_node=None, parent_node=None, children_nodes=[_box("a"), _box("b")])
_mixed = NS(node_name="FIELDSET", attributes={"role": "group", "aria-label": "Decision"}, ax_node=None, parent_node=None,
            children_nodes=[_box("a"), _box("b"), NS(node_name="TEXTAREA", attributes={}, ax_node=None, children_nodes=None)])
check("the recorder notes a group that holds only boxes (two or more), and not one that also holds another field",
      _group_of_boxes(NS(parent_node=_only)) and not _group_of_boxes(NS(parent_node=_mixed)) and not _group_of_boxes(NS(parent_node=NS(**{**_only.__dict__, "children_nodes": [_box("a")]}))))

# ------------------------------------------------------------------ part 2: in a real browser (fake model)

SITE = "http://localhost:5182"
try:
    if "--offline" in sys.argv:
        raise RuntimeError("asked to stay offline")  # part 2 resets the site: never run it while a real run is using the site
    urllib.request.urlopen(SITE + "/admin/state", timeout=2)
except Exception:
    print(f"\nSKIPPED part 2 (--offline, or the Expense Desk site is not running on {SITE})")
    print(f"\n{checks} checks passed")
    sys.exit(0)

EXPECTED = json.load(open(site_path("expense-desk", "expected.json")))
SEED5 = json.load(open(site_path("expense-desk", "data/seed.json")))
CLAIM5 = {c["claim_no"]: c for c in SEED5["claims"]}
GRADE5 = {e["id"]: e["grade"] for e in SEED5["employees"]}
LABEL = {"approve": "Approve", "deduct": "Approve with deduction", "sendback": "Send back to employee", "reject": "Reject"}
asked: list[list[str]] = []  # the fields each form question asked about


# The amounts a careful model writes, as formulas of the numbers printed on the claim's pages (the total claimed, the advance,
# the hotel line, the exchange rate and the nightly limit), never as numbers it worked out itself (grounding refuses those).
# Takes a claim number; returns {"payable": formula, "deduction": formula}.
def amount_formulas(claim_no):
    from datetime import date

    c = CLAIM5[claim_no]
    pol = SEED5["policy"]
    total = sum(l["amount"] * pol["rates"][l["currency"]] for l in c["lines"])
    tier = next(t for t, cities in pol["tiers"].items() if c["city"] in cities)
    nights = (date.fromisoformat(c["trip_end"]) - date.fromisoformat(c["trip_start"])).days
    nightly = pol["limits"][tier][GRADE5[c["employee_id"]]]
    hotel = " + ".join(f"{l['amount']}*{pol['rates'][l['currency']]}" for l in c["lines"] if l["category"] == "Hotel") or "0"
    deduction = f"(({hotel}) - {nightly}*{nights})"
    return {"payable": f"{{= {total} - {deduction} - {c['advance']} }}" if EXPECTED[claim_no].get("deduction") is not None else f"{{= {total} - {c['advance']} }}",
            "deduction": f"{{= {deduction} }}"}


async def fake_ask_json(ctx, prompt, schema=None, temperature=0.0):
    assert schema is px._FormDecisions, f"this plan copies nothing from pages, yet asked {schema} {prompt[:300]}"
    decisions = []
    for block in re.split(r'^### record "', prompt, flags=re.M)[1:]:
        key = block.split('"', 1)[0]
        e = EXPECTED[re.search(r"claim_id = (EXP-\d+)", block).group(1)]
        keys = re.findall(r"^  - (\w+): label", block, flags=re.M)
        asked.append(keys)
        claim_no = re.search(r"claim_id = (EXP-\d+)", block).group(1)
        sums = amount_formulas(claim_no)
        answer = {"outcome": LABEL[e["outcome"]], "payable": sums["payable"] if e.get("payable") is not None else "", "deduction": sums["deduction"] if e.get("deduction") is not None else "", "hotel_over_limit": "yes",
                  "missing": ", ".join(e.get("missing", [])), "reason": e.get("reason", ""), "attest": "yes", "message": ""}
        values = [{"field": k, "value": answer[k]} for k in keys if answer.get(k)]
        decisions.append({"record": key, "facts": "from the pages", "missing": "", "checks": [{"rule": "otherwise: the one that applies", "known": True, "expr": "true", "values": values}]})
    return {"decisions": decisions}


px.ask_json = fake_ask_json
ctx = RunContext(target_url=SITE, workflow_name="test", match_key_field="claim_id", model_name="m", gemini_api_key="x", task_instructions="the task")


def state():
    return json.load(urllib.request.urlopen(SITE + "/admin/state"))


def reset():
    urllib.request.urlopen(urllib.request.Request(SITE + "/admin/reset", method="POST"))


from playwright.async_api import async_playwright

pick = {}
for claim, e in EXPECTED.items():
    pick.setdefault(e["outcome"] + ("2" if len(e.get("missing", [])) > 1 else ""), claim)


async def run(claim, context, *, dry=False, plan=None, **kw):
    page = await context.new_page()
    await page.goto(SITE + "/")
    out = await px.run_plan(plan or compile_plan(FIELDS, TRACE)[0], {"claim_id": claim}, ctx=ctx, page=page, emit=lambda e: None, dry=dry, key=claim, **kw)
    text = await px._quick_read(page)
    await page.close()
    return out, text


async def part2():
    reset()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        # collapsed sections are read in full
        page = await context.new_page()
        await page.goto(SITE + "/#/policy")
        text = await px._quick_read(page)
        await page.close()
        check("a page's collapsed sections are opened and read (the exchange rates the first record never opened)", "Rupees per unit" in text and "International" in text)

        # the row locator finds exactly the record's row, as a whole word, after the search box filtered the list
        page = await context.new_page()
        await page.goto(SITE + "/#/claims")
        claim = sorted(EXPECTED)[0]
        await page.get_by_placeholder("Search by claim number or employee").fill(claim[:-1])  # several claims share this prefix
        await asyncio.sleep(1.2)
        found = await px._resolve(page, [{"css": "tr", "contains": "{claim_id}"}], {"claim_id": claim}, {}, wait=True)
        hit = found is not None and claim in await found.inner_text()
        await page.close()
        check("the row that contains the record's number is found as a whole word, not a longer number that starts with it", hit)

        # a dry run decides at the wizard's first step (the later steps' known fields too) and never presses its Next, which may
        # save a step on the site (15 Oct review: a wizard's Next pressed in a dry run saved step 1 before the live agent ran)
        no = pick["deduct"]
        asked.clear()
        out, _t = await run(no, context, dry=True)
        e = EXPECTED[no]
        check("a dry run of the wizard decides at its first step and returns the values it would enter, the later steps' too" if out.ok else f"dry run failed: {out.error}",
              out.ok and not out.acted and out.planned.get("outcome") == "Approve with deduction" and out.planned.get("attest") == "yes")
        check("  one question (two voting passes) decides the first step and the fields the first record filled on later steps; Next is never pressed",
              len(asked) == 2 and all(set(a) == {"outcome", "payable", "attest"} for a in asked) and "deduction" not in out.planned)
        check("  a dry run saves nothing", state()[no] is None)

        # 13 Oct, run #72: a send-back claim failed on the wizard's second step (the model's answers gave no values), after Next had been
        # pressed, and the record was lost because every Next counted as acting. Now a Next only counts when a change request went out
        no = pick["deduct"]
        real_ask = px.ask_json
        counter = []

        async def empty_after_first(ctx_, prompt, schema=None, temperature=0.0):
            counter.append(1)
            if len(counter) <= 2:  # the first question (two voting passes) is answered normally
                return await real_ask(ctx_, prompt, schema, temperature)
            blocks = re.split(r'^### record "', prompt, flags=re.M)[1:]
            return {"decisions": [{"record": b.split('"', 1)[0], "facts": "", "missing": "", "checks": [{"rule": "r", "known": True, "expr": "true", "values": []}]} for b in blocks]}

        px.ask_json = empty_after_first
        try:
            counter.clear()
            out, _t = await run(no, context, mutations=lambda: 0)
            check("a wizard step that fails after Next, with no change request sent, leaves the record not acted (the live agent can still do it)",
                  (not out.ok) and (not out.acted) and bool(out.error) and out.steps >= 2 and state()[no] is None)
            counter.clear()
            out, _t = await run(no, context)
            check("  without the counter every Next still counts as acting, as before", (not out.ok) and out.acted and state()[no] is None)
            ticks = {"n": 0}

            def rising():
                ticks["n"] += 1
                return ticks["n"]

            counter.clear()
            out, _t = await run(no, context, mutations=rising)
            check("  a counter that has risen by the time Next has been pressed (a change request went out) means acted", (not out.ok) and out.acted and state()[no] is None)
        finally:
            px.ask_json = real_ask
        reset()

        # real runs of each kind of outcome
        for name in ("approve", "deduct", "sendback", "sendback2", "reject"):
            claim = pick.get(name)
            if claim is None:
                continue
            out, text = await run(claim, context)
            got, e = state()[claim], EXPECTED[claim]
            good = out.ok and out.acted and got is not None and got["outcome"] == e["outcome"] and got.get("payable") == e.get("payable") and got.get("deduction") == e.get("deduction") and got.get("missing") == e.get("missing") and got.get("reason") == e.get("reason")
            check(f"{name}: the plan searches, opens the row, reads the tabs and policy, and goes through the wizard ({claim}: {e})" if good else f"{name} FAILED: {out.error} {got} {text[:200]}", good)

        # ---- a decision program decides every kind of claim, through all three steps of the wizard, with no model call
        reset()
        programmed = compile_plan(FIELDS, TRACE)[0]
        learn_fields(programmed, deduct["fields"], deduct["trace"])
        learn_fields(programmed, reject["fields"], reject["trace"])
        # the fields of a send-back, as a send-back record's route would teach them (the run-time reader finds their options)
        programmed["commit"]["controls"].append({"key": "missing", "node": "input", "type": "checkbox", "label": "Receipts missing for", "value": {"computed": True}, "stage": 1})
        programmed["program"] = expense_program()
        foreign = next(n for n, e in EXPECTED.items() if e["outcome"] == "deduct" and any(l["category"] == "Hotel" and l["currency"] != "INR" for l in CLAIM5[n]["lines"]))
        local = next(n for n, e in EXPECTED.items() if e["outcome"] == "deduct" and all(l["currency"] == "INR" for l in CLAIM5[n]["lines"] if l["category"] == "Hotel"))
        claims = [pick["approve"], local, foreign, pick["sendback"], pick.get("sendback2") or pick["sendback"], pick["reject"]]
        asked.clear()
        results = []
        for claim in claims:
            out, _t = await run(claim, context, plan=programmed, program_mode="use")
            results.append((claim, out))
        got = state()
        right = [c for c, out in results if out.ok and out.decided_by == "program" and got[c] and all(got[c].get(k) == EXPECTED[c].get(k) for k in ("outcome", "payable", "deduction", "missing", "reason"))]
        check("a decision program decides approvals, deductions (a hotel in rupees and one abroad), send-backs and a rejection, through the wizard"
              if len(right) == len(claims) else f"program FAILED: {[(c, o.error, o.decided_by, got[c]) for c, o in results if c not in right]}", len(right) == len(claims))
        check("  with zero model calls", asked == [])
        other = next(n for n, e in EXPECTED.items() if e["outcome"] == "deduct" and n not in claims)
        shadow = await shadow_check(context, programmed, other)
        check("  a dry run never lets the program decide, but says whether it agreed with the model (program_check)" if shadow is True else f"shadow FAILED: {shadow}", shadow is True)
        await browser.close()
    reset()


# Runs a dry run with the program beside the model on one claim. Takes the context, the plan and the claim; returns True
# when the model decided, the program's verdict on the same claim agreed, and nothing was saved (else what happened).
async def shadow_check(context, plan, claim):
    out, _t = await run(claim, context, plan=plan, program_mode="use", dry=True)
    good = out.ok and out.decided_by == "model" and out.program_check is not None and out.program_check[1] is True and state()[claim] is None
    return True if good else (out.ok, out.error, out.decided_by, out.program_check, out.planned)


# A hand-written decision program for the Expense Desk's rules: facts read by label from the claim's summary, from its receipts
# table and from the policy's tables (the city's tier, the nightly limit for the tier and the grade, the exchange rate), and
# the rules in order, trusted for every kind of decision (as if each had agreed twice).
def expense_program():
    label = lambda name, text, kind="number": {"name": name, "type": kind, "source": {"kind": "label", "label": text}}
    receipts = lambda name, kind, category_op, column, aggregate: {"name": name, "type": kind, "source": {
        "kind": "table", "where": [category_op], "column": column, "aggregate": aggregate}}
    hotel = {"column": "Category", "op": "eq", "value": "Hotel"}
    policy = lambda name, kind, where, column: {"name": name, "type": kind, "source": {"kind": "table", "page": "/#/policy", "where": [where], "column": column, "aggregate": "first"}}
    deducted = {"outcome": "Approve with deduction", "hotel_over_limit": "yes", "attest": "yes"}
    program = {"version": 1, "facts": [
        label("days", "Days from trip end to submission"), label("nights", "Nights"), label("total", "Total claimed (in rupees)"), label("advance", "Advance paid"),
        label("grade", "Grade", "text"), label("city", "Destination", "text"),
        receipts("missing_count", "number", {"column": "Receipt", "op": "eq", "value": "Missing"}, "Category", "count"),
        receipts("missing", "list", {"column": "Receipt", "op": "eq", "value": "Missing"}, "Category", "list"),
        receipts("hotels", "number", hotel, "Category", "count"), receipts("hotel_amount", "number", hotel, "Amount", "sum"),
        receipts("hotel_currency", "text", hotel, "Currency", "first"),
        policy("rate", "number", {"column": "Currency", "op": "eq", "value": "{hotel_currency}"}, "Rupees per unit"),
        policy("tier", "text", {"column": "Cities", "op": "contains", "value": "{city}"}, "Tier"),
        policy("nightly", "number", {"column": "City tier", "op": "eq", "value": "{tier}"}, "{grade}")],
        "rules": [
            {"name": "late: reject", "when": "days > 45", "set": {"outcome": "Reject", "reason": "Late submission", "attest": "yes"}},
            {"name": "missing receipts: send back", "when": "missing_count > 0", "set": {"outcome": "Send back to employee", "missing": "{missing}", "attest": "yes"}},
            {"name": "hotel in rupees over the limit: deduct", "when": "hotels > 0 and hotel_currency == 'INR' and hotel_amount > nightly * nights",
             "set": {**deducted, "deduction": "{= hotel_amount - nightly * nights }", "payable": "{= total - (hotel_amount - nightly * nights) - advance }"}},
            {"name": "hotel abroad over the limit: deduct", "when": "hotels > 0 and hotel_currency != 'INR' and hotel_amount * rate > nightly * nights",
             "set": {**deducted, "deduction": "{= hotel_amount * rate - nightly * nights }", "payable": "{= total - (hotel_amount * rate - nightly * nights) - advance }"}},
            {"name": "otherwise: approve", "when": "true", "set": {"outcome": "Approve", "payable": "{= total - advance }", "attest": "yes"}},
        ],
        "trust": {"branches": {}, "since_spot": 0}}
    for i, rule in enumerate(program["rules"]):
        program["trust"]["branches"]["p%d: " % (i + 1) + " | ".join(f"{k}={v}" for k, v in sorted(rule["set"].items()) if k in ("outcome", "reason"))] = 2
    return program


asyncio.run(part2())
print(f"\n{checks} checks passed")
