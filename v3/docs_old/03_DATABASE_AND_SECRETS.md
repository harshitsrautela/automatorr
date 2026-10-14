# 03. Database and secrets

v2 kept state in local files (`cache.sqlite3`, `results.json`, `Logs.md`). v3 keeps **all of it** in one PostgreSQL
database, so the app survives a restart, a redeploy, or a host with a throw-away disk.

## Where the database lives

`DATABASE_URL` in `v3/.env`. Here it is the same Supabase project as the test sites, but isolated:

- a limited login `terminautomator_app` owns a private schema `agent_state`;
- that schema is not exposed through Supabase's public API, and **row-level security is on with no policies**, so the
  project's public (anon) key can never read these tables;
- the tables are created automatically at first connection (`app_db._SCHEMA`, all `CREATE TABLE IF NOT EXISTS`).

Connection handling in `app_db.py`: a pool of 1 to 10 connections, each tested before use (Supabase's pooler drops idle
connections) and recycled after 120 s idle. Prepared statements are off because the pooler does not support them.
SQL is written with `?` placeholders and rewritten to `%s` by a thin wrapper.

## The tables

```
 YOUR CONFIG           secrets ----< workflows
                                        |
 RUN HISTORY                          runs ----< run_events
                                        |
 "CACHE" (all learned)   cached_steps   run_metrics   recipe_traces   recipes
```

| Table | One row is | Notes |
|---|---|---|
| `secrets` | one saved key or credential | `kind` is `llm_key` or `db_credential`; `ciphertext` is encrypted JSON; `last4` is only for the masked preview |
| `workflows` | one saved task | URL, prompts, match key, model, secret ids, `use_cache`, `max_concurrency`, `verify_kind`/`verify_config`, `reconcile`, `headless`, the uploaded rows in `data_json`, and **`cache_epoch`** |
| `runs` | one press of Run | status `running`, `verifying`, `done`, `error`, `aborted`; the full verification report as JSON |
| `run_events` | one log line of a run | `(run_id, seq)` is the key; the Logs page asks for "everything after seq N" |
| `cached_steps` | one remembered click or typing (v2 idea) | keyed by `(workflow_name, path_variant, target_key)` |
| `run_metrics` | one finished record | mode, cache hits and misses, LLM steps, miss reasons; feeds `/api/cache/stats` |
| `recipe_traces` | what the AI did on one record, with the text of each page it saw | only the newest 6 per workflow and variant are kept; a trace can be about 100 KB because of the page text |
| `recipes` | what was learned | one row per `(workflow_name, path_variant)`; its JSON is a **recipe** (no `kind`), a **plan** (`kind: plan`, with trust counters) or **`live_only`** (nothing could be learned, or a plan was thrown away; the reason is stored) |

## What "the cache" means in v3, and how to wipe it

"Cache" is **four tables**: `cached_steps`, `run_metrics`, `recipe_traces`, `recipes`. Everything the system has *learned*
is in those. Workflows, secrets and run history are **not** cache.

Two ways to make the next run start from nothing:

1. **Soft: bump the epoch** (`POST /api/workflows/{id}/reset-cache`, or change the URL or match key). The workflow moves
   to a new namespace `wf<id>:e<n+1>`. Nothing is deleted; old rows are simply never read again.
2. **Hard: delete the rows** from the four tables. This is what a "fresh test" should do when you also want the metrics
   to start at zero (see doc 10).

Why it matters: a recipe is **loaded from the database at the start of each run** (`_claim_recipe`). A second run on the
same workflow therefore skips learning entirely and goes straight to the recipe. That is the whole point of the cache, and
also exactly why a test that wants to include the learning step must clear it first.

Related old files: `storage/cache.sqlite3` and `storage/app.sqlite3` are leftovers from before the move to Postgres. No
code references SQLite any more.

## The log pipeline (why logs never slow a browser)

Engine events (`record_start`, `step`, `field_hit`, `note`, `record_done`, `verification_done`, ...) go through:

```
engine -> on_event -> run_service._redact (hides key values) -> app_db.append_event
       -> in-memory queue -> writer thread (batches of up to 200, every 0.3 s) -> run_events
```

If the database blips, the writer retries every 2 seconds instead of dropping events. Before a run is marked finished,
`flush_events()` waits until the queue is empty, so the last lines are visible together with the final status.

## Secrets

Saved keys never sit in the database in clear text.

- **Encryption**: Fernet (symmetric). The master key is `SECRETS_ENCRYPTION_KEY` if set, otherwise a key file at
  `storage/master.key`, created on first use with owner-only permissions (0600).
- **Back that key up.** Lose it and every saved secret is unreadable.
- **Never returned**: the API only ever sends `••••` plus the last four characters.
- **Redaction**: `run_service._redact` replaces every known key value in an event with `••••` before it is stored, so a
  key cannot leak into the Logs page.
- **Two kinds**:
  - `llm_key`: one Gemini key.
  - `db_credential`: a Supabase URL plus a service-role key, used only to **verify results** after a run.
- **Fallbacks to `.env`**: if a workflow has no LLM secret, the server's `GEMINI_API_KEY` is used. If it has no DB secret,
  `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are used. Extra Gemini keys come from `GEMINI_API_KEY_2`, `_3`, ... and
  join the pool automatically (doc 07).

### The one thing to remove before sharing

`DEV_PREFILL_SECRETS=1` in `.env` makes `/api/dev-prefill` return the database login to the browser so the Add-secret form
can be pre-filled. It only answers requests from this machine, but it trusts only that check. Delete the line before
exposing the app anywhere. The app has **no login** yet; keep the port local.
