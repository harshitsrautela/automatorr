"""Scores the plan engine's DECISIONS against the AP Desk test site's correct answers, with the real model but no live agent.
It dry-runs the plan learned from tests/fixtures/ap_trace.json (nothing is saved on the site) on N records and compares what
the plan would enter with expected.json. About 3 model calls per record, so it is a cheap way to judge a prompt change.

    python tests/score_plan.py 10        # the first 10 records of records.json, one at a time (needs the v3 server on :8002 and AP Desk on :5180)
    python tests/score_plan.py 30 5      # 30 records, 5 at a time through the question batchers (the way a trusted plan runs)
    python tests/score_plan.py 30 5 returns   # the same on the Returns Desk (testsites/returns-desk, port 5181, workflow 35)
"""

import asyncio
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sites import site_path  # noqa: E402

from playwright.async_api import async_playwright

import recipe.llm as llm
import recipe.plan_exec as px
from recipe.plan import compile_plan
from run_context import RunContext, extra_keys_from_env
from run_service import gemini_primary_key

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
PARALLEL = int(sys.argv[2]) if len(sys.argv) > 2 else 1
SITE_NAME = sys.argv[3] if len(sys.argv) > 3 else "ap"
HERE = os.path.dirname(os.path.abspath(__file__))
SITES = {
    "ap": {"url": "http://localhost:5180", "dir": "ap-desk", "fixture": "ap_trace.json", "key": "invoice_no", "workflow": 34},
    "returns": {"url": "http://localhost:5181", "dir": "returns-desk", "fixture": "returns_trace.json", "key": "rma", "workflow": 35},
}
cfg = SITES[SITE_NAME]
SITE, KEY = cfg["url"], cfg["key"]
fx = json.load(open(os.path.join(HERE, "fixtures", cfg["fixture"])))
plan, why = compile_plan(fx["fields"], fx["trace"])
assert plan, why
base = site_path(cfg["dir"])
expected = json.load(open(os.path.join(base, "expected.json")))
records = [r[KEY] for r in json.load(open(os.path.join(base, "records.json")))][:N]
workflow = json.load(urllib.request.urlopen(f"http://localhost:8002/api/workflows/{cfg['workflow']}"))
key = gemini_primary_key(None)
ctx = RunContext(target_url=SITE, workflow_name="score", match_key_field=KEY, model_name=workflow["model_name"], gemini_api_key=key,
                 extra_gemini_keys=tuple(k for k in extra_keys_from_env() if k != key), task_instructions=workflow["task_prompt"])

calls = {"n": 0}
real_ask = px.ask_json


async def counting(*a, **k):
    calls["n"] += 1
    return await real_ask(*a, **k)


px.ask_json = counting
STATUS = {"Approved": "Approve for payment", "On hold": "Place on hold", "Rejected": "Reject"}


def right_returns(exp, got):
    if got.get("disposition") != exp["disposition"]:
        return False, f"disposition {got.get('disposition')!r} (wanted {exp['disposition']!r})"
    want = {"reason": exp.get("reason"), "refund_amount": str(exp["refund_amount"]) if "refund_amount" in exp else None, "replacement_sku": exp.get("replacement_sku"), "widget1": exp.get("pickup_date")}
    for k, v in want.items():
        if v is not None and _norm_val(got.get(k)) != v:
            return False, f"{k} {got.get(k)!r} (wanted {v!r})"
    return True, ""


def _norm_val(v):
    return None if v is None else str(v).strip()


def right(exp, got):
    if SITE_NAME == "returns":
        return right_returns(exp, got)
    if got.get("status") != STATUS[exp["status"]]:
        return False, f"status {got.get('status')!r} (wanted {STATUS[exp['status']]!r})"
    if exp["status"] == "Approved":
        return (got.get("payment_date") == exp["payment_date"]), f"date {got.get('payment_date')!r} (wanted {exp['payment_date']})"
    if got.get("reason") != exp["reason"]:
        return False, f"reason {got.get('reason')!r} (wanted {exp['reason']!r})"
    if exp["status"] == "On hold":
        nums = re.findall(r"\d+(?:\.\d+)?", (got.get("note") or "").replace(",", ""))
        if sorted(nums) != sorted(str(n) for n in exp["note_numbers"]):
            return False, f"note {got.get('note')!r} (wanted numbers {exp['note_numbers']})"
    return True, ""


async def main():
    ok = 0
    batchers = px.PlanBatchers(ctx) if PARALLEL > 1 else None
    queue = asyncio.Queue()
    for no in records:
        queue.put_nowait(no)
    results = {}
    started = asyncio.get_event_loop().time()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()

        async def worker():
            while True:
                try:
                    no = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                page = await context.new_page()
                before = calls["n"]
                out = await px.run_plan(plan, {KEY: no}, ctx=ctx, page=page, emit=lambda e: None, dry=True, key=no, batchers=batchers)
                await page.close()
                results[no] = (out, out.llm_calls)

        await asyncio.gather(*(worker() for _ in range(PARALLEL)))
        await browser.close()
    if batchers is not None:
        print(f"slot groups {batchers.slots.groups}, decision groups {batchers.form.groups}")
        await batchers.close()
    for no in records:
        out, charged = results[no]
        good, why = right(expected[no], out.planned) if out.ok else (False, f"did not run: {out.error}")
        ok += good
        print(("ok   " if good else "MISS ") + f"{no} {expected[no].get('status') or expected[no].get('disposition')}/{expected[no].get('reason') or 'pay'} charged={charged} {why}")
    secs = asyncio.get_event_loop().time() - started
    print(f"\ncorrect {ok}/{len(records)}; model calls {calls['n']} ({calls['n'] / len(records):.2f} per record); {secs:.0f} s for {len(records)} records ({PARALLEL} at a time)")


asyncio.run(main())
