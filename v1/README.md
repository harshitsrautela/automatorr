# Data Entry Automation Agent

An AI browser agent (browser-use + Gemini) reads records from a JSON file and
fills them into a web form, one record at a time. After the full batch runs,
a separate evaluation pass reads the target Supabase table directly and
compares what actually landed against what was expected — the agent's own
self-reported success/failure is never trusted for grading.

Scope for now: data entry only (e.g. company onboarding records). No
concurrency, no retries beyond a basic try/except, no auth, no deployment.

## Setup

1. **Create a virtual environment**

   ```bash
   python3.11 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright's browser binaries** (browser-use depends on Playwright)

   ```bash
   playwright install chromium
   ```

4. **Set up environment variables**

   ```bash
   cp .env.example .env
   ```

   Then fill in:
   - `GEMINI_API_KEY` — from Google AI Studio
   - `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` — from your Supabase project settings (service_role key, not anon — the evaluator needs unrestricted read access)
   - `SUPABASE_TABLE` — the table your form writes into
   - `TARGET_FORM_URL` — your local form, e.g. `http://localhost:3000/form`

5. **Prepare a records file**

   A JSON array of objects, e.g.:

   ```json
   [
     { "record_id": "1", "name": "Jane Doe", "email": "jane@example.com", "department": "Sales" }
   ]
   ```

   `record_id` is optional — if omitted, one is generated from the row index.

6. **Run the API**

   ```bash
   uvicorn main:app --reload
   ```

## Usage

```bash
# Kick off a batch run (blocking — returns the full report)
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"records_path": "sample_records.json"}'

# Check progress
curl http://localhost:8000/status

# Get the last saved report
curl http://localhost:8000/results
```

## Notes

- **Field matching**: the evaluator matches each expected record to a
  Supabase row using `MATCH_KEY_FIELD` (default `email`). If that field
  itself was entered wrong by the agent, the record shows up as
  `matched_in_db: false` with reason `record_not_found_in_db` — distinct
  from a per-field value mismatch on a row that *was* found. Check
  `.env`'s `MATCH_KEY_FIELD` if email isn't a reliable unique key for your form.

- **Field comparison normalization**: `evaluation/normalizers.py` normalizes
  values before comparing (dates, phone numbers, currency, generic strings)
  so formatting differences don't register as false failures. Field type is
  guessed from the field name; add overrides in `FIELD_NAME_OVERRIDES` in
  that file if the guess is wrong for a specific field.

- **browser-use API surface**: `agent/runner.py` imports `Agent` and
  `ChatGoogle` from `browser_use`. If your installed version's API differs,
  that's the only place you'll need to adjust — the batching/error-handling
  logic around it doesn't depend on the exact browser-use version.
