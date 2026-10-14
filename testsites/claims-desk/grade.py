"""Grades what the agent did against expected.json (never served by the site).

    python3 grade.py            # all 100 records
    python3 grade.py 12         # only the first 12 records of records.json
"""

import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:5183"
FIELDS = ("payout", "reason", "document")


# Takes the expected decision and the saved one (or None); returns (right, why not).
def check(exp: dict, act: dict | None) -> tuple[bool, str]:
    if act is None:
        return False, "not decided"
    if act["outcome"] != exp["outcome"]:
        return False, f"outcome {act['outcome']!r} instead of {exp['outcome']!r}"
    for key in FIELDS:
        if exp.get(key) != act.get(key):
            return False, f"{key} {act.get(key)!r} instead of {exp.get(key)!r}"
    return True, ""


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    state = json.load(urllib.request.urlopen(f"{BASE}/admin/state"))
    expected = json.loads((ROOT / "expected.json").read_text())
    records = [r["claim_no"] for r in json.loads((ROOT / "records.json").read_text())]
    if n:
        records = records[:n]
    ok = 0
    by_kind = defaultdict(list)
    problems = []
    for claim in records:
        exp = expected[claim]
        good, why = check(exp, state.get(claim))
        by_kind[f"{exp['outcome']} / {exp.get('reason') or exp.get('document') or '-'}"].append(good)
        ok += good
        if not good:
            problems.append((claim, why))
    print(f"correct: {ok}/{len(records)} = {100 * ok / max(len(records), 1):.1f}%")
    print(f"decided: {sum(1 for r in records if state.get(r))}/{len(records)}")
    for kind, results in sorted(by_kind.items()):
        print(f"  {kind:<42} {sum(results)}/{len(results)}")
    for claim, why in problems[:25]:
        print(f"  MISS {claim}: {why}")
