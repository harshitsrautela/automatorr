"""Generates the Expense Desk test data (100 travel expense claims, employees, the travel policy) and the correct decision for
every claim, computed from the raw data by rules that do not look at the scenario labels.

    python3 scripts/generate_seed.py
Writes data/seed.json (served by the site through its JSON API), expected.json and records.json (never served).
"""

import json
import random
import string
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rnd = random.Random(20261010)
TODAY = date(2026, 10, 9)

GRADES = ["L1", "L2", "L3", "L4"]
TIERS = {
    "Tier 1": ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Kolkata", "Pune"],
    "Tier 2": ["Jaipur", "Lucknow", "Kochi", "Indore", "Chandigarh", "Ahmedabad", "Coimbatore"],
    "Tier 3": ["Nashik", "Mysuru", "Udaipur", "Shillong", "Guwahati", "Visakhapatnam"],
    "International": ["Dubai", "Singapore", "London", "Frankfurt", "New York"],
}
TIER_OF = {city: tier for tier, cities in TIERS.items() for city in cities}
# Hotel limit per night in rupees, by city tier and grade
LIMITS = {
    "Tier 1": {"L1": 4000, "L2": 5500, "L3": 7000, "L4": 9000},
    "Tier 2": {"L1": 3000, "L2": 4000, "L3": 5000, "L4": 6500},
    "Tier 3": {"L1": 2000, "L2": 2800, "L3": 3500, "L4": 4500},
    "International": {"L1": 9000, "L2": 11000, "L3": 13000, "L4": 16000},
}
RATES = {"INR": 1, "USD": 83, "EUR": 90, "GBP": 105, "AED": 23, "SGD": 62}
LOCAL_CCY = {"Dubai": "AED", "Singapore": "SGD", "London": "GBP", "Frankfurt": "EUR", "New York": "USD"}
WINDOW_DAYS = 45
NAMES = ["Aarav Mehta", "Diya Kapoor", "Kabir Singh", "Ananya Rao", "Vihaan Shah", "Ishita Nair", "Arjun Patel", "Meera Iyer", "Rohan Gupta",
         "Saanvi Joshi", "Aditya Verma", "Kiara Bose", "Reyansh Das", "Navya Menon", "Krish Malhotra", "Tara Sethi", "Dev Chauhan", "Riya Khanna",
         "Neel Bhatt", "Zoya Qureshi", "Om Kulkarni", "Pari Saxena", "Yash Thakur", "Myra Pillai"]

PLAN = [("late", 12), ("late_edge45", 3), ("missing_one", 10), ("missing_two", 5), ("approve_inr", 18), ("approve_foreign", 12),
        ("deduct_inr", 15), ("deduct_foreign", 10), ("hotel_at_limit", 5), ("late_and_missing", 3), ("no_hotel", 7)]
scenarios = [n for n, c in PLAN for _ in range(c)]
assert len(scenarios) == 100
rnd.shuffle(scenarios)

employees = [{"id": f"E{1000 + i}", "name": n, "grade": rnd.choice(GRADES)} for i, n in enumerate(NAMES)]
used_ids: set[int] = set()
used_keys: set[str] = set()


def claim_no() -> str:
    while True:
        n = rnd.randint(10000, 99999)
        if n not in used_ids:
            used_ids.add(n)
            return f"EXP-{n}"


def key() -> str:
    # Opaque route ids. Some have no digits at all ("kxqfab"), as random ids often do.
    while True:
        k = "".join(rnd.choice(string.ascii_lowercase + string.digits) for _ in range(6))
        if k not in used_keys:
            used_keys.add(k)
            return k


def split(total: int, parts: int) -> list[int]:
    cuts = sorted(rnd.sample(range(1, total), parts - 1)) if parts > 1 else []
    return [b - a for a, b in zip([0, *cuts], [*cuts, total])]


claims = []
for scenario in scenarios:
    emp = rnd.choice(employees)
    foreign = scenario in ("approve_foreign", "deduct_foreign") or (scenario not in ("approve_inr", "deduct_inr") and rnd.random() < 0.25)
    city = rnd.choice(TIERS["International"]) if foreign else rnd.choice(TIERS["Tier 1"] + TIERS["Tier 2"] + TIERS["Tier 3"])
    tier = TIER_OF[city]
    ccy = LOCAL_CCY.get(city, "INR")
    nights = rnd.randint(1, 5)
    trip_end = TODAY - timedelta(days=rnd.randint(50, 120))
    trip_start = trip_end - timedelta(days=nights)
    if scenario in ("late", "late_and_missing"):
        gap = rnd.randint(46, 70)
    elif scenario == "late_edge45":
        gap = 45
    else:
        gap = rnd.randint(1, 44)
    submitted = trip_end + timedelta(days=gap)
    limit_total = LIMITS[tier][emp["grade"]] * nights
    rate = RATES[ccy]

    lines = []
    if scenario != "no_hotel":
        if scenario.startswith("deduct"):
            hotel_inr = limit_total + rate * rnd.randint(5, 60) * (10 if rate == 1 else 1)
        elif scenario == "hotel_at_limit":
            hotel_inr = limit_total
        else:
            hotel_inr = max(rate * 10, limit_total - rate * rnd.randint(1, 40) * (10 if rate == 1 else 1))
        # the hotel bill is in the local currency; make it a whole number of that currency
        hotel_amount = hotel_inr // rate
        if scenario == "hotel_at_limit" and limit_total % rate:
            # the limit cannot be met exactly in this currency: pay the hotel in rupees instead (a company booking)
            lines.append({"category": "Hotel", "description": f"{city} hotel, {nights} night{'s' if nights > 1 else ''} (company booking)", "amount": limit_total, "currency": "INR"})
        else:
            lines.append({"category": "Hotel", "description": f"{city} hotel, {nights} night{'s' if nights > 1 else ''}", "amount": hotel_amount, "currency": ccy})
    meals = rnd.randint(4, 40) * (50 if ccy == "INR" else 1)
    lines.append({"category": "Meals", "description": f"Meals in {city}", "amount": meals, "currency": ccy})
    if rnd.random() < 0.7:
        lines.append({"category": "Taxi", "description": "Airport and local travel", "amount": rnd.randint(3, 30) * (40 if ccy == "INR" else 1), "currency": ccy})
    lines.append({"category": rnd.choice(["Flight", "Train"]) if not foreign else "Flight", "description": "Travel to and from " + city, "amount": rnd.randint(30, 400) * 50, "currency": "INR"})
    rnd.shuffle(lines)
    day = trip_start
    for line in lines:
        line["date"] = (trip_start + timedelta(days=rnd.randint(0, nights))).isoformat()
        line["receipt"] = "Attached"
    if scenario in ("missing_one", "late_and_missing"):
        rnd.choice(lines)["receipt"] = "Missing"
    elif scenario == "missing_two":
        for line in rnd.sample(lines, 2):
            line["receipt"] = "Missing"
    lines.sort(key=lambda l: (l["date"], l["category"]))
    total_inr = sum(l["amount"] * RATES[l["currency"]] for l in lines)
    advance = rnd.choice([0, 0, 2000, 5000, 10000]) if total_inr > 25000 else 0
    claims.append({
        "key": key(), "claim_no": claim_no(), "employee_id": emp["id"], "city": city,
        "trip_start": trip_start.isoformat(), "trip_end": trip_end.isoformat(), "submitted": submitted.isoformat(),
        "advance": advance, "purpose": rnd.choice(["Client visit", "Team offsite", "Vendor audit", "Conference", "Site inspection", "Training"]),
        "lines": lines, "scenario": scenario,
        "activity": [
            {"at": submitted.isoformat(), "text": f"Submitted by {emp['name']}"},
            {"at": (submitted + timedelta(days=1)).isoformat(), "text": "Manager approved, sent to finance"},
        ],
    })
rnd.shuffle(claims)
emp_by_id = {e["id"]: e for e in employees}


def expected_for(c: dict) -> dict:
    """The correct decision, from the raw data and the rules in the task prompt (in that order)."""
    emp = emp_by_id[c["employee_id"]]
    gap = (date.fromisoformat(c["submitted"]) - date.fromisoformat(c["trip_end"])).days
    if gap > WINDOW_DAYS:
        return {"outcome": "reject", "reason": "Late submission"}
    missing = sorted({l["category"] for l in c["lines"] if l["receipt"] == "Missing"})
    if missing:
        return {"outcome": "sendback", "missing": missing}
    nights = (date.fromisoformat(c["trip_end"]) - date.fromisoformat(c["trip_start"])).days
    limit_total = LIMITS[TIER_OF[c["city"]]][emp["grade"]] * nights
    total = sum(l["amount"] * RATES[l["currency"]] for l in c["lines"])
    hotel = sum(l["amount"] * RATES[l["currency"]] for l in c["lines"] if l["category"] == "Hotel")
    if hotel > limit_total:
        deduction = hotel - limit_total
        return {"outcome": "deduct", "deduction": deduction, "payable": total - deduction - c["advance"], "hotel_over_limit": True}
    return {"outcome": "approve", "payable": total - c["advance"]}


expected = {c["claim_no"]: expected_for(c) for c in claims}
for c in claims:
    e = expected[c["claim_no"]]
    assert e.get("payable", 1) > 0, (c, e)
    sc = c["scenario"]
    want = {"late": "reject", "late_and_missing": "reject", "missing_one": "sendback", "missing_two": "sendback", "deduct_inr": "deduct",
            "deduct_foreign": "deduct", "hotel_at_limit": "approve", "approve_inr": "approve", "approve_foreign": "approve", "no_hotel": "approve"}.get(sc)
    if want:
        assert e["outcome"] == want, (sc, e, c)

public = [{k: v for k, v in c.items() if k != "scenario"} for c in claims]
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / "seed.json").write_text(json.dumps({
    "today": TODAY.isoformat(), "claims": public, "employees": employees,
    "policy": {"limits": LIMITS, "tiers": TIERS, "rates": RATES, "window_days": WINDOW_DAYS},
}, indent=1))
(ROOT / "expected.json").write_text(json.dumps(expected, indent=1))
order = [c["claim_no"] for c in claims]
rnd.shuffle(order)
(ROOT / "records.json").write_text(json.dumps([{"claim_id": n} for n in order], indent=1))

print("claims", len(claims), "employees", len(employees))
print("expected:", dict(Counter(e["outcome"] for e in expected.values())))
print("keys without digits:", sum(1 for c in claims if not any(ch.isdigit() for ch in c["key"])))
print("foreign:", sum(1 for c in claims if c["city"] in LOCAL_CCY))
