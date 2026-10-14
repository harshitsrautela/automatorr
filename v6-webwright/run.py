"""Webwright contender for the AP Desk comparison: "AI does the first record, then its code repeats it".

    ./run.sh                       # all 5 records in bench/records.json, in order
    ./run.sh --limit 2             # first 2 records only
    ./run.sh --only INV-26-2820    # one record (runs Webwright on it, i.e. a learn run)
    ./run.sh --use-saved-script    # skip learning: every record runs artifacts/final_script.py (fallback still on)
    ./run.sh --check               # setup check only, no model calls

Protocol
  1. The first selected record runs Webwright (base.yaml + config/bench_gemini.yaml) on bench/tasks/<INV>.txt
     plus one generic sentence (ASK below). Its final_script.py is copied to artifacts/.
  2. Every later record runs that saved script with the invoice number as its argument (0 model calls).
  3. If the script is missing or exits non-zero, the record falls back to a Webwright run on its own task
     (same ASK sentence; calls counted). If that fallback is Submitted and leaves a script, the script
     replaces artifacts/final_script.py for the records after it.
Results: results/<UTC stamp>.json (one row per record + summary). Model calls come from the bench proxy log.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "bench"
VENV_BIN = HERE / ".venv" / "bin"
PY = VENV_BIN / "python"
ARTIFACTS = HERE / "artifacts"
RESULTS = HERE / "results"
OUTPUTS = HERE / "outputs"
SAVED_SCRIPT = ARTIFACTS / "final_script.py"
TOOL_TAG = "webwright"
START_URL = "http://localhost:5180"
CONFIGS = ["base.yaml", str(HERE / "config" / "bench_gemini.yaml")]

# The only text added to the bench task (generic; no site hints).
ASK = ("Also: leave final_script.py as a re-runnable script that takes the invoice number as a "
       "command-line argument and does this whole task for any invoice.")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def proxy_calls(start: str, end: str) -> dict:
    """Counts this tool's calls in bench/logs/calls.jsonl between two ISO times (read-only)."""
    ok = total = tin = tout = 0
    log = BENCH / "logs" / "calls.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("tool") != TOOL_TAG or not (start <= r["at"] <= end):
                continue
            total += 1
            if 200 <= r.get("status", 0) < 300:
                ok += 1
            u = r.get("usage") or {}
            tin += u.get("in") or 0
            tout += u.get("out") or 0
    return {"ok": ok, "all": total, "tokens_in": tin, "tokens_out": tout}


def child_env(with_key: bool) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(HERE / ".venv")
    env["PYTHONUNBUFFERED"] = "1"
    env["BROWSER_MODE"] = "local"
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        env.pop(k, None)
    if with_key:
        env["OPENROUTER_API_KEY"] = "bench"  # literal placeholder; the bench proxy swaps in real keys
    return env


def run_proc(cmd: list[str], *, cwd: Path, env: dict, timeout: int, log_path: Path) -> tuple[int | None, str, bool]:
    """Runs cmd in its own process group, tees output to log_path. Returns (rc, output, timed_out)."""
    with log_path.open("w") as fh:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            rc = None
    return rc, log_path.read_text(errors="replace"), timed_out


def find_final_script(workspace: Path) -> Path | None:
    root = workspace / "final_script.py"
    if root.exists():
        return root
    runs = sorted(workspace.glob("final_runs/run_*/final_script.py"),
                  key=lambda p: int(re.sub(r"\D", "", p.parent.name) or 0))
    return runs[-1] if runs else None


def run_webwright(invoice: str, task: str, out_dir: Path, timeout: int, label: str) -> dict:
    row: dict = {"webwright_task_added_sentence": ASK}
    cmd = [str(PY), "-m", "cuawright.webwright.run.cli", "main"]
    for c in CONFIGS:
        cmd += ["-c", c]
    cmd += ["-t", f"{task.rstrip()}\n\n{ASK}", "--start-url", START_URL, "--task-id", invoice, "-o", str(out_dir)]
    rc, out, timed_out = run_proc(cmd, cwd=HERE, env=child_env(True), timeout=timeout,
                                  log_path=out_dir / f"{invoice}.{label}.log")
    row.update(returncode=rc, timed_out=timed_out)
    workspaces = sorted(out_dir.glob(f"{invoice}_*/"), key=lambda p: p.stat().st_mtime)
    if not workspaces:
        row.update(success=False, errors=[f"no Webwright workspace found; output tail: {out[-1500:]}"])
        return row
    ws = workspaces[-1]
    row["workspace"] = str(ws)
    errors = []
    traj = ws / "trajectory.json"
    if traj.exists():
        info = json.loads(traj.read_text()).get("info", {})
        row.update(exit_status=info.get("exit_status"), webwright_turns=info.get("api_calls"),
                   format_errors=info.get("format_errors"), final_output=info.get("submission") or "")
    else:
        errors.append("trajectory.json missing")
    if timed_out:
        errors.append(f"Webwright killed after {timeout}s")
    if rc not in (0, None):
        errors.append(f"Webwright exited {rc}: {out[-1500:]}")
    rt = ws / "runtime_errors.jsonl"
    if rt.exists() and rt.read_text().strip():
        errors.append("runtime_errors.jsonl: " + rt.read_text()[-1500:])
    script = find_final_script(ws)
    row["script_produced"] = str(script) if script else None
    row["success"] = row.get("exit_status") == "Submitted"  # self-reported (gated by its self_reflection PASS)
    row["errors"] = errors
    return row


def invoice_arg_style(script: Path) -> list[str]:
    """How to pass the invoice to the script: a positional arg, or an --invoice... flag if that is what it defines."""
    try:
        tree = ast.parse(script.read_text())
    except SyntaxError:
        return []
    flags, positional = [], False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if names and not names[0].startswith("-"):
                positional = True
            flags += [n for n in names if n.startswith("--")]
    if positional:
        return []
    inv = [f for f in flags if "invoice" in f.lower()]
    return [inv[0]] if inv else []


def run_script(invoice: str, out_dir: Path, timeout: int) -> dict:
    run_dir = (out_dir / f"script_{invoice}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    script = run_dir / "final_script.py"
    shutil.copy2(SAVED_SCRIPT, script)
    argv = invoice_arg_style(script) + [invoice]
    env = child_env(False) | {"WORKSPACE_DIR": str(run_dir), "FINAL_SCRIPT_PATH": str(script),
                              "TMPDIR": str(run_dir)}
    rc, out, timed_out = run_proc([str(PY), str(script), *argv], cwd=run_dir, env=env, timeout=timeout,
                                  log_path=run_dir / "stdout.txt")
    errors = []
    if timed_out:
        errors.append(f"script killed after {timeout}s")
    elif rc != 0:
        errors.append(f"script exited {rc}: {out[-1500:]}")
    return {"script_dir": str(run_dir), "script_argv": argv, "returncode": rc, "timed_out": timed_out,
            "final_output": out[-3000:], "success": rc == 0, "errors": errors}


def save_script(src: Path, invoice: str, stamp: str, workspace: Path) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    shutil.copy2(src, SAVED_SCRIPT)
    shutil.copy2(src, ARTIFACTS / f"final_script.from_{invoice}.{stamp}.py")
    if (workspace / "plan.md").exists():
        shutil.copy2(workspace / "plan.md", ARTIFACTS / f"plan.from_{invoice}.{stamp}.md")


def check() -> int:
    ok = True
    try:
        r = subprocess.run([str(PY), "-c", "import cuawright.webwright, playwright; print('imports ok')"],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip()); ok &= r.returncode == 0
        r = subprocess.run([str(PY), "-c", (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            " b=p.chromium.launch(headless=True); g=b.new_page(); g.goto('%s'); print('site:', g.title()); b.close()"
            % START_URL)], capture_output=True, text=True, timeout=60)
        print(r.stdout.strip() or r.stderr.strip()[-500:]); ok &= r.returncode == 0
        with socket.create_connection(("127.0.0.1", 8899), timeout=3):
            print("proxy: port 8899 open")
    except Exception as exc:  # noqa: BLE001
        print("check failed:", exc); ok = False
    print("saved script:", SAVED_SCRIPT if SAVED_SCRIPT.exists() else "none yet")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="run only the first N records")
    ap.add_argument("--only", help="run only this invoice number")
    ap.add_argument("--use-saved-script", action="store_true",
                    help="no learn run: every record uses artifacts/final_script.py (fallback still applies)")
    ap.add_argument("--no-fallback", action="store_true", help="do not run Webwright when the script fails")
    ap.add_argument("--webwright-timeout", type=int, default=3600, help="wall clock per Webwright run, s (3600)")
    ap.add_argument("--script-timeout", type=int, default=600, help="wall clock per script run, s (600)")
    ap.add_argument("--check", action="store_true", help="setup check only (no model calls)")
    args = ap.parse_args()
    if args.check:
        return check()

    invoices = [r["invoice_no"] for r in json.loads((BENCH / "records.json").read_text())]
    if args.only:
        invoices = [i for i in invoices if i == args.only] or [args.only]
    if args.limit:
        invoices = invoices[: args.limit]
    if args.use_saved_script and not SAVED_SCRIPT.exists():
        print("no artifacts/final_script.py to reuse", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUTPUTS / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{stamp}.json"
    run = {"tool": TOOL_TAG, "model": "gemini-3.5-flash-lite", "started": now_iso(), "configs": CONFIGS,
           "added_sentence": ASK, "webwright_commit": subprocess.run(
               ["git", "-C", str(HERE / "webwright"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "outputs": str(out_dir), "records": []}

    have_script = args.use_saved_script
    for n, inv in enumerate(invoices, 1):
        task = (BENCH / "tasks" / f"{inv}.txt").read_text()
        start, t0 = now_iso(), time.time()
        print(f"[{n}/{len(invoices)}] {inv} ...", flush=True)
        if not have_script:
            row = {"mode": "webwright"} | run_webwright(inv, task, out_dir, args.webwright_timeout, "learn")
        else:
            row = {"mode": "script"} | run_script(inv, out_dir, args.script_timeout)
            if not row["success"] and not args.no_fallback:
                fb = run_webwright(inv, task, out_dir, args.webwright_timeout, "fallback")
                row = {"mode": "fallback", "script_attempt": row} | fb
        # A learn run's script is always kept; a fallback's script replaces it only if that run was Submitted.
        produced = row.get("script_produced")
        if produced and (row["mode"] == "webwright" or row.get("success")):
            save_script(Path(produced), inv, stamp, Path(row["workspace"]))
            row["script_saved_to"] = str(SAVED_SCRIPT)
            have_script = True
        end = now_iso()
        row = {"invoice_no": inv, "start": start, "end": end, "seconds": round(time.time() - t0, 1),
               "model_calls": proxy_calls(start, end)} | row
        run["records"].append(row)
        print(f"    mode={row['mode']} success={row.get('success')} calls={row['model_calls']['ok']} "
              f"turns={row.get('webwright_turns')} {row['seconds']}s errors={len(row.get('errors') or [])}", flush=True)
        out_path.write_text(json.dumps(run, indent=2, default=str))

    recs = run["records"]
    run["ended"] = now_iso()
    run["model_calls_total"] = proxy_calls(run["started"], run["ended"])
    modes = {m: sum(r["mode"] == m for r in recs) for m in ("webwright", "script", "fallback")}
    try:
        g = subprocess.run([sys.executable, str(BENCH / "grade.py"), "--json"], capture_output=True, text=True, timeout=30)
        run["grade"] = json.loads(g.stdout.strip().splitlines()[-1]) if g.returncode == 0 else g.stderr[-500:]
    except Exception as exc:  # noqa: BLE001
        run["grade"] = f"grade.py failed: {exc}"
    run["summary"] = (f"webwright: {sum(bool(r.get('success')) for r in recs)}/{len(recs)} self-reported success, "
                      f"modes {modes}, model calls {run['model_calls_total']['ok']} ok "
                      f"({run['model_calls_total']['all']} all), "
                      f"{sum(r['seconds'] for r in recs):.0f}s total; reusable script: "
                      f"{'artifacts/final_script.py' if SAVED_SCRIPT.exists() else 'none'}")
    out_path.write_text(json.dumps(run, indent=2, default=str))
    print("\n" + run["summary"])
    print("grade:", run["grade"])
    print(f"results: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
