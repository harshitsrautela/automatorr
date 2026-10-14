"""Backend checks that need no browser and no LLM: secrets, workflows, runs, logs, verification and the storage layer.

Run against any PostgreSQL. Each run uses its own throwaway schema and drops it afterwards, so real data is never touched:

    TEST_DATABASE_URL=postgresql://user:pass@host:5432/postgres python tests/test_backend.py
"""

import json
import os
import secrets as _secrets
import sys
import time
from datetime import datetime, timezone

import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE_URL = os.environ.get("TEST_DATABASE_URL")
if not BASE_URL:
    sys.exit("Set TEST_DATABASE_URL to a PostgreSQL connection string (a throwaway schema is created inside it).")

# A scratch master key and schema, set up before the app modules read their settings
SCHEMA = "t_" + _secrets.token_hex(4)
os.environ["MASTER_KEY_PATH"] = os.path.join(ROOT, "storage", f"test-master-{SCHEMA}.key")
with psycopg.connect(BASE_URL, autocommit=True) as admin:
    admin.execute(f"CREATE SCHEMA {SCHEMA}")
os.environ["DATABASE_URL"] = BASE_URL + ("&" if "?" in BASE_URL else "?") + f"options=-c%20search_path%3D{SCHEMA}"

from fastapi.testclient import TestClient  # noqa: E402

import app_db  # noqa: E402
import main  # noqa: E402
import run_service  # noqa: E402
from cache.store import CachedStep, CacheStore  # noqa: E402
from data.models import RunResult  # noqa: E402
from orchestrator import _replayable_steps  # noqa: E402

passed = 0


# Prints PASS or FAIL for one check and stops at the first failure.
def ok(cond, msg):
    global passed
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)
    passed += 1


# Waits until the engine is idle again (a run finished), up to ten seconds.
def wait_idle():
    for _ in range(100):
        if run_service.current_run_id() is None:
            return True
        time.sleep(0.1)
    return False


# A stand-in for the Supabase client: returns fixed rows for the table the run wrote and for the answers view.
class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        return self

    def in_(self, *_):
        return self

    def execute(self):
        result = type("Result", (), {})()
        result.data = self.rows
        return result


class FakeClient:
    def table(self, name):
        if name == "credit_requests":
            return FakeQuery([{"id": 1, "decision": "approve"}, {"id": 2, "decision": None}, {"id": 3, "decision": "reject"}])
        return FakeQuery(
            [
                {"request_id": 1, "expected_decision": "approve"},
                {"request_id": 2, "expected_decision": "hold"},
                {"request_id": 3, "expected_decision": "approve"},
            ]
        )


try:
    with TestClient(main.app) as c:
        # ------------------------------------------------------------ storage layer
        with app_db.db() as conn:
            tables = {r["tablename"]: r["rowsecurity"] for r in conn.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname=?", (SCHEMA,)).fetchall()}
        want = {"secrets", "workflows", "runs", "run_events", "cached_steps", "run_metrics"}
        ok(want <= set(tables), "all 6 tables are created on startup")
        ok(all(tables[t] for t in want), "row-level security is on for every table")

        store = CacheStore()
        step = CachedStep("wf-test", "default", 1, 0, "input", "email", "type the email", "/x", "input", {"id": "e"}, "h", "fp", "")
        store.upsert_step(step)
        store.upsert_step(step)
        got = store.get_steps("wf-test", "default")
        ok(len(got) == 1 and got[0].field_name == "email" and got[0].attributes == {"id": "e"}, "learned steps save, upsert once and read back")
        store.record_run(
            workflow_name="wf-test", path_variant="default", record_id="1", mode="cold",
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            cache_hits=3, cache_misses=1, llm_steps=5, miss_reasons=["x"],
        )
        stats = store.hit_rate_stats()
        ok(stats and stats[0]["cache_hits"] == 3 and abs(stats[0]["hit_rate"] - 0.75) < 1e-9, "run metrics and hit rate")

        # ------------------------------------------------------------ secrets
        r = c.post("/api/secrets", json={"label": "My Gemini", "kind": "llm_key", "value": "AIzaSECRETKEY123456"})
        ok(r.status_code == 201 and "AIza" not in r.text and r.json()["preview"] == "••••3456", "create llm secret, masked")
        llm_id = r.json()["id"]
        r = c.post("/api/secrets", json={"label": "Test DB", "kind": "db_credential", "url": "https://x.supabase.co/", "key": "sb_secret_ABCDEFGH1234"})
        db_id = r.json()["id"]
        ok(r.status_code == 201, "create db secret")
        ok(c.post("/api/secrets", json={"label": "My Gemini", "kind": "llm_key", "value": "x" * 20}).status_code == 409, "duplicate label -> 409")
        ok(c.post("/api/secrets", json={"label": "bad", "kind": "llm_key"}).status_code == 400, "missing value -> 400")
        listing = c.get("/api/secrets").json()
        ok(len(listing) == 2 and all("value" not in s and "ciphertext" not in s for s in listing), "list never returns values")
        with app_db.db() as conn:
            stored = [bytes(r["ciphertext"]) for r in conn.execute("SELECT ciphertext FROM secrets").fetchall()]
        ok(all(b"AIzaSECRETKEY123456" not in b and b"sb_secret_ABCDEFGH1234" not in b for b in stored), "the database holds only ciphertext")
        r = c.put(f"/api/secrets/{llm_id}", json={"value": "AIzaNEWKEY99999999"})
        ok(r.json()["preview"] == "••••9999", "replace value")
        r = c.put(f"/api/secrets/{db_id}", json={"key": "sb_secret_ROTATED0000"})
        ok(r.status_code == 200 and r.json()["preview"] == "••••0000", "partial db update keeps the url")
        ok(run_service._load_secret(db_id, "db_credential", "x")["url"] == "https://x.supabase.co", "url preserved and normalised")

        # ------------------------------------------------------------ workflows
        wf = dict(
            name="Credit decisions", target_url="http://localhost:5174", master_prompt="decide", task_prompt="Open the ledger and decide.",
            match_key="id", model_name="gemini-x", llm_secret_id=llm_id, db_secret_id=db_id, use_cache=False, max_concurrency=2,
            verify_kind="expected_view",
            verify_config=dict(
                table="credit_requests", key_column="id", value_column="decision", expected_view="credit_requests_expected",
                expected_key_column="request_id", expected_value_column="expected_decision",
            ),
            data=[{"id": 1}, {"id": 2}, {"id": 3}],
        )
        r = c.post("/api/workflows", json=wf)
        ok(r.status_code == 201 and r.json()["record_count"] == 3 and r.json()["headless"] is False, "create workflow (visible browser by default)")
        wid = r.json()["id"]
        r2 = c.post("/api/workflows", json=wf)
        ok(r2.status_code == 201 and r2.json()["name"] == "Credit decisions (2)", "same name -> auto-numbered, not an error")
        c.delete(f"/api/workflows/{r2.json()['id']}")
        ok(c.post("/api/workflows", json={**wf, "name": "z", "target_url": "ftp://x"}).status_code == 400, "bad url -> 400")
        ok(c.post("/api/workflows", json={**wf, "name": "z", "llm_secret_id": db_id}).status_code == 400, "wrong-kind secret -> 400")
        ok(c.delete(f"/api/secrets/{llm_id}").status_code == 409, "cannot delete a used secret")
        r = c.put(f"/api/workflows/{wid}", json={**wf, "target_url": "http://localhost:5174/other", "headless": True})
        ok(r.json()["cache_epoch"] == 2 and r.json()["headless"] is True, "URL change bumps the cache epoch; headless saves")
        ok(c.get("/api/health").json()["force_headless"] is False, "health reports no server override")

        # ------------------------------------------------------------ a run with a fake engine and fake database client
        seen = {}

        def fake_batch(records, ctx, on_event=None, abort_check=None):
            seen["ctx"] = ctx
            on_event({"type": "batch_start", "total": len(records)})
            for i, rec in enumerate(records, 1):
                on_event({"type": "record_start", "record_id": rec.record_id, "index": i, "total": len(records), "name": rec.record_id, "mode": "live"})
                on_event({"type": "step", "record_id": rec.record_id, "step_index": 1, "instruction": f"key is {ctx.gemini_api_key}", "actions": ["click(index=3)"]})
                on_event({"type": "record_done", "record_id": rec.record_id, "success": True, "summary": "Approved", "mode": "live", "llm_steps": 1, "cache_hits": 0, "cache_misses": 0})
            on_event({"type": "batch_done", "total": len(records), "succeeded": len(records), "failed": 0})
            return [RunResult(record_id=r.record_id, success=True, timestamp=datetime.now(timezone.utc)) for r in records]

        run_service.run_batch_cached = fake_batch
        run_service.create_client = lambda url, key: FakeClient()

        r = c.post(f"/api/workflows/{wid}/runs", json={"limit": 3})
        ok(r.status_code == 201, "start run")
        rid = r.json()["run_id"]
        for _ in range(100):
            st = c.get(f"/api/runs/{rid}").json()
            if st["status"] not in ("running", "verifying"):
                break
            time.sleep(0.1)
        ok(st["status"] == "done", f"run finished: {st['status']} {st['error']}")
        ok(abs(st["accuracy"] - 1 / 3) < 1e-9, f"accuracy 1 of 3 correct = {st['accuracy']:.2f}")
        rep = {x["record_id"]: x["field_results"][0] for x in st["report"]["per_record_results"]}
        ok(rep["2"]["reason"] == "not_decided" and rep["3"]["reason"] == "value_mismatch" and rep["1"]["passed"], "per-record reasons")
        ev = c.get(f"/api/runs/{rid}/events?after=0").json()
        ok(len(ev["events"]) == 12 and [e["seq"] for e in ev["events"]] == list(range(1, 13)), "12 events stored, in order, none lost by the background writer")
        ok(all("AIzaNEWKEY" not in json.dumps(e) for e in ev["events"]), "secret redacted from events")
        ev2 = c.get(f"/api/runs/{rid}/events?after={ev['events'][4]['seq']}").json()
        ok(ev2["events"][0]["seq"] == 6, "resume after a sequence number works")
        ctx = seen["ctx"]
        ok(ctx.use_cache is False and ctx.headless is True and ctx.task_instructions == "Open the ledger and decide." and ctx.workflow_name == "wf1:e2", "context built from the workflow")

        # ------------------------------------------------------------ the headless setting reaches the engine
        row = {**c.get(f"/api/workflows/{wid}").json(), "headless": 1, "reconcile": 0, "data_json": "[]", "verify_config": "{}", "task_prompt": "x", "cache_epoch": 2}
        ctx, _ = run_service.build_context({**row, "headless": 0})
        ok(ctx.headless is False, "task says visible -> the engine gets headless=False")
        run_service.FORCE_HEADLESS = True
        ctx, _ = run_service.build_context({**row, "headless": 0})
        ok(ctx.headless is True, "server override forces headless even if the task says visible")
        run_service.FORCE_HEADLESS = False

        # ------------------------------------------------------------ edge cases
        ok(wait_idle(), "engine goes idle after runs")
        bad = c.post("/api/workflows", json={**wf, "name": "nodata", "data": []}).json()["id"]
        rr = c.post(f"/api/workflows/{bad}/runs", json={})
        ok(rr.status_code == 201, "no data -> the task runs once")
        wait_idle()
        ok(c.get(f"/api/runs/{rr.json()['run_id']}").json()["total"] == 1, "a no-data run has exactly 1 record")

        mk = lambda f: CachedStep("w", "default", 1, 0, "click" if f is None else "input", f, "", "/x", "div", {}, None, "fp", "")
        ok(_replayable_steps([mk(None), mk(None)]) == [], "click-only learned steps are never replayed")
        ok(len(_replayable_steps([mk("name"), mk(None)])) == 2, "steps tied to a data field are replayed")
        ok(_replayable_steps([]) == [], "no steps -> nothing to replay")

        ok(c.get("/api/nope").status_code == 404 and c.get("/api/nope").headers["content-type"].startswith("application/json"), "unknown api path -> JSON 404")
        ok(c.get("/logs").status_code in (200, 503), "frontend route answers")
        ok(c.delete(f"/api/workflows/{wid}").status_code == 200 and c.get(f"/api/runs/{rid}").status_code == 404, "delete workflow removes its runs")

    print(f"\nALL {passed} BACKEND CHECKS PASSED")
finally:
    app_db.flush_events()
    with psycopg.connect(BASE_URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    try:
        os.remove(os.environ["MASTER_KEY_PATH"])
    except OSError:
        pass
