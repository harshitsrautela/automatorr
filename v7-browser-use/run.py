"""Plain browser-use control for the AP Desk comparison: every record is done from scratch by a fresh live Agent.

Nothing is reused between records: each one gets a new Agent, a new LLM client, and a new headless browser with a
throwaway profile. The agent sees only the task text in bench/tasks/<INVOICE>.txt.

    .venv/bin/python run.py                  # all 5 records in bench/records.json, in order
    .venv/bin/python run.py --limit 1        # just the first record
    .venv/bin/python run.py --only INV-26-2820
    .venv/bin/python run.py --check-llm      # one tiny model call through the proxy (tag browser-use-setup), no browser
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "bench"
RESULTS = HERE / "results"

# Keep browser-use's config/extension dirs inside this folder, and send nothing to browser-use's cloud/telemetry
os.environ.setdefault("BROWSER_USE_CONFIG_DIR", str(HERE / ".browseruse"))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")

from browser_use import Agent, BrowserProfile  # noqa: E402
from browser_use.llm import ChatGoogle  # noqa: E402
from browser_use.llm.messages import UserMessage  # noqa: E402

PROXY = os.environ.get("BENCH_PROXY", "http://127.0.0.1:8899")
TOOL_TAG = "browser-use"
MODEL = os.environ.get("MODEL_NAME", "gemini-3.5-flash-lite")  # same default as v3 (run_context.py)
API_KEY = "bench"  # placeholder; the proxy swaps in the real key

# Mirrored from v3 (agent/runner.py + orchestrator.py cold run): text-only DOM (no screenshots), browser-use judge off,
# default flash_mode (off), default max_steps (500 = Agent.run default, v3 passes none), default step/LLM timeouts,
# fresh temp profile per record, keep_alive=False, Chromium with Playwright's default 1280x720 viewport, no extensions.
USE_VISION = False
USE_JUDGE = False
# The same Chromium build v3 launches (Playwright 1.63 -> chromium-1243, "Google Chrome for Testing"). browser-use's own
# lookup misses that bundle name and would fall back to the system Google Chrome, so it is pinned here. CHROME_PATH overrides.
def _find_chromium() -> str | None:
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/Library/Caches/ms-playwright")).expanduser()
    hits = sorted(base.glob("chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    return str(hits[-1]) if hits else None  # None = let browser-use pick


CHROMIUM = _find_chromium()
# v3 makes one attempt per call and retries 5xx up to 3 times itself (key switching on 429 is the proxy's job here)
LLM_MAX_RETRIES = 3


# Builds the Gemini chat model pointed at the counting proxy under the given tool tag.
def build_llm(tag: str = TOOL_TAG) -> ChatGoogle:
    return ChatGoogle(
        model=MODEL,
        api_key=API_KEY,
        http_options={"base_url": f"{PROXY}/{tag}/"},
        max_retries=LLM_MAX_RETRIES,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Counts the proxy's logged calls for this tool between two ISO times (read-only on bench/logs/calls.jsonl).
def proxy_calls(start: str, end: str, tag: str = TOOL_TAG) -> dict:
    log = BENCH / "logs" / "calls.jsonl"
    out = {"all": 0, "ok": 0, "tokens_in": 0, "tokens_out": 0}
    if not log.exists():
        return out
    for line in log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("tool") != tag or not (start <= r.get("at", "") <= end):
            continue
        out["all"] += 1
        out["ok"] += 1 if 200 <= int(r.get("status", 0)) < 300 else 0
        u = r.get("usage") or {}
        out["tokens_in"] += u.get("in") or 0
        out["tokens_out"] += u.get("out") or 0
    return out


# Runs one record with a brand-new Agent and browser; returns a result dict.
async def run_record(invoice: str, task: str, max_steps: int, use_vision: bool) -> dict:
    row: dict = {"invoice_no": invoice, "use_vision": use_vision, "started_at": now_iso()}
    t0 = time.monotonic()
    agent = None
    try:
        with tempfile.TemporaryDirectory(prefix="v7_bu_profile_") as profile_dir:
            profile = BrowserProfile(
                headless=True,
                executable_path=CHROMIUM,
                user_data_dir=profile_dir,
                keep_alive=False,
                viewport={"width": 1280, "height": 720},
                enable_default_extensions=False,
            )
            agent = Agent(task=task, llm=build_llm(), browser_profile=profile, use_vision=use_vision, use_judge=USE_JUDGE)
            try:
                history = await agent.run(max_steps=max_steps)
            finally:
                try:
                    await agent.close()
                except Exception:
                    pass
        row["steps"] = history.number_of_steps()
        row["is_done"] = history.is_done()
        row["success"] = bool(history.is_successful())
        row["final_answer"] = history.final_result()
        row["errors"] = [e for e in history.errors() if e]
        if history.usage is not None:
            row["usage"] = {
                "prompt_tokens": history.usage.total_prompt_tokens,
                "completion_tokens": history.usage.total_completion_tokens,
                "llm_entries": history.usage.entry_count,
            }
    except Exception as exc:  # one bad record must not stop the batch
        row.setdefault("steps", None)
        row["success"] = False
        row["exception"] = f"{type(exc).__name__}: {exc}"
    row["ended_at"] = now_iso()
    row["duration_s"] = round(time.monotonic() - t0, 1)
    return row


async def check_llm() -> None:
    llm = build_llm("browser-use-setup")
    start = now_iso()
    resp = await llm.ainvoke([UserMessage(content="Reply with the single word: ok")])
    await asyncio.sleep(0.5)
    print("model said:", repr(resp.completion), "| proxy calls logged:", proxy_calls(start, now_iso(), "browser-use-setup"))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="run only the first N records")
    ap.add_argument("--only", help="run only this invoice number")
    ap.add_argument("--max-steps", type=int, default=500, help="Agent.run max_steps (default 500, as v3)")
    ap.add_argument("--vision-retry", action="store_true",
                    help="like v3's cold run: if a record fails text-only, retry it once with vision (off by default)")
    ap.add_argument("--check-llm", action="store_true", help="make one tiny model call through the proxy and exit")
    args = ap.parse_args()

    if args.check_llm:
        await check_llm()
        return 0

    invoices = [r["invoice_no"] for r in json.loads((BENCH / "records.json").read_text())]
    if args.only:
        invoices = [i for i in invoices if i == args.only] or [args.only]
    if args.limit:
        invoices = invoices[: args.limit]

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS / f"{stamp}.json"
    run = {
        "tool": TOOL_TAG, "model": MODEL, "proxy": f"{PROXY}/{TOOL_TAG}/", "started_at": now_iso(),
        "settings": {"use_vision": USE_VISION, "use_judge": USE_JUDGE, "flash_mode": False, "max_steps": args.max_steps,
                     "headless": True, "browser": CHROMIUM or "browser-use default", "viewport": "1280x720", "llm_max_retries": LLM_MAX_RETRIES,
                     "vision_retry": args.vision_retry, "reuse_between_records": "none"},
        "records": [],
    }

    for inv in invoices:
        task = (BENCH / "tasks" / f"{inv}.txt").read_text()
        print(f"\n=== {inv}: starting ===", flush=True)
        row = await run_record(inv, task, args.max_steps, USE_VISION)
        if args.vision_retry and not row["success"]:
            print(f"=== {inv}: text-only run failed, retrying once with vision ===", flush=True)
            first = row
            row = await run_record(inv, task, args.max_steps, True)
            row["first_attempt"] = first
            row["started_at"] = first["started_at"]
        row["proxy_calls"] = proxy_calls(row["started_at"], row["ended_at"])
        run["records"].append(row)
        out_path.write_text(json.dumps(run, indent=2, default=str))  # written after every record
        print(f"=== {inv}: success={row['success']} steps={row.get('steps')} calls={row['proxy_calls']['all']} "
              f"time={row['duration_s']}s ===", flush=True)

    run["ended_at"] = now_iso()
    recs = run["records"]
    total_calls = sum(r["proxy_calls"]["all"] for r in recs)
    summary = (f"browser-use control: {sum(r['success'] for r in recs)}/{len(recs)} self-reported success, "
               f"steps={sum(r.get('steps') or 0 for r in recs)}, proxy calls={total_calls} "
               f"({total_calls / max(len(recs), 1):.1f}/record), time={sum(r['duration_s'] for r in recs):.0f}s")
    run["summary"] = summary
    out_path.write_text(json.dumps(run, indent=2, default=str))
    print("\n" + summary)
    print(f"results: {out_path}")
    print("grade with: python3 ../bench/grade.py   |   count with: python3 ../bench/calls.py browser-use")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
