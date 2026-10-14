"""Checks for the plan engine on the Claims Desk, the hazards site (testsites/claims-desk, `node server.js`, port 5183): a sign-in
whose session runs out after 25 page loads, a cookie banner and a "What's new" pop-up, a claims list that loads as you scroll (and
sometimes fails, with a Retry box), slow pages, a policy page reached by a link whose text is an id, and a decision form inside an
iframe built from an ARIA radio group, searchable comboboxes whose options appear in a portal, and a contenteditable note.

    python tests/test_plan_hazards.py              # add --offline to skip part 2 (it resets the site's decisions)

Part 1 needs nothing but the scripted trace (tests/fixtures/hazards_trace.json, rebuilt by make_hazards_fixture.py); part 1b
runs plans on made-up pages served from memory in a headless browser (no site needed).
Part 2 runs plans in a real headless browser against the site with a FAKE model that answers from the site's own data (writing
its amounts as formulas of the numbers printed on the pages, and quoting the pages as evidence), so it costs no model calls. It is
skipped when the site is not running, and it resets the site's decisions."""

import asyncio
import copy
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sites import site_path  # noqa: E402

import recipe.plan_exec as px
from recipe.plan import PLAN_VERSION, committed_values, compile_plan, learn_fields, values_agree
from run_context import RunContext

HERE = os.path.dirname(os.path.abspath(__file__))
FX = json.load(open(os.path.join(HERE, "fixtures", "hazards_trace.json")))
FIELDS, TRACE, INSTRUCTIONS = FX["fields"], FX["trace"], FX["instructions"]
NOTE_KEY = "edit:Internal note (optional)"
checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# Takes nothing; returns the plan learned from the main trace, taught the fields of the other branches (the payout of an
# approval, the document of a request), as the orchestrator teaches it from agreeing records.
def learned_plan():
    plan, _why = compile_plan(FIELDS, TRACE, INSTRUCTIONS)
    for other in FX["other_branches"].values():
        learn_fields(plan, other["fields"], other["trace"], INSTRUCTIONS)
    return plan


# ------------------------------------------------------------------ part 1: compiling the scripted trace

plan, why = compile_plan(FIELDS, TRACE, INSTRUCTIONS)
check("a plan is learned from the Claims Desk trace (sign-in, banner, pop-up, Retry, endless list, policy, iframe form)", plan is not None and why == "ok" and plan["version"] == PLAN_VERSION)
gather = plan["gather"]
check("the sign-in is the plan's login: the sign-in page's type and its username, password and Sign in steps",
      plan["login"]["page"] == "/login?next" and [(o["op"], o.get("value")) for o in plan["login"]["steps"]] == [("fill", "desk.auditor"), ("fill", "audit-2026"), ("click", None)])
check("the cookie banner's Accept all, the pop-up's Got it and the list's Retry are optional steps",
      [o["by"][-1].get("name") for o in gather if o.get("optional")] == ["Accept all", "Got it", "Retry"])
row = next(o for o in gather if o.get("by") == [{"css": "tr", "contains": "{claim_no}"}])
check("the claim's row (a <tr> with no name) is found by the claim number, by growing the list ('Load more' is not a step)",
      row.get("paged") and row.get("own") and not any(c.get("name") == "Load more" for o in gather for c in o.get("by", [])))
check("the policy link (its text is an id) is a value read off the claim's page", plan["slots"]["s1"]["seen_on"] == "/claims/*" and plan["slots"]["s1"]["example"].startswith("POL-"))
check("coming back from the policy is a step that reopens the claim's own address", gather[-1]["op"] == "revisit" and gather[gather[-1]["to"]] is row)
keys = [(c["key"], c["type"]) for c in plan["commit"]["controls"]]
check("the iframe form's controls: an ARIA radio group by its group's name, a combobox by its input's name, the note by its label",
      keys == [("aria:Outcome", "radio"), ("reason", "combobox"), (NOTE_KEY, "richtext")])
full = learned_plan()
check("the other branches teach the plan the payout (a number) and the document (another combobox)",
      [(c["key"], c["type"]) for c in full["commit"]["controls"]][3:] == [("payout", "number"), ("document", "combobox")])
live = committed_values(TRACE, {c["key"] for c in full["commit"]["controls"]})
check("what the live agent entered is read from the trace with the same keys", live == {"aria:Outcome": "Decline", "reason": "No cover on loss date", NOTE_KEY: "Checked against the policy."})
check("a combobox is compared like a choice and a rich-text box like a note",
      px._kind_of({"key": "reason", "tag": "select", "type": "combobox"}) == "select" and px._kind_of({"key": NOTE_KEY, "tag": "textarea", "type": "richtext"}) == "note")

# grounding, on the pages the approval's trace read (its claim page and its policy page)
SEED = json.load(open(site_path("claims-desk", "data/seed.json")))
EXPECTED = json.load(open(site_path("claims-desk", "expected.json")))
CLAIMS = {c["claim_no"]: c for c in SEED["claims"]}
POLICIES = {p["policy_no"]: p for p in SEED["policies"]}
approve_no, approve = next((n, o) for n, o in FX["other_branches"].items() if EXPECTED[n]["outcome"] == "Approve")
seen_pages = [(e["url"], e["page_text"]) for e in approve["trace"] if e.get("page_text")]
claim, policy = CLAIMS[approve_no], POLICIES[CLAIMS[approve_no]["policy_no"]]
cover = next(r for r in policy["coverage"] if r["peril"] == claim["peril"])
ctl = [{"key": "aria:Outcome", "tag": "input", "type": "radio", "label": "Outcome", "visible": True, "options": ["Approve", "Approve partially", "Decline", "Request information"]},
       {"key": "payout", "tag": "input", "type": "number", "label": "Payout (₹)", "visible": False, "options": []}]


def answer(payout, quotes):
    return {"facts": "", "missing": "", "checks": [{"rule": "otherwise approve", "known": True, "expr": "true", "evidence": quotes,
                                                    "values": [{"field": "aria:Outcome", "value": "Approve"}, {"field": "payout", "value": payout}]}]}


ground = (seen_pages, FIELDS, INSTRUCTIONS)
honest = px._values_from_answer(answer(f"{{= {claim['amount']} - {cover['deductible']} }}", [f"Status {policy['status']}"]), ctl, {}, ground)
check("an answer whose payout is a formula of numbers printed on the claim and the policy pages is used", honest[0]["payout"] == str(EXPECTED[approve_no]["payout"]))
check("  and its evidence quotes are returned with it, then the numbers its values were worked out from (for pruning)",
      honest[4][0] == f"Status {policy['status']}" and str(claim["amount"]) in honest[4])
try:
    px._values_from_answer(answer(f"{{= {claim['amount']} * 85 / 100 }}", []), ctl, {}, ground)
    guessed = ""
except px.PlanError as exc:
    guessed = str(exc)
check("a payout worked out with a rate no page shows (85%) is refused as not shown by the pages", "pages do not show what" in guessed)
try:
    px._values_from_answer(answer(f"{{= {claim['amount']} - {cover['deductible']} }}", ["Status Lapsed"]), ctl, {}, ground)
    misquoted = ""
except px.PlanError as exc:
    misquoted = str(exc)
check("an evidence quote that is not on the pages is refused too", "pages do not show what" in misquoted and "evidence" in misquoted)
check("without a grounding context (as older callers use it) the answer is not checked", px._values_from_answer(answer("12345", []), ctl, {})[0]["payout"] == "12345")

# ------------------------------------------------------------------ part 1b: made-up pages in a real browser (no site needed)
# Pages served from memory at http://edge.test/ and a fake model that answers fixed values: a form in an iframe whose page has
# a field of the same name outside it, a record's row that is missing from a list with an unrelated Next button, and read-backs
# that cannot read the record's page.

from playwright.async_api import async_playwright

EDGE = {"pages": {}, "posts": [], "answer": {}, "rec_visits": 0, "rec_fail": None}
EDGE_CTX = RunContext(target_url="http://edge.test/", workflow_name="test", match_key_field="no", model_name="m", gemini_api_key="x", task_instructions="Approve with amount 100.")


async def edge_ask_json(ctx, prompt, schema=None, temperature=0.0):
    values = [{"field": k, "value": v} for k, v in EDGE["answer"].items()]
    return {"decisions": [{"record": "record", "facts": "", "missing": "", "checks": [{"rule": "otherwise", "known": True, "expr": "true", "values": values, "evidence": []}]}]}


# Serves the made-up pages; a POST is recorded and answered with "Saved". The record page /rec/C-1 answers EDGE["rec_fail"]
# (503, or "login": the page sends the browser to the sign-in page) from its second visit on (the read-back).
async def edge_route(route):
    req = route.request
    if req.method == "POST":
        EDGE["posts"].append((req.url.split("edge.test", 1)[1], req.post_data))
        await route.fulfill(status=200, content_type="text/html", body="<h1>Saved</h1>")
        return
    path = req.url.split("edge.test", 1)[1].split("?")[0]
    if path == "/rec/C-1":
        EDGE["rec_visits"] += 1
        if EDGE["rec_visits"] > 1 and EDGE["rec_fail"] == 503:
            await route.fulfill(status=503, content_type="text/html", body="<h1>Service unavailable</h1>")
            return
        if EDGE["rec_visits"] > 1 and EDGE["rec_fail"] == "login":
            await route.fulfill(status=200, content_type="text/html", body="<script>location.replace('/login?next=/rec/C-1')</script>")
            return
    body = EDGE["pages"].get(path)
    await route.fulfill(status=200 if body else 404, content_type="text/html", body=body or "not found")


def edge_control(key, node, typ=""):
    return {"key": key, "node": node, "type": typ, "label": key, "value": {"computed": True}, "stage": 0}


async def part1b():
    saved_ask = px.ask_json
    px.ask_json = edge_ask_json
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.route("http://edge.test/**", edge_route)

            # ---- a form in an iframe; the main page has a field named like one of the form's (a quick calculator)
            EDGE["pages"]["/"] = """<h1>Claim C-1</h1><div>Quick calc: <input name="amount" aria-label="calc"></div>
              <iframe src="/form" style="width:600px;height:300px"></iframe>"""
            EDGE["pages"]["/form"] = """<form method=post action="/save"><label>Status <select name=status><option>Approve</option>
              <option>Reject</option></select></label><label>Amount <input name=amount type=number></label><button>Save</button></form>"""
            for order in (["status", "amount"], ["amount", "status"]):
                EDGE["posts"].clear()
                EDGE["answer"] = {"status": "Approve", "amount": "100"}
                specs = {"status": edge_control("status", "select"), "amount": edge_control("amount", "input", "number")}
                plan = {"kind": "plan", "version": PLAN_VERSION, "start": "http://edge.test/", "slots": {}, "gather": [], "key_fields": [],
                        "commit": {"controls": [specs[k] for k in order], "submit": [{"op": "click", "by": [{"role": "button", "name": "Save"}]}], "advance": []}}
                page = await context.new_page()
                out = await px.run_plan(plan, {"no": "C-1"}, ctx=EDGE_CTX, page=page, emit=lambda e: None)
                await asyncio.sleep(0.3)
                calc = await page.locator("input[name=amount]").first.input_value()
                await page.close()
                check(f"a form in an iframe is filled there, not in a same-named field on the main page (plan's first control: {order[0]})",
                      out.ok and EDGE["posts"] == [("/save", "status=Approve&amount=100")] and calc == "")

            # ---- the record's row is not in a list that is not paged: the list may be grown, but its Next button is never pressed
            EDGE["posts"].clear()
            EDGE["pages"]["/list"] = """<table><thead><tr><th>No</th></tr></thead><tbody><tr><td>C-9</td></tr></tbody></table>
              <form method=post action="/bump"><button>Next</button></form><form method=post action="/day2"><button>2</button></form>"""
            plan = {"kind": "plan", "version": PLAN_VERSION, "start": "http://edge.test/list", "slots": {}, "key_fields": ["no"],
                    "gather": [{"op": "click", "by": [{"contains": "{no}", "css": "tr"}], "bound": ["no"], "own": True, "uses": []}],
                    "commit": {"controls": [], "submit": [], "advance": []}}
            page = await context.new_page()
            out = await px.run_plan(plan, {"no": "C-1"}, ctx=EDGE_CTX, page=page, emit=lambda e: None, dry=True)
            await page.close()
            check("a record's row missing from a list that is not paged: not found, and no Next or page-number button was pressed",
                  not out.ok and "could not find" in (out.error or "") and EDGE["posts"] == [])

            # ---- read-backs that cannot read the record's page: saved (ok), but not verified
            EDGE["pages"]["/"] = '<a href="/rec/C-1">C-1</a>'
            EDGE["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1><form method=post action=/save><label>Status <select name=status><option>Approve</option>
              <option>Reject</option></select></label><button>Save</button></form>"""
            EDGE["pages"]["/login"] = """<form method=post action=/signin><label>Username <input name=u></label><label>Password <input type=password name=p></label><button>Sign in</button></form>"""
            EDGE["answer"] = {"status": "Approve"}
            for fail in (503, "login"):
                EDGE["rec_visits"], EDGE["rec_fail"] = 0, fail
                EDGE["posts"].clear()
                plan = {"kind": "plan", "version": PLAN_VERSION, "start": "http://edge.test/", "slots": {}, "readback": {"shows": True}, "key_fields": ["no"],
                        "login": {"page": "/login?next", "steps": [{"op": "fill", "by": [{"role": "textbox", "name": "No such box"}], "value": "x"}]},
                        "gather": [{"op": "click", "by": [{"role": "link", "name": "{no}"}], "bound": ["no"], "own": True, "expect": "/rec/*", "uses": []}],
                        "commit": {"controls": [edge_control("status", "select")], "submit": [{"op": "click", "by": [{"role": "button", "name": "Save"}]}], "advance": []}}
                page = await context.new_page()
                out = await px.run_plan(plan, {"no": "C-1"}, ctx=EDGE_CTX, page=page, emit=lambda e: None)
                await page.close()
                why = "the record's page answers 503" if fail == 503 else "it is sent to sign in and signing in again fails"
                check(f"a read-back where {why}: the record is saved (ok, no error) but unverified",
                      out.ok and out.acted and out.error is None and out.unverified and (out.readback or {}).get("error")
                      and EDGE["posts"][:1] == [("/save", "status=Approve")])
            await browser.close()
    finally:
        px.ask_json = saved_ask
        EDGE["rec_fail"] = None


asyncio.run(part1b())

# ------------------------------------------------------------------ part 1c: the 15 Oct review's cases (pages from memory)
# Never act twice, never save a guess: a gather click that saves is pressed once (never in a dry run, never again after the
# session ran out), a confirmation that saves is never answered by the pop-up closer, a wizard's Next is never pressed in a
# dry run and counts as acting, the saving button of the iframe's form is pressed (not the main page's same-named one), a
# decision program is compared with the model on every field, a read-back only counts what the save changed, a list that is
# not paged is never moved on with a Next, and a widget the agent sets counts its model calls.

ADV = {"pages": {}, "posts": [], "answer": {}, "form_loads": 0, "logged_in": True, "expire_once": False, "asks": 0}
ADV_CTX = RunContext(target_url="http://adv.test/", workflow_name="test", match_key_field="no", model_name="m", gemini_api_key="x",
                     task_instructions="If the amount claimed is under 500, Approve with payout = amount claimed; otherwise Decline.")
ADV_LOGIN_PAGE = """<h1>Sign in</h1><label>Username <input name=u></label><label>Password <input type=password name=p></label>
<button type=button onclick="fetch('/signin',{method:'POST'}).then(()=>location.href=new URLSearchParams(location.search).get('next')||'/')">Sign in</button>"""
ADV_LOGIN = {"page": "/login?next", "steps": [
    {"op": "fill", "by": [{"css": "input", "attr": "name", "value": "u"}], "value": "u1"},
    {"op": "fill", "by": [{"css": "input", "attr": "name", "value": "p"}], "value": "pw"},
    {"op": "click", "by": [{"role": "button", "name": "Sign in"}]}]}


async def adv_ask_json(ctx, prompt, schema=None, temperature=0.0):
    ADV["asks"] += 1
    if schema is px._SlotAnswers:
        ADV["slot_asks"] = ADV.get("slot_asks", 0) + 1
        names = re.findall(r"^  - (s\d+): for a different record", prompt, re.M)
        return {"records": [{"record": "record", "values": [{"name": n, "value": ADV["slot"]} for n in names]}]}
    answer = ADV["answer"]
    if callable(answer):
        return answer(prompt)
    values = [{"field": k, "value": v} for k, v in answer.items()]
    return {"decisions": [{"record": "record", "facts": "", "missing": "", "checks": [{"rule": "otherwise", "known": True, "expr": "true", "values": values, "evidence": []}]}]}


# Serves the made-up pages at http://adv.test/; a POST is recorded ("/path" or "/path?body") and answered "ok". The page
# /rec/C-1/form sends the browser to sign in on its first load when ADV["expire_once"] is set (the session ran out there).
async def adv_route(route):
    req = route.request
    path = req.url.split("adv.test", 1)[1]
    if req.method == "POST":
        ADV["posts"].append(path + ("?" + req.post_data if req.post_data else ""))
        if path == "/signin":
            ADV["logged_in"] = True
        await route.fulfill(status=200, content_type="text/html", body="<h1>Saved</h1>")
        return
    bare = path.split("?")[0]
    if bare == "/login":
        await route.fulfill(status=200, content_type="text/html", body=ADV_LOGIN_PAGE)
        return
    if bare == "/rec/C-1/form":
        ADV["form_loads"] += 1
        if ADV["expire_once"] and ADV["form_loads"] == 1:
            ADV["logged_in"] = False
        if not ADV["logged_in"]:
            await route.fulfill(status=200, content_type="text/html", body="<script>location.replace('/login?next=/rec/C-1/form')</script>")
            return
    body = ADV["pages"].get(bare)
    await route.fulfill(status=200 if body else 404, content_type="text/html; charset=utf-8", body=body or "not found")


def adv_plan(gather, controls, submit=("Save",), advance=(), **more):
    plan = {"kind": "plan", "version": PLAN_VERSION, "start": "http://adv.test/rec/{no}", "slots": {}, "key_fields": [], "gather": gather,
            "commit": {"controls": controls, "submit": [{"op": "click", "by": [{"role": "button", "name": n}]} for n in submit],
                       "advance": [{"op": "click", "by": [{"role": "button", "name": n}]} for n in advance]}}
    plan.update(more)
    return plan


async def adv_run(context, plan, **kw):
    page = await context.new_page()
    out = await px.run_plan(plan, kw.pop("fields", {"no": "C-1"}), ctx=ADV_CTX, page=page, emit=lambda e: None, **kw)
    await asyncio.sleep(0.3)
    await page.close()
    return out


async def part1c():
    saved_ask = px.ask_json
    px.ask_json = adv_ask_json
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.route("http://adv.test/**", adv_route)

            # ---- a click on the route that saves ("Start review" assigns the case), and the session runs out on the next page
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1>
              <button type=button onclick="fetch('/start-review',{method:'POST'}).then(()=>location.href='/rec/C-1/form')">Start review</button>"""
            ADV["pages"]["/rec/C-1/form"] = """<h1>Claim C-1</h1><form onsubmit="event.preventDefault();fetch('/save',{method:'POST'})">
              <label>Status <select name=status><option>Approve</option><option>Decline</option></select></label><button>Save</button></form>"""
            start = [{"op": "click", "by": [{"role": "button", "name": "Start review"}], "expect": "/rec/*/form", "goal": "start the review", "bound": [], "uses": []}]
            plan = adv_plan(start, [edge_control("status", "select")], login=ADV_LOGIN)
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=True, answer={"status": "Approve"})
            out = await adv_run(context, plan, dry=True)
            check("a dry run stops before a route's click that may save ('Start review'): nothing is pressed",
                  not out.ok and "dry run stops before" in (out.error or "") and ADV["posts"] == [] and not out.acted)
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=True)
            out = await adv_run(context, plan)
            check("a real run presses it once: the session ran out after it, the site went on to the form after signing in, so it is not pressed again",
                  out.ok and out.acted and out.logins == 1 and ADV["posts"].count("/start-review") == 1 and ADV["posts"][-1] == "/save")
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1>
              <button type=button onclick="fetch('/start-review',{method:'POST'}).then(()=>location.href='/rec/C-1/form')">Start review</button>"""
            ADV["pages"]["/"] = "<h1>Home</h1>"
            ADV_LOGIN_HOME = dict(ADV_LOGIN, page="/login?next")
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=True)
            saved_page = ADV_LOGIN_PAGE
            globals()["ADV_LOGIN_PAGE"] = ADV_LOGIN_PAGE.replace("get('next')||'/'", "get('nowhere')||'/'")  # a site that does not go on
            out = await adv_run(context, adv_plan(start, [edge_control("status", "select")], login=ADV_LOGIN_HOME))
            globals()["ADV_LOGIN_PAGE"] = saved_page
            check("  when the site does not go on to where the click led, the click is not made again: the record fails as acted",
                  not out.ok and out.acted and ADV["posts"].count("/start-review") == 1 and "not done again" in (out.error or ""))

            # ---- a task with no form: Approve on the record's page is its saving step
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1>
              <button type=button onclick="fetch('/approve',{method:'POST'}).then(()=>location.href='/rec/C-1/form')">Approve</button>"""
            ADV["pages"]["/rec/C-1/form"] = "<h1>Claim C-1 approved</h1>"
            approve = [{"op": "click", "by": [{"role": "button", "name": "Approve"}], "expect": "/rec/*/form", "goal": "approve", "bound": [], "uses": []}]
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=False)
            out = await adv_run(context, adv_plan(approve, [], submit=()), dry=True)
            check("a plan made before saving steps were marked: its Approve on the route is never pressed in a dry run", not out.ok and ADV["posts"] == [])
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=True)
            out = await adv_run(context, adv_plan(approve, [], submit=(), login=ADV_LOGIN))
            check("  and a real run presses it once, counts it as acting, and does not press it again after signing in",
                  out.ok and out.acted and ADV["posts"].count("/approve") == 1)
            ADV.update(posts=[], form_loads=0, logged_in=True, expire_once=False)
            out = await adv_run(context, adv_plan([], [], submit=("Approve",), start="http://adv.test/rec/{no}"), dry=True)
            check("a compiled no-form plan (Approve as its saving step) is a dry run that presses nothing", out.ok and ADV["posts"] == [])

            # ---- a confirmation that saves appears over the form while it is being filled: nothing in it is pressed
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1>
              <form onsubmit="event.preventDefault();fetch('/save',{method:'POST'})">
                <label>Status <select name=status onchange="if(this.value==='Decline')document.getElementById('dlg').style.display='block'">
                  <option>Approve</option><option>Decline</option></select></label>
                <label><input type=checkbox name=notify> Notify the customer</label><button>Save</button></form>
              <div id=dlg role=dialog aria-modal=true style="display:none;position:fixed;inset:0;background:#fff">
                <p>Decline claim C-1 and email the customer now?</p>
                <button type=button onclick="this.parentNode.style.display='none'">Cancel</button>
                <button type=button onclick="fetch('/decline-now',{method:'POST'});this.parentNode.style.display='none'">OK</button></div>"""
            ADV.update(posts=[], answer={"status": "Decline", "notify": "yes"})
            out = await adv_run(context, adv_plan([], [edge_control("status", "select"), edge_control("notify", "input", "checkbox")]))
            check("a confirmation (Cancel / OK) that appears after a field was set is never answered, and the record is not done again",
                  not out.ok and out.acted and "dialog" in (out.error or "") and ADV["posts"] == [])

            # ---- a wizard whose Next saves its step on the site
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1><div id=s1><label>Status <select name=status><option>Approve</option><option>Decline</option></select></label>
                <button type=button onclick="fetch('/step1',{method:'POST'});s1.hidden=true;s2.hidden=false">Next</button></div>
              <div id=s2 hidden><label>Reason <select name=reason><option>Other</option></select></label>
                <button type=button onclick="fetch('/save',{method:'POST'})">Submit</button></div>"""
            later = dict(edge_control("reason", "select"), stage=1)
            wizard = adv_plan([], [edge_control("status", "select"), later], submit=("Submit",), advance=("Next",))
            ADV.update(posts=[], answer={"status": "Decline", "reason": "Other"})
            out = await adv_run(context, wizard, dry=True)
            check("a dry run of a wizard never presses its Next, and returns the later step's value it decided too",
                  out.ok and ADV["posts"] == [] and out.planned == {"status": "Decline", "reason": "Other"})
            ADV.update(posts=[], answer={"status": "Decline", "reason": "Not an option"})
            out = await adv_run(context, wizard)
            check("  a real run that fails on a later step after pressing Next counts as acting (never done again by the live agent)",
                  not out.ok and out.acted and ADV["posts"] == ["/step1"])

            # ---- the decision form is in an iframe; the main page has another form whose button has the same name
            inner = "<form method=post action='/decide'><label>Payout <input name=payout type=number></label><button>Submit</button></form>"
            ADV["pages"]["/rec/C-1"] = f"""<h1>Claim C-1</h1><p>Payout due ₹4,500</p><form method=post action="/comment"><label>Comment <input name=comment></label><button>Submit</button></form>
              <iframe srcdoc="{inner.replace('"', '&quot;')}"></iframe>"""
            ADV.update(posts=[], answer={"payout": "4500"})
            out = await adv_run(context, adv_plan([], [edge_control("payout", "input", "number")], submit=("Submit",)))
            check("the saving button of the form's own frame is pressed, not the main page's button of the same name",
                  out.ok and ADV["posts"] == ["/decide?payout=4500"])

            # ---- a decision program that leaves out a field the model fills is not counted as agreeing
            amounts = {"C-1": 300, "C-2": 420}
            for no, amount in amounts.items():
                ADV["pages"][f"/rec/{no}"] = (f"<h1>Claim {no}</h1><dl><dt>Amount claimed</dt><dd>₹{amount}</dd></dl><form method=post action='/save'>"
                                              "<label>Outcome <select name=outcome><option value=''>Choose</option><option>Approve</option><option>Decline</option></select></label>"
                                              "<label>Payout <input name=payout type=number></label><button>Save</button></form>")
            ADV["answer"] = lambda prompt: (lambda amt: {"decisions": [{"record": "record", "facts": "", "missing": "", "checks": [
                {"rule": "under 500: approve, payout = amount", "known": True, "expr": f"{amt} < 500", "evidence": [f"Amount claimed ₹{amt}"],
                 "values": [{"field": "outcome", "value": "Approve"}, {"field": "payout", "value": f"{{= {amt} }}"}]}]}]})(
                int(re.search(r"Amount claimed\n₹(\d+)", prompt).group(1)))
            program = {"version": 1, "facts": [{"name": "amount", "type": "number", "source": {"kind": "label", "page": "/rec/*", "label": "Amount claimed"}}],
                       "rules": [{"name": "under 500 approve", "when": "amount < 500", "set": {"outcome": "Approve"}},
                                 {"name": "otherwise decline", "when": "true", "set": {"outcome": "Decline"}}],
                       "trust": {"branches": {}, "since_spot": 0}}
            prog_plan = adv_plan([], [edge_control("outcome", "select"), edge_control("payout", "input", "number")], program=program)
            checks_seen = []
            for no in amounts:
                out = await adv_run(context, prog_plan, fields={"no": no}, dry=True, program_mode="shadow")
                checks_seen.append(out.program_check)
            check("a program that leaves out a field the model fills (payout) disagrees with it: compared on every field either side sets",
                  all(c is not None and c[1] is False for c in checks_seen))
            program["rules"][0]["set"] = {"outcome": "Approve", "payout": "{amount}"}
            out = await adv_run(context, prog_plan, fields={"no": "C-1"}, dry=True, program_mode="shadow")
            check("  the same program with the payout agrees", out.program_check is not None and out.program_check[1] is True)

            # ---- read-back counts only what the save changed: a record page that already printed the value proves nothing
            ADV["pages"]["/"] = '<a href="/rec/C-1">C-1</a>'
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1><p>Requested resolution</p><p>Approve</p><p>Decision</p><p>Not decided yet</p>
              <form method=post action=/save-nothing><label>Status <select name=status><option>Approve</option><option>Reject</option></select></label><button>Save</button></form>"""
            ADV.update(posts=[], answer={"status": "Approve"})
            rb_plan = adv_plan([{"op": "click", "by": [{"role": "link", "name": "{no}"}], "bound": ["no"], "own": True, "expect": "/rec/*", "uses": []}],
                               [edge_control("status", "select")], start="http://adv.test/", readback={"shows": True}, key_fields=["no"])
            out = await adv_run(context, rb_plan)
            check("a read-back whose value was printed on the record's page before saving (and the save changed nothing) does not count it as seen",
                  out.ok and out.acted and out.unverified and "status" in (out.readback or {}).get("missing", []))

            # ---- a list that is not paged: a plain Next button is never pressed while looking for the record's row
            ADV["pages"]["/"] = """<table><thead><tr><th>Claim</th></tr></thead><tbody><tr><td><a href="#">EXP-1</a></td></tr></tbody></table>
              <button onclick="window.pressed=(window.pressed||0)+1;document.querySelector('tbody').innerHTML='<tr><td>page '+window.pressed+'</td></tr>'">Next</button>"""
            page = await context.new_page()
            await page.goto("http://adv.test/")
            got = await px._find_by_growing(page, [{"role": "link", "name": "{no}"}], {"no": "EXP-9"}, {})
            pressed = await page.evaluate("window.pressed || 0")
            await page.close()
            check("growing a list for a record's row never presses a Next button (that is paging, which this step does not do)", got is None and pressed == 0)

            # ---- a value read by code is read by the model again every 10th time, so the code's reading keeps being checked
            ADV["pages"]["/rec/C-1"] = '<h1>Return C-1</h1><p>Order</p><p>ORD-77</p><a href="/orders/ORD-77">ORD-77</a>'
            ADV["pages"]["/orders/ORD-77"] = "<h1>Order ORD-77</h1>"
            ADV.update(slot="ORD-77", slot_asks=0)
            code = {"label": "Order", "shape": r"^[A-Z]{3}-\d{2}$", "agree": 5, "uses": 8}
            slot_plan = adv_plan([{"op": "click", "by": [{"role": "link", "name": "{s1}"}], "expect": "/orders/*", "bound": [], "uses": ["s1"]}], [], submit=(),
                                 slots={"s1": {"example": "ORD-12", "seen_on": "/rec/*", "hint": "the order", "code": code}})
            first = await adv_run(context, slot_plan, dry=True)
            second = await adv_run(context, slot_plan, dry=True)
            third = await adv_run(context, slot_plan, dry=True)
            check("a slot read by code is read by the model on every 10th reading, and the two readings are compared",
                  first.ok and second.ok and third.ok and ADV["slot_asks"] == 1 and second.slot_checks == {"s1": True} and not first.slot_checks
                  and not third.slot_checks and code["uses"] == 11)

            # ---- a widget the live agent sets: its model calls are counted
            ADV["pages"]["/rec/C-1"] = """<h1>Claim C-1</h1><form method=post action=/save><label>Status <select name=status><option>Approve</option></select></label>
              <p>Due 05 Nov 2026</p><div class=picker>Pick a date</div><button>Save</button></form>"""
            ADV.update(posts=[], answer={"status": "Approve", "widget1": "2026-11-05"}, asks=0)
            widget = {"key": "widget1", "node": "widget", "type": "widget", "label": "Pick a date", "hint": "", "value": {"computed": True}, "stage": 0, "opener": []}

            async def agent_fill(label, value, hint):
                return True, 3  # the scoped live agent took 3 model calls

            out = await adv_run(context, adv_plan([], [edge_control("status", "select"), widget]), widget_fill=agent_fill)
            check("the model calls of the agent that set a widget count in the record's model calls", out.ok and out.llm_calls == ADV["asks"] + 3)
            await browser.close()
    finally:
        px.ask_json = saved_ask


asyncio.run(part1c())

# ------------------------------------------------------------------ part 2: in a real browser (fake model)

SITE = "http://localhost:5183"
try:
    if "--offline" in sys.argv:
        raise RuntimeError("asked to stay offline")  # part 2 resets the site: never run it while a real run is using the site
    urllib.request.urlopen(SITE + "/admin/state", timeout=2)
except Exception:
    print(f"\nSKIPPED part 2 (--offline, or the Claims Desk site is not running on {SITE})")
    print(f"\n{checks} checks passed")
    sys.exit(0)

LISTED = [c["claim_no"] for c in sorted(sorted(SEED["claims"], key=lambda c: c["claim_no"]), key=lambda c: c["reported_on"], reverse=True)]
calls = {"slots": 0, "form": 0}
fake = {"quotes": True}


# A stand-in for the model that answers from the site's own data. It writes a payout as a formula of the numbers the claim and
# policy pages print, and quotes the pages it used. A rich-text note is written into the note the form really has.
async def fake_ask_json(ctx, prompt, schema=None, temperature=0.0):
    blocks = re.split(r'^### record "', prompt, flags=re.M)[1:]
    if schema is px._SlotAnswers:
        calls["slots"] += 1
        records = []
        for block in blocks:
            claim_no = re.search(r"claim_no = (CLM-26-\d+)", block).group(1)
            records.append({"record": block.split('"', 1)[0], "values": [{"name": n, "value": CLAIMS[claim_no]["policy_no"]} for n in re.findall(r"^  - (s\d+):", block, flags=re.M)]})
        return {"records": records}
    if schema is px._FormDecisions:
        calls["form"] += 1
        decisions = []
        for block in blocks:
            claim_no = re.search(r"claim_no = (CLM-26-\d+)", block).group(1)
            e, c = EXPECTED[claim_no], CLAIMS[claim_no]
            p = POLICIES[c["policy_no"]]
            row = next(r for r in p["coverage"] if r["peril"] == c["peril"])
            values = [{"field": "aria:Outcome", "value": e["outcome"]}]
            if e["outcome"] == "Approve":
                values.append({"field": "payout", "value": f"{{= {c['amount']} - {row['deductible']} }}"})
            if e["outcome"] == "Approve partially":
                values.append({"field": "payout", "value": f"{{= {row['limit']} - {row['deductible']} }}"})
            for name in ("reason", "document"):
                if e.get(name):
                    values.append({"field": name, "value": e[name]})
            quotes = [f"Peril {c['peril']}"] + ([f"Status {p['status']}"] if fake["quotes"] else [])
            decisions.append({"record": block.split('"', 1)[0], "facts": "from the pages", "missing": "",
                              "checks": [{"rule": "the rule that applies", "known": True, "expr": "true", "values": values, "evidence": quotes}]})
        return {"decisions": decisions}
    raise AssertionError("unexpected question")


px.ask_json = fake_ask_json
ctx = RunContext(target_url=SITE, workflow_name="test", match_key_field="claim_no", model_name="m", gemini_api_key="x", task_instructions=INSTRUCTIONS)


def state():
    return json.load(urllib.request.urlopen(SITE + "/admin/state"))


def admin(path):
    urllib.request.urlopen(urllib.request.Request(SITE + path, method="POST", data=b""))


# Takes an expected outcome, a reason or document (or None) and the part of the list to look in; returns such a claim.
def pick(outcome, why=None, low=0, high=100):
    return next(n for n in LISTED[low:high] if EXPECTED[n]["outcome"] == outcome and why in (None, EXPECTED[n].get("reason"), EXPECTED[n].get("document")))


# Takes a claim number and the saved decisions; returns True when it was saved as expected.json says.
def saved_right(claim_no, saved):
    got, e = saved.get(claim_no), EXPECTED[claim_no]
    return got is not None and all(got.get(k) == e.get(k) for k in ("outcome", "payout", "reason", "document"))


async def run(claim_no, context, the_plan=None, **kw):
    page = await context.new_page()
    events = []
    out = await px.run_plan(the_plan or learned_plan(), {"claim_no": claim_no}, ctx=ctx, page=page, emit=events.append, key=claim_no, **kw)
    await page.close()
    return out, events


# Takes a browser context; signs in afresh (a new session) and makes page loads until the session has served `loads` of them.
async def session_with(context, loads):
    page = await context.new_page()
    await page.goto(SITE + "/logout")
    await page.get_by_label("Username").fill(SEED["login"]["username"])
    await page.get_by_label("Password").fill(SEED["login"]["password"])
    async with page.expect_navigation():
        await page.get_by_role("button", name="Sign in").click()  # the dashboard: load 1
    for _ in range(loads - 1):
        await page.goto(SITE + "/", wait_until="domcontentloaded")
    await page.close()


async def part2():
    admin("/admin/reset")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})

        # ---- a fresh browser: sent to sign in, a cookie banner, "What's new", a failed list load, a claim deep in the list
        deep = pick("Decline", "Peril not covered", 50)
        admin("/admin/fail-next")
        out, events = await run(deep, context)
        saved = state()
        check(f"a fresh browser: the plan signs in, gets past the banner, the pop-up and a failed load, scrolls to row {LISTED.index(deep) + 1}, and decides in the iframe ({deep})"
              if out.ok else f"fresh browser FAILED: {out.error}", out.ok and out.acted and saved_right(deep, saved))
        said = [str(e.get("field", "")) for e in events]
        check("  the cookie banner and the What's new box were closed on the way (by the plan's optional steps, or as interruptions)",
              any(f.startswith("closed ") and "Reject" in f or f.startswith("Accept the cookie banner") for f in said)
              and any(f.startswith("closed ") and ("Close" in f or "Got it" in f) or f.startswith("Close the What's new box") for f in said))
        check("  the decision cost one slot question and one voted form question (2 calls), and the model decided", calls == {"slots": 1, "form": 2} and out.decided_by == "model" and out.llm_calls == 3)
        check("  the ARIA radio group and the combobox are compared as choices", out.kinds.get("aria:Outcome") == "select" and out.kinds.get("reason") == "select")
        check("  nothing else was saved", [k for k, v in saved.items() if v] == [deep])

        # ---- each kind of outcome, one after another in the same browser: the session runs out on the way (6 page loads a record)
        kinds = [pick("Approve", None, 0, 40), pick("Approve partially", None, 0, 40), pick("Decline", "No cover on loss date", 0, 40),
                 pick("Request information", "Police report", 0, 60), pick("Approve", None, 40)]
        logins = 0
        for claim_no in kinds:
            out, _ev = await run(claim_no, context)
            logins += out.logins
            e = EXPECTED[claim_no]
            check(f"{e['outcome']} {e.get('reason') or e.get('document') or e.get('payout')}: the plan fills the iframe form and saves it ({claim_no})"
                  if out.ok else f"{claim_no} FAILED: {out.error}", out.ok and out.acted and saved_right(claim_no, state()))
        check(f"the session ran out on the way and the plan signed in again by itself ({logins} time(s))", logins >= 1)

        # ---- a dry run: what it would enter, the record's own page, the pages its decision used
        claim_no = pick("Approve partially", None, 0, 60)
        while state().get(claim_no):
            claim_no = pick("Approve partially", None, LISTED.index(claim_no) + 1)
        page = await context.new_page()
        out = await px.run_plan(learned_plan(), {"claim_no": claim_no}, ctx=ctx, page=page, emit=lambda e: None, key=claim_no, dry=True)
        text = await px._quick_read(page)
        await page.close()
        check("a dry run states the values it would enter, and saves nothing" if out.ok else f"dry run FAILED: {out.error}",
              out.ok and not out.acted and out.planned.get("aria:Outcome") == "Approve partially" and out.planned.get("payout") == str(EXPECTED[claim_no]["payout"]) and state()[claim_no] is None)
        check("  it gives the record's own page address (for reading the saved values back later)", out.record_url and out.record_url.rstrip("/").endswith("/claims/" + CLAIMS[claim_no]["key"]))
        check("  it keeps the model's decision as an example (fields, pages, values) for learning a decision program",
              out.example and out.example["values"] == out.planned and any("/policies/" in u for u, _t in out.example["pages"]))
        check(f"  the pages its evidence quotes were found on count as the pages it used (the claim's and the policy's among them: {sorted(out.used_pages)})", {"/claims/*", "/policies/*"} <= out.used_pages)
        check("the text read from the claim page includes the form inside its iframe", "[frame]" in text and "Outcome" in text and "Submit decision" in text)

        # ---- read-back: the record's page is opened again after saving
        readable = learned_plan()
        readable["readback"] = {"shows": True, "checked": 2}
        claim_no = pick("Approve", None, 0, 40)
        while state().get(claim_no):
            claim_no = pick("Approve", None, LISTED.index(claim_no) + 1)
        out, _ev = await run(claim_no, context, readable)
        check("read-back: after saving, the claim's page shows the outcome and the payout that were entered" if out.ok else f"read-back run FAILED: {out.error}",
              out.ok and out.readback and {"aria:Outcome", "payout"} <= set(out.readback.get("seen", [])) and not out.unverified)

        # a test hook makes the site save another payout than the one the form sent
        async def tamper(route):
            body = json.loads(route.request.post_data or "{}")
            if body.get("payout") is not None:
                body["payout"] = int(body["payout"]) + 1000
            await route.continue_(post_data=json.dumps(body))

        await context.route("**/api/claims/*/decision", tamper)
        claim_no = pick("Approve", None, LISTED.index(claim_no) + 1)
        out, _ev = await run(claim_no, context, readable)
        await context.unroute("**/api/claims/*/decision", tamper)
        got = state()[claim_no]
        check("read-back flags a save whose page shows another payout: the record is saved but unverified (never retried)",
              out.ok and out.acted and out.unverified and "payout" in out.readback.get("missing", []) and got and got["payout"] == EXPECTED[claim_no]["payout"] + 1000)

        # ---- the session runs out exactly at the decision frame: the whole window is sent to sign in, in the middle of the record
        fresh = await browser.new_context(viewport={"width": 1280, "height": 800})
        await session_with(fresh, 20)  # the record's 6th load (the form's frame, on coming back from the policy) is the 26th
        claim_no = pick("Decline", "No cover on loss date", 0, 40)
        while state().get(claim_no):
            claim_no = pick("Decline", "No cover on loss date", LISTED.index(claim_no) + 1)
        out, _ev = await run(claim_no, fresh)
        check(f"a session that runs out as the form's frame loads: the plan signs in again, comes back to the claim and saves ({claim_no})"
              if out.ok else f"expiry run FAILED: {out.error}", out.ok and out.logins == 1 and saved_right(claim_no, state()))
        await fresh.close()

        # ---- pruning: a side trip to the policy page left out
        policy_step = next(i for i, o in enumerate(learned_plan()["gather"]) if "s1" in o.get("uses", []))
        claim_no = pick("Request information", "Police report", 0, 60)
        fake["quotes"] = False  # an answer that quotes only the claim's own page
        page = await context.new_page()
        out = await px.run_plan(learned_plan(), {"claim_no": claim_no}, ctx=ctx, page=page, emit=lambda e: None, key=claim_no, dry=True, skip={policy_step})
        await page.close()
        check("a skipped side trip: the plan never opens the policy, does not go back either, and still decides from the claim's page"
              if out.ok else f"skip run FAILED: {out.error}", out.ok and out.planned.get("document") == "Police report" and not any("/policies/" in u for u, _t in out.example["pages"]))
        fake["quotes"] = True
        claim_no = pick("Approve", None, 0, 60)
        page = await context.new_page()
        out = await px.run_plan(learned_plan(), {"claim_no": claim_no}, ctx=ctx, page=page, emit=lambda e: None, key=claim_no, dry=True, skip={policy_step})
        await page.close()
        check("  but an answer that needs the policy (its deductible, its status) is refused when the policy was skipped", not out.ok and "pages do not show what" in (out.error or ""))

        # ---- a claim the site does not have: the list is grown to its end, and nothing is saved
        before = state()
        out, _ev = await run("CLM-26-00000", context)
        check("a claim that is not in the list is not found once the list is grown to its end, and nothing is saved", not out.ok and not out.acted and state() == before)
        await browser.close()
    admin("/admin/reset")


asyncio.run(part2())
print(f"\n{checks} checks passed")
