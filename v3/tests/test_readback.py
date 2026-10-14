"""Checks for read-back (recipe/readback.py): after a plan saves a record, its own page is opened again and the saved values
must be shown there.

    python tests/test_readback.py

Part 1 needs nothing: numbers, dates, the labels of choices and short texts compared with page texts in the shapes the plan
executor reads (innerText, a definition list's "Label\\nValue" lines, tab-separated table rows, the "[table rows with their
headings]" block). Part 2 opens record pages in a headless browser. The pages are served by the test itself from a made-up
address (http://desk.test, answered with page.route; no server and no network): a page that shows the decision, one that
only shows the form (which still lists every choice), a single-page app with hash routes, a screen that showed a decision it
never saved, and a missing record. No model is called."""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

import recipe.plan_exec as px
import recipe.readback as rb
from recipe.readback import read_back, readback_report, shown_text, site_shows_decisions, value_seen

checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ part 1: no browser needed

# A record's page as the plan executor reads it: a definition list, a table with headings, and the table-rows block after it
SHOWN = """Item LIB-7731
Borrower
Asha Rao
Due date
05 Nov 2026
Decision
Approved with deduction
Fee charged
₹1,984
Next review
12 Nov 2026
Reasons: Damaged · Late
Note
Waived half the fee.
Charge\tAmount\tPosted
Late fee\t₹1,25,000\t2026-10-19
Damage\tRs.480\t2026-10-20

[table rows with their headings]
Charge: Late fee | Amount: ₹1,25,000 | Posted: 2026-10-19
Charge: Damage | Amount: Rs.480 | Posted: 2026-10-20
"""

check("a number is found by its value (1984 is ₹1,984)", value_seen("1984", "other", SHOWN) is True)
check("Indian grouping and a currency written against the digits are read", value_seen("125000", "other", SHOWN) is True and value_seen("480", "other", SHOWN) is True)
check("decimals and signs do not matter (12.50 is 12.5, -200 is ₹200)", value_seen("12.5", "other", "Rate: 12.50%") is True and value_seen("-200", "other", "Deduction −₹200") is True)
check("a number that is not on the page is missing", value_seen("1985", "other", SHOWN) is False)
check("digits inside an id or a word are not numbers", value_seen("7731", "other", SHOWN) is False and value_seen("41142", "other", "Ref INV-41142") is False and value_seen("2", "other", "the 2nd copy") is False)
check("the digits of a date are not numbers", value_seen("2026", "other", SHOWN) is False and value_seen("5", "other", "Due 05 Nov 2026") is False)

check("a date is found by its day (2026-11-12 is 12 Nov 2026)", value_seen("2026-11-12", "widget", SHOWN) is True)
check("dates in other spellings are the same day", value_seen("2026-11-05", "widget", "Due Nov 5, 2026") is True and value_seen("2026-09-05", "text", "Return by 05 Sept 2026") is True and value_seen("2026-11-05", "widget", "Due 05/11/2026") is True)
check("a date written long is found as ISO on the page", value_seen("19 October 2026", "text", SHOWN) is True)
check("a different day is missing", value_seen("2026-11-06", "widget", SHOWN) is False)

check("a choice is found by the stems of its words (Approve with deduction ~ Approved with deduction)", value_seen("Approve with deduction", "select", SHOWN) is True)
check("a past tense in d/t counts too (Send back to reviewer ~ Sent back)", value_seen("Send back to reviewer", "select", "Status\nSent back") is True)
check("another choice is missing", value_seen("Reject", "select", SHOWN) is False and value_seen("Send back to reviewer", "select", SHOWN) is False)
check("a choice's words must sit together, not across the page", value_seen("Approve damage waiver", "select", "Approved by Asha\nDamage\t₹480\nWaiver form") is False)
check("a shorter shown label must hold the choice's first word (Approved yes; a Deduction cell or a Back link no)",
      value_seen("Approve with deduction", "select", "Status: Approved") is True
      and value_seen("Approve with deduction", "select", "Charge\tAmount\nDeduction\t₹200") is False
      and value_seen("Send back to reviewer", "select", "← Back\nReviewer\nAsha Rao") is False)
check("help text is not a shown choice", value_seen("Waive the fee", "select", "Staff may waive the fee when a book is damaged in transit.") is False and value_seen("Waive the fee", "select", "Status\nFee waived") is True)
check("a label without long words must appear as a whole word", value_seen("Yes", "select", "Paid\tYes") is True and value_seen("Yes", "select", "Paid yesterday") is False)
check("ticked boxes are each looked for", value_seen("Damaged, Late", "checks", SHOWN) is True and value_seen("Damaged, Lost", "checks", SHOWN) is False)
check("a short text is found by its words in order", value_seen("Invoiced 740, received 738", "text", "Invoiced ₹740, received ₹738.") is True and value_seen("Waived half the fee", "note", SHOWN) is True)
check("a short text in another order is missing", value_seen("received 738, invoiced 740", "text", "Invoiced ₹740, received ₹738.") is False)
check("an empty value, a yes/no box and a long note cannot be checked",
      value_seen("", "select", SHOWN) is None and value_seen("yes", "check", SHOWN) is None
      and value_seen("The borrower called twice about the damage and agreed to pay half of the fee", "note", SHOWN) is None)

VALUES = {"decision": "Approve with deduction", "fee": "1984", "reasons": "Damaged, Late", "review_on": "2026-11-12", "notify": "yes", "note": "Waived half the fee"}
KINDS = {"decision": "select", "fee": "other", "reasons": "checks", "review_on": "widget", "notify": "check", "note": "note"}
report = readback_report(VALUES, KINDS, SHOWN)
check("the report sorts every value, in order", report == {"seen": ["decision", "fee", "reasons", "review_on", "note"], "missing": [], "unchecked": ["notify"]})
report = readback_report({**VALUES, "fee": "2000", "decision": "Reject"}, KINDS, SHOWN)
check("values that are not shown are missing", report["missing"] == ["decision", "fee"] and "reasons" in report["seen"])
BEFORE = "Item LIB-7731\nDue date\n05 Nov 2026\nFee charged\n₹1,984"
check("with the page from before saving, a value that was already there does not count",
      readback_report({"fee": "1984", "due": "2026-11-05"}, {"fee": "other", "due": "widget"}, BEFORE + "\nSaved", before=BEFORE)["missing"] == ["fee", "due"]
      and readback_report({"fee": "1984"}, {"fee": "other"}, BEFORE + "\nPaid\n₹1,984", before=BEFORE)["seen"] == ["fee"])

# Choices that share words: with the control's options known, a shown choice must fit the saved option better than any other
OUTCOMES = ["Approve", "Approve partially", "Decline", "Request information"]
check("with the options known, a shorter sibling is not the saved option (Approve shown, Approve partially saved)",
      value_seen("Approve partially", "select", "Outcome\nApprove", OUTCOMES) is False
      and value_seen("Approve partially", "select", "Outcome\nApproved", OUTCOMES) is False)
check("with the options known, a longer sibling is not the saved option (Approve partially shown, Approve saved)",
      value_seen("Approve", "select", "Outcome\nApprove partially", OUTCOMES) is False
      and value_seen("Approve", "select", "Outcome\nApproved partially", OUTCOMES) is False)
check("with the options known, the right option is still seen in other forms",
      value_seen("Approve", "select", "Outcome\nApproved", OUTCOMES) is True
      and value_seen("Approve partially", "select", "Outcome: Approved partially on 12 Nov", OUTCOMES) is True
      and value_seen("Decline", "select", "Outcome\nDeclined", OUTCOMES) is True)
check("(without the options, the spec's rule still lets a shorter shown label stand for a longer one)", value_seen("Approve partially", "select", "Outcome\nApprove") is True)
check("a choice that is turned around is not shown (Not approved, Approval pending, wasn't approved)",
      value_seen("Approve", "select", "Status\nNot approved") is False
      and value_seen("Approve", "select", "Status\nApproval pending") is False
      and value_seen("Approve", "select", "Status\nRequest wasn't approved") is False
      and value_seen("No refund", "select", "Outcome\nNo refund") is True)
check("a heading is not a shown value (Rejection reason: none given is not Reject)",
      value_seen("Reject", "select", "Rejection reason: none given") is False and value_seen("Reject", "select", "Outcome: Rejected") is True)
check("a label without a content word must be a whole cell, line or heading's value (No, N/A)",
      value_seen("No", "select", "Item No. 7731\nStatus\nApproved") is False and value_seen("No", "select", "No records found") is False
      and value_seen("No", "select", "Police report: No") is True and value_seen("N/A", "select", "Rate\tN/A") is True
      and value_seen("N/A", "select", "Rate N/A") is False)
check("ticked boxes that share words are told apart by the options",
      value_seen("Damaged", "checks", "Reasons: Damaged packaging, Late", ["Damaged", "Damaged packaging", "Late"]) is False
      and value_seen("Damaged, Damaged packaging", "checks", "Reasons: Damaged, Damaged packaging", ["Damaged", "Damaged packaging", "Late"]) is True)
check("a sentence that reports the choice for this record is shown (has been approved!, was approved by)",
      value_seen("Approve", "select", "Your claim has been approved!") is True
      and value_seen("Approve", "select", "This request was approved by the manager.") is True
      and value_seen("Approve", "select", "Your claim was not approved.") is False)
check("a sentence of help text or policy is still not a shown choice",
      value_seen("Approve", "select", "Claims that were approved last month are listed below.") is False
      and value_seen("Waive the fee", "select", "Late fees are waived for members over 65.") is False)
check("European numbers are read (€1.984,00 is 1984, 1.250.000 is 1250000; 1.984 kg stays a decimal)",
      value_seen("1984", "other", "Total €1.984,00") is True and value_seen("1250000", "other", "Total 1.250.000 €") is True
      and value_seen("1984", "other", "Weight 1.984 kg") is False and value_seen("1.984", "other", "Weight 1.984 kg") is True)
check("dates with two-digit years are read as 20xx (05-Nov-26, 05/11/26)",
      value_seen("2026-11-05", "widget", "Due 05-Nov-26") is True and value_seen("2026-11-05", "widget", "Due 05/11/26") is True
      and value_seen("2026-11-06", "widget", "Due 05/11/26") is False and value_seen("26", "other", "Due 05-Nov-26") is False)
check("the report takes each control's options",
      readback_report({"decision": "Approve partially", "fee": "1500"}, {"decision": "select", "fee": "other"}, "Outcome\nApprove\nPayout\n₹1,500",
                      options={"decision": OUTCOMES}) == {"seen": ["fee"], "missing": ["decision"], "unchecked": []})

check("a site that shows the values it saved", site_shows_decisions({"seen": ["a", "b"], "missing": ["c"], "unchecked": []}) is True)
check("a site that does not (nothing seen, or more than one missing, or the page would not open)",
      not site_shows_decisions({"seen": [], "missing": ["a"], "unchecked": ["b"]})
      and not site_shows_decisions({"seen": ["a"], "missing": ["b", "c"], "unchecked": []})
      and not site_shows_decisions({"seen": ["a"], "missing": [], "unchecked": [], "error": "the record's page answered 500"}))


# ------------------------------------------------------------------ part 2: record pages in a browser

SITE = "http://desk.test"
# What the made-up site has saved, per item number (the test sets it the way a save would)
saved: dict[str, dict[str, str]] = {}

# The form every record page carries: it lists every choice, so a page that shows only the form must not count as showing them
FORM = """<form id="decide">
  <label for="decision">Decision</label>
  <select id="decision"><option>Approve</option><option>Approve with deduction</option><option>Send back to reviewer</option></select>
  <fieldset><legend>Reasons</legend>
    <label><input type="checkbox" name="r"> Damaged</label> <label><input type="checkbox" name="r"> Late</label> <label><input type="checkbox" name="r"> Lost</label>
  </fieldset>
  <div><label><input type="radio" name="tier"> Standard</label> <label><input type="radio" name="tier"> Priority</label></div>
  <label for="fee">Fee charged</label><input id="fee" type="number">
  <button type="button" style="color: rebeccapurple">Save decision</button>
</form>"""


# Takes an item number; returns its page, with the saved decision when there is one.
def record_page(no: str) -> str:
    s = saved.get(no)
    shown = ""
    if s:
        shown = f"""<h2>Saved decision</h2>
<dl><dt>Status</dt><dd>{s['status']}</dd><dt>Tier</dt><dd>{s['tier']}</dd><dt>Fee charged</dt><dd>₹{int(s['fee']):,}</dd>
<dt>Next review</dt><dd>{s['review']}</dd><dt>Reasons</dt><dd>{s['reasons']}</dd></dl>"""
    return f"""<!doctype html><html><body>
<nav><a href="/">Home</a> <a href="/items">Items</a></nav>
<h1>Item LIB-{no}</h1>
<dl><dt>Borrower</dt><dd>Asha Rao</dd><dt>Due date</dt><dd>05 Nov 2026</dd></dl>
<table><thead><tr><th>Charge</th><th>Amount</th></tr></thead><tbody><tr><td>Overdue fee</td><td>₹620</td></tr><tr><td>Repair</td><td>₹1,364</td></tr></tbody></table>
{shown}
{FORM}
</body></html>"""


# A single-page app: one document, the record chosen by the address after "#", drawn by script from the site's saved data
APP = """<!doctype html><html><body><div id="root">Loading…</div>
<script>
const SAVED = __SAVED__;
function draw() {
  if (location.hash === '#/items/99') { location.replace('#/login'); return; }
  const m = location.hash.match(/^#\\/items\\/(\\d+)$/);
  const root = document.getElementById('root');
  if (!m) { root.innerHTML = '<h1>Items</h1><p>Pick an item.</p>'; return; }
  const s = SAVED[m[1]];
  setTimeout(() => {
    root.innerHTML = '<h1>Item LIB-' + m[1] + '</h1>'
      + (s ? '<dl><dt>Status</dt><dd>' + s.status + '</dd><dt>Fee charged</dt><dd>₹' + Number(s.fee).toLocaleString('en-IN') + '</dd></dl>' : '')
      + '<label>Decision <select><option>Approve</option><option>Approve with deduction</option></select></label>';
  }, 200);
}
window.addEventListener('hashchange', draw);
draw();
</script></body></html>"""


# More pages of the made-up site, set by the checks: {path: (status, extra headers, body)}
PAGES: dict[str, tuple[int, dict[str, str], str]] = {}


# Answers the made-up site's requests: the pages in PAGES, /items/<n> and /app (with the saved data as it is now); anything
# else is a 404.
async def serve(route) -> None:
    path = route.request.url[len(SITE):].split("#")[0].split("?")[0] or "/"
    if path in PAGES:
        status, headers, body = PAGES[path]
        await route.fulfill(status=status, headers={"content-type": "text/html; charset=utf-8", **headers}, body=body)
    elif path.startswith("/items/"):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=record_page(path.rsplit("/", 1)[1]))
    elif path == "/app":
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=APP.replace("__SAVED__", json.dumps(saved)))
    else:
        await route.fulfill(status=404, content_type="text/html; charset=utf-8", body="<h1>Not found</h1>")


async def part2() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route(SITE + "/**", serve)
        values = {"decision": "Approve with deduction", "reasons": "Damaged, Late", "tier": "Priority", "fee": "1984", "review_on": "2026-11-12"}
        kinds = {"decision": "select", "reasons": "checks", "tier": "select", "fee": "other", "review_on": "widget"}

        # A record that was saved: the page shows the decision (and still carries the form with every choice)
        saved["7"] = {"status": "Approved with deduction", "tier": "Priority", "fee": "1984", "review": "12 Nov 2026", "reasons": "Damaged, Late"}
        await page.goto(SITE + "/items/9")
        report = await read_back(page, SITE + "/items/7", values, kinds, px._quick_read)
        check("a page that shows the decision: every value is seen", report["seen"] == list(values) and report["missing"] == [] and "error" not in report)
        check("the read-back went to the record's own page", page.url == SITE + "/items/7" and "LIB-7" in report["text"])
        check("this site shows the decisions it saved", site_shows_decisions(report))
        check("the labels and options hidden while reading are shown again, styles as they were",
              await page.is_visible("#decision") and await page.is_visible("legend")
              and await page.get_attribute("button", "style") == "color: rebeccapurple"
              and await page.get_attribute("#decision", "style") is None
              and await page.evaluate("() => !document.querySelector('[data-readback-hidden]') && !document.querySelector('style') && document.adoptedStyleSheets.length === 0"))
        report = await read_back(page, SITE + "/items/7", {"tier": "Standard", "decision": "Send back to reviewer"}, {"tier": "select", "decision": "select"}, px._quick_read)
        check("an option the form offers is not taken for the saved one (Standard, Send back are missing)", report["missing"] == ["tier", "decision"])

        # A record that was not saved: only the form, which lists every choice and every box
        await page.goto(SITE + "/items/8")
        raw = await px._quick_read(page)
        check("(the form's options are in the page's text)", value_seen("Approve with deduction", "select", raw) is True and value_seen("Damaged, Late", "checks", raw) is True)
        report = await read_back(page, SITE + "/items/8", values, kinds, px._quick_read)
        check("a page that does not show the decision: the form's options, labels and buttons do not count", report["seen"] == [] and report["missing"] == list(values))
        check("a site like that does not show its decisions", not site_shows_decisions(report))

        # A value that was on the page before saving (the overdue fee, ₹620) is not proof: the page from before is compared
        before = await px._quick_read(page)
        report = await read_back(page, SITE + "/items/8", {"fee": "620"}, {"fee": "other"}, px._quick_read)
        check("(without the page from before, ₹620 in the charges table looks like the saved fee)", report["seen"] == ["fee"])
        report = await read_back(page, SITE + "/items/8", {"fee": "620"}, {"fee": "other"}, px._quick_read, before=before)
        check("with the page from before saving, the unsaved fee is missing", report["missing"] == ["fee"])
        saved["8"] = {"status": "Approved", "tier": "Standard", "fee": "620", "review": "12 Nov 2026", "reasons": "Late"}
        report = await read_back(page, SITE + "/items/8", {"decision": "Approve", "fee": "620", "reasons": "Late"},
                                 {"decision": "select", "fee": "other", "reasons": "checks"}, px._quick_read, before=before)
        check("and once it is saved, the fee shown a second time is seen", report["seen"] == ["decision", "fee", "reasons"])
        saved.pop("8")
        await page.goto(SITE + "/items/8")
        before = await shown_text(page)
        check("shown_text leaves out labels, buttons and options", "Asha Rao" in before and "Save decision" not in before and "Approve with deduction" not in before and "Borrower" not in before)
        saved["8"] = {"status": "Approved", "tier": "Standard", "fee": "620", "review": "12 Nov 2026", "reasons": "Late"}
        report = await read_back(page, SITE + "/items/8", {"decision": "Approve", "fee": "620", "due": "2026-11-05"},
                                 {"decision": "select", "fee": "other", "due": "widget"}, px._quick_read, before=before)
        check("with shown_text as the page from before, only what the save changed counts", report["seen"] == ["decision", "fee"] and report["missing"] == ["due"])

        # A relative address is read against the page that is open
        report = await read_back(page, "/items/7", {"fee": "1984"}, {"fee": "other"}, px._quick_read)
        check("a relative address opens the record too", report["seen"] == ["fee"] and page.url == SITE + "/items/7")

        # A single-page app: the record's address is a hash route of the same document
        saved["12"] = {"status": "Sent back", "fee": "125000"}
        await page.goto(SITE + "/app#/items/3")
        report = await read_back(page, SITE + "/app#/items/12", {"decision": "Send back to reviewer", "fee": "125000"}, {"decision": "select", "fee": "other"}, px._quick_read)
        check("a hash route is opened and read (Sent back, ₹1,25,000)", report["seen"] == ["decision", "fee"] and page.url.endswith("#/items/12"))
        check("the report keeps the page's text, cut at 2,000 characters", "LIB-12" in report["text"] and "Loading" not in report["text"] and len(report["text"]) <= 2000)
        report = await read_back(page, "#/items/12", {"fee": "125000"}, {"fee": "other"}, px._quick_read)
        check("a bare hash route works from the app's own page", report["seen"] == ["fee"])

        # A screen that showed a decision it never saved: the read-back loads the page afresh, so it is not fooled
        await page.goto(SITE + "/app#/items/14")
        await page.wait_for_selector("h1:has-text('LIB-14')")
        await page.evaluate("() => document.getElementById('root').insertAdjacentHTML('beforeend', '<p>Status: Approved with deduction</p>')")
        check("(the screen shows the decision before the read-back)", value_seen("Approve with deduction", "select", await page.inner_text("body")) is True)
        report = await read_back(page, SITE + "/app#/items/14", {"decision": "Approve with deduction"}, {"decision": "select"}, px._quick_read)
        check("a decision that was only on the screen is missing once the record is opened again", report["missing"] == ["decision"])

        # A long page is cut in the report
        saved["15"] = {"status": "Approved", "tier": "Standard", "fee": "620", "review": "12 Nov 2026", "reasons": "Late " + "and late " * 300}
        report = await read_back(page, SITE + "/items/15", {"fee": "620"}, {"fee": "other"}, px._quick_read)
        check("a long page's text is cut at 2,000 characters", len(report["text"]) == 2000 and report["seen"] == ["fee"])

        # A record that is not there
        report = await read_back(page, SITE + "/gone/1", {"fee": "1984", "notify": "yes"}, {"fee": "other", "notify": "check"}, px._quick_read)
        check("a page that answers 404: every value is unchecked and the error says why", report["seen"] == [] and report["missing"] == [] and report["unchecked"] == ["fee", "notify"] and "404" in report.get("error", ""))
        check("no address: nothing is opened", (await read_back(page, "", {"fee": "1"}, {"fee": "other"}, px._quick_read)).get("error") == "no address to open the record again")

        # A site whose Content-Security-Policy blocks inline styles: the labels and options are still hidden while reading
        csp = {"content-security-policy": "default-src 'self'; style-src 'self'; script-src 'self'"}
        outcome_form = """<form><label for="d">Decision</label>
  <select id="d"><option>Approve</option><option>Approve partially</option><option>Decline</option><option>Request information</option></select>
  <label><input type="radio" name="t"> Standard</label> <label><input type="radio" name="t"> Priority</label>
  <button type="button" style="color: teal">Save decision</button></form>"""
        PAGES["/claims/1"] = (200, csp, f"<html><body><h1>Claim 1</h1>{outcome_form}</body></html>")
        PAGES["/claims/2"] = (200, csp, f"<html><body><h1>Claim 2</h1><dl><dt>Outcome</dt><dd>Request information</dd><dt>Tier</dt><dd>Priority</dd></dl>{outcome_form}</body></html>")
        csp_values, csp_kinds = {"decision": "Request information", "tier": "Priority"}, {"decision": "select", "tier": "select"}
        report = await read_back(page, SITE + "/claims/1", csp_values, csp_kinds, px._quick_read)
        check("a page whose security rules block inline styles: the form's options still do not count",
              report["seen"] == [] and report["missing"] == ["decision", "tier"] and not site_shows_decisions(report))
        html = await page.evaluate("() => document.documentElement.outerHTML")
        await shown_text(page)
        check("(and that page is left exactly as it was)", await page.evaluate("() => document.documentElement.outerHTML") == html
              and await page.is_visible("#d") and await page.evaluate("() => document.adoptedStyleSheets.length === 0"))
        report = await read_back(page, SITE + "/claims/2", csp_values, csp_kinds, px._quick_read)
        check("a page with the same rules that shows the decision: it is seen", report["seen"] == ["decision", "tier"])

        # A page whose own styles beat the hiding (a cascade layer's !important, an inline !important): those texts are taken out after
        PAGES["/claims/3"] = (200, {}, """<html><head><style>@layer base { select { display: inline-block !important; } }</style></head><body>
<h1>Claim 3</h1><label>Decision <select><option>Approve</option><option>Decline</option></select></label>
<div><button style="display: inline-block !important">Standard</button> <button style="display: inline-block !important">Priority</button></div></body></html>""")
        report = await read_back(page, SITE + "/claims/3", {"decision": "Decline", "tier": "Priority"}, csp_kinds, px._quick_read)
        check("options and buttons a page's own styles keep shown still do not count", report["missing"] == ["decision", "tier"])

        # Radio buttons and checkboxes whose text is not in a <label>
        PAGES["/claims/4"] = (200, {}, """<html><body><h1>Claim 4</h1><form>
<div><input type="radio" name="t"> Standard <input type="radio" name="t"> Priority</div>
<div><input type="checkbox" name="c"> Damaged <input type="checkbox" name="c"> <b>Late</b><br><input type="checkbox" name="c"> Lost</div>
</form><p>Notes</p></body></html>""")
        report = await read_back(page, SITE + "/claims/4", {"tier": "Priority", "reasons": "Damaged, Late"}, {"tier": "select", "reasons": "checks"}, px._quick_read)
        check("the text next to a radio button or checkbox without a label does not count", report["seen"] == [] and report["missing"] == ["tier", "reasons"])
        html = await page.evaluate("() => document.body.innerHTML")
        text = await shown_text(page)
        check("(those texts are hidden for the read, and the page is put back exactly)",
              "Priority" not in text and "Late" not in text and "Lost" not in text and "Notes" in text and await page.evaluate("() => document.body.innerHTML") == html)

        # The site sends the browser elsewhere: a login page, the list of records, a single-page app's login route
        PAGES["/login"] = (200, {}, "<html><body><h1>Sign in</h1><p>Your session expired.</p><label>Password <input type=password></label></body></html>")
        PAGES["/claims"] = (200, {}, "<html><body><h1>Claims</h1><table><thead><tr><th>Claim</th><th>Outcome</th><th>Payout</th></tr></thead>"
                                     "<tbody><tr><td>CLM-1</td><td>Approve</td><td>₹1,500</td></tr></tbody></table></body></html>")
        PAGES["/claims/7"] = (200, {}, "<html><body><script>location.replace('/login?next=/claims/7')</script></body></html>")
        PAGES["/claims/9"] = (200, {}, "<html><body><script>location.replace('/claims')</script></body></html>")
        payout_values, payout_kinds = {"decision": "Approve", "payout": "1500"}, {"decision": "select", "payout": "other"}
        report = await read_back(page, SITE + "/claims/7", payout_values, payout_kinds, px._quick_read)
        check("sent to a login page: every value is unchecked and the error says where it landed",
              report["unchecked"] == ["decision", "payout"] and report["seen"] == report["missing"] == [] and "landed on " + SITE + "/login" in report.get("error", ""))
        report = await read_back(page, SITE + "/claims/9", payout_values, payout_kinds, px._quick_read)
        check("sent to the list: another record's row is not taken for this one",
              report["unchecked"] == ["decision", "payout"] and report["seen"] == [] and "landed on " + SITE + "/claims," in report.get("error", ""))
        await page.goto(SITE + "/app#/items/3")
        report = await read_back(page, SITE + "/app#/items/99", {"fee": "1"}, {"fee": "other"}, px._quick_read)
        check("a single-page app that routes to its login instead: unchecked", report["unchecked"] == ["fee"] and "#/login" in report.get("error", ""))
        check("a page under the record's own, or one with more in its query, is still the record's page",
              rb._same_record(SITE + "/claims/7/summary?tab=1", SITE + "/claims/7") and rb._same_record(SITE + "/r?id=7&lang=en", SITE + "/r?id=7")
              and not rb._same_record(SITE + "/r?id=8", SITE + "/r?id=7") and not rb._same_record(SITE + "/claims", SITE + "/claims/7"))

        # A record page with a long history table: an Edit and a Delete button on every row
        rows = "".join(f"<tr><td>Change {i}</td><td>₹{i}</td><td><button>Edit</button> <button>Delete</button></td></tr>" for i in range(4000))
        PAGES["/claims/10"] = (200, {}, f"<html><body><h1>Claim 10</h1><p>Outcome: Declined</p><table><thead><tr><th>Change</th><th>Amount</th><th></th></tr></thead><tbody>{rows}</tbody></table></body></html>")
        started = time.monotonic()
        report = await read_back(page, SITE + "/claims/10", {"decision": "Decline"}, {"decision": "select"}, px._quick_read)
        check("a page with 8,000 buttons is read in a few seconds, not tens", report["seen"] == ["decision"] and time.monotonic() - started < 5)
        keep, rb._SHOWN_TIMEOUT_S = rb._SHOWN_TIMEOUT_S, 0.0
        try:
            report = await read_back(page, SITE + "/claims/10", {"decision": "Decline"}, {"decision": "select"}, px._quick_read)
        finally:
            rb._SHOWN_TIMEOUT_S = keep
        check("a page that cannot be read without its forms in time: unchecked, never compared with the options still in it",
              report["unchecked"] == ["decision"] and "could not read the page without its forms" in report.get("error", ""))

        # A record page that is the saved form, filled in: what its controls hold now is what it shows
        PAGES["/claims/11"] = (200, {}, """<html><body><h1>Claim 11</h1><form>
<label>Decision <select><option value="">Choose…</option><option>Approve</option><option selected>Decline</option></select></label>
<label>Payout <input type="number" value="1500"></label>
<div><input type="radio" name="t"> Standard <input type="radio" name="t" checked> Priority</div>
<label><input type="checkbox" name="c" checked> Damaged</label> <label><input type="checkbox" name="c"> Late</label>
<label>Note <textarea>Photos checked</textarea></label>
<button type="button">Save</button></form></body></html>""")
        prefilled_kinds = {"decision": "select", "payout": "other", "tier": "select", "reasons": "checks", "note": "note"}
        report = await read_back(page, SITE + "/claims/11", {"decision": "Decline", "payout": "1500", "tier": "Priority", "reasons": "Damaged", "note": "Photos checked"},
                                 prefilled_kinds, px._quick_read)
        check("the saved form, filled in: the chosen option, the field's value, the ticked radio and box and the note are seen",
              report["seen"] == ["decision", "payout", "tier", "reasons", "note"])
        report = await read_back(page, SITE + "/claims/11", {"decision": "Approve", "tier": "Standard", "reasons": "Damaged, Late"}, prefilled_kinds, px._quick_read)
        check("(the options it does not hold are still missing)", report["missing"] == ["decision", "tier", "reasons"])
        PAGES["/claims/12"] = (200, {}, """<html><body><h1>Claim 12</h1><div>Status <button type="button" aria-haspopup="listbox">Declined</button></div></body></html>""")
        report = await read_back(page, SITE + "/claims/12", {"decision": "Decline"}, {"decision": "select"}, px._quick_read)
        check("a status shown as a dropdown's button is seen", report["seen"] == ["decision"])

        # Frames: a decision shown in a frame is read; a form in a frame does not count
        PAGES["/claims/13"] = (200, {}, """<html><body><h1>Claim 13</h1><iframe srcdoc="<dl><dt>Outcome</dt><dd>Declined</dd></dl>"></iframe>
<iframe srcdoc="<form><select><option>Approve</option><option>Request information</option></select><button>Save</button></form>"></iframe></body></html>""")
        report = await read_back(page, SITE + "/claims/13", {"decision": "Decline"}, {"decision": "select"}, px._quick_read)
        check("a decision shown in a frame is seen", report["seen"] == ["decision"])
        report = await read_back(page, SITE + "/claims/13", {"decision": "Request information"}, {"decision": "select"}, px._quick_read)
        check("the options of a form in a frame do not count", report["missing"] == ["decision"])

        # The page from before saving: the reader's text with a label on the same line as its value; a page longer than the reader keeps
        PAGES["/claims/14"] = (200, {}, "<html><body><h1>Claim 14</h1><p><label>Fee charged</label> ₹620</p><p><label>Status</label> Pending</p></body></html>")
        await page.goto(SITE + "/claims/14")
        before = await px._quick_read(page)
        report = await read_back(page, SITE + "/claims/14", {"fee": "620"}, {"fee": "other"}, px._quick_read, before=before)
        check("the reader's text as the page from before: a value on its label's line is not new", "Fee charged ₹620" in before and report["missing"] == ["fee"])
        history = "<p>" + "History entry text. " * 900 + "</p>"
        PAGES["/claims/15"] = (200, {}, f"<html><body><h1>Claim 15</h1>{history}<table><thead><tr><th>Charge</th><th>Amount</th></tr></thead>"
                                        "<tbody><tr><td>Overdue fee</td><td>₹620</td></tr></tbody></table></body></html>")
        await page.goto(SITE + "/claims/15")
        before = await shown_text(page)
        report = await read_back(page, SITE + "/claims/15", {"fee": "620"}, {"fee": "other"}, px._quick_read, before=before)
        check("a page longer than the reader keeps: shown_text has the rows past the cut too, so they are not new", "Amount: ₹620" in before and report["missing"] == ["fee"])
        report = await read_back(page, SITE + "/claims/15", {"fee": "620"}, {"fee": "other"}, px._quick_read)
        check("(and without the page from before, the row past the cut is read)", report["seen"] == ["fee"])

        # The control's options passed to read_back tell sibling options apart
        PAGES["/claims/16"] = (200, {}, "<html><body><h1>Claim 16</h1><p>Outcome: Approve</p></body></html>")
        report = await read_back(page, SITE + "/claims/16", {"decision": "Approve partially"}, {"decision": "select"}, px._quick_read, options={"decision": OUTCOMES})
        check("read_back with the options: Approve shown is not Approve partially saved", report["missing"] == ["decision"])
        report = await read_back(page, SITE + "/claims/16", {"decision": "Approve"}, {"decision": "select"}, px._quick_read, options={"decision": OUTCOMES})
        check("(and Approve saved is seen)", report["seen"] == ["decision"])

        await browser.close()


asyncio.run(part2())
print(f"\n{checks} checks passed")
