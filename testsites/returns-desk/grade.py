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
BASE = "http://localhost:5181"
n = int(sys.argv[1]) if len(sys.argv) > 1 else None
state = json.load(urllib.request.urlopen(f"{BASE}/admin/state"))
expected = json.loads((ROOT / "expected.json").read_text())
records = [r["rma"] for r in json.loads((ROOT / "records.json").read_text())]
if n:
    records = records[:n]


def check(exp, act):
    if act is None:
        return False, "not decided"
    if act["disposition"] != exp["disposition"]:
        return False, f"disposition {act['disposition']!r} instead of {exp['disposition']!r}"
    for key in ("reason", "refund_amount", "replacement_sku", "pickup_date"):
        if exp.get(key) != act.get(key):
            return False, f"{key} {act.get(key)!r} instead of {exp.get(key)!r}"
    return True, ""


ok = 0
by_kind = defaultdict(list)
problems = []
for rma in records:
    exp = expected[rma]
    good, why = check(exp, state.get(rma))
    by_kind[f"{exp['disposition']} / {exp.get('reason') or '-'}"].append(good)
    ok += good
    if not good:
        problems.append((rma, why))
print(f"correct: {ok}/{len(records)} = {100 * ok / max(len(records), 1):.1f}%")
print(f"decided: {sum(1 for r in records if state.get(r))}/{len(records)}")
for kind, results in sorted(by_kind.items()):
    print(f"  {kind:<26} {sum(results)}/{len(results)}")
for rma, why in problems[:25]:
    print(f"  MISS {rma}: {why}")
