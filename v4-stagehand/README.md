# v4-stagehand: the Stagehand contender

This folder runs the 5 AP Desk bench records (`../bench/records.json`) through **Stagehand 3.7.3** (`@browserbasehq/stagehand`, npm tag `v3-latest`). Each record gets the bench's natural-language task text and nothing else: no hand-written selectors and no site-specific `act()` steps. Stagehand's own agent cache is on, so if it can reuse record 1's work for later records, it does.

## Setup

```bash
cd ~/Downloads/terminautomator/v4-stagehand
npm install                      # local only; run.sh does this if node_modules is missing
```

Requirements:

- Node 22 or newer.
- The bench proxy running on `http://127.0.0.1:8899`.
- The AP Desk site running on `http://localhost:5180`.
- Playwright's Chromium in `~/Library/Caches/ms-playwright/chromium-*`. It is already there.

No Browserbase account is needed (`env: "LOCAL"`).

## Run

```bash
./run.sh --clear-cache                 # the comparison run: 5 records in order, one at a time, empty cache at start
./run.sh --limit 2                     # first N records
./run.sh --only INV-26-2820            # one record (comma-separate for several)
./run.sh --mode text --clear-cache     # exact per-record bench text (no reuse possible, see below)
./run.sh --dry-run                     # show instructions and cache-key inputs; no browser, no model calls
./run.sh --check                       # wiring check: about 3 model calls, decides nothing
python3 ../bench/grade.py              # the real score (from the site)
python3 ../bench/calls.py stagehand    # model calls the proxy saw
```

Other options: `--agent-mode dom|hybrid` (default `dom`), `--max-steps N` (default 40), `--no-cache`, `--headed`, `--verbose 0|1|2`.

Each run writes:

- `results/<UTC timestamp>.json`, with one entry per record. Each entry holds the start and end time, seconds, steps and action list, `cache_hit`, the cache log events, the agent's final message, the success flag, errors, the model calls counted locally, the proxy calls in that record's time window, `AgentResult.usage` and `stagehand.metrics`.
- `results/<UTC timestamp>.summary.txt`, a table of the same data.

## How it is wired

- **Agent.** `stagehand.agent({ mode: "dom" })` followed by `.execute({ instruction, maxSteps: 40, variables })`.
  - This is Stagehand's non-CUA agent. It works on the accessibility tree and its tools are `act`, `fillForm`, `ariaTree`, `extract`, `goto`, `navback`, `scroll`, `keys`, `wait`, `think` and `done`, so it suits a text model.
  - Stagehand would pick `hybrid` (screenshots and coordinate clicks) for this model on its own, because any name containing `gemini-3` matches its pattern list. `dom` is used here to match the text-only setup of the other tools. `--agent-mode hybrid` is available.
  - `maxSteps` is 40 because Stagehand's default of 20 is too few for this task.
- **Model.** `{ modelName: "google/gemini-3.5-flash-lite", apiKey: "bench", baseURL: "http://127.0.0.1:8899/stagehand/v1beta" }`.
  - The `provider/model` form goes through the AI SDK google provider (`createGoogleGenerativeAI({ apiKey, baseURL })`), which accepts any model name. There is no allow-list.
  - The provider appends `/models/<name>:generateContent` to the base URL, so the base URL keeps `/v1beta`.
  - `STAGEHAND_MODEL` and `STAGEHAND_BASE_URL` override these values.
- **Call counting.** Stagehand's documented `middleware` option on the model config counts every generate or stream call per record. The proxy log is the authoritative count.
  - The local count also includes AI SDK retries.
  - `AgentResult.usage` and `stagehand.metrics` undercount: they do not include the agent's final `done` step.
- **Browser.** Local headless Chrome for Testing from Playwright's cache. The installed `/Applications/Google Chrome.app` quits on its own about 30 s after a headless launch on this Mac, which kills the run mid-record. `STAGEHAND_CHROME` overrides the path.
- **One Stagehand instance per record.** Each record gets a fresh browser that starts at `about:blank`. The cache lives on disk in `cache/`, so reuse works across records and across runs.

## How the cache works, and whether records 2-5 can reuse record 1

Stagehand 3.x has a local **agent cache**, turned on with `cacheDir`. It is in `node_modules/@browserbasehq/stagehand/dist/esm/lib/v3/cache/AgentCache.js`.

- **Key.** sha256 of:
  - the trimmed instruction
  - the start URL
  - `maxSteps`
  - the model, system prompt and tool configuration
  - the variable *names*, without their values
- **Store.** After a live run that reports `success`, Stagehand writes `cache/agent-<key>.json`. The file holds the recorded steps: `act` with resolved XPath selectors, `fillForm`, `goto`, `keys` with the typed text, `navback`, `scroll` and `wait`.
- **Replay.** On a key hit, Stagehand replays those steps without asking the model. Model calls happen only if a selector no longer resolves and act self-heal starts. It then returns the *stored* `AgentResult` with `metadata.cacheHit: true`, which means the final message and action list are record 1's.

The full bench text contains the invoice number, so `--mode text` gives each record a different key. That mode never reuses anything.

The default `--mode variables` uses the supported way to keep the key constant:

- The instruction is the bench text with the invoice number replaced by `%invoice_no%`.
- The value is passed as `variables: { invoice_no: { value, description } }`.
- Stagehand tells the model that the variable exists (name and description only; the model never sees the value) and that it should write `%invoice_no%` where it types it.
- On replay, Stagehand substitutes the current record's value into those tokens.

`--dry-run` shows that all 5 records then produce the same cache key.

**So reuse happens, but it is a blind replay.** The cache holds a fixed list of clicks and typed text. It has no reads and no decision logic: `extract` and `ariaTree` steps are skipped on replay. On records 2-5:

- Only text typed as `%invoice_no%` changes per record.
- Everything record 1's model typed literally is replayed unchanged: the PO number it searched for, the decision, the reason, the note and the payment date.
- Expect record 1's decision to be saved on each later invoice. That is right only when the outcome happens to match. The 5 records cover all 5 decision kinds, so expect most replays to be graded wrong, even though `success: true` with 0 model calls is reported.

The runner records `cache_hit` and `cached_entry_timestamp` so these rows are easy to spot. Check `grade.py` for the real score.

## Caveats

- **Version.** npm `latest` is 4.2.1, but v4 does not fit this comparison:
  - It has no `agent()` (only `act`, `observe` and `extract`).
  - Its cache is server-side on Browserbase only.
  - Its model config takes no `baseURL`, so it cannot use the bench proxy.
  That is why 3.7.3 (`v3-latest`) is used.
- **What the model sees.** In variables mode the model works with `%invoice_no%` rather than the literal invoice number. That is the price of a stable cache key. `--mode text` gives the model exactly the bench text.
- **When nothing is cached.** A record is cached only when the agent reports success. If record 1 fails, record 2 runs live and may become the cached one.
- **Leftover cache.** A cache left from an earlier run makes even record 1 a replay. Use `--clear-cache` for a from-scratch run; the runner warns when `cache/` is not empty.
- **The `act` cache.** The agent's `act` tool also uses Stagehand's act cache in the same `cacheDir`, keyed on the act instruction and URL. Those instructions are written by the model for each record, so hits there are unlikely.
