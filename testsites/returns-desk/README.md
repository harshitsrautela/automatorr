# Returns Desk: a third, deliberately harder test site

A warehouse returns (RMA) desk, built to stress the parts of the browser agent's plan engine that the Credit Desk (v2) and the
AP Desk (v3) do not. For each return the agent decides **Refund / Replace / Reject**, works out a refund amount, and sets a
pick-up date in a calendar.

## What is hard on purpose

| Feature | Why it is hard |
|---|---|
| The returns list is **paged** (numbered pages, Prev, Next) and has **no search box** | the record has to be found by paging |
| **The route branches on the reason**: a wrong item needs only the order, a defect needs the item's warranty, a change of mind needs the item, its category and the policy table | one trace sees one branch |
| `/policy` is **very long** (about 19,600 characters; the fee table starts at character 9,366) | the row that matters is far down the page |
| The pick-up date is a **custom calendar** (a button, month buttons, day buttons), not a date input | not a native control; the day buttons carry only a number |
| Links whose text is an **id** (`ORD-10128`, `SKU-8649`) | the order and item pages are reached by clicking text that belongs to the record |
| Conditional fields (refund amount, replacement SKU, reason) and a **confirm dialog** | as on the AP Desk |
| Opaque URLs (`/returns/88637011`), numbers in Indian grouping, dates as `05 Nov 2026` | nothing can be read off the address |

## Run it

```bash
cd testsites/returns-desk
python3 scripts/generate_seed.py     # only to rebuild the data (seeded, so the output is stable)
node server.js                       # http://localhost:5181 (127.0.0.1 only), no install needed
curl -X POST localhost:5181/admin/reset        # reset the decisions before every test
python3 grade.py 12                  # grade the first 12 records after a run (no number: all 100)
```

## The task (one record = `{"rma": "RMA-71683"}`)

The first rule that applies decides:

1. Reason is **Wrong item**: **Replace**, replacement SKU = the SKU the customer *ordered* (the order page), pick-up date = the
   next pick-up slot (even for final-sale items).
2. The item is **final sale** (item page): **Reject**, reason *Final sale*.
3. Reason is **Defective**: days since delivery more than the item's warranty days: **Reject**, *Out of warranty*; otherwise
   **Refund** quantity x unit price (from the order), pick-up date = the next slot.
4. Reason is **Changed mind**: more than 30 days: **Reject**, *Outside window*; otherwise **Refund** quantity x unit price minus
   the restocking fee (none when Unopened; otherwise the *Opened* or *Damaged* percentage for the item's category in the policy
   table), pick-up date = the next slot.

Edge cases are in the data: exactly 30 days (still allowed), a defect on the last day of its warranty (still covered), a wrong item
that is also final sale (still replaced).

## Files

- `server.js`: the whole site. `public/app.js`: conditional fields, the calendar, the confirm dialog. `public/style.css`.
- `data/seed.json`: what the site serves. `data/state.json`: decisions made.
- `expected.json`: the correct decision per return (**never served**). `records.json`: the 100 records to feed the agent.
- `grade.py [N]`: compares `/admin/state` with `expected.json` (disposition, reason, refund amount, replacement SKU, pick-up date).
- `scripts/generate_seed.py`: builds the data and computes the answers by rules independent of the scenario labels.
- Admin for the harness: `GET /admin/state`, `POST /admin/reset`.
