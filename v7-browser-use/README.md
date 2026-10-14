# v7-browser-use: the plain browser-use control

This is the control in the tool comparison on the AP Desk test site (http://localhost:5180). It runs the plain
browser-use 0.13.10 live agent on Gemini (`gemini-3.5-flash-lite`) and does every record from scratch, with no
caching, plans or recipes. Comparing it with terminautomator v3 shows how much v3's learned plan saves.

## What it reuses between records

Nothing. Each record gets:
- a new `Agent`
- a new Gemini client
- a new headless Chromium with a temporary profile that is deleted afterwards

The agent sees only the natural-language task in `../bench/tasks/<INVOICE>.txt`, the same text every tool gets. It has
no selectors and no site-specific steps.

## Setup (already done; to redo from nothing)

```sh
cd ~/Downloads/terminautomator/v7-browser-use
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install browser-use==0.13.10
```

Nothing is installed globally. The browser is Playwright's Chromium build `chromium-1243` ("Google Chrome for
Testing"), which is already in `~/Library/Caches/ms-playwright`. v3's Playwright 1.63 launches the same build. If that
folder is missing, set `CHROME_PATH` to a Chromium or Chrome executable, or leave it unset and browser-use picks a
browser itself. browser-use keeps its config folder in `.browseruse/` here. Telemetry and cloud sync are off.

## Before a run

- The AP Desk must be running on http://localhost:5180.
- The bench proxy must be running on http://127.0.0.1:8899 (`node ../bench/proxy.js`).

All model calls go to `http://127.0.0.1:8899/browser-use/` with the placeholder key `bench`. The proxy puts in the real
key, paces the calls, and logs each one to `../bench/logs/calls.jsonl` under the tag `browser-use`.

## Run

```sh
./run.sh                       # the 5 records in ../bench/records.json, in order, one at a time
./run.sh --limit 1             # only the first record
./run.sh --only INV-26-2820    # one record
./run.sh --check-llm           # one tiny model call through the proxy (tag browser-use-setup), no browser
```

Other flags:
- `--max-steps N`: the step cap. The default is 500, the same as v3, which uses browser-use's default.
- `--vision-retry`: if a record fails text-only, retry it once with screenshots. v3's first (cold) run does this, but
  it is off here by default because this is plain browser-use.

Afterwards:

```sh
python3 ../bench/grade.py              # grades the site's decisions for the 5 records
python3 ../bench/calls.py browser-use  # model calls and tokens for this tool
```

## What it records

The run writes `results/<UTC time>.json` after each record and saves the console output to `results/<UTC time>.log`.
For each record, the JSON holds:
- start and end time, and duration
- steps taken
- the agent's final answer
- `is_done`, and its self-reported `success`
- step errors, and any exception
- the token usage browser-use reports
- `proxy_calls`: the proxy's log lines for the tag `browser-use` between the record's start and end

The run ends with one summary line: successes, total steps, total calls, calls per record and total time. The
agent's own `success` flag is only what it claims about itself. `grade.py` decides what is correct.

## Settings copied from v3 (`v3/agent/runner.py` and `orchestrator.py`, cold run)

| Setting | Value |
|---|---|
| Model | `gemini-3.5-flash-lite` |
| `use_vision` | False (text DOM only) |
| `use_judge` | False |
| `flash_mode` | off (default) |
| Planning | on (default) |
| `max_steps` | 500 (default) |
| `max_failures` | 5 (default) |
| `max_actions_per_step` | 5 (default) |
| `llm_timeout` | 75 s (default) |
| `step_timeout` | 180 s (default) |
| Browser profile | new temporary profile per record, `keep_alive=False` |
| Browser and viewport | Playwright Chromium, 1280x720 |
| Browser extensions | none |

Differences from v3:
- **Headless browser.** v3 opens a visible window unless `HEADLESS` is set.
- **Starting page.** v3 opens the start page before the agent begins. Here the agent starts on a blank tab, and the
  task's first line tells it to open the site.
- **Retries.** v3 rotates keys itself. Here the proxy handles keys and the per-minute limit, so the Gemini client
  only retries errors (`max_retries=3`).
