"""Grades what the agent did against expected.json (never served by the site).

    python3 grade.py            # all 100 records
    python3 grade.py 10         # only the first 10 records of records.json (what a limited run does)
"""

import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://localhost:5180"
n = int(sys.argv[1]) if len(sys.argv) > 1 else None

state = json.load(urllib.request.urlopen(f"{BASE}/admin/state"))
expected = json.loads((ROOT / "expected.json").read_text())
records = [r["invoice_no"] for r in json.loads((ROOT / "records.json").read_text())]
if n:
    records = records[:n]


def has_number(text: str, number: int) -> bool:
    return bool(re.search(rf"(?<![\d.]){number}(?![\d])", (text or "").replace(",", "")))


def check(exp: dict, act: dict | None) -> tuple[bool, str]:
    if act is None:
        return False, "not decided"
    if act["status"] != exp["status"]:
        return False, f"status {act['status']!r} instead of {exp['status']!r}"
    if exp.get("reason") != act.get("reason"):
        return False, f"reason {act.get('reason')!r} instead of {exp.get('reason')!r}"
    if exp["status"] == "Approved" and act.get("payment_date") != exp["payment_date"]:
        return False, f"payment date {act.get('payment_date')!r} instead of {exp['payment_date']!r}"
    if exp["status"] == "On hold" and not all(has_number(act.get("note") or "", x) for x in exp["note_numbers"]):
        return False, f"note {act.get('note')!r} lacks the numbers {exp['note_numbers']}"
    return True, ""


ok = 0
by_kind: dict[str, list[bool]] = defaultdict(list)
problems = []
for inv in records:
    exp = expected[inv]
    good, why = check(exp, state.get(inv))
    kind = f"{exp['status']} / {exp.get('reason') or 'pay'}"
    by_kind[kind].append(good)
    ok += good
    if not good:
        problems.append((inv, kind, why))

print(f"correct: {ok}/{len(records)} = {100 * ok / max(len(records), 1):.1f}%")
decided = sum(1 for inv in records if state.get(inv))
print(f"decided: {decided}/{len(records)}")
for kind, results in sorted(by_kind.items()):
    print(f"  {kind:<28} {sum(results)}/{len(results)}")
for inv, kind, why in problems[:25]:
    print(f"  MISS {inv} ({kind}): {why}")
