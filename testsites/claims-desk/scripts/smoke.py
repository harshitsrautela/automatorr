"""Smoke test for the Claims Desk: drives the site in a headless browser the way a person would.

    node server.js &                                                          # the site on http://localhost:5183
    python scripts/smoke.py

It resets the decisions; sends a few odd requests a browser would not (another app's cookie that is not %-encoded, a JSON body
of null, a malformed Host header, an unreadable address) and checks that each is answered and the site keeps running. Then it
is sent to sign in, accepts the cookie banner, is refused a wrong password and signs in; dismisses the "What's new" box; makes a
list load fail and retries it; scrolls the endless list; reads a claim and its policy; then decides five claims with different
correct outcomes by filling the decision form inside the claim page's iframe (an ARIA radio group, a number, searchable
comboboxes whose options appear in a portal, a contenteditable note) and checks each saved decision the way grade.py does,
through /admin/state. Last it starts a new session (closing "What's new" with its × this time), runs it out (its 26th page load
is the decision frame), signs in again and resets the decisions. A session that runs out during the other steps is signed in again on the way, as a person would.
Exits 1 at the first failed check.
"""

import asyncio
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FrameLocator, Locator, Page, async_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True  # importing grade.py must not leave a __pycache__ in the site's folder
from grade import check as grade_check  # noqa: E402  (grade.py sits in the site's folder)

BASE = "http://localhost:5183"
SEED = json.loads((ROOT / "data" / "seed.json").read_text())
EXPECTED = json.loads((ROOT / "expected.json").read_text())
LOGIN = SEED["login"]
CLAIMS = {c["claim_no"]: c for c in SEED["claims"]}
POLICIES = {p["policy_no"]: p for p in SEED["policies"]}
# The list's order: newest report first, then by claim number (as the server sorts it)
LISTED = [c["claim_no"] for c in sorted(sorted(SEED["claims"], key=lambda c: c["claim_no"]), key=lambda c: c["reported_on"], reverse=True)]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]
# One claim of each kind, aimed at deeper and deeper rows of the list so that finding it needs more and more scrolling
KINDS = [("Approve", None, 6), ("Approve partially", None, 27), ("Decline", "No cover on loss date", 45), ("Decline", "Peril not covered", 63),
         ("Request information", "Police report", 84)]
NOTE = "Checked against the policy by the smoke test."

checks = 0


# Takes a check's name and whether it held; prints "ok" or "FAIL" with the name and stops the test at the first failure.
def check(name: str, ok: bool) -> None:
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# Takes an admin path and a method; returns the site's JSON answer.
def admin(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


# Takes a method, a path, headers (a "Host" one replaces the usual) and a body; sends them to the site exactly as given, without
# following redirects. Returns (status, headers in lower case, body text), or (0, {}, "") when the site does not answer.
def raw(method: str, path: str, headers: dict | None = None, body: str = "") -> tuple[int, dict, str]:
    headers = headers or {}
    site = urlparse(BASE)
    conn = http.client.HTTPConnection(site.hostname, site.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host="Host" in headers, skip_accept_encoding=True)
        for name, value in {**headers, "Content-Length": str(len(body.encode()))}.items():
            conn.putheader(name, value)
        conn.endheaders(body.encode())
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException):
        return 0, {}, ""
    finally:
        conn.close()


# Takes an ISO date; returns it as the site shows it ("05 Sept 2026").
def nice(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d} {MONTHS[int(m) - 1]} {y}"


# Takes whole rupees; returns them as the site shows them, in Indian grouping ("₹1,25,000").
def rupees(n: int) -> str:
    head, tail = str(n)[:-3], str(n)[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(([head] if head else []) + groups + [tail])


# Takes a claim number; returns True when its amount is exactly its peril's limit (an edge case: still a full Approve).
def at_limit(claim_no: str) -> bool:
    c = CLAIMS[claim_no]
    row = next(r for r in POLICIES[c["policy_no"]]["coverage"] if r["peril"] == c["peril"])
    return row["covered"] and c["amount"] == row["limit"]


# Returns the claims to decide: one of each kind in KINDS, the one nearest its target row (for Approve, an at-limit claim).
def pick_claims() -> list[str]:
    picked = []
    for outcome, why, target in KINDS:
        pool = [n for n in LISTED if EXPECTED[n]["outcome"] == outcome and why in (None, EXPECTED[n].get("reason"), EXPECTED[n].get("document"))]
        if outcome == "Approve":
            pool = [n for n in pool if at_limit(n)] or pool
        picked.append(min(pool, key=lambda n: abs(LISTED.index(n) - target)))
    return picked


# Takes a page; returns the path of the address it shows.
def path_of(page: Page) -> str:
    return urlparse(page.url).path


# Takes a locator, a time limit in seconds and the state to wait for; returns whether it got there in time.
async def seen(loc: Locator, timeout: float = 10.0, state: str = "visible") -> bool:
    try:
        await loc.wait_for(state=state, timeout=timeout * 1000)
        return True
    except PlaywrightError:
        return False


# Takes a page showing the sign-in form; signs in and waits for the page the site sends it back to.
async def sign_in(page: Page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    await page.get_by_label("Username").fill(LOGIN["username"])
    await page.get_by_label("Password").fill(LOGIN["password"])
    async with page.expect_navigation():
        await page.get_by_role("button", name="Sign in").click()


# Takes a page; when it shows the "What's new" box (it appears a moment after the first page of a session loads), presses
# "Got it" and waits until the site has recorded that. Returns whether there was a box.
async def dismiss_whats_new(page: Page) -> bool:
    if not await page.locator("body[data-whats-new='1']").count():
        return False
    dialog = page.get_by_role("dialog", name="What's new")
    await dialog.wait_for(state="visible", timeout=5000)
    async with page.expect_response(lambda r: r.url.endswith("/api/whats-new")):
        await dialog.get_by_role("button", name="Got it").click()
    await dialog.wait_for(state="detached")
    return True


# Takes a page that just loaded something; signs in again when the session ran out (the site brings the page back), then closes
# the "What's new" box. Returns whether it had to sign in.
async def settle(page: Page) -> bool:
    signed = path_of(page) == "/login"
    if signed:
        await sign_in(page)
    await dismiss_whats_new(page)
    return signed


# Takes a page and a path on the site; opens it and settles it. Returns whether it had to sign in.
async def goto(page: Page, path: str) -> bool:
    await page.goto(BASE + path)
    return await settle(page)


# Takes the claims list page; returns (rows shown, whether a Retry box is up, the text under the list).
async def list_state(page: Page) -> tuple[int, bool, str]:
    return (await page.locator("#rows tr").count(), await page.get_by_role("button", name="Retry").count() > 0,
            await page.locator("#list-foot").inner_text())


# Takes the claims list page, the state seen before and a time limit in seconds; waits until the list changes (more rows, a
# Retry box, or its end) and returns the new state.
async def list_changed(page: Page, before: tuple, wait: float = 8.0) -> tuple[int, bool, str]:
    loop = asyncio.get_running_loop()
    end = loop.time() + wait
    while loop.time() < end:
        now = await list_state(page)
        if now[0] != before[0] or (now[1] and not before[1]) or now[2].startswith("All "):
            return now
        await asyncio.sleep(0.15)
    return await list_state(page)


# Takes the claims list page and something to do that asks for more rows; does it, presses Retry while loads fail (at most 4
# times) and returns how many rows the list then shows.
async def more_rows(page: Page, act) -> int:
    before = await list_state(page)
    await act()
    now = await list_changed(page, before)
    for _ in range(4):
        if not now[1]:
            break
        await page.get_by_role("button", name="Retry").click()
        now = await list_changed(page, (now[0], False, ""))
    return now[0]


# Takes the claims list page and a claim number; scrolls the endless list (pressing Retry when a load fails, and "Load more"
# when scrolling loads nothing) until the claim's row is there. Returns the row, or None when the list ended without it.
async def find_row(page: Page, claim_no: str) -> Locator | None:
    state = await list_changed(page, (0, False, ""))
    for _ in range(40):
        row = page.locator("#rows tr", has_text=claim_no)
        if await row.count():
            return row.first
        if state[1]:
            await page.get_by_role("button", name="Retry").click()
            state = await list_changed(page, (state[0], False, ""))
        elif state[2].startswith("All "):
            return None
        else:
            await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            new = await list_changed(page, state, 3.0)
            if new == state and await page.get_by_role("button", name="Load more").count():
                await page.get_by_role("button", name="Load more").click()
                new = await list_changed(page, state)
            state = new
    return None


# Takes a page on its way to a claim (by its key); signs in again whenever the site asks, and waits until the decision form is
# drawn inside the claim page's iframe. Returns the frame, or None after 30 seconds.
async def claim_form(page: Page, key: str) -> FrameLocator | None:
    loop = asyncio.get_running_loop()
    end = loop.time() + 30
    frame = page.frame_locator("iframe.decide")
    while loop.time() < end:
        try:
            if path_of(page) == "/login":
                await settle(page)
                continue
            if path_of(page) == f"/claims/{key}" and await frame.locator("[role=radiogroup]").count():
                await dismiss_whats_new(page)
                return frame
        except PlaywrightError:
            pass  # the page moved on while it was being looked at
        await asyncio.sleep(0.2)
    return None


# Takes a page and a claim number; finds the claim in the endless list, clicks its row and waits for its decision form.
# Returns the form's frame.
async def open_claim(page: Page, claim_no: str) -> FrameLocator:
    key = CLAIMS[claim_no]["key"]
    await goto(page, "/claims")
    row = await find_row(page, claim_no)
    check(f"{claim_no} is found by scrolling the list (row {LISTED.index(claim_no) + 1})", row is not None)
    await row.click()
    await page.wait_for_url(lambda u: f"/claims/{key}" in u or "/login" in u)
    frame = await claim_form(page, key)
    check(f"{claim_no}: clicking its row opens the claim and its decision form", frame is not None)
    return frame


# Takes the decision frame and the option wanted in its combobox; picks it as a person does (type a word, click the option that
# appears in the portal) and returns what the box then shows.
async def pick_option(frame: FrameLocator, value: str) -> str:
    box = frame.get_by_role("combobox")
    await box.click()
    await box.fill("")
    await box.press_sequentially(value.split()[0].lower(), delay=40)
    await frame.get_by_role("option", name=value, exact=True).click()
    return await box.input_value()


# Takes the page, its decision frame, a claim number, the expected decision and a note (or ""); fills the form, submits it and
# returns whether the claim page then showed the toast and the decision.
async def decide(page: Page, frame: FrameLocator, claim_no: str, exp: dict, note: str = "") -> bool:
    radio = frame.get_by_role("radio", name=exp["outcome"], exact=True)
    await radio.click()
    check(f"{claim_no}: the {exp['outcome']!r} radio is checked", await radio.get_attribute("aria-checked") == "true")
    if exp.get("payout") is not None:
        await frame.locator("input[name=payout]").fill(str(exp["payout"]))
    for key in ("reason", "document"):
        if exp.get(key):
            check(f"{claim_no}: {exp[key]!r} is picked from the combobox", await pick_option(frame, exp[key]) == exp[key])
    if note:
        await frame.get_by_role("textbox", name="Internal note (optional)").fill(note)
    await frame.get_by_role("button", name="Submit decision").click()
    toast = page.locator("#toast").filter(has_text=f"Decision saved for {claim_no}.")
    return await seen(toast) and await seen(page.locator("#decided").filter(has_text=exp["outcome"]))


# ------------------------------------------------------------------ the parts


# Requests a browser seldom or never makes, which the site must still answer without stopping: another app's cookie on
# localhost that is not %-encoding (browsers send a host's cookies to every port), a decision posted as JSON null, a Host
# header that is not an address, and an address that cannot be read. Takes a claim that must stay undecided.
def part_odd_requests(claim_no: str) -> None:
    stray = "other=100%"
    check("a stray '%' in another app's cookie still gets the sign-in page", raw("GET", "/login", {"Cookie": stray})[0] == 200)
    form = urlencode({"username": LOGIN["username"], "password": LOGIN["password"], "next": "/claims"})
    status, head, _ = raw("POST", "/login", {"Cookie": stray, "Content-Type": "application/x-www-form-urlencoded"}, form)
    sid = re.match(r"sid=(\w+)", head.get("set-cookie", ""))
    check("... signing in with it works", status == 303 and head.get("location") == "/claims" and sid is not None)
    both = {"Cookie": f"{stray}; sid={sid.group(1)}"}
    check("... and so do the pages and the API", raw("GET", "/claims", both)[0] == 200 and raw("GET", "/api/stats", both)[0] == 200)
    key = CLAIMS[claim_no]["key"]
    status, _, text = raw("POST", f"/api/claims/{key}/decision", {**both, "Content-Type": "application/json"}, "null")
    check("a decision posted as JSON null is refused with 422 'Choose an outcome.', and nothing is saved",
          status == 422 and json.loads(text).get("error") == "Choose an outcome." and admin("/admin/state")[claim_no] is None)
    status, head, _ = raw("GET", "/claims", {"Host": "["})
    check("a malformed Host header is ignored: the page answers as usual (no session: sent to sign in)",
          status == 303 and head.get("location", "").startswith("/login"))
    check("an address that cannot be read ('//[/claims') is answered 400", raw("GET", "//[/claims")[0] == 400)
    check("... and the site keeps running", raw("GET", "/admin/state")[0] == 200)


# Signing in, the cookie banner and the "What's new" box. Ends on the claims list.
async def part_sign_in(page: Page) -> None:
    await page.goto(BASE + "/claims")
    url = urlparse(page.url)
    check("a visit without signing in is sent to /login?next=/claims", url.path == "/login" and parse_qs(url.query).get("next") == ["/claims"])
    banner = page.get_by_role("region", name="Cookie consent")
    check("a new browser gets a cookie banner", await seen(banner, 5))
    fixed = await banner.evaluate("e => { const s = getComputedStyle(e); return s.position === 'fixed' && s.bottom === '0px'; }")
    check("the banner is fixed at the bottom of the window", fixed)
    await banner.get_by_role("button", name="Accept all").click()
    check("Accept all closes the banner", await seen(banner, 5, "detached"))
    cookies = {c["name"]: c["value"] for c in await page.context.cookies()}
    check("Accept all is remembered in a cookie", cookies.get("consent") == "all")
    await page.get_by_label("Username").fill(LOGIN["username"])
    await page.get_by_label("Password").fill("not-the-password")
    await page.get_by_role("button", name="Sign in").click()
    check("a wrong password is refused", await seen(page.get_by_role("alert").filter(has_text="Wrong username or password.")) and path_of(page) == "/login")
    await sign_in(page)
    cookies = {c["name"]: c for c in await page.context.cookies()}
    check("signing in sets a session cookie and goes back to the page asked for", path_of(page) == "/claims" and bool(cookies.get("sid", {}).get("value")))
    dialog = page.get_by_role("dialog", name="What's new")
    check("the first page of a session shows a 'What's new' box", await seen(dialog, 5))
    async with page.expect_response(lambda r: r.url.endswith("/api/whats-new")):
        await dialog.get_by_role("button", name="Got it").click()
    check("'Got it' closes it", await seen(dialog, 5, "detached"))
    await page.goto(BASE + "/claims", wait_until="domcontentloaded")
    check("pages say 'Loading…' until the slow API answers", "Loading…" in await page.locator("#view").inner_text())
    await asyncio.sleep(1.5)
    check("neither the box nor the cookie banner comes back in the same session",
          not await page.locator("body[data-whats-new]").count() and not await dialog.count() and not await banner.count())


# The endless list: its first rows, a failed load with Retry, and loading more by scrolling.
async def part_list(page: Page) -> None:
    state = await list_changed(page, (0, False, ""))
    if state[1]:  # the first load may fail on its own (1 in 15)
        await more_rows(page, page.get_by_role("button", name="Retry").click)
    check("the list starts with 20 rows", await page.locator("#rows tr").count() == 20)
    check("the list has no search box, and its rows are not links",
          await page.locator("#view input").count() == 0 and await page.locator("#rows a").count() == 0)
    admin("/admin/fail-next", "POST")
    await page.get_by_role("button", name="Load more").click()
    box = page.get_by_role("alert").filter(has_text="Something went wrong")
    check("'Load more' asks for more rows; a failed load shows 'Something went wrong'", await seen(box))
    rows = await more_rows(page, box.get_by_role("button", name="Retry").click)
    check("its Retry button loads the next 20 rows", rows == 40)
    rows = await more_rows(page, lambda: page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)"))
    check("scrolling to the bottom loads 20 more rows without pressing anything", rows == 60)


# A claim page's facts and its policy page.
async def part_policy(page: Page, claim_no: str) -> None:
    c = CLAIMS[claim_no]
    pol = POLICIES[c["policy_no"]]
    await open_claim(page, claim_no)
    facts = await page.locator("dl.facts").inner_text()
    check("the claim page shows the peril, the date of loss and the amount claimed in rupees",
          c["peril"] in facts and nice(c["loss_date"]) in facts and rupees(c["amount"]) in facts)
    async with page.expect_navigation():
        await page.get_by_role("link", name=c["policy_no"], exact=True).click()
    await settle(page)
    check("the claim's policy link opens the policy page", path_of(page) == f"/policies/{c['policy_no']}")
    cover = page.locator("table").first
    check("the policy page draws its coverage table", await seen(cover))
    heads = await cover.locator("thead th").all_inner_texts()
    check("the coverage table reads Peril | Covered | Limit (₹) | Deductible (₹)", heads == ["Peril", "Covered", "Limit (₹)", "Deductible (₹)"])
    text = await page.locator("#view").inner_text()
    check("the policy page shows its status and its start and end dates", pol["status"] in text and nice(pol["start"]) in text and nice(pol["end"]) in text)


# Deciding the picked claims through the iframe form, each checked as grade.py checks it.
async def part_decisions(page: Page, picked: list[str]) -> None:
    radio_ids = []
    typed_tried = False
    for i, claim_no in enumerate(picked):
        exp = EXPECTED[claim_no]
        frame = await open_claim(page, claim_no)
        radio_ids.append(await frame.get_by_role("radio").first.get_attribute("id"))
        if exp.get("reason") and not typed_tried:
            typed_tried = True
            await frame.get_by_role("radio", name="Decline", exact=True).click()
            box = frame.get_by_role("combobox")
            await box.fill(exp["reason"])
            check("typing in the combobox shows matching options in a portal", await seen(frame.get_by_role("option", name=exp["reason"], exact=True)))
            await box.press("Tab")
            await page.wait_for_timeout(400)
            check("leaving the combobox without picking an option throws the typed text away", await box.input_value() == "")
            await frame.get_by_role("button", name="Submit decision").click()
            refused = frame.get_by_role("alert").filter(has_text="Choose a decline reason from the list.")
            check("so the form refuses to submit, and nothing is saved", await seen(refused, 5) and admin("/admin/state")[claim_no] is None)
        note = NOTE if i == 0 else ""
        check(f"{claim_no}: submitting shows a toast and the decision on the claim page", await decide(page, frame, claim_no, exp, note))
        saved = admin("/admin/state")[claim_no]
        good, why = grade_check(exp, saved)
        what = exp.get("reason") or exp.get("document") or (f"payout {exp['payout']}" if exp.get("payout") is not None else "")
        check(f"{claim_no}: saved as {exp['outcome']} / {what} ({why or 'as expected.json says'})", good)
        if note:
            check(f"{claim_no}: the contenteditable note is saved", saved.get("note") == NOTE)
    check("control ids are new on every draw", len(set(radio_ids)) == len(radio_ids) and all(radio_ids))
    last = picked[-1]
    await goto(page, f"/claims/{CLAIMS[last]['key']}")
    line = page.locator("#decided")
    check("the claim page shows the saved decision after a reload", await seen(line.filter(has_text=EXPECTED[last]["outcome"])))
    state = admin("/admin/state")
    check("only the five claims are decided", sorted(k for k, v in state.items() if v) == sorted(picked))


# A session's 26th page load ends it; when that load is the decision frame, the whole window goes to sign in and comes back.
async def part_expiry(page: Page, claim_no: str) -> None:
    key = CLAIMS[claim_no]["key"]
    await page.goto(BASE + "/logout")
    check("signing out goes to the sign-in page", path_of(page) == "/login")
    await sign_in(page)  # a new session: its first page load is the dashboard
    dialog = page.get_by_role("dialog", name="What's new")
    check("a new session starts on the dashboard and shows 'What's new' again", path_of(page) == "/" and await seen(dialog, 5))
    async with page.expect_response(lambda r: r.url.endswith("/api/whats-new")):
        await dialog.get_by_role("button", name="Close", exact=True).click()
    await page.goto(BASE + "/")  # page load 2
    await asyncio.sleep(1.5)  # the box would be up by now
    check("closing it with its × keeps it away for the rest of the session too",
          not await page.locator("body[data-whats-new]").count() and not await dialog.count())
    for _ in range(22):  # page loads 3 to 24
        await page.goto(BASE + "/")
    check("a session serves 24 page loads", path_of(page) == "/")
    await page.goto(BASE + f"/claims/{key}")  # page load 25; its decision frame is load 26
    try:
        await page.wait_for_url(lambda u: urlparse(u).path == "/login", timeout=10000)
    except PlaywrightError:
        pass
    query = parse_qs(urlparse(page.url).query)
    check("the 26th load (the decision frame) ends the session and sends the whole window to sign in",
          path_of(page) == "/login" and query.get("expired") == ["1"] and query.get("next") == [f"/claims/{key}"])
    check("the sign-in page says the session expired", await seen(page.get_by_text("Your session has expired."), 5))
    await sign_in(page)
    check("signing in again goes back to the claim", path_of(page) == f"/claims/{key}")
    check("and its decision form is drawn again", await claim_form(page, key) is not None)


# Runs every part against the site on BASE (which must be up), then resets the decisions.
async def main() -> None:
    try:
        state = admin("/admin/state")
    except (urllib.error.URLError, OSError):
        print(f"the Claims Desk is not running on {BASE} (cd testsites/claims-desk && node server.js)")
        sys.exit(1)
    check("the site serves 100 claims", len(state) == 100 and len(EXPECTED) == 100)
    admin("/admin/reset", "POST")
    check("a reset clears every decision", all(v is None for v in admin("/admin/state").values()))
    picked = pick_claims()
    check("five claims with five different expected outcomes are picked",
          len({(EXPECTED[n]["outcome"], EXPECTED[n].get("reason")) for n in picked}) == 5 and any(at_limit(n) for n in picked))
    part_odd_requests(picked[0])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            context.set_default_timeout(15000)
            page = await context.new_page()
            await part_sign_in(page)
            await part_list(page)
            await part_policy(page, picked[2])
            await part_decisions(page, picked)
            await part_expiry(page, picked[0])
        finally:
            await browser.close()
    admin("/admin/reset", "POST")
    check("the decisions are reset at the end", all(v is None for v in admin("/admin/state").values()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            admin("/admin/reset", "POST")  # never leave the smoke test's decisions behind, even after a failure
        except (urllib.error.URLError, OSError):
            pass
    print(f"\n{checks} checks passed")
