"""Builds tests/fixtures/hazards_trace.json: a trace of an agent working through ONE claim on the Claims Desk (the hazards site,
testsites/claims-desk, port 5183), in the same format the real recorder (recipe/trace.py) produces, plus two shorter traces of
other kinds of decision (an approval with a payout, a request for a document) for teaching the plan the fields of those branches.

The main trace is driven by a script that behaves the way the live agent does on this site: it is sent to sign in, accepts the
cookie banner, signs in with the test account written in the task's instructions, closes the "What's new" box, presses Retry on
the list's failed load, scrolls the endless list (scrolling is not recorded, as the real recorder does not record it) and presses
"Load more" until the claim's row shows, clicks the row (a <tr> with no name), opens the claim's policy (a link whose text is an
id), goes back, and fills the decision form inside the claim page's iframe: an ARIA radio button, a searchable combobox (types
a few words, clicks the option that appears in a portal), a contenteditable note, then Submit decision.

    python tests/make_hazards_fixture.py        # needs the Claims Desk on :5183; it resets the site's decisions
"""

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sites import site_path  # noqa: E402
from recipe.plan import trace_key  # noqa: E402

SITE = "http://localhost:5183"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = site_path("claims-desk")
SEED = json.load(open(os.path.join(ROOT, "data", "seed.json")))
EXPECTED = json.load(open(os.path.join(ROOT, "expected.json")))
CLAIMS = {c["claim_no"]: c for c in SEED["claims"]}
LOGIN = SEED["login"]  # test values for this local site, written by its generator
# The list's order: newest report first, then by claim number (as the server sorts it)
LISTED = [c["claim_no"] for c in sorted(sorted(SEED["claims"], key=lambda c: c["claim_no"]), key=lambda c: c["reported_on"], reverse=True)]
INSTRUCTIONS = f"""Sign in to the Claims Desk as {LOGIN['username']} with the password {LOGIN['password']}. For each claim, open the claims list, find the claim and open it, open its policy, and decide the claim in the decision form. The first rule that applies decides:
1. The policy is not Active, or the date of loss is outside the policy's start and end dates: Decline, reason No cover on loss date.
2. The claim's peril is not covered in the policy's coverage table: Decline, reason Peril not covered.
3. The peril is Theft and there is no police report: Request information, document Police report.
4. The amount claimed is more than the peril's limit: Approve partially, payout = limit - deductible.
5. Otherwise Approve, payout = amount claimed - deductible."""


# Takes an expected outcome, a reason or document (or None) and the rows of the list it should be between; returns the first
# claim of that kind in that part of the list.
def pick(outcome, why, low, high):
    return next(n for n in LISTED[low:high] if EXPECTED[n]["outcome"] == outcome and why in (None, EXPECTED[n].get("reason"), EXPECTED[n].get("document")))


MAIN = pick("Decline", "No cover on loss date", 44, 70)  # deep in the list: found by scrolling and "Load more"
OTHERS = [pick("Approve", None, 0, 20), pick("Request information", "Police report", 0, 40)]

# What the recorder keeps of an element: its tag, its attributes, its accessible name as "text" (the name a screen reader
# reads), and for an element with no name the text inside it; for one choice of a group, the group's name.
_INFO = """(el) => {
  const clean = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const byIds = (ids) => clean(String(ids || '').split(/\\s+/).map((i) => { const e = i && document.getElementById(i); return e ? e.innerText : ''; }).join(' '));
  const role = (el.getAttribute('role') || '').toLowerCase();
  const tag = el.tagName.toLowerCase();
  let name = clean(el.getAttribute('aria-label')) || byIds(el.getAttribute('aria-labelledby'));
  if (!name && el.labels && el.labels.length) name = clean(el.labels[0].innerText);
  if (!name && (['button', 'a'].includes(tag) || ['radio', 'option', 'button', 'link', 'checkbox', 'switch', 'tab'].includes(role))) name = clean(el.innerText);
  const out = {node: tag, attrs: Object.fromEntries([...el.attributes].map((a) => [a.name, a.value])), text: name, xpath: ''};
  if (!name) out.inner = clean(el.innerText).slice(0, 300);
  if (['radio', 'checkbox', 'switch', 'option', 'menuitemradio', 'menuitemcheckbox'].includes(role)) {
    const g = el.parentElement && el.parentElement.closest('[role=radiogroup], [role=group], [role=listbox], [role=menu]');
    const gn = g ? (clean(g.getAttribute('aria-label')) || byIds(g.getAttribute('aria-labelledby'))) : '';
    if (gn) out.group = gn;
  }
  return out;
}"""


# Takes an admin path; sends it to the site (POST) and returns its answer.
def admin(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(SITE + path, method="POST", data=b"")))


class Recorder:
    """Collects trace entries the way recipe/trace.py does: one per action, with the page's text on the first visit of an address."""

    def __init__(self):
        self.trace = []
        self.seen_urls = set()

    # Takes the page; returns its text as the agent would read it (the page and its frames), once it has finished loading.
    def page_text(self, page):
        for _ in range(40):
            text = page.inner_text("body")
            if "Loading…" not in text:
                break
            page.wait_for_timeout(150)
        for frame in page.frames[1:]:
            try:
                text += "\n" + frame.inner_text("body", timeout=2000)
            except Exception:  # noqa: BLE001
                pass
        return text[:8000]

    # Records one action. Takes the page, the kind, the element's locator (or None), the agent's goal and the typed value.
    def add(self, page, kind, locator, goal, value=None):
        entry = {"step": len(self.trace) + 1, "goal": goal, "kind": kind, "url": page.url, "el": None}
        if value is not None:
            entry["value"] = value
        if locator is not None:
            entry["el"] = locator.evaluate(_INFO)
            entry["el"]["xpath"] = f"el{len(self.trace) + 1}:" + (entry["el"]["attrs"].get("name") or entry["el"]["text"] or entry["el"]["node"])
        if page.url not in self.seen_urls:
            entry["page_text"] = self.page_text(page)
            self.seen_urls.add(page.url)
        self.trace.append(entry)

    # Records a click and makes it.
    def click(self, page, locator, goal):
        self.add(page, "click", locator, goal)
        locator.click()

    # Records typing and does it (a key at a time, as a person types).
    def type(self, page, locator, value, goal):
        self.add(page, "input", locator, goal, value)
        if locator.evaluate("e => e.isContentEditable"):
            locator.fill(value)
        else:
            locator.fill("")
            locator.press_sequentially(value, delay=30)


# Takes the claims list page; waits until its rows (or its Retry box) show, pressing Retry silently for a load that failed by
# chance (1 in 15), so the trace keeps only the Retry it means to show. Returns how many rows show.
def rows_settled(page, quiet=True):
    for _ in range(80):
        if quiet and page.get_by_role("button", name="Retry").count():
            page.get_by_role("button", name="Retry").click()
        foot = page.locator("#list-foot").inner_text() if page.locator("#list-foot").count() else ""
        if page.locator("#rows tr").count() and "Loading" not in foot:
            return page.locator("#rows tr").count()
        page.wait_for_timeout(150)
    return page.locator("#rows tr").count()


# Takes a page and a claim number; signs in (when sent to), closes the banner and the box without recording, and opens the
# claims list. Used before the shorter traces, which start on the list like an agent that is signed in already.
def to_list_quietly(page):
    page.goto(SITE + "/claims")
    if "/login" in page.url:
        page.get_by_label("Username").fill(LOGIN["username"])
        page.get_by_label("Password").fill(LOGIN["password"])
        with page.expect_navigation():
            page.get_by_role("button", name="Sign in").click()
    banner = page.get_by_role("region", name="Cookie consent")
    dialog = page.get_by_role("dialog", name="What's new")
    page.wait_for_timeout(1300)
    if dialog.count():  # it covers the banner too
        dialog.get_by_role("button", name="Got it").click()
        dialog.wait_for(state="detached")
    if banner.count():
        banner.get_by_role("button", name="Reject non-essential").click()
    rows_settled(page)


# Takes the recorder, the page and a claim number; opens the claim from the list (it must be among the rows shown), reads its
# policy and comes back. Returns the decision form's frame.
def open_claim_and_policy(rec, page, claim_no):
    c = CLAIMS[claim_no]
    rec.click(page, page.locator("#rows tr", has_text=claim_no), f"Open claim {claim_no} from the list.")
    page.wait_for_url(f"**/claims/{c['key']}")
    frame = page.frame_locator("iframe.decide")
    frame.locator("[role=radiogroup]").wait_for()
    link = page.get_by_role("link", name=c["policy_no"], exact=True)
    rec.click(page, link, f"Open policy {c['policy_no']} to check its status, dates and coverage.")
    page.wait_for_url(f"**/policies/{c['policy_no']}")
    page.locator("table").first.wait_for()
    rec.add(page, "back", None, "Go back to the claim to decide it.")
    page.go_back()
    page.wait_for_url(f"**/claims/{c['key']}")
    frame.locator("[role=radiogroup]").wait_for()
    return frame


# Takes the recorder, the page, the form's frame and the claim number; picks the outcome and fills what it needs, as the
# expected decision says, and submits.
def decide(rec, page, frame, claim_no, note=""):
    e = EXPECTED[claim_no]
    rec.click(page, frame.get_by_role("radio", name=e["outcome"], exact=True), f"Choose {e['outcome']}.")
    if e.get("payout") is not None:
        rec.type(page, frame.locator("input[name=payout]"), str(e["payout"]), "Enter the payout.")
    for name in ("reason", "document"):
        if e.get(name):
            box = frame.get_by_role("combobox")
            rec.click(page, box, f"Open the {name} list.")
            rec.type(page, box, " ".join(e[name].split()[:2]), f"Search the {name} list.")
            option = frame.get_by_role("option", name=e[name], exact=True)
            option.wait_for()
            rec.click(page, option, f"Pick {e[name]}.")
    if note:
        rec.type(page, frame.get_by_role("textbox", name="Internal note (optional)"), note, "Write an internal note.")
    rec.click(page, frame.get_by_role("button", name="Submit decision"), "Submit the decision.")
    page.locator("#toast").filter(has_text=f"Decision saved for {claim_no}.").wait_for()


# Takes the browser; records the main trace in a fresh browser context (no cookies) and returns it.
def record_main(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    rec = Recorder()
    page.goto(SITE + "/claims")  # sent to /login?next=%2Fclaims
    banner = page.get_by_role("region", name="Cookie consent")
    banner.wait_for()
    rec.click(page, banner.get_by_role("button", name="Accept all"), "Accept the cookie banner so it does not cover the page.")
    rec.type(page, page.get_by_label("Username"), LOGIN["username"], "Type the username from the instructions.")
    rec.type(page, page.get_by_label("Password"), LOGIN["password"], "Type the password from the instructions.")
    admin("/admin/fail-next")  # the list's first load fails, so the agent meets the Retry box
    with page.expect_navigation():
        rec.click(page, page.get_by_role("button", name="Sign in"), "Sign in.")
    dialog = page.get_by_role("dialog", name="What's new")
    dialog.wait_for()
    rec.click(page, dialog.get_by_role("button", name="Got it"), "Close the What's new box.")
    page.get_by_role("button", name="Retry").wait_for()
    rec.click(page, page.get_by_role("button", name="Retry"), "The list failed to load: press Retry.")
    rows_settled(page)
    target = LISTED.index(MAIN)
    while page.locator("#rows tr").count() + 20 <= target:  # scrolling, which the recorder does not record
        before = page.locator("#rows tr").count()
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        for _ in range(40):
            rows_settled(page)
            if page.locator("#rows tr").count() > before:
                break
            page.wait_for_timeout(150)
    while not page.locator("#rows tr", has_text=MAIN).count():
        rec.click(page, page.get_by_role("button", name="Load more"), f"Load more claims to find {MAIN}.")
        rows_settled(page)
    frame = open_claim_and_policy(rec, page, MAIN)
    decide(rec, page, frame, MAIN, note="Checked against the policy.")
    context.close()
    return rec.trace


with sync_playwright() as pw:
    admin("/admin/reset")
    browser = pw.chromium.launch(headless=True)

    # ---- the main trace: everything in the way of a fresh browser. The form draws its controls with random ids, and now and
    # then a random id looks like a stable one ("cewrtvi5", like "field5"); the note is then keyed by that id, which the next
    # draw does not have. A trace like that is a real possibility, but not the one this fixture is for: it is made again.
    for _attempt in range(6):
        admin("/admin/reset")
        main_trace = record_main(browser)
        note = next(e["el"] for e in main_trace if e["el"] and "contenteditable" in e["el"]["attrs"])
        if trace_key(note).startswith("edit:"):
            break
    else:
        raise SystemExit("the note's id looked stable in every try")

    # ---- shorter traces of the other kinds of decision, by an agent that is signed in already
    others = {}
    for claim_no in OTHERS:
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        to_list_quietly(page)
        while not page.locator("#rows tr", has_text=claim_no).count():
            page.get_by_role("button", name="Load more").click()
            rows_settled(page)
        rec = Recorder()
        frame = open_claim_and_policy(rec, page, claim_no)
        decide(rec, page, frame, claim_no)
        others[claim_no] = {"fields": {"claim_no": claim_no}, "trace": rec.trace}
        context.close()
    browser.close()
    admin("/admin/reset")

os.makedirs(os.path.join(HERE, "fixtures"), exist_ok=True)
json.dump({"claim_no": MAIN, "fields": {"claim_no": MAIN}, "instructions": INSTRUCTIONS, "trace": main_trace, "other_branches": others},
          open(os.path.join(HERE, "fixtures", "hazards_trace.json"), "w"), indent=1, ensure_ascii=False)
kinds = ", ".join(n + " (" + EXPECTED[n]["outcome"] + ")" for n in OTHERS)
print(f"{MAIN}: {len(main_trace)} entries; page text on {sum(1 for t in main_trace if t.get('page_text'))} pages; other branches: {kinds}")
