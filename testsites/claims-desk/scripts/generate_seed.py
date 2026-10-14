"""Generates the Claims Desk test data (100 property insurance claims and the policies they are made on) and the correct decision
for every claim, computed from the raw data by rules that do not look at the scenario labels.

    python3 scripts/generate_seed.py
Writes data/seed.json (served by the site through its API, together with the site's test sign-in), expected.json and records.json
(never served). It prints the sign-in the site accepts.
"""

import json
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rnd = random.Random(20261011)
TODAY = date(2026, 10, 9)

# The site's one user. Test values for this local site only; the server reads them from data/seed.json.
LOGIN = {"username": "desk.auditor", "password": "audit-2026"}

PERILS = ["Fire", "Theft", "Flood", "Storm", "Accidental damage", "Water leak", "Earthquake", "Riot"]
# The range a policy's limit for each peril is drawn from, in rupees
LIMIT_RANGE = {
    "Fire": (500000, 2500000), "Theft": (50000, 300000), "Flood": (200000, 1000000), "Storm": (100000, 600000),
    "Accidental damage": (25000, 150000), "Water leak": (50000, 250000), "Earthquake": (300000, 1500000), "Riot": (100000, 500000),
}
DEDUCTIBLES = [1000, 2500, 5000, 7500, 10000, 15000, 25000]
PRODUCTS = ["Home Shield", "Home Shield Plus", "Home and Contents"]
REASONS = ["No cover on loss date", "Peril not covered", "Duplicate claim"]
DOCUMENTS = ["Police report", "Photos", "Repair invoice"]
NAMES = ["Aarav Mehta", "Diya Kapoor", "Kabir Singh", "Ananya Rao", "Vihaan Shah", "Ishita Nair", "Arjun Patel", "Meera Iyer", "Rohan Gupta",
         "Saanvi Joshi", "Aditya Verma", "Kiara Bose", "Reyansh Das", "Navya Menon", "Krish Malhotra", "Tara Sethi", "Dev Chauhan", "Riya Khanna",
         "Neel Bhatt", "Zoya Qureshi", "Om Kulkarni", "Pari Saxena", "Yash Thakur", "Myra Pillai", "Kunal Bajaj", "Isha Desai", "Rahul Sen",
         "Anika Reddy", "Farhan Ali", "Lavanya Krishnan"]
DESCRIPTIONS = {
    "Fire": ["Kitchen fire damaged the cabinets and the chimney", "Short circuit fire in the living room", "Fire spread from a neighbouring flat"],
    "Theft": ["Laptop and jewellery taken during a break-in", "Bicycle and tools taken from the locked garage", "Television and cash taken while the family was away"],
    "Flood": ["Ground floor flooded after heavy rain", "Basement storage under water for two days", "Flood water damaged the furniture and the floor"],
    "Storm": ["Roof tiles blown off in a storm", "Tree fell on the boundary wall in high winds", "Windows broken by a hailstorm"],
    "Accidental damage": ["Television knocked off its stand", "Glass dining table cracked while moving it", "Paint spilled on a new sofa"],
    "Water leak": ["Burst pipe in the bathroom wall", "Overhead tank leaked into the bedroom ceiling", "Washing machine hose split and soaked the floor"],
    "Earthquake": ["Cracks in the walls after the tremor", "Water tank and parapet fell during an earthquake", "Plaster and tiles came down in the tremor"],
    "Riot": ["Shop front and shutters damaged in a riot", "Car port and gate damaged during unrest", "Windows smashed during a street protest"],
}

# scenario -> count. The edge cases: a loss on the policy's first or last day (covered), a claim exactly at the limit (Approve,
# not partial), a lapsed policy whose dates do cover the loss (still declined), and two claims where an earlier rule must win.
PLAN = [("approve", 20), ("over_limit", 16), ("at_limit", 4), ("last_day", 4), ("first_day", 2), ("theft_with_report", 8),
        ("theft_no_report", 10), ("peril_not_covered", 14), ("lapsed", 8), ("before_start", 4), ("after_end", 6),
        ("lapsed_and_not_covered", 2), ("theft_not_covered_no_report", 2)]
scenarios = [n for n, c in PLAN for _ in range(c)]
assert len(scenarios) == 100
rnd.shuffle(scenarios)

used: dict[str, set] = {"pol": set(), "clm": set(), "key": set()}
policies: list[dict] = []
shareable: list[dict] = []  # active policies in force today that more claims may be made on


# Takes a prefix, a pool name and a number range; returns an id with a number not used before in that pool.
def unique(prefix: str, pool: str, lo: int, hi: int, width: int = 0) -> str:
    while True:
        n = rnd.randint(lo, hi)
        if n not in used[pool]:
            used[pool].add(n)
            return f"{prefix}{n:0{width}d}"


# Returns an opaque route id for a claim's address: 8 hex digits, never repeated.
def route_key() -> str:
    while True:
        k = "%08x" % rnd.getrandbits(32)
        if k not in used["key"]:
            used["key"].add(k)
            return k


# Takes a date; returns the last day of a one-year policy that starts on it.
def one_year(start: date) -> date:
    return date(start.year + 1, start.month, start.day) - timedelta(days=1)


# Takes a date and a number of months; returns the same day that many months later (the day is at most the 28th).
def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


# Takes a status, the start date and perils that must or must not be covered; makes a policy, records it and returns it.
def new_policy(status: str = "Active", start: date | None = None, cover: tuple = (), uncover: tuple = ()) -> dict:
    if start is None:
        start = TODAY - timedelta(days=rnd.randint(30, 330))
    if start.month == 2 and start.day == 29:
        start += timedelta(days=1)
    end = one_year(start)
    covered = (set(rnd.sample(PERILS, rnd.randint(5, 7))) | set(cover)) - set(uncover)
    coverage = []
    for peril in PERILS:
        if peril in covered:
            lo, hi = LIMIT_RANGE[peril]
            limit = rnd.randint(lo // 5000, hi // 5000) * 5000
            coverage.append({"peril": peril, "covered": True, "limit": limit, "deductible": rnd.choice([d for d in DEDUCTIBLES if d * 5 <= limit])})
        else:
            coverage.append({"peril": peril, "covered": False, "limit": None, "deductible": None})
    premium = rnd.randint(24, 160) * 400
    payments = []
    due = start
    while due <= min(TODAY, end):
        payments.append({"due": due.isoformat(), "amount": premium // 4, "paid_on": min(due + timedelta(days=rnd.randint(0, 12)), TODAY).isoformat()})
        due = add_months(due, 3)
    if status == "Lapsed":
        for p in payments[-rnd.randint(1, min(2, len(payments) - 1)):]:
            p["paid_on"] = None
    policy = {"policy_no": unique("POL-", "pol", 1000000, 9999999), "holder": rnd.choice(NAMES), "product": rnd.choice(PRODUCTS), "status": status,
              "start": start.isoformat(), "end": end.isoformat(), "premium": premium, "coverage": coverage, "payments": payments}
    policies.append(policy)
    return policy


# Takes a policy and a peril; returns the policy's coverage row for that peril.
def cover_of(policy: dict, peril: str) -> dict:
    return next(row for row in policy["coverage"] if row["peril"] == peril)


# Takes a peril; returns an active policy covering it that is in force today: an existing one now and then, else a new one.
def policy_covering(peril: str) -> dict:
    pool = [p for p in shareable if cover_of(p, peril)["covered"]]
    if pool and rnd.random() < 0.35:
        return rnd.choice(pool)
    policy = new_policy(cover=(peril,))
    shareable.append(policy)
    return policy


# Takes a policy; returns a day of loss inside its dates and within the last 150 days (at least 2 days ago).
def loss_inside(policy: dict) -> date:
    lo = max(date.fromisoformat(policy["start"]), TODAY - timedelta(days=150))
    hi = min(date.fromisoformat(policy["end"]), TODAY - timedelta(days=2))
    return lo + timedelta(days=rnd.randint(0, (hi - lo).days))


# Takes a coverage row and whether the claim is above the limit; returns an amount claimed (a multiple of 50) on the right side of it.
def amount_for(row: dict, over: bool) -> int:
    if over:
        return row["limit"] + rnd.randint(1, row["limit"] // 100) * 50
    return rnd.randint((row["deductible"] + 500) // 50, (row["limit"] - 500) // 50) * 50


# Takes a policy; returns a peril it covers, sometimes Theft when it covers that.
def covered_peril(policy: dict) -> str:
    return rnd.choice([row["peril"] for row in policy["coverage"] if row["covered"]])


claims = []
taken: set[tuple] = set()  # (policy, peril, day of loss): never two claims for the same loss, which would look like a duplicate
for scenario in scenarios:
    over = rnd.random() < 0.4
    police = None
    if scenario in ("approve", "over_limit", "at_limit"):
        peril = rnd.choice(PERILS)
        policy = policy_covering(peril)
        loss = loss_inside(policy)
        row = cover_of(policy, peril)
        amount = row["limit"] if scenario == "at_limit" else amount_for(row, scenario == "over_limit")
    elif scenario in ("theft_with_report", "theft_no_report"):
        peril = "Theft"
        policy = policy_covering(peril)
        loss = loss_inside(policy)
        amount = amount_for(cover_of(policy, peril), over)
        police = "Yes" if scenario == "theft_with_report" else "No"
    elif scenario in ("last_day", "after_end"):
        policy = new_policy(start=TODAY - timedelta(days=rnd.randint(380, 500)))
        peril = covered_peril(policy)
        end = date.fromisoformat(policy["end"])
        loss = end if scenario == "last_day" else end + timedelta(days=rnd.randint(1, min(60, (TODAY - end).days - 1)))
        amount = amount_for(cover_of(policy, peril), over)
    elif scenario in ("first_day", "before_start"):
        policy = new_policy(start=TODAY - timedelta(days=rnd.randint(20, 120)))
        peril = covered_peril(policy)
        start = date.fromisoformat(policy["start"])
        loss = start if scenario == "first_day" else start - timedelta(days=rnd.randint(1, 40))
        amount = amount_for(cover_of(policy, peril), over)
    elif scenario == "peril_not_covered":
        policy = rnd.choice(shareable) if shareable and rnd.random() < 0.35 else new_policy()
        peril = rnd.choice([row["peril"] for row in policy["coverage"] if not row["covered"]])
        loss = loss_inside(policy)
        amount = rnd.randint(100, 6000) * 50
    elif scenario == "lapsed":
        policy = new_policy(status="Lapsed", start=TODAY - timedelta(days=rnd.randint(120, 330)))
        peril = covered_peril(policy)
        loss = loss_inside(policy)
        amount = amount_for(cover_of(policy, peril), over)
    elif scenario == "lapsed_and_not_covered":
        policy = new_policy(status="Lapsed", start=TODAY - timedelta(days=rnd.randint(120, 330)))
        peril = rnd.choice([row["peril"] for row in policy["coverage"] if not row["covered"]])
        loss = loss_inside(policy)
        amount = rnd.randint(100, 6000) * 50
    else:  # theft_not_covered_no_report: the peril rule comes before the police report rule
        policy = new_policy(uncover=("Theft",))
        peril, police = "Theft", "No"
        loss = loss_inside(policy)
        amount = rnd.randint(100, 4000) * 50
    if peril == "Theft" and police is None:
        police = "Yes" if scenario not in ("lapsed", "after_end", "before_start", "peril_not_covered") else rnd.choice(["Yes", "No"])
    if (policy["policy_no"], peril, loss) in taken:
        raise SystemExit(f"two claims for the same loss ({policy['policy_no']}, {peril}, {loss}); change the seed")
    taken.add((policy["policy_no"], peril, loss))
    claim = {
        "key": route_key(), "claim_no": unique("CLM-26-", "clm", 1000, 9999, 5), "policy_no": policy["policy_no"], "claimant": policy["holder"],
        "peril": peril, "loss_date": loss.isoformat(), "reported_on": min(loss + timedelta(days=rnd.randint(0, 12)), TODAY).isoformat(),
        "amount": amount, "photos": rnd.randint(1, 12), "description": rnd.choice(DESCRIPTIONS[peril]), "scenario": scenario,
    }
    if peril == "Theft":
        claim["police_report"] = police
    claims.append(claim)
rnd.shuffle(claims)
policy_by_no = {p["policy_no"]: p for p in policies}


def expected_for(c: dict) -> dict:
    """The correct decision, from the raw data and the rules in the task (the first rule that applies)."""
    policy = policy_by_no[c["policy_no"]]
    loss = date.fromisoformat(c["loss_date"])
    if policy["status"] != "Active" or not date.fromisoformat(policy["start"]) <= loss <= date.fromisoformat(policy["end"]):
        return {"outcome": "Decline", "reason": "No cover on loss date"}
    row = cover_of(policy, c["peril"])
    if not row["covered"]:
        return {"outcome": "Decline", "reason": "Peril not covered"}
    if c["peril"] == "Theft" and c.get("police_report") != "Yes":
        return {"outcome": "Request information", "document": "Police report"}
    if c["amount"] > row["limit"]:
        return {"outcome": "Approve partially", "payout": row["limit"] - row["deductible"]}
    return {"outcome": "Approve", "payout": c["amount"] - row["deductible"]}


expected = {c["claim_no"]: expected_for(c) for c in claims}
WANT = {"approve": ("Approve",), "at_limit": ("Approve",), "over_limit": ("Approve partially",), "last_day": ("Approve", "Approve partially"),
        "first_day": ("Approve", "Approve partially"), "theft_with_report": ("Approve", "Approve partially"),
        "theft_no_report": ("Request information",), "peril_not_covered": ("Decline", "Peril not covered"),
        "theft_not_covered_no_report": ("Decline", "Peril not covered"), "lapsed": ("Decline", "No cover on loss date"),
        "lapsed_and_not_covered": ("Decline", "No cover on loss date"), "before_start": ("Decline", "No cover on loss date"),
        "after_end": ("Decline", "No cover on loss date")}
for c in claims:
    e, want = expected[c["claim_no"]], WANT[c["scenario"]]
    if c["scenario"] in ("last_day", "first_day", "theft_with_report"):
        assert e["outcome"] in want, (c, e)
    elif len(want) == 2:
        assert (e["outcome"], e.get("reason")) == want, (c, e)
    else:
        assert e["outcome"] == want[0], (c, e)
    assert e.get("payout", 1) > 0, (c, e)
    assert e.get("reason", REASONS[0]) in REASONS and e.get("document", DOCUMENTS[0]) in DOCUMENTS
    assert c.get("police_report") in (("Yes", "No") if c["peril"] == "Theft" else (None,))

public = [{k: v for k, v in c.items() if k != "scenario"} for c in claims]
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / "seed.json").write_text(json.dumps({
    "today": TODAY.isoformat(), "login": LOGIN, "reasons": REASONS, "documents": DOCUMENTS, "claims": public, "policies": policies,
}, indent=1))
(ROOT / "expected.json").write_text(json.dumps(expected, indent=1))
order = [c["claim_no"] for c in claims]
rnd.shuffle(order)
(ROOT / "records.json").write_text(json.dumps([{"claim_no": n} for n in order], indent=1))

print("claims", len(claims), "policies", len(policies), "shared by more than one claim:",
      sum(1 for n in Counter(c["policy_no"] for c in claims).values() if n > 1))
print("expected:", dict(Counter((e["outcome"], e.get("reason") or e.get("document") or "") for e in expected.values())))
print("edge cases: loss on the last day", sum(1 for c in claims if c["loss_date"] == policy_by_no[c["policy_no"]]["end"]),
      "| on the first day", sum(1 for c in claims if c["loss_date"] == policy_by_no[c["policy_no"]]["start"]),
      "| exactly at the limit", sum(1 for c in claims if expected[c["claim_no"]]["outcome"] == "Approve"
                                    and c["amount"] == cover_of(policy_by_no[c["policy_no"]], c["peril"])["limit"]))
print(f"sign in as {LOGIN['username']} / {LOGIN['password']} (test values for this local site)")
