"""Runs the same records through the v1 and v2 services over HTTP and prints a comparison table."""

import argparse
import json
import time
import urllib.request
from urllib.error import URLError


# POSTs a JSON payload to a URL with the given timeout.
# Takes the url, payload and timeout and returns the decoded JSON response.
def _post_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# GETs a URL with the given timeout.
# Takes the url and timeout and returns the decoded JSON response.
def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Runs one batch on a service via POST /run and times it.
# Takes base_url, records_path and timeout; returns the report dict and elapsed seconds.
def _run_batch(base_url: str, records_path: str, timeout: float) -> tuple[dict, float]:
    started = time.monotonic()
    report = _post_json(f"{base_url}/run", {"records_path": records_path}, timeout)
    elapsed = time.monotonic() - started
    return report, elapsed


# Parses arguments, runs the records through v1 and v2, and prints the comparison table.
# Also prints the v2 cache hit rate per workflow; exits with a message if a service is unreachable.
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", default="sample_records.json", help="records_path passed to each service's own POST /run (resolved server-side, relative to wherever that uvicorn process was started)")
    parser.add_argument("--v1-url", default="http://localhost:8000")
    parser.add_argument("--v2-url", default="http://localhost:8001")
    parser.add_argument("--timeout", type=float, default=3600.0, help="seconds to wait for each batch — browser automation is slow, default is generous on purpose")
    args = parser.parse_args()

    print(f"Running {args.records!r} through v1 ({args.v1_url}) ...")
    try:
        v1_report, v1_seconds = _run_batch(args.v1_url, args.records, args.timeout)
    except URLError as exc:
        raise SystemExit(f"Couldn't reach v1 at {args.v1_url} — is it running? ({exc})")

    print(f"Running {args.records!r} through v2 ({args.v2_url}) ...")
    try:
        v2_report, v2_seconds = _run_batch(args.v2_url, args.records, args.timeout)
    except URLError as exc:
        raise SystemExit(f"Couldn't reach v2 at {args.v2_url} — is it running? ({exc})")

    try:
        cache_stats = _get_json(f"{args.v2_url}/cache/stats", args.timeout)
    except URLError:
        cache_stats = {"workflows": []}

    rows = [
        ("wall_clock_seconds", round(v1_seconds, 1), round(v2_seconds, 1)),
        ("total_records", v1_report["total_records"], v2_report["total_records"]),
        ("total_fields", v1_report["total_fields"], v2_report["total_fields"]),
        ("field_accuracy", round(v1_report["field_accuracy"], 4), round(v2_report["field_accuracy"], 4)),
        ("unmatched_records", v1_report["unmatched_records"], v2_report["unmatched_records"]),
    ]

    print("\n%-20s %12s %12s" % ("metric", "v1", "v2"))
    print("-" * 46)
    for name, v1_val, v2_val in rows:
        print("%-20s %12s %12s" % (name, v1_val, v2_val))

    print("\nv2 cache hit rate per workflow (this is the permanent, ongoing metric — keep watching it after this one-off comparison too):")
    workflows = cache_stats.get("workflows", [])
    if not workflows:
        print("  (no run_metrics rows yet)")
    for wf in workflows:
        hit_rate = wf["hit_rate"]
        hit_rate_str = f"{hit_rate:.1%}" if hit_rate is not None else "n/a"
        print(
            f"  {wf['workflow_name']} / {wf['path_variant']}: {hit_rate_str} "
            f"({wf['cache_hits']} hits, {wf['cache_misses']} misses, {wf['runs']} runs)"
        )


if __name__ == "__main__":
    main()
