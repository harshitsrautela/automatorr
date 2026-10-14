"""Generates the Returns Desk test data (100 returns, their orders, products and the policy fee table) and the correct
decision for every return, computed from the raw data by rules that do not look at the scenario labels.

    python3 scripts/generate_seed.py
Writes data/seed.json (served by the site), expected.json and records.json (never served).
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rnd = random.Random(20261009)
TODAY = date(2026, 10, 8)  # the site's "today": days since delivery are computed against it

CATEGORIES = [
    "Footwear - Running", "Footwear - Formal", "Footwear - Casual", "Footwear - Sandals", "Footwear - Boots", "Footwear - Kids",
    "Socks", "Insoles", "Laces", "Shoe care", "Bags - Backpack", "Bags - Duffel", "Bags - Wallet", "Belts", "Caps", "Gloves", "Scarves",
    "Sunglasses", "Watches - Analog", "Watches - Digital", "Fitness bands", "Water bottles", "Yoga mats", "Dumbbells", "Skipping ropes",
    "Resistance bands", "Gym bags", "Towels", "Sweatbands", "Shin guards", "Football", "Cricket bat", "Cricket ball", "Badminton racket",
    "Shuttlecocks", "Tennis racket", "Tennis balls", "Table tennis bat", "Swim goggles", "Swim caps", "Cycling helmet", "Cycling gloves",
    "Knee supports", "Ankle supports", "Back supports", "Jerseys", "Track pants", "Shorts", "Jackets", "Rain wear", "Thermals",
    "Tents", "Sleeping bags", "Trekking poles", "Headlamps", "Compasses", "Hydration packs", "Camp stoves", "Binoculars", "Knives",
]
OPEN_FEE = {c: rnd.choice([5, 10, 15, 20, 25]) for c in CATEGORIES}
DAMAGED_FEE = {c: rnd.choice([25, 30, 40, 50]) for c in CATEGORIES}
CUSTOMERS = ["Aarav Mehta", "Diya Kapoor", "Kabir Singh", "Ananya Rao", "Vihaan Shah", "Ishita Nair", "Arjun Patel", "Meera Iyer", "Rohan Gupta",
             "Saanvi Joshi", "Aditya Verma", "Kiara Bose", "Reyansh Das", "Navya Menon", "Krish Malhotra", "Tara Sethi", "Dev Chauhan", "Riya Khanna"]

# scenario -> count. "wrong_item_final" checks that a wrong item is replaced even when the product is final sale.
PLAN = [("changed_unopened", 10), ("changed_opened", 12), ("changed_damaged", 8), ("changed_day30", 3), ("changed_late", 8), ("final_sale", 10),
        ("defective_ok", 11), ("defective_edge", 3), ("defective_expired", 10), ("wrong_item", 21), ("wrong_item_final", 4)]
scenarios = [n for n, c in PLAN for _ in range(c)]
assert len(scenarios) == 100
rnd.shuffle(scenarios)

used = {"rma": set(), "ord": set(), "sku": set()}


def unique(prefix: str, pool: str, lo: int, hi: int) -> str:
    while True:
        n = rnd.randint(lo, hi)
        if n not in used[pool]:
            used[pool].add(n)
            return f"{prefix}{n}"


products, orders, returns = [], [], []
for scenario in scenarios:
    category = rnd.choice(CATEGORIES)
    sku = unique("SKU-", "sku", 1000, 9999)
    final_sale = scenario in ("final_sale", "wrong_item_final")
    warranty = rnd.choice([90, 180, 365, 730])
    if scenario.startswith("changed"):
        reason = "Changed mind"
    elif scenario == "final_sale":
        reason = rnd.choice(["Changed mind", "Defective"])
    elif scenario.startswith("defective"):
        reason = "Defective"
    else:  # wrong_item, wrong_item_final
        reason = "Wrong item"
    if scenario == "changed_unopened":
        condition = "Unopened"
    elif scenario == "changed_damaged":
        condition = "Damaged"
    elif reason == "Changed mind":
        condition = "Opened"
    else:
        condition = rnd.choice(["Opened", "Opened", "Damaged"])
    price = 20 * rnd.randint(10, 150)
    qty = rnd.randint(1, 6)
    if scenario == "changed_day30":
        days = 30
    elif scenario == "changed_late":
        days = rnd.randint(31, 70)
    elif scenario == "defective_edge":
        days = warranty  # exactly at the end of the warranty: still covered
    elif scenario == "defective_expired":
        days = warranty + rnd.randint(1, 120)
    elif reason == "Defective":
        days = rnd.randint(5, warranty - 1)
    else:
        days = rnd.randint(2, 29)
    products.append({"sku": sku, "name": f"{category} item {sku[-4:]}", "category": category, "final_sale": "Yes" if final_sale else "No", "warranty_days": warranty})
    order_no = unique("ORD-", "ord", 10000, 99999)
    shipped = sku
    ordered = sku
    if reason == "Wrong item":
        ordered = unique("SKU-", "sku", 1000, 9999)  # what the customer actually ordered
        products.append({"sku": ordered, "name": f"{rnd.choice(CATEGORIES)} item {ordered[-4:]}", "category": rnd.choice(CATEGORIES), "final_sale": "No", "warranty_days": 365})
    delivered = TODAY - timedelta(days=days)
    orders.append({"order_no": order_no, "ordered_sku": ordered, "shipped_sku": shipped, "unit_price": price, "qty": qty, "delivered_on": delivered.isoformat(), "payment": rnd.choice(["Card", "UPI", "Net banking", "Cash on delivery"])})
    returns.append({
        "id": "%08x" % rnd.getrandbits(32), "rma": unique("RMA-", "rma", 70000, 79999), "customer": rnd.choice(CUSTOMERS), "order_no": order_no, "sku": shipped,
        "qty": qty, "reason": reason, "condition": condition, "delivered_on": delivered.isoformat(), "pickup_slot": (date(2026, 10, 12) + timedelta(days=rnd.randint(0, 70))).isoformat(),
        "scenario": scenario,
    })
rnd.shuffle(returns)

product_by_sku = {p["sku"]: p for p in products}
order_by_no = {o["order_no"]: o for o in orders}


def days_since(r: dict) -> int:
    return (TODAY - date.fromisoformat(r["delivered_on"])).days


def expected_for(r: dict) -> dict:
    """The correct decision, from the raw data and the rules in the task prompt (in that order)."""
    order, product = order_by_no[r["order_no"]], product_by_sku[r["sku"]]
    d = days_since(r)
    if r["reason"] == "Wrong item":
        return {"disposition": "Replace", "replacement_sku": order["ordered_sku"], "pickup_date": r["pickup_slot"]}
    if product["final_sale"] == "Yes":
        return {"disposition": "Reject", "reason": "Final sale"}
    if r["reason"] == "Defective":
        if d > product["warranty_days"]:
            return {"disposition": "Reject", "reason": "Out of warranty"}
        return {"disposition": "Refund", "refund_amount": order["qty"] * order["unit_price"], "pickup_date": r["pickup_slot"]}
    if d > 30:
        return {"disposition": "Reject", "reason": "Outside window"}
    fee = 0 if r["condition"] == "Unopened" else OPEN_FEE[product["category"]] if r["condition"] == "Opened" else DAMAGED_FEE[product["category"]]
    return {"disposition": "Refund", "refund_amount": order["qty"] * order["unit_price"] * (100 - fee) // 100, "pickup_date": r["pickup_slot"]}


expected = {r["rma"]: expected_for(r) for r in returns}
for r in returns:  # the data was built so that every refund is a whole number of rupees
    o = order_by_no[r["order_no"]]
    e = expected[r["rma"]]
    if e["disposition"] == "Refund":
        fee = 100 - (e["refund_amount"] * 100) // (o["qty"] * o["unit_price"])
        assert e["refund_amount"] * 100 == o["qty"] * o["unit_price"] * (100 - fee)

public_returns = [{k: v for k, v in r.items() if k != "scenario"} for r in returns]
(ROOT / "data" / "seed.json").write_text(json.dumps({
    "today": TODAY.isoformat(), "returns": public_returns, "orders": orders, "products": products,
    "policy_fees": [{"category": c, "opened": OPEN_FEE[c], "damaged": DAMAGED_FEE[c]} for c in CATEGORIES],
}, indent=1))
(ROOT / "expected.json").write_text(json.dumps(expected, indent=1))
order = [r["rma"] for r in returns]
rnd.shuffle(order)
(ROOT / "records.json").write_text(json.dumps([{"rma": n} for n in order], indent=1))

from collections import Counter

print("returns", len(returns), "orders", len(orders), "products", len(products))
print("expected:", dict(Counter((e["disposition"], e.get("reason", "")) for e in expected.values())))
