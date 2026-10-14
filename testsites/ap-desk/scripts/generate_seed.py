"""Generates the AP Desk test data: vendors, purchase orders, goods receipts, 100 invoices, and the correct decision
for every invoice. The correct answers are computed from the raw data by independent rules (not from the scenario
labels), so a mistake in the generator cannot silently match a mistake in the site.

    python3 scripts/generate_seed.py
Writes data/seed.json (served by the site), expected.json and records.json (never served).
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rnd = random.Random(20261008)

VENDOR_NAMES = [
    "Kalyani Leather Works", "Sunrise Sole Makers", "Bharat Rubber Co", "Mehta Thread & Trim", "Orion Footwear Parts",
    "Patel Eyelets Pvt Ltd", "Gupta Adhesives", "Royal Hide Traders", "Deccan Foam Industries", "Lotus Packaging",
    "Apex Buckles & Hardware", "Nair Insole Supplies", "Shree Canvas Mills", "Verma Dyes & Chemicals", "Ganga Laces",
    "Himalaya Leather Tannery", "Modern Sole Press", "Anand Cartons", "Tiwari Stitching Needles", "Zenith Velcro Works",
    "Saraswati Cotton Lining", "Bombay Heel Makers", "Kapoor Polish & Wax", "Eastern Zip Fasteners",
]
ITEMS = [
    "Rubber sole sheets", "Leather hides grade A", "Foam insole rolls", "Nylon thread spools", "Metal eyelets box",
    "Contact adhesive drums", "Canvas fabric rolls", "Shoe laces bundle", "Heel blocks", "Packaging cartons",
    "Velcro strips", "Polish tins", "Zip fasteners", "Cotton lining rolls", "Brass buckles",
]
BLOCKED_IDX = {3, 8, 13, 17, 21}


def d(s: str) -> date:
    return date.fromisoformat(s)


vendors = [
    {"id": f"V-{101 + i}", "name": n, "status": "Blocked" if i in BLOCKED_IDX else "Active", "city": rnd.choice(["Agra", "Kanpur", "Chennai", "Ludhiana", "Kolkata", "Pune", "Jaipur", "Surat"])}
    for i, n in enumerate(VENDOR_NAMES)
]
active = [v for v in vendors if v["status"] == "Active"]
blocked = [v for v in vendors if v["status"] == "Blocked"]

used_po: set[int] = set()
used_grn: set[int] = set()
used_inv: set[int] = set()


def new_po_number() -> str:
    while True:
        n = rnd.randint(4000, 4999)
        if n not in used_po:
            used_po.add(n)
            return f"PO-{n}"


def new_grn() -> str:
    while True:
        n = rnd.randint(70000, 79999)
        if n not in used_grn:
            used_grn.add(n)
            return f"GRN-{n}"


def new_invoice_no() -> str:
    while True:
        n = rnd.randint(1000, 9999)
        if n not in used_inv:
            used_inv.add(n)
            return f"INV-26-{n}"


def split_qty(total: int) -> list[int]:
    """Splits a received quantity into 1 to 3 partial deliveries (none when nothing arrived)."""
    if total <= 0:
        return []
    parts = min(rnd.choice([1, 1, 2, 3]), total)
    cuts = sorted(rnd.sample(range(1, total), parts - 1)) if parts > 1 else []
    edges = [0, *cuts, total]
    return [edges[i + 1] - edges[i] for i in range(parts)]


# scenario name -> how many invoices
PLAN = [("approve", 26), ("approve_edge_equal", 4), ("approve_edge_2pct", 4), ("short", 14), ("short_and_price", 4),
        ("price", 14), ("price_just_over", 4), ("blocked", 12), ("closed", 9), ("missing", 9)]
scenarios = [name for name, n in PLAN for _ in range(n)]
rnd.shuffle(scenarios)
assert len(scenarios) == 100

pos: list[dict] = []
receipts: list[dict] = []
invoices: list[dict] = []


def make_receipts(po_number: str, total: int) -> None:
    for q in split_qty(total):
        receipts.append({"grn": new_grn(), "po_number": po_number, "received_on": (d("2026-08-05") + timedelta(days=rnd.randint(0, 55))).isoformat(), "qty": q})


for scenario in scenarios:
    po_price = rnd.choice(range(100, 1001, 50))  # multiples of 50, so 2% is a whole number of rupees
    ordered = rnd.choice(range(200, 2001, 50))
    inv_qty = max(50, round(ordered * rnd.uniform(0.4, 1.0) / 10) * 10)
    vendor = rnd.choice(blocked) if scenario == "blocked" else rnd.choice(active)
    po_number = new_po_number()
    two_pct = po_price * 2 // 100  # whole rupees, exact
    inv_price = po_price + rnd.choice([-1, 1]) * rnd.randint(0, two_pct)  # within tolerance
    received = inv_qty + rnd.choice([0, 0, 10, 20, 50, 120])  # enough
    po_status = "Open"

    if scenario == "approve_edge_equal":
        received = inv_qty
    elif scenario == "approve_edge_2pct":
        inv_price = po_price + rnd.choice([-1, 1]) * two_pct  # exactly 2%, must still be approved
    elif scenario in ("short", "short_and_price"):
        received = max(0, inv_qty - rnd.choice([1, 2, 5, 10, 20, 40, inv_qty]))
        if scenario == "short_and_price":
            inv_price = po_price + rnd.choice([-1, 1]) * (two_pct + rnd.randint(2, 30))
    elif scenario == "price":
        inv_price = po_price + rnd.choice([-1, 1]) * (two_pct + rnd.randint(2, 40))
    elif scenario == "price_just_over":
        inv_price = po_price + rnd.choice([-1, 1]) * (two_pct + 1)
    elif scenario == "closed":
        po_status = "Closed"
    inv_price = max(1, inv_price)

    invoice_no_po = po_number
    if scenario == "missing":
        # the invoice quotes a PO number that does not exist: a digit swap of a real one
        digits = list(po_number[3:])
        i = rnd.randint(0, 2)
        digits[i], digits[i + 1] = digits[i + 1], digits[i]
        swapped = "PO-" + "".join(digits)
        if swapped == po_number or int(swapped[3:]) in used_po:
            swapped = f"PO-{rnd.choice([n for n in range(4000, 5000) if n not in used_po])}"
        invoice_no_po = swapped
    pos.append({"id": f"po{len(pos) + 1:03d}", "number": po_number, "vendor_id": vendor["id"], "item": rnd.choice(ITEMS), "ordered_qty": ordered, "unit_price": po_price, "status": po_status})
    make_receipts(po_number, received)

    inv_date = d("2026-08-10") + timedelta(days=rnd.randint(0, 45))
    invoices.append({
        "id": "%08x" % rnd.getrandbits(32), "invoice_no": new_invoice_no(), "vendor_id": vendor["id"], "po_number": invoice_no_po,
        "qty": inv_qty, "unit_price": inv_price, "invoice_date": inv_date.isoformat(),
        "due_date": (inv_date + timedelta(days=rnd.choice([30, 45, 60]))).isoformat(), "scenario": scenario,
    })

# A "missing" invoice quotes a swapped PO number; the real PO made for it above is dropped, together with its receipts
# (every PO that no invoice quotes goes), and then distractors are added.
quoted = {i["po_number"] for i in invoices}
orphan_numbers = {p["number"] for p in pos if p["number"] not in quoted}
pos = [p for p in pos if p["number"] in quoted]
receipts = [r for r in receipts if r["po_number"] not in orphan_numbers]

# Distractor POs and receipts that no invoice uses (the lists are not just the invoices' own rows)
for _ in range(22):
    v = rnd.choice(vendors)
    n = new_po_number()
    pos.append({"id": f"po{len(pos) + 1:03d}", "number": n, "vendor_id": v["id"], "item": rnd.choice(ITEMS), "ordered_qty": rnd.choice(range(200, 2001, 50)), "unit_price": rnd.choice(range(100, 1001, 50)), "status": rnd.choice(["Open", "Open", "Closed"])})
    make_receipts(n, rnd.choice([0, 100, 250, 400]))
rnd.shuffle(pos)
rnd.shuffle(receipts)
for i, p in enumerate(pos, start=1):
    p["id"] = f"po{i:03d}"

po_by_number = {p["number"]: p for p in pos}
vendor_by_id = {v["id"]: v for v in vendors}


def expected_for(inv: dict) -> dict:
    """The correct decision, from the raw data and the rules in the task prompt."""
    vendor = vendor_by_id[inv["vendor_id"]]
    po = po_by_number.get(inv["po_number"])
    if vendor["status"] == "Blocked":
        return {"status": "Rejected", "reason": "Vendor blocked"}
    if po is None or po["status"] != "Open":
        return {"status": "Rejected", "reason": "No open PO"}
    got = sum(r["qty"] for r in receipts if r["po_number"] == po["number"])
    if inv["qty"] > got:
        return {"status": "On hold", "reason": "Short receipt", "note_numbers": [inv["qty"], got]}
    if abs(inv["unit_price"] - po["unit_price"]) * 100 > po["unit_price"] * 2:
        return {"status": "On hold", "reason": "Price variance", "note_numbers": [inv["unit_price"], po["unit_price"]]}
    return {"status": "Approved", "payment_date": inv["due_date"]}


expected = {inv["invoice_no"]: expected_for(inv) for inv in invoices}
public_invoices = [{k: v for k, v in inv.items() if k != "scenario"} for inv in invoices]
(ROOT / "data" / "seed.json").write_text(json.dumps({"vendors": vendors, "purchase_orders": pos, "receipts": receipts, "invoices": public_invoices}, indent=1))
(ROOT / "expected.json").write_text(json.dumps(expected, indent=1))
order = [inv["invoice_no"] for inv in invoices]
rnd.shuffle(order)
(ROOT / "records.json").write_text(json.dumps([{"invoice_no": n} for n in order], indent=1))

from collections import Counter

print("vendors", len(vendors), "POs", len(pos), "receipts", len(receipts), "invoices", len(invoices))
print("expected:", dict(Counter((e["status"], e.get("reason", "")) for e in expected.values())))
print("scenario vs expected mismatches:", [(i["scenario"], expected[i["invoice_no"]]) for i in invoices if i["scenario"] == "approve" and expected[i["invoice_no"]]["status"] != "Approved"][:3])
