"""Wiring check (a few model calls): one extraction block reads the AP Desk homepage title through Skyvern + bench proxy.

    .venv/bin/python smoke.py

Does not click, type, decide or save anything. Prints the run status, the extracted title, and the proxy calls it made.
"""

import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import run  # noqa: E402

api = run.Api("http://127.0.0.1:8010")
if not api.up():
    sys.exit("server not up: ./start_server.sh")
definition = {
    "title": "wiring check (read AP Desk title)",
    "proxy_location": "NONE",
    "run_with": "agent",
    "workflow_definition": {
        "parameters": [],
        "blocks": [
            {
                "block_type": "extraction",
                "label": "read_title",
                "url": run.SITE,
                "data_extraction_goal": "What is the name/title of this web application as shown on the page? Only read; do not interact.",
                "data_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
                "max_steps_per_run": 1,
            }
        ],
    },
}
wpid = run.ensure_workflow(api, definition, False)
start = run.now()
r = api.call("POST", "/v1/run/workflows", {"workflow_id": wpid, "proxy_location": "NONE", "parameters": {}})
rid = r["run_id"]
while r.get("status") not in run.TERMINAL:
    time.sleep(2)
    r = api.call("GET", f"/v1/runs/{rid}")
time.sleep(5)
end = run.now()
print(json.dumps({"run_id": rid, "status": r["status"], "output": r.get("output"), "failure_reason": r.get("failure_reason")}, indent=2, default=str))
print("proxy calls:", run.proxy_calls(start, end))
d = run.run_details(rid)
print("skyvern steps:", d["steps"], "tokens:", d["tokens"], "llm_request artifacts:", d["skyvern_llm_request_artifacts"])
