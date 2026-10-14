# Runbook — how to actually run this

Practical steps to run a batch, from a clean start. See `ARCHITECTURE.md`
for what's happening conceptually, `LEARNINGS.md` for gotchas you'll
probably hit.

## One-time setup

1. **Python env** (this repo, `~/Downloads/app`):
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   Ignore the README's "install Playwright's browser binaries" step — the
   installed `browser-use` version doesn't use Playwright at all anymore
   (it drives Chrome directly via CDP). No separate browser install needed.

2. **Environment variables**: copy `.env.example` to `.env` and fill in:
   - `GEMINI_API_KEY`
   - `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` (service_role, not anon —
     the evaluator needs unrestricted read access)
   - `SUPABASE_TABLE` (`people` for DataDesk)
   - `TARGET_FORM_URL=http://localhost:5173` — DataDesk is a single-page
     app, not a separate `/form` route, so it's just the root URL.

3. **DataDesk frontend** (`testsites`) needs its
   own `.env` (different from the backend's):
   ```
   VITE_SUPABASE_URL=...
   VITE_SUPABASE_ANON_KEY=...
   ```
   (anon/publishable key here, not the service role key — this one's
   exposed to the browser.)

## Every time you want to run a batch

Three terminals:

**1. DataDesk frontend** (skip if already running):
```bash
cd testsites
npm run dev
```
Confirm it's up at `http://localhost:5173`.

**2. Automation backend**:
```bash
cd ~/Downloads/app
source venv/bin/activate
uvicorn main:app --reload
```

**3. Trigger a batch**:
```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"records_path": "sample_records.json"}'
```
This **blocks** until every record has been processed (records run one at
a time — see `ARCHITECTURE.md` for why). Expect it to visibly take over a
fresh Chrome window per record. The response is the full `BatchReport`
JSON: field-by-field pass/fail for every record.

While it's running, check progress from another terminal:
```bash
curl http://localhost:8000/status
```

Re-fetch the last completed report any time:
```bash
curl http://localhost:8000/results
```

## Writing a records file

`sample_records.json` is a plain JSON array. Each object's keys should
match DataDesk's form fields:

```json
{
  "record_id": "1",
  "first_name": "...", "last_name": "...", "email": "...", "phone": "...",
  "date_of_birth": "YYYY-MM-DD", "gender": "...",
  "company": "...", "job_title": "...",
  "country": "...", "city": "...", "address": "...", "postal_code": "...",
  "department": "...", "employment_type": "...",
  "experience_years": 0, "salary": 0,
  "newsletter": true, "notes": "..."
}
```

`record_id` is optional (auto-generated from array index if omitted) — it's
your own bookkeeping label, not a database ID.

**`country`, `department`, `employment_type`, `gender` are dropdowns in
DataDesk with a fixed option list** (see `App.jsx`'s `COUNTRIES` /
`DEPARTMENTS` / `EMPLOYMENT_TYPES` / `GENDERS` constants for the exact
values). Using a value outside that list won't crash anything, but that
field usually won't get set — this is expected, not a bug, if you're
intentionally testing an out-of-list value.

## Cleaning up

Each Chrome window closes itself automatically when its record finishes
(`keep_alive=False`) — nothing to manually kill under normal operation. If
a batch gets interrupted mid-run, check `ps aux | grep -i "google chrome"`
for anything orphaned.

To stop the backend: `Ctrl+C` in its terminal (or `pkill -f "uvicorn main:app"`).
