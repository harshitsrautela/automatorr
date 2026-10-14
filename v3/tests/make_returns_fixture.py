"""Builds tests/fixtures/returns_trace.json: a trace of an agent working through ONE return on the Returns Desk site, in the same
format the real recorder (recipe/trace.py) produces. It is driven by a script that behaves the way the live agent does on this
site (finds the return by clicking Next through the list, detours through the order, the item and the policy page, comes back
to the return, fills the form, picks the date in the calendar), so the plan engine can be developed offline.

    python tests/make_returns_fixture.py        # needs the Returns Desk site on :5181; it resets the site's decisions
"""

import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sites import site_path  # noqa: E402

from playwright.sync_api import sync_playwright

SITE = "http://localhost:5181"
HERE = os.path.dirname(os.path.abspath(__file__))
SEED = json.load(open(site_path("returns-desk", "data/seed.json")))
EXPECTED = json.load(open(site_path("returns-desk", "expected.json")))
RETURNS = {r["rma"]: r for r in SEED["returns"]}
# A change-of-mind refund (so the route needs the order, the item and the policy page), as far down the list as possible
ordered = sorted(RETURNS)
candidates = [n for n in ordered if RETURNS[n]["reason"] == "Changed mind" and EXPECTED[n]["disposition"] == "Refund" and RETURNS[n]["condition"] != "Unopened"]
RMA = candidates[len(candidates) // 2 + 3]
rec = RETURNS[RMA]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

trace = []
seen_urls = set()
step = [0]

_INFO = """(el) => ({
  node: el.tagName.toLowerCase(),
  attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value])),
  text: (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || '').trim().slice(0, 120),
  xpath: ''
})"""


def page_text(page):
    return page.inner_text("body")[:8000]


def record(page, kind, locator, goal, value=None):
    step[0] += 1
    entry = {"step": step[0], "goal": goal, "kind": kind, "url": page.url, "el": None}
    if value is not None:
        entry["value"] = value
    if locator is not None:
        entry["el"] = locator.evaluate(_INFO)
        if kind == "input":
            label = locator.evaluate("el => (el.labels && el.labels[0] ? el.labels[0].innerText : '')")
            entry["el"]["text"] = label or entry["el"]["text"]
    if page.url not in seen_urls:
        entry["page_text"] = page_text(page)
        seen_urls.add(page.url)
    trace.append(entry)


def click(page, locator, goal):
    record(page, "click", locator, goal)
    locator.click()
    page.wait_for_load_state("load")


with sync_playwright() as pw:
    urllib.request.urlopen(urllib.request.Request(SITE + "/admin/reset", method="POST"))
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(SITE + "/")
    click(page, page.get_by_role("link", name="Returns", exact=True), "Open the Returns page.")
    # no search box: click Next until the return is on the page
    while page.get_by_role("link", name=RMA, exact=True).count() == 0:
        click(page, page.get_by_role("link", name="Next ›", exact=True), f"Look for {RMA} on the next page of the list.")
    click(page, page.get_by_role("link", name=RMA, exact=True), f"Open return {RMA}.")
    detail_url = page.url
    click(page, page.get_by_role("link", name=rec["order_no"], exact=True), f"Open order {rec['order_no']} to read the unit price.")
    step[0] += 1
    trace.append({"step": step[0], "goal": "Go back to the return.", "kind": "back", "url": page.url, "el": None})  # the url is where the step starts
    page.go_back()
    click(page, page.get_by_role("link", name=rec["sku"], exact=True), f"Open item {rec['sku']} to see its category and whether it is final sale.")
    click(page, page.get_by_role("link", name="Return policy", exact=True), "Open the Return policy to find the restocking fee for this category.")
    click(page, page.get_by_role("link", name="Returns", exact=True), "Go back to the Returns list to open the return again and fill the form.")
    while page.get_by_role("link", name=RMA, exact=True).count() == 0:
        click(page, page.get_by_role("link", name="Next ›", exact=True), f"Look for {RMA} on the next page of the list.")
    click(page, page.get_by_role("link", name=RMA, exact=True), f"Open return {RMA} to fill in the decision.")
    e = EXPECTED[RMA]
    record(page, "select", page.locator("#disposition"), "Choose Refund.", value="Refund")
    page.select_option("#disposition", label="Refund")
    record(page, "input", page.locator("#refund_amount"), "Enter the refund amount.", value=str(e["refund_amount"]))
    page.fill("#refund_amount", str(e["refund_amount"]))
    click(page, page.get_by_role("button", name="Choose date", exact=True), "Open the calendar to choose the pick-up date.")
    y, m, d = map(int, e["pickup_date"].split("-"))
    while page.inner_text("#cal-title") != f"{MONTHS[m - 1]} {y}":
        click(page, page.locator("#cal-next"), "Go to the next month in the calendar.")
    click(page, page.locator(f"#cal-grid button:text-is('{d}')"), f"Choose day {d} as the pick-up date.")
    click(page, page.get_by_role("button", name="Save decision", exact=True), "Save the decision.")
    click(page, page.locator("#confirm-yes"), "Confirm the decision in the dialog.")
    browser.close()
    urllib.request.urlopen(urllib.request.Request(SITE + "/admin/reset", method="POST"))

for e in trace:
    if e["kind"] == "click" and e["el"]:
        e["el"]["xpath"] = ""
os.makedirs(os.path.join(HERE, "fixtures"), exist_ok=True)
json.dump({"rma": RMA, "fields": {"rma": RMA}, "trace": trace}, open(os.path.join(HERE, "fixtures", "returns_trace.json"), "w"), indent=1)
print(f"{RMA}: {len(trace)} entries; page text on {sum(1 for t in trace if t.get('page_text'))} pages; pager clicks: {sum(1 for t in trace if t['kind']=='click' and (t['el'] or {}).get('text','').startswith('Next'))}")
