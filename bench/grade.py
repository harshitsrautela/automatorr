"""Grades the 5 comparison records on the AP Desk (same checks as testsites/ap-desk/grade.py).

    python3 grade.py            # prints the score and each miss
    python3 grade.py --json     # one JSON line (for the results table)
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = Path(__file__).resolve().parent.parent / "testsites" / "ap-desk"
state = json.load(urllib.request.urlopen("http://localhost:5180/admin/state"))
expected = json.loads((SITE / "expected.json").read_text())
records = [r["invoice_no"] for r in json.loads((HERE / "records.json").read_text())]


def has_number(text, number):
    return bool(re.search(rf"(?<![\d.]){number}(?![\d])", (text or "").replace(",", "")))


def check(exp, act):
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


rows = []
for inv in records:
    good, why = check(expected[inv], state.get(inv))
    rows.append({"invoice_no": inv, "ok": good, "why": why, "expected": expected[inv], "got": state.get(inv)})
score = sum(r["ok"] for r in rows)
extra = sorted(k for k, v in state.items() if v and k not in records)
if "--json" in sys.argv:
    print(json.dumps({"correct": score, "of": len(rows), "misses": [r for r in rows if not r["ok"]], "other_invoices_decided": extra}))
else:
    print(f"correct: {score}/{len(rows)}")
    for r in rows:
        print(f"  {'ok  ' if r['ok'] else 'MISS'} {r['invoice_no']} {r['expected'].get('status')} / {r['expected'].get('reason') or 'pay'} {r['why']}")
    if extra:
        print(f"  decided invoices outside the 5: {extra}")
