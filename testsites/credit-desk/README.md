# Credit Desk: the first test site

A React + Vite app on Supabase. Retailers ask for credit; an agent reads the request, looks the retailer up on a ledger, applies
the rules and clicks Approve, Reject or Hold. 100 requests; the expected decisions are stored in a table the UI never shows.
The agent works on `/` (credit requests) and `/ledger` (retailers). `/people` is the original DataDesk form used by `v1/` and `v2/`.

## Set up (once)

1. Create a Supabase project and run the SQL in `supabase/migrations/` in order (SQL editor, or `supabase db push`).
2. `cp .env.example .env` and fill in `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
3. `npm install && npm run dev -- --port 5174` (the docs and tests assume port 5174; Vite defaults to 5173).

The "Reset all decisions" button on the requests page clears the decisions between runs.
