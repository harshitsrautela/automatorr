"""Counts the model calls the proxy saw, per tool (optionally only after a time).

    python3 calls.py                    # every tool, all time
    python3 calls.py stagehand 2026-10-13T08:00   # one tool, from that UTC time on
"""

import collections
import json
import sys
from pathlib import Path

tool = sys.argv[1] if len(sys.argv) > 1 else None
since = sys.argv[2] if len(sys.argv) > 2 else ""
rows = [json.loads(l) for l in (Path(__file__).parent / "logs" / "calls.jsonl").read_text().splitlines() if l.strip()]
rows = [r for r in rows if r["at"] >= since and (tool is None or r["tool"] == tool)]
by = collections.defaultdict(collections.Counter)
tokens = collections.Counter()
for r in rows:
    by[(r["tool"], r["model"])][r["status"]] += 1
    u = r.get("usage") or {}
    tokens[(r["tool"], "in")] += u.get("in") or 0
    tokens[(r["tool"], "out")] += u.get("out") or 0
for (t, m), c in sorted(by.items()):
    ok = sum(n for s, n in c.items() if 200 <= s < 300)
    print(f"{t:<12} {m:<28} ok={ok:<5} all={sum(c.values()):<5} by status={dict(c)}  tokens in={tokens[(t, 'in')]} out={tokens[(t, 'out')]}")
