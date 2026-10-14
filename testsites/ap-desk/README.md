# AP Desk: a second, differently built test site

A held-out test target for the terminautomator browser agent, built so the recipe engine is **not** tuned to the Credit Desk.
It is an accounts-payable desk: for each invoice the agent must do a **three-way match** (invoice, purchase order, goods
receipts) and record a decision.

## How it differs from DataDesk (v1) and the Credit Desk (v2)

| | DataDesk | Credit Desk | AP Desk |
|---|---|---|---|
| App type | React SPA + Supabase | React SPA + Supabase | **Server-rendered pages, real form POSTs, no dependencies** |
| Task | type record fields into a form | read a ledger, click Approve / Reject / Hold | **match across 4 pages, then fill a conditional form** |
| Row actions | none | buttons labelled `Approve request 7` | **none; the invoice number is a plain link to an opaque URL** |
| Paging | none | Next | **numbered pages, no Next** |
| Finding a record | a list | search box + paging | search box (GET form) |
| Values entered | the record's values | none (one click) | **computed from what was read** (a date, a reason, a typed note) |
| Last step | submit | one click | **dropdown, fields that appear per choice, a confirm dialog, a form POST** |
| Things to add up | none | none | **several partial goods receipts per PO** |
| Dates | ISO | n/a | shown as `05 Nov 2026`, entered as a date input (`2026-11-05`) |

## Run it

```bash
cd testsites/ap-desk
python3 scripts/generate_seed.py     # only if you want new data (it is seeded, so the output is stable)
node server.js                       # http://localhost:5180 (127.0.0.1 only)
```

## The task (one record = `{"invoice_no": "INV-26-1234"}`)

First rule that applies decides:

1. Vendor is **Blocked**: Reject, reason *Vendor blocked*.
2. PO not found, or not **Open**: Reject, reason *No open PO*.
3. Quantity invoiced **>** total received: Place on hold, reason *Short receipt*, note `Invoiced X, received Y`.
4. Unit price differs from the PO price by **more than 2%** of the PO price: Place on hold, reason *Price variance*,
   note `Invoice price X, PO price Y`.
5. Otherwise: Approve for payment, payment date = the invoice's due date.

Edge cases are in the data on purpose: received exactly equal to invoiced, variance of exactly 2% (must still approve),
2.x% (must hold), a PO number with two digits swapped (not found), closed POs, vendors blocked while everything else is fine.

## Files

- `server.js`: the whole site. `public/app.js`: the conditional fields and the confirm dialog. `public/style.css`.
- `data/seed.json`: what the site serves (vendors, POs, receipts, 100 invoices). `data/state.json`: decisions made.
- `expected.json`: the correct decision per invoice (**never served**). `records.json`: the 100 records to feed the agent.
- `grade.py [N]`: compares `/admin/state` with `expected.json` (status, reason, payment date, numbers in the note).
- `scripts/generate_seed.py`: builds the data and computes the answers by rules independent of the scenario labels.
- Admin for the harness: `GET /admin/state`, `POST /admin/reset`.

## Reset before every test

```bash
curl -X POST localhost:5180/admin/reset
```
