# Claims Desk: a fifth test site, with the hazards of real web apps

A property insurance claims desk. For each claim the agent decides **Approve / Approve partially / Decline / Request
information**, works out the payout from the policy's coverage table, and fills a decision form that lives in an iframe and is
built from ARIA widgets.

The first four sites test reading and deciding. This one tests whether the terminautomator plan engine copes with the things real
web apps put in the way: a sign-in that runs out, banners and pop-ups over the page, a list that loads as you scroll and sometimes
fails, slow answers, and a form made of custom widgets inside a frame.

## What is hard on purpose

| Feature | Why it is hard |
|---|---|
| A **sign-in page** (`/login`) and a session cookie; **sessions expire after 25 page loads or 10 minutes**, and the site then sends the browser to `/login?expired=1&next=...` and back to that page after signing in | a run of many records signs in several times, in the middle of a record; the decision frame counts as a page load, and when it is the one that runs out, the whole window (not just the frame) is sent to sign in and comes back to the claim |
| A **cookie-consent banner** fixed at the bottom of the window ("Accept all" / "Reject non-essential") on every new browser | it covers whatever is at the bottom of the page until it is answered |
| A **"What's new" box** (a modal: role=dialog, aria-modal) **once per session**: it appears on the session's first page and, while it is left open, on the pages after it, until it is closed with "Got it" or the × (accessible name "Close") | it appears half a second after the page, over everything, and comes back with every new session |
| The claims list `/claims` **loads as you scroll**: 20 rows, then 20 more each time the window nears the bottom ("Load more" is a fallback); **no search box**; rows are clickable `<tr>`s, not links | the record has to be found by scrolling, and clicked by its row |
| About **1 list load in 15 fails** with a "Something went wrong" box and a **Retry** button | a retry is needed to go on |
| **Slow answers** (300 to 1,500 ms) for every API call and for the decision frame, with "Loading…" until they arrive | content arrives late, twice over on the claim page (its facts, then the frame, then the form in it) |
| The claim page links to its **policy** (`POL-` + digits), whose page holds the status, the start and end dates and the **coverage table** (Peril, Covered, Limit (₹), Deductible (₹)) | the facts that decide are on a second page reached by a link whose text is an id |
| The **decision form is inside a same-origin `<iframe>`** | controls are not in the page's own document |
| The outcome is an **ARIA radio group** (`div[role=radiogroup]` of `div[role=radio]`) | no native radio buttons to set |
| The decline reason and the document needed are **searchable comboboxes** (`div` + `input[role=combobox]`): the options appear a moment after typing, in a **portal** at the end of the frame's body, and **typing alone chooses nothing** (leaving the box throws typed text away; an option must be clicked or picked with Enter) | filling the input is not enough; the option has to be found outside the form and picked |
| An optional **contenteditable** "Internal note" | not an input or a textarea |
| Saving is a `fetch`; the claim page shows a **toast** and redraws its decision line | no page load after saving |
| **Control ids are random on every draw**; only native inputs keep a stable `name` (`payout`, `reason`, `document`) | an engine that finds fields by id breaks |
| Opaque addresses (`/claims/9f1c04ab`), amounts in Indian grouping (`₹5,12,550`), dates as `05 Sept 2026` | nothing can be read off the address |

## Run it

```bash
cd testsites/claims-desk
python3 scripts/generate_seed.py     # only to rebuild the data (seeded, so the output is stable)
node server.js                       # http://localhost:5183 (127.0.0.1 only), no install needed
curl -X POST localhost:5183/admin/reset        # reset the decisions before every test
python3 grade.py 12                  # grade the first 12 records after a run (no number: all 100)
```

Sign in as **`desk.auditor`** with password **`audit-2026`**. These are test values for this local site only; they are kept in
`data/seed.json` (written by the generator, which also prints them).

Knobs for testing (environment variables of `node server.js`): `LIST_FAIL_RATE` (default `0.0667`, 1 in 15; `0` turns the failures
off) and `DELAY_MS` (default `300-1500`).

## The task (one record = `{"claim_no": "CLM-26-04123"}`)

The first rule that applies decides:

1. The policy is not **Active**, or the date of loss is outside the policy's start and end dates: **Decline**, reason *No cover on
   loss date* (a loss on the first or the last day is covered).
2. The claim's peril is not covered in the policy's coverage table: **Decline**, reason *Peril not covered*.
3. The peril is **Theft** and there is no police report: **Request information**, document *Police report*.
4. The amount claimed is more than the peril's limit: **Approve partially**, payout = limit - deductible.
5. Otherwise **Approve**, payout = amount claimed - deductible (an amount exactly at the limit is a full Approve).

Data: 100 claims on 71 policies (16 policies have more than one claim): 32 Approve, 22 Approve partially, 20 Decline *No cover on
loss date* (10 on lapsed policies, 10 with the loss outside the dates), 16 Decline *Peril not covered*, 10 Request information
*Police report*; 27 thefts. Edge cases are in the data: a loss on the policy's last day (4) or first day (2), a claim exactly at
the limit (4), a lapsed policy whose dates do cover the loss, a lapsed policy that also does not cover the peril (the first rule
wins), and a theft without a police report on a policy that does not cover theft (the peril rule wins). *Duplicate claim*,
*Photos* and *Repair invoice* are options in the form that no claim needs.

## Files

- `server.js`: the site (page shells, the JSON API, sign-in and sessions, the admin endpoints). `public/app.js`: every page but
  the form (the cookie banner, the "What's new" box, the endless list with Retry, the claim and policy pages).
  `public/decide.js`: the decision form in the iframe (the ARIA radio group, the comboboxes and their portal, the note).
  `public/style.css`.
- `data/seed.json`: what the site serves, with the test sign-in. `data/state.json`: decisions made.
- `expected.json`: the correct decision per claim (**never served**). `records.json`: the 100 records to feed the agent.
- `grade.py [N]`: compares `/admin/state` with `expected.json` (outcome, payout, decline reason, document asked for).
- `scripts/generate_seed.py`: builds the data and computes the answers by rules independent of the scenario labels (and asserts
  that every scenario got the outcome it was built for).
- `scripts/smoke.py`: first sends a few odd requests (another app's cookie on localhost that is not %-encoded, a decision posted
  as JSON `null`, a malformed Host header, an address that cannot be read) and checks that each gets an answer (the page, 422,
  the usual redirect, 400) and the site keeps running; then
  drives the site in headless Chromium (Playwright): is sent to sign in, accepts the cookie banner, is refused
  a wrong password, signs in, dismisses "What's new", makes a list load fail and retries it, scrolls the list, reads a claim and
  its policy, decides five claims of five different outcomes through the iframe form (including a typed-but-not-picked combobox
  value that must be refused), checks each saved decision as `grade.py` does, starts a new session and closes "What's new" with
  its × (it must not come back), runs that session out at its 26th load (the decision frame) and signs in again, and resets the
  decisions. Run it with the site up:
  `python scripts/smoke.py`.
- Admin for the harness (no sign-in; the site listens on 127.0.0.1 only): `GET /admin/state`, `POST /admin/reset`, and
  `POST /admin/fail-next` (the next list load fails, so a test can see the Retry box on purpose).
