"""Runs the comparison records through a self-hosted Skyvern (v5-skyvern), one at a time, in order.

    ./run.sh                       # all 5 records from ../bench/records.json
    ./run.sh --limit 2             # first 2 records
    ./run.sh --only INV-26-2820    # one record (repeat --only or comma-separate for more)
    ./run.sh --block task_v2       # use Skyvern 2.0 (planner) block instead of the default 1.0 task block
    ./run.sh --run-with agent      # no script caching (pure agent every record)

How a record runs: one saved Skyvern workflow ("AP Desk invoice decision") with one workflow parameter `invoice_no` and
one block whose goal is the exact bench task text, the invoice number replaced by {{ invoice_no }} (rendered text is
checked to equal ../bench/tasks/<INV>.txt). The workflow is created once and reused (data/workflow.json), with
run_with=code + ai_fallback=true + cache_key "default": Skyvern runs the first record with its agent and then
generates a Python script from that run; later records run that cached script (falling back to the agent per block
if the script fails). Results: results/<UTC timestamp>.json (+ results/scripts/ for the generated code).
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent / "bench"
DB = HERE / "data" / "skyvern.db"
SERVER_LOG = HERE / "data" / "server.log"
STATE = HERE / "data" / "workflow.json"
RESULTS = HERE / "results"
SITE = "http://localhost:5180"
TERMINAL = {"completed", "failed", "terminated", "canceled", "timed_out"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env_value(name: str) -> str | None:
    for line in (HERE / ".env").read_text().splitlines():
        m = re.match(rf"\s*{name}\s*=\s*['\"]?([^'\"\s#]+)", line)
        if m:
            return m.group(1)
    return None


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def key(self) -> str:
        k = env_value("SKYVERN_API_KEY")
        if not k:
            sys.exit("no SKYVERN_API_KEY in .env (the server writes it on first start)")
        return k

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60) -> dict | list:
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"x-api-key": self.key(), "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:2000]}") from None

    def up(self) -> bool:
        try:
            urllib.request.urlopen(self.base + "/api/v1/heartbeat", timeout=5)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------- the task text (same text every tool gets)


def goal_template(records: list[str]) -> str:
    """bench/tasks/<first>.txt with that invoice number replaced by {{ invoice_no }}; checked against every record."""
    first = records[0]
    template = (BENCH / "tasks" / f"{first}.txt").read_text().replace(first, "{{ invoice_no }}")
    for inv in records:
        want = (BENCH / "tasks" / f"{inv}.txt").read_text()
        if template.replace("{{ invoice_no }}", inv) != want:
            sys.exit(f"task template does not reproduce bench/tasks/{inv}.txt exactly")
    return template


def workflow_definition(goal: str, block: str, run_with: str, max_steps: int, code_version: int | None) -> dict:
    if block == "task":  # Skyvern 1.0 engine: one agent task, screenshot + DOM per step
        blk = {
            "block_type": "task",
            "label": "decide_invoice",
            "url": SITE,  # the URL the task text starts with ("Go to http://localhost:5180")
            "navigation_goal": goal,
            "parameter_keys": ["invoice_no"],
            "max_steps_per_run": max_steps,
        }
    else:  # Skyvern 2.0 engine: planner splits the prompt into sub-tasks
        blk = {
            "block_type": "task_v2",
            "label": "decide_invoice",
            "url": SITE,
            "prompt": goal,
            "max_steps": max_steps,
        }
    wf = {
        "title": f"AP Desk invoice decision ({block}, {run_with}{f', code_version {code_version}' if code_version else ''})",
        "description": "terminautomator tool comparison: one AP Desk invoice per run",
        "proxy_location": "NONE",
        "run_with": run_with,
        "ai_fallback": True,
        "cache_key": "default",  # one cached script for every invoice_no (the parameter is passed into it)
        "workflow_definition": {
            "parameters": [
                {"parameter_type": "workflow", "key": "invoice_no", "workflow_parameter_type": "string"},
            ],
            "blocks": [blk],
        },
    }
    if code_version:  # unset = Skyvern's default, which stores code_version 2 (adaptive caching) in 1.0.55
        wf["code_version"] = code_version
    return wf


def ensure_workflow(api: Api, definition: dict, fresh: bool) -> str:
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:16]
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not fresh and digest in state:
        return state[digest]["workflow_permanent_id"]
    wf = api.call("POST", "/v1/workflows", {"json_definition": definition})
    wpid = wf["workflow_permanent_id"]
    state[digest] = {"workflow_permanent_id": wpid, "created_at": now(), "title": definition["title"]}
    STATE.write_text(json.dumps(state, indent=2))
    print(f"created workflow {wpid}  ({definition['title']})")
    return wpid


# ---------------------------------------------------------------- what Skyvern recorded (read-only SQLite)


def db() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def scripts_for(wpid: str) -> list[dict]:
    with db() as c:
        rows = c.execute(
            """select ws.script_id, ws.cache_key_value, ws.status, ws.workflow_run_id as generated_from_run,
                      (select max(version) from scripts s where s.script_id = ws.script_id and s.deleted_at is null) as version
               from workflow_scripts ws where ws.workflow_permanent_id = ? and ws.deleted_at is null
               order by ws.modified_at desc""",
            (wpid,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_script_code(script_id: str, tag: str) -> list[str]:
    """Copies the latest revision's generated files (main.py ...) into results/scripts/<tag>/."""
    saved = []
    with db() as c:
        rev = c.execute(
            "select script_revision_id, version from scripts where script_id = ? and deleted_at is null order by version desc limit 1",
            (script_id,),
        ).fetchone()
        if not rev:
            return saved
        files = c.execute(
            """select f.file_path, a.uri from script_files f left join artifacts a on a.artifact_id = f.artifact_id
               where f.script_revision_id = ? and f.deleted_at is null""",
            (rev["script_revision_id"],),
        ).fetchall()
    out = RESULTS / "scripts" / f"{tag}_{script_id}_v{rev['version']}"
    for f in files:
        uri = f["uri"] or ""
        src = Path(uri[len("file://") :]) if uri.startswith("file://") else None
        if src and src.exists():
            dst = out / f["file_path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            saved.append(str(dst.relative_to(HERE)))
    return saved


def run_details(run_id: str) -> dict:
    """Steps, actions, tokens, LLM-call artifacts, script use for one workflow run (and its child runs)."""
    with db() as c:
        ids, frontier = {run_id}, [run_id]
        while frontier:  # task_v2 runs its sub-tasks in child workflow runs
            q = ",".join("?" * len(frontier))
            kids = [
                r[0]
                for r in c.execute(
                    f"""select workflow_run_id from workflow_runs where parent_workflow_run_id in ({q})
                        union select block_workflow_run_id from workflow_run_blocks
                        where workflow_run_id in ({q}) and block_workflow_run_id is not null""",
                    frontier + frontier,
                )
            ]
            frontier = [k for k in kids if k and k not in ids]
            ids.update(frontier)
        q = ",".join("?" * len(ids))
        idl = list(ids)
        wr = dict(c.execute("select * from workflow_runs where workflow_run_id = ?", (run_id,)).fetchone() or {})
        tasks = [
            dict(r)
            for r in c.execute(
                f"""select task_id, status, failure_reason, extracted_information, max_steps_per_run, started_at, finished_at
                    from tasks where workflow_run_id in ({q}) order by created_at""",
                idl,
            )
        ]
        tq = ",".join("?" * len(tasks)) or "''"
        tids = [t["task_id"] for t in tasks]
        steps = c.execute(
            f"""select count(*) n, sum(input_token_count) tin, sum(output_token_count) tout,
                       sum(coalesce(reasoning_token_count,0)) treason, sum(step_cost) cost
                from steps where task_id in ({tq})""",
            tids,
        ).fetchone()
        actions = [
            dict(r)
            for r in c.execute(
                f"""select action_type, status, created_by, count(*) n from actions
                    where workflow_run_id in ({q}) or task_id in ({tq})
                    group by action_type, status, created_by order by n desc""",
                idl + tids,
            )
        ]
        llm_artifacts = c.execute(
            f"""select count(*) from artifacts where artifact_type = 'llm_request'
                and (workflow_run_id in ({q}) or task_id in ({tq}) or run_id in ({q})
                     or observer_cruise_id in (select observer_cruise_id from observer_cruises where workflow_run_id in ({q})))""",
            idl + tids + idl + idl,
        ).fetchone()[0]
        thoughts = c.execute(
            f"""select count(*) from observer_thoughts where observer_cruise_id in
                (select observer_cruise_id from observer_cruises where workflow_run_id in ({q}))""",
            idl,
        ).fetchone()[0]
        blocks = [
            dict(r)
            for r in c.execute(
                f"""select label, block_type, status, failure_reason, script_run, llm_cost from workflow_run_blocks
                    where workflow_run_id = ? order by created_at""",
                (run_id,),
            )
        ]
        episodes = c.execute(
            f"select count(*) from script_fallback_episodes where workflow_run_id in ({q})", idl
        ).fetchone()[0]
    return {
        "workflow_run": {
            k: wr.get(k)
            for k in ("status", "failure_reason", "run_with", "ai_fallback", "code_gen", "script_run", "started_at", "finished_at")
        },
        "child_workflow_runs": len(ids) - 1,
        "blocks": blocks,
        "tasks": tasks,
        "steps": steps["n"] or 0,
        "tokens": {"in": steps["tin"] or 0, "out": steps["tout"] or 0, "reasoning": steps["treason"] or 0},
        "skyvern_step_cost_usd": round(steps["cost"] or 0, 6),
        "actions": actions,
        "action_count": sum(a["n"] for a in actions),
        "skyvern_llm_request_artifacts": llm_artifacts,
        "task_v2_planner_thoughts": thoughts,
        "script_fallback_episodes": episodes,
    }


def execution_mode(run_id: str) -> str | None:
    """Skyvern logs one 'Workflow run execution mode resolved' line per run: code | code_generation | agent."""
    if not SERVER_LOG.exists():
        return None
    mode = None
    with SERVER_LOG.open(errors="replace") as f:
        for line in f:
            if run_id in line and "execution mode resolved" in line:
                m = re.search(r"execution_mode=(\w+)", ANSI.sub("", line))
                mode = m.group(1) if m else mode
    return mode


def proxy_calls(start: str, end: str) -> dict:
    """Model calls the bench proxy logged for tool 'skyvern' in [start, end]."""
    rows = []
    log = BENCH / "logs" / "calls.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("tool") == "skyvern" and start <= r["at"] <= end:
                rows.append(r)
    ok = [r for r in rows if 200 <= r["status"] < 300]
    return {
        "all": len(rows),
        "ok": len(ok),
        "tokens_in": sum((r.get("usage") or {}).get("in") or 0 for r in rows),
        "tokens_out": sum((r.get("usage") or {}).get("out") or 0 for r in rows),
        "statuses": sorted({r["status"] for r in rows}),
    }


def last_proxy_call(after: str) -> str | None:
    log = BENCH / "logs" / "calls.jsonl"
    last = None
    if log.exists():
        for line in log.read_text().splitlines():
            if '"tool":"skyvern"' in line:
                r = json.loads(line)
                if r["at"] >= after:
                    last = r["at"]
    return last


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", action="append", default=[], help="invoice number(s); repeat or comma-separate")
    ap.add_argument("--block", choices=["task", "task_v2"], default="task")
    ap.add_argument("--run-with", choices=["code", "agent"], default="code")
    ap.add_argument("--code-version", type=int, choices=[1, 2], help="script caching flavour; default = Skyvern's (2, adaptive)")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--fresh-workflow", action="store_true", help="create a new workflow (no cached script yet)")
    ap.add_argument("--timeout-min", type=float, default=30)
    ap.add_argument("--settle-sec", type=float, default=20, help="wait after a run ends until no new model calls")
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    a = ap.parse_args()

    api = Api(a.base)
    if not api.up():
        subprocess.run([str(HERE / "start_server.sh")], check=True)

    all_records = [r["invoice_no"] for r in json.loads((BENCH / "records.json").read_text())]
    goal = goal_template(all_records)
    only = [x for o in a.only for x in o.split(",") if x]
    records = [r for r in all_records if not only or r in only]
    if a.limit:
        records = records[: a.limit]

    definition = workflow_definition(goal, a.block, a.run_with, a.max_steps, a.code_version)
    wpid = ensure_workflow(api, definition, a.fresh_workflow)
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS / f"{stamp}.json"
    out = {
        "tool": "skyvern",
        "skyvern_version": subprocess.run(
            [str(HERE / ".venv" / "bin" / "python"), "-c", "import importlib.metadata as m;print(m.version('skyvern'))"],
            capture_output=True, text=True,
        ).stdout.strip(),
        "model": "gemini-3.5-flash-lite (Skyvern LLM_KEY=GEMINI_3.5_FLASH_LITE via bench proxy)",
        "workflow_permanent_id": wpid,
        "config": {k: getattr(a, k) for k in ("block", "run_with", "code_version", "max_steps")},
        "started_at": now(),
        "records": [],
    }

    for inv in records:
        before = scripts_for(wpid)
        start = now()
        t0 = time.time()
        rec = {"invoice_no": inv, "start": start, "script_before": before}
        print(f"\n== {inv}  (cached script before: {'yes v%s' % before[0]['version'] if before else 'no'})", flush=True)
        try:
            run = api.call(
                "POST",
                "/v1/run/workflows",
                {
                    "workflow_id": wpid,
                    "parameters": {"invoice_no": inv},
                    "proxy_location": "NONE",
                    "title": f"{inv} {stamp}",
                },
            )
            run_id = run["run_id"]
            rec["run_id"] = run_id
            status = run.get("status")
            while status not in TERMINAL:
                if time.time() - t0 > a.timeout_min * 60:
                    api.call("POST", f"/v1/runs/{run_id}/cancel")
                    rec["error"] = f"harness timeout after {a.timeout_min} min (run canceled)"
                    break
                time.sleep(3)
                run = api.call("GET", f"/v1/runs/{run_id}")
                status = run.get("status")
            run_end = now()
            # Script generation / review can still call the model after the run is marked done: wait until quiet.
            quiet_since = time.time()
            last = last_proxy_call(start)
            while time.time() - quiet_since < a.settle_sec:
                time.sleep(2)
                cur = last_proxy_call(start)
                if cur != last:
                    last, quiet_since = cur, time.time()
            end = now()
            rec.update(
                {
                    "run_end": run_end,
                    "end": end,
                    "seconds": round(time.time() - t0, 1),
                    "run_seconds": round(
                        (dt.datetime.fromisoformat(run_end.rstrip("Z")) - dt.datetime.fromisoformat(start.rstrip("Z"))).total_seconds(), 1
                    ),
                    "status": status,
                    "output": run.get("output"),
                    "failure_reason": run.get("failure_reason"),
                    "errors": run.get("errors"),
                    "script_run": run.get("script_run"),
                    "execution_mode": execution_mode(run_id),
                    "skyvern": run_details(run_id),
                    "proxy_calls": proxy_calls(start, end),
                }
            )
        except Exception as e:  # keep going with the next record
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["end"] = now()
        after = scripts_for(wpid)
        rec["script_after"] = after
        rec["cached_script_used"] = rec.get("execution_mode") == "code"
        if after and (not before or after[0]["version"] != before[0]["version"]):
            rec["script_files_saved"] = save_script_code(after[0]["script_id"], f"{stamp}_{inv}")
        out["records"].append(rec)
        out_path.write_text(json.dumps(out, indent=2, default=str))
        sk = rec.get("skyvern", {})
        print(
            f"   status={rec.get('status')} mode={rec.get('execution_mode')} seconds={rec.get('seconds')} "
            f"steps={sk.get('steps')} actions={sk.get('action_count')} proxy_calls={rec.get('proxy_calls', {}).get('all')} "
            f"script_after={'v%s' % after[0]['version'] if after else 'none'} {rec.get('error') or ''}",
            flush=True,
        )
        if rec.get("failure_reason"):
            print(f"   failure: {str(rec['failure_reason'])[:300]}")

    out["finished_at"] = now()
    recs = out["records"]
    out["summary"] = {
        "records": len(recs),
        "completed": sum(r.get("status") == "completed" for r in recs),
        "cached_script_used": [r["invoice_no"] for r in recs if r.get("cached_script_used")],
        "proxy_calls_total": sum(r.get("proxy_calls", {}).get("all", 0) for r in recs),
        "proxy_calls_per_record": {r["invoice_no"]: r.get("proxy_calls", {}).get("all") for r in recs},
        "seconds_per_record": {r["invoice_no"]: r.get("seconds") for r in recs},
        "steps_per_record": {r["invoice_no"]: r.get("skyvern", {}).get("steps") for r in recs},
        "grade": "run: python3 ../bench/grade.py",
    }
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print("\nsummary:", json.dumps(out["summary"], indent=2))
    print(f"wrote {out_path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
