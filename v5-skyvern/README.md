# v5-skyvern: Skyvern in the tool comparison

Skyvern **1.0.55** (open source, from PyPI), self-hosted with **no Docker and no Postgres**. It runs from its own
venv in this folder, uses an embedded **SQLite** database, and drives a **headless** Playwright Chromium. It runs the
same 5 AP Desk records as the other tools (`../bench/records.json`) and gets the same task text (`../bench/tasks/<INV>.txt`).

## Setup (already done)

```sh
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install "skyvern[local,server]==1.0.55"   # the plain `skyvern` package is only the client/CLI
# Playwright's Chromium (chromium-1243) was already in ~/Library/Caches/ms-playwright. If it is missing:
.venv/bin/playwright install chromium
```

`.env` holds all the config. Nothing goes to `~/.skyvern`: the database, artifacts, logs and temp files are all in `data/`.

| Setting | Value | Why |
|---|---|---|
| `DATABASE_STRING` | `sqlite+aiosqlite:///…/data/skyvern.db` | Skyvern ≥1.0 supports SQLite. On first start it creates the tables, a local org and an API key by itself. |
| `PORT` | 8010 (bound to 127.0.0.1 by `server.py`) | Keeps clear of the bench ports. The CLI's `skyvern run server` binds 0.0.0.0. |
| `BROWSER_TYPE` | `chromium-headless` | Headless browser. |
| `ENABLE_GEMINI`, `LLM_KEY`, `SECONDARY_LLM_KEY` | `true`, `GEMINI_3.5_FLASH_LITE`, same | Skyvern's own registered config for this model: litellm `gemini/gemini-3.5-flash-lite`, `supports_vision=True`, thinking level "minimal". Every LLM role (action, extraction, select, goal check, script generation, script review) uses this key. |
| `GEMINI_API_KEY`, `GEMINI_API_BASE` | `bench`, `http://127.0.0.1:8899/skyvern/v1beta` | litellm sends native Gemini calls to `…/skyvern/v1beta/models/gemini-3.5-flash-lite:generateContent`, with `x-goog-api-key: bench`. The proxy swaps in the real keys, then counts and paces the calls. |
| `ALLOWED_HOSTS` | `'["localhost"]'` | Skyvern's navigation guard blocks loopback hosts by default ("blocked navigation destination"). This lets through exactly `localhost`. |
| `SKYVERN_TELEMETRY` | `false` (PostHog host also set to a dead local port) | Telemetry is off. |
| `SECRET_KEY`, `SKYVERN_API_KEY` | local random value / local JWT | Skyvern needs these for its own local API auth. The server writes `SKYVERN_API_KEY` to `.env` on first start. Neither is a third-party key. |

The **OpenAI-compatible** path (`ENABLE_OPENAI_COMPATIBLE` + `OPENAI_COMPATIBLE_*` with
`…/skyvern/v1beta/openai/`) also exists, but it was not used. The native path keeps Skyvern's own model config for this
model (vision on, minimal thinking). The OpenAI-compatible path would need `OPENAI_COMPATIBLE_SUPPORTS_VISION=true`
and would lose the thinking setting.

## Run

```sh
./start_server.sh            # starts the server in the background (log data/server.log, about 10-60 s to come up)
./run.sh                     # all 5 records, in order, one at a time (starts the server if needed)
./run.sh --limit 2
./run.sh --only INV-26-2820  # or --only A,B
./run.sh --fresh-workflow    # new workflow = no cached script yet (use this after an AP Desk reset to start clean)
./start_server.sh stop
python3 ../bench/grade.py ; python3 ../bench/calls.py skyvern
```

Other options: `--block task_v2` (Skyvern 2.0 planner instead of the 1.0 task block), `--run-with agent` (no script
caching), `--code-version 1|2`, `--max-steps N` (default 40), `--timeout-min` (default 30).

`smoke.py` is the wiring check: one extraction block reads the AP Desk title. It does not click or save anything.

### Output

`results/<UTC timestamp>.json` has one entry per record:
- start, run end and settled end (the harness waits until no model call has arrived for 20 s, because script generation and review run after the run is marked done) and seconds
- `status`, `output`, `failure_reason`, `errors`
- `execution_mode`: `agent`, `code_generation` (configured for code but no script yet, so the agent runs and a script is generated) or `code` (the cached script ran). Read from Skyvern's own "Workflow run execution mode resolved" log line.
- `cached_script_used`, `script_run` (script id and whether an AI fallback fired), `script_before` and `script_after` (script versions for the workflow)
- `skyvern`: steps, tasks, actions by type, tokens and cost from Skyvern's `steps` table, the count of Skyvern's own `llm_request` artifacts, and fallback episodes, all read from SQLite in read-only mode
- `proxy_calls`: the bench proxy's count for tool `skyvern` in that record's window. This is the comparable number.

When a script version changes, its generated Python is copied to `results/scripts/`.

The same file ends with a summary block.

## How the task is given (fairness)

There is one saved Skyvern **workflow**, "AP Desk invoice decision (task, code)", stored in `data/workflow.json`. It has:
- one workflow parameter, `invoice_no`, of type string
- one **task block** (the Skyvern 1.0 engine: screenshot plus DOM each step), with `url` set to `http://localhost:5180`. That is the URL the task text starts with, and setting it saves Skyvern an LLM call to work it out.
- a `navigation_goal` that is exactly the bench task text, with the invoice number replaced by `{{ invoice_no }}`. `run.py` checks that the rendered text equals each `bench/tasks/<INV>.txt`.
- `parameter_keys: [invoice_no]`, so the agent also gets the same `invoice_no` as its navigation payload

There are no selectors and no site-specific steps.

## How reuse works ("run with code", script caching)

This is Skyvern's built-in mechanism, and it is on for this workflow: `run_with: code`, `ai_fallback: true`,
`cache_key: "default"`. In 1.0.55 Skyvern also stores `code_version: 2` ("adaptive caching") by default.

1. **Record 1 (no script yet)** runs in `code_generation` mode. The agent does the task step by step. If the run
   ends `completed`, Skyvern **generates a Python script** from the actions it recorded (Playwright-style
   `page.click/fill/select_option` with the element's selector, plus the intention as an AI prompt). It stores the
   script under the workflow and cache key. The cache key is the literal `default`, so **one script serves every
   `invoice_no`**. Values that came from the parameter become `context.parameters['invoice_no']`. Script generation
   itself is deterministic in this version: field naming no longer calls an LLM.
2. **Records 2-5** run in `code` mode. The cached script replays the steps. Each action tries its selector first and
   uses AI only if the selector fails (`ai="fallback"`). Some inputs are marked `ai="proactive"`, which means the
   LLM produces the value every run. If a whole block fails, Skyvern falls back to the agent for that block
   (`ai_fallback`). With adaptive caching, a **script reviewer** (an LLM) then looks at the fallback episodes and
   may write a new script version. Each run's file records which version it used.
3. If record 1 ends `failed`, `terminated` or `timed_out`, **no script is generated**. Record 2 then runs as the agent
   again and tries to generate one.

**Can records 2-5 reuse record 1? Yes, mechanically.** The workflow, the parameter and the cache key are all set up for
it. Whether reuse gives a *correct* decision is what the live run will show. The open question is the
**data-dependent part**. Each invoice can need a different decision (Reject, Hold or Approve), reason, note numbers
or date, and some records are not found the same way. A script recorded from record 1 replays record 1's path. It is
right on later records only where the generated code chose AI or proactive values, or where a failure triggers the
agent fallback. A replay that "succeeds" with record 1's literal choice would be wrong but would not fail, so grade
with `bench/grade.py` rather than trusting `status`. The generated code is saved to `results/scripts/` so you can
check how each field was handled.

## Caveats

- The cache persists in `data/skyvern.db` across `run.sh` calls. After the AP Desk site is reset, run with
  `--fresh-workflow` so record 1 again starts with no script. Otherwise it would replay an earlier run's script.
- Skyvern's own overhead per record includes one `generate-workflow-run-block-description` call (a UI summary,
  cached only for an identical rendered prompt), goal-check calls (`complete_verification`) and select-agent calls.
  The proxy counts all of these.
- Model calls are paced by the proxy (13 per minute per key). Skyvern's LLM timeout is 300 s, so a call that waits
  for the pacing does not time out.
- No UI: the pip package has no frontend (`skyvern run ui` needs Node and the repo). Look at runs through
  `results/*.json`, `data/server.log` and the screenshots in `data/artifacts/`.
- The server takes about 1 minute to start the first time (imports), and a few seconds after that.
- Model calls spent while setting up: 4 (the wiring check, including one block-description call from a first attempt
  that was blocked by the navigation guard). No invoice was opened, decided or saved.
