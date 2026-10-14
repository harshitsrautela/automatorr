# Expense Desk: a fourth test site, built like a modern web app

A travel-expense audit desk. For each claim the agent decides **Approve / Approve with deduction / Send back / Reject**, works out
the amount to reimburse (with currency conversion and a hotel limit), and goes through a **three-step decision wizard**.

It exists to test whether the terminautomator plan engine is general, not tuned to the earlier sites. Everything about *how* the
site works was chosen to differ from the Credit Desk (v2), the AP Desk (v3) and the Returns Desk (v4).

## What is different on purpose

| Feature | Why it is a test |
|---|---|
| **One HTML page**; every page is drawn in the browser from a JSON API (`/api/...`) with a 150 to 600 ms delay and a "Loading…" placeholder | page addresses are hash routes (`#/claims/kxqfab`); content arrives late |
| Record ids in the address are **random letters and digits**, some with **no digits at all** (`kxqfab`) | an id-detector that looks for digits misses them |
| The claim is found with a **type-to-filter search box** (no button) | typing, not clicking, finds the record |
| Result rows are **clickable table rows**, not links | the agent clicks a plain `<td>` with no id, role or name |
| The claim page has **tabs** (Summary, Receipts, Activity) that **keep the address** | different content at the same address |
| The policy is in **collapsed sections** (`<details>`) | text that is not on the page until a section is opened |
| The decision is a **three-step wizard in a modal**: radio buttons; then fields that depend on the choice (numbers, a group of checkboxes, a select); then a confirmation checkbox that enables Submit | multi-step form, radio buttons and checkboxes, fields that only exist after Next |
| Form controls get a **new random id** every time they are drawn (`f50324`); only their `name` is stable | an engine that finds fields by id breaks |
| Saving is a `fetch` POST with a toast ("Decision saved for EXP-41142."), not a form post | no page load after saving |
| Amounts in **INR, USD, EUR, GBP, AED, SGD** | a conversion before the comparison |

## Run it

```bash
cd testsites/expense-desk
python3 scripts/generate_seed.py     # only to rebuild the data (seeded, so the output is stable)
node server.js                       # http://localhost:5182 (127.0.0.1 only), no install needed
curl -X POST localhost:5182/admin/reset        # reset the decisions before every test
python3 grade.py 12                  # grade the first 12 records after a run (no number: all 100)
```

## The task (one record = `{"claim_id": "EXP-41142"}`)

The first rule that applies decides:

1. Submitted more than **45 days** after the trip ended: **Reject**, reason *Late submission* (day 45 is still in time).
2. Any receipt **Missing**: **Send back**, ticking every category whose receipt is missing.
3. Hotel cost in rupees (amount x the currency's rate) **more than** the limit (nightly limit for the city's tier and the
   employee's grade, x nights): **Approve with deduction**; deduction = hotel cost - limit; reimburse = total claimed - deduction -
   advance; tick *Hotel above policy limit*.
4. Otherwise **Approve**; reimburse = total claimed - advance.

Data: 100 claims (45 approve, 25 approve with deduction, 15 send back, 15 reject), 37 trips abroad. Edge cases are in the data:
exactly 45 days, a hotel exactly at the limit, late *and* missing a receipt (late wins), two receipts missing.

## Files

- `server.js`: the JSON API and the admin endpoints. `public/index.html`, `public/app.js` (the whole app: router, list, claim page,
  tabs, wizard, policy), `public/style.css`.
- `data/seed.json`: what the API serves. `data/state.json`: decisions made.
- `expected.json`: the correct decision per claim (**never served**). `records.json`: the 100 records to feed the agent.
- `grade.py [N]`: compares `/admin/state` with `expected.json` (outcome, amount to reimburse, deduction, the hotel tick, the
  missing categories, the reason).
- `scripts/generate_seed.py`: builds the data and computes the answers by rules independent of the scenario labels.
- Admin for the harness: `GET /admin/state`, `POST /admin/reset`.
