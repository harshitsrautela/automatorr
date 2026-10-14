# v6-webwright: Webwright contender

This folder tests Webwright (Microsoft Research, now called CUAWright) on the AP Desk comparison (`../bench`). It checks the approach "AI does the first record, then writes code that repeats it".

## Setup (already done)

| What | Value |
|---|---|
| Repo | https://github.com/microsoft/Webwright (MIT), cloned into `webwright/` (git-ignored: `git clone` it at commit ff44a05) |
| Commit | `ff44a0524e0812b69814efcec5e583f0d8cdde0c` (2026-10-06, "Fix project page link case") |
| Clone edits | **None** (`git -C webwright status` is clean) |
| Python | `.venv/` made with `/opt/homebrew/bin/python3.12`, `pip install -e ./webwright` (cuawright 0.2.0, playwright 1.63.0) |
| Browser | Playwright Chromium (cache in `~/Library/Caches/ms-playwright`), headless, local (`browser_mode: local`, no Browserbase) |

To rebuild from scratch:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e ./webwright
.venv/bin/python -m playwright install chromium
./run.sh --check        # imports, site title, proxy port; makes no model calls
```

The clone's entry and install code was read before running: `setup.py`, `pyproject.toml`, `run/cli.py`, the models, `environments/local_workspace.py`, `agents/default.py`, and the tools. The only network targets are the configured model endpoint and Browserbase, which is used only in browserbase mode and is never used here. The `setup.py` hook only records git provenance.

## Model wiring (config only, no source edit)

Webwright's `openai` backend speaks the OpenAI **Responses** API (`/v1/responses`), which Gemini's OpenAI-compatible endpoint is not documented to serve (not tested, to save quota). Its `openrouter` backend is a plain OpenAI **chat/completions** client with a configurable endpoint and model. `config/bench_gemini.yaml` (stacked after the stock `base.yaml`) points that backend at the bench proxy:

```yaml
model:
  model_class: openrouter
  model_name: gemini-3.5-flash-lite
  openrouter_endpoint: http://127.0.0.1:8899/webwright/v1beta/openai/chat/completions
  provider_require_parameters: false
```

The key comes from the env var `OPENROUTER_API_KEY`. `run.py` sets it to the literal `bench`, and the proxy swaps in the real key. No real key is written anywhere. The inner tools (`image_qa`, `self_reflection`) reuse the same model block, so their calls also go through the proxy and are counted. Requests use `response_format: json_schema` (strict), and Gemini accepts it.

Other settings in the overlay:

| Setting | Value here | Stock default |
|---|---|---|
| `agent.step_limit` | 60 | 100 |
| `agent.summary_every_n_steps` | 20 | 20 |
| `agent.require_self_reflection_success` | true | true |
| `model.max_output_tokens` | 8192 | 4000 |
| `model.request_timeout_seconds` | 180 | 120 |
| `environment.command_timeout_seconds` | 240 | 240 |

`run.py` also limits wall-clock time: 3600 s per Webwright run and 600 s per script run.

## The protocol (`run.py`)

`./run.sh` runs the 5 invoices in `../bench/records.json` in order, one at a time.

1. **Record 1 (mode `webwright`)**
   - Runs the documented "script-based run" CLI: `python -m cuawright.webwright.run.cli main -c base.yaml -c config/bench_gemini.yaml -t <task> --start-url http://localhost:5180 --task-id <INV> -o outputs/<stamp>`.
   - `<task>` is `bench/tasks/<INV>.txt` plus one generic sentence, the only thing we add:
     > Also: leave final_script.py as a re-runnable script that takes the invoice number as a command-line argument and does this whole task for any invoice.
   - Webwright's own loop then runs: plan.md, exploration scripts, final_script.py run in `final_runs/run_<id>/`, then a self_reflection judge. "Done" is gated on the judge passing.
   - The script it leaves (workspace `final_script.py`, otherwise the latest `final_runs/run_*/final_script.py`) is copied to `artifacts/final_script.py`. A dated copy and its `plan.md` are kept as well.
2. **Records 2-5 (mode `script`)**
   - Runs `.venv/bin/python final_script.py <INV>` with 0 model calls. Before running, `run.py` reads the script's argparse calls. If the script defines only an `--invoice...` flag, `run.py` passes `--that-flag <INV>` instead of a positional argument.
   - Each run gets its own folder `outputs/<stamp>/script_<INV>/`, with `WORKSPACE_DIR` and `BROWSER_MODE=local` set and no API key in the environment.
   - Success means exit code 0.
3. **Fallback (mode `fallback`)**
   - If the script is missing or exits non-zero (or times out), Webwright runs on that record's task with the same added sentence. Its calls are counted.
   - If that run is Submitted, its script replaces `artifacts/final_script.py` for the records after it. The failed script attempt is kept in the row (`script_attempt`).

Each row in `results/<UTC stamp>.json` records:
- invoice and start/end time, with seconds
- mode
- `webwright_turns` (agent turns, from `trajectory.json` `info.api_calls`) and `exit_status`
- `model_calls` (every call in the proxy log for tag `webwright` in that time window, so it includes image_qa and self_reflection calls)
- `final_output` (Webwright's final response, or the script's stdout tail)
- `success`
- `errors`
- the workspace path

After the last record, `run.py` runs `../bench/grade.py --json` (read-only GET of `/admin/state`). It then writes a one-line summary.

Run options:

```
./run.sh                     # all 5
./run.sh --limit 2           # first 2
./run.sh --only INV-26-2820  # one record (a learn run on it)
./run.sh --use-saved-script  # no learn run: reuse artifacts/final_script.py for every record
./run.sh --no-fallback       # script failures stay failures
python3 ../bench/calls.py webwright
```

`success` is self-reported:
- **webwright mode:** `exit_status == "Submitted"`, which needs Webwright's own self_reflection judge to pass.
- **script mode:** exit code 0.

Correctness comes from `bench/grade.py`.

## Caveats

- **Flash-lite in a coding loop.** In the wiring smoke test (3 turns, `outputs/smoke/`), flash-lite followed the protocol. It wrote plan.md, self_reflect_config.json and a final_script, but its first script had a broken string (a raw newline inside a quoted `"...\n"` in a heredoc), which it then fixed. Webwright's prompts were tuned for gpt-5.x and Claude, so expect format errors, retries and a high turn count on record 1.
- **Call count is much higher than the number of turns.** `self_reflection` scores every screenshot (one call each), then makes one final aggregate call, and it is re-run after every failed attempt. History compaction every 20 steps also costs one call.
- **Decision saved more than once.** The final script runs (and any re-runs) during record 1 save the decision on the real site. Exploration scripts might too. Each save probably overwrites the last, but the grade reflects the last save. A script that saves and then crashes triggers a fallback that decides the record again.
- **The script never had its argument tested.** Webwright runs its script only on record 1's own invoice, so the reusable script was never tested on other invoices before records 2-5. That is the point of the test.
- **Script exit code 0 does not mean the decision was right.** Only grade.py says so.
- **`crafted_cli.yaml` was not used.** Webwright ships this config to make the final script a parameterised CLI, but it is hard-wired to Browserbase cloud sessions, which cannot reach localhost. We used the stock `base.yaml` plus the one-sentence request instead.
- **Agent commands run unsandboxed.** Webwright runs model-written shell commands directly on this Mac (`subprocess.run(shell=True)`), with cwd set to the run workspace. That is how Webwright works; it has no sandbox.
- **Step limit.** If record 1 reaches the 60-step limit without "Submitted", whatever script it left is still saved and tried on records 2-5, with fallback.
