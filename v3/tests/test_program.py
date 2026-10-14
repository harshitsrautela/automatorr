"""Checks for decision programs (recipe/facts.py and recipe/program.py):

    python tests/test_program.py

Needs nothing: no browser, no model, no website. It checks reading facts off realistic page texts (a definition list,
"Label: value" lines, a key-value table, tables printed with tabs and in the "[table rows with their headings]" block), the
expression language (what is allowed and what is refused), two hand-written programs (a library's late fees, and a
commission rate looked up by tier and grade with the column picked by a fact), catching a wrong rule against verified
examples, learning a program with a fake model (a good answer, a bad one corrected on the retry, bad twice), and the trust
bookkeeping."""

import asyncio
import copy
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recipe.program as prog
from recipe.facts import cell_matches, extract_fact, label_value, learn_label, parse_date, parse_number, table_rows, value_shape
from recipe.llm import LlmHalt
from recipe.program import (ProgramError, UnknownFact, evaluate_expr, learn_program, note_program_check, note_program_used, page_type,
                            parse_program, program_prompt, program_trusted_for, render_template, run_program, spot_check_due, trim_page,
                            validate_program)
from run_context import RunContext

checks = 0


def _raises(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# Runs fn and returns the exception it raised (None when it raised nothing).
def raised(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


MENU = "City Library · Loans · Members · Fees"


# The text plan_exec._quick_read gives for a loan's page: a definition list (label on one line, value on the next).
def loan_page(loan, member, item, due, returned, cost):
    text = f"{MENU}\nLoan {loan}\nMember\n{member}\nTitle\nThe Quiet Sea\nItem type\n{item}\nDue date\n{due}\nReturned on\n{returned}\nReplacement cost\n{cost}\nRenewals\t0"
    return f"http://lib.test/loans/{loan}", text


# A member's page: "Label: value" lines.
def member_page(member, membership):
    return f"http://lib.test/members/{member}", f"{MENU}\nMember {member}\nName: Asha Rao\nMembership: {membership}\nJoined: 05 Sept 2024\nLoans this year: 14"


FEES = ("http://lib.test/fees", f"{MENU}\nFee schedule\nItem type\tDaily rate\tMax days\nBook\t₹10\t30\nDVD\t₹25\t30\nMagazine\t₹5\t30\n\n"
        "[table rows with their headings]\nItem type: Book | Daily rate: ₹10 | Max days: 30\nItem type: DVD | Daily rate: ₹25 | Max days: 30\n"
        "Item type: Magazine | Daily rate: ₹5 | Max days: 30")


# One library record: its fields and its pages, oldest first (the member's page left out when with_member is False).
def library_record(loan, member, membership, item, due, returned, cost, with_member=True):
    pages = [loan_page(loan, member, item, due, returned, cost)]
    if with_member:
        pages.append(member_page(member, membership))
    pages.append(FEES)
    return {"loan_id": loan}, pages


LIBRARY_RULES = ("Late fees. 1. A book returned on or before its due date: outcome No fee. 2. Staff members pay nothing: outcome "
                 "Waived. 3. Otherwise charge the daily rate for the item type (fee schedule) for each day late, never more than the "
                 "replacement cost: outcome Fee charged, the amount, a note with the days late, and email the member.")
LIBRARY_FORM = [
    {"key": "outcome", "tag": "select", "type": "", "label": "Outcome", "visible": True, "options": ["No fee", "Waived", "Fee charged"]},
    {"key": "amount", "tag": "input", "type": "number", "label": "Amount (₹)", "visible": True, "options": []},
    {"key": "note", "tag": "textarea", "type": "", "label": "Note", "visible": True, "options": []},
    {"key": "notify", "tag": "input", "type": "checkbox", "label": "Email the member", "visible": True, "options": []},
]
LIBRARY_KINDS = {"outcome": "select", "amount": "other", "note": "note", "notify": "check"}
LIBRARY_TYPES = ["/loans/*", "/members/*", "/fees"]
LIBRARY = {
    "version": 1,
    "facts": [
        {"name": "due", "type": "date", "source": {"kind": "label", "page": "/loans/*", "label": "Due date"}},
        {"name": "returned", "type": "date", "source": {"kind": "label", "page": "/loans/*", "label": "Returned on"}},
        {"name": "membership", "type": "text", "source": {"kind": "label", "page": "/members/*", "label": "Membership"}},
        {"name": "item_type", "type": "text", "source": {"kind": "label", "page": "/loans/*", "label": "Item type"}},
        {"name": "daily_rate", "type": "number", "source": {"kind": "table", "page": "/fees", "where": [{"column": "Item type", "op": "eq", "value": "{item_type}"}],
                                                            "column": "Daily rate", "aggregate": "first"}},
        {"name": "cost", "type": "number", "source": {"kind": "label", "page": "/loans/*", "label": "Replacement cost"}},
    ],
    "rules": [
        {"name": "returned on time", "when": "returned <= due", "set": {"outcome": "No fee", "notify": "no"}},
        {"name": "staff pay nothing", "when": "membership == 'staff'", "set": {"outcome": "Waived", "notify": "no"}},
        {"name": "daily rate per day late, at most the replacement cost", "when": "true",
         "set": {"outcome": "fee charged", "amount": "₹{= min(days_between(due, returned) * daily_rate, cost) }",
                 "note": "{= days_between(due, returned) } days late at {daily_rate} a day", "notify": "yes"}},
    ],
}

LATE = library_record("L-2041", "M-5531", "Adult", "DVD", "12 Oct 2026", "19 Oct 2026", "₹450")
ON_DUE_DAY = library_record("L-2042", "M-5532", "Adult", "Book", "05 Sept 2026", "2026-09-05", "₹300")
STAFF = library_record("L-2043", "M-5533", "Staff", "Book", "01 Oct 2026", "09 Oct 2026", "₹300")
CAPPED = library_record("L-2044", "M-5534", "Adult", "DVD", "01 Sept 2026", "Oct 1, 2026", "₹450")
NO_MEMBER_LATE = library_record("L-2045", "M-5535", "Adult", "Book", "01 Oct 2026", "09 Oct 2026", "₹300", with_member=False)
NO_MEMBER_ON_TIME = library_record("L-2046", "M-5536", "Adult", "Book", "10 Oct 2026", "09 Oct 2026", "₹300", with_member=False)


def example(record, values):
    fields, pages = record
    return {"fields": fields, "pages": pages, "values": values}


LIBRARY_EXAMPLES = [
    example(LATE, {"outcome": "Fee charged", "amount": "175", "note": "7 days late at 25 a day", "notify": "yes"}),
    example(ON_DUE_DAY, {"outcome": "No fee", "notify": "no"}),
    example(STAFF, {"outcome": "Waived", "notify": "no"}),
    example(CAPPED, {"outcome": "Fee charged", "amount": "450", "note": "30 days late at 25 a day", "notify": "yes"}),
]


# ------------------------------------------------------------------ reading numbers, dates, tables and labels

check("numbers: currency signs and codes, both thousands styles, %, a unit word",
      parse_number("₹2,480") == 2480 and parse_number("₹1,25,000") == 125000 and parse_number("USD 120") == 120 and parse_number("20%") == 20
      and parse_number("45 days") == 45 and parse_number("Rs. 1,234.50") == Decimal("1234.50") and parse_number("1,234,567") == 1234567)
check("numbers: a minus sign and decimals are kept", parse_number("-₹500") == -500 and parse_number("12.5") == Decimal("12.5") and parse_number("₹ -3.75") == Decimal("-3.75"))
check("numbers: none, two, or a size word give None", parse_number("Pending") is None and parse_number("2 items, 3 boxes") is None
      and parse_number("2.5 lakh") is None and parse_number("") is None)
check("dates: every written form gives ISO", {parse_date(t) for t in ("2026-10-19", "19 Oct 2026", "19 October 2026", "Oct 19, 2026", "October 19 2026",
                                                                    "19/10/2026", "10/19/2026")} == {"2026-10-19"} and parse_date("05 Sept 2026") == "2026-09-05")
check("dates: an ambiguous, impossible or missing date gives None", parse_date("05/06/2026") is None and parse_date("31 Feb 2026") is None
      and parse_date("next week") is None and parse_date("12 Oct 2026 to 19 Oct 2026") is None)

fee_rows = table_rows(FEES[1])
check("table rows: read from the block and the tab-separated text, each row once",
      fee_rows == [{"Item type": "Book", "Daily rate": "₹10", "Max days": "30"}, {"Item type": "DVD", "Daily rate": "₹25", "Max days": "30"},
                   {"Item type": "Magazine", "Daily rate": "₹5", "Max days": "30"}])
check("table rows: a table printed only with tabs is read too", table_rows("Fee schedule\nItem type\tDaily rate\nBook\t₹10\nDVD\t₹25\nTotal items: 2")
      == [{"Item type": "Book", "Daily rate": "₹10"}, {"Item type": "DVD", "Daily rate": "₹25"}])
ORDER = ("http://shop.test/orders/ORD-99751", "Order ORD-99751\nItem\tQty\tPrice\nSocks\t2\t₹200\nSocks\t2\t₹200\nShoe\t1\t₹2,480\n\n[table rows with their headings]\n"
         "Item: Socks | Qty: 2 | Price: ₹200\nItem: Socks | Qty: 2 | Price: ₹200\nItem: Shoe | Qty: 1 | Price: ₹2,480")
check("table rows: two identical lines of one order both stay", len(table_rows(ORDER[1])) == 3)

loan_text = LATE[1][0][1]
member_text = LATE[1][1][1]
check("label: a definition list gives the next line", label_value(loan_text, "Due date") == "12 Oct 2026" and label_value(loan_text, "item TYPE") == "DVD")
check("label: 'Label: value' and 'Label<tab>value' lines", label_value(member_text, "Membership") == "Adult" and label_value(loan_text, "Renewals") == "0")
check("label: a label line with a trailing colon", label_value("Due date:\n12 Oct 2026", "Due date") == "12 Oct 2026")
check("label: a two-cell table row", label_value("[table rows with their headings]\nField: Unit price | Value: ₹620", "Unit price") == "₹620")
check("label: missing, or printed twice with different values, gives None", label_value(loan_text, "Fine paid") is None
      and label_value("Status: Open\nHistory\nStatus: Closed", "Status") is None and label_value("Status: Open\nStatus: open", "Status") == "Open")
check("learning a label from a value", learn_label(loan_text, "12 Oct 2026") == "Due date" and learn_label(member_text, "Adult") == "Membership")
check("a value printed under two labels teaches nothing", learn_label("Quantity ordered\n2\nQuantity returned\n2", "2") is None)
check("value shapes", value_shape("ORD-99751") == r"^[A-Z]{3}-\d{5}$" and re.fullmatch(value_shape("ORD-99751"), "RMA-12345") is not None
      and re.fullmatch(value_shape("ORD-99751"), "OR-1234") is None and value_shape("x" * 41) == ".+")

ZONES = ("http://ship.test/zones", "Delivery zones\nZone\tCities\tSurcharge\nNorth\tDelhi, Chandigarh, Jaipur\t₹40\nWest\tMumbai, Pune, Nashik\t₹25\n"
         "South\tChennai, Bengaluru\t₹30\n\n[table rows with their headings]\nZone: North | Cities: Delhi, Chandigarh, Jaipur | Surcharge: ₹40\n"
         "Zone: West | Cities: Mumbai, Pune, Nashik | Surcharge: ₹25\nZone: South | Cities: Chennai, Bengaluru | Surcharge: ₹30")
surcharge = {"name": "surcharge", "type": "number", "source": {"kind": "table", "page": "/zones", "where": [{"column": "Cities", "op": "contains", "value": "{city}"}],
                                                                "column": "Surcharge", "aggregate": "first"}}
check("table fact: 'contains' finds a city in a list of cities, as a whole word", extract_fact(surcharge, {}, [ZONES], {"city": "pune"}) == 25
      and extract_fact(surcharge, {}, [ZONES], {"city": "Pun"}) is None)
check("table fact: 'in', and the list aggregate gives a sorted list",
      extract_fact({"name": "z", "type": "list", "source": {"kind": "table", "page": "/zones", "where": [{"column": "Zone", "op": "in", "value": "South, North"}],
                                                             "column": "Zone", "aggregate": "list"}}, {}, [ZONES], {}) == ["North", "South"])
check("table fact: sum, count and max over an order's lines",
      extract_fact({"name": "t", "type": "number", "source": {"kind": "table", "page": "/orders/*", "column": "Price", "aggregate": "sum"}}, {}, [ORDER], {}) == 2880
      and extract_fact({"name": "n", "type": "number", "source": {"kind": "table", "page": "/orders/*", "where": [{"column": "Item", "op": "eq", "value": "socks"}],
                                                                  "aggregate": "count"}}, {}, [ORDER], {}) == 2
      and extract_fact({"name": "m", "type": "number", "source": {"kind": "table", "page": "/orders/*", "column": "Price", "aggregate": "max"}}, {}, [ORDER], {}) == 2480)
check("a missing page, row or column gives None",
      extract_fact(surcharge, {}, [ORDER], {"city": "Pune"}) is None and extract_fact(surcharge, {}, [ZONES], {"city": "Goa"}) is None
      and extract_fact(surcharge, {}, [ZONES], {}) is None)
check("field, const and bool facts", extract_fact({"name": "a", "type": "text", "source": {"kind": "field", "field": "loan_id"}}, {"loan_id": "L-1"}, [], {}) == "L-1"
      and extract_fact({"name": "b", "type": "number", "source": {"kind": "const", "value": "₹5,000"}}, {}, [], {}) == 5000
      and extract_fact({"name": "c", "type": "bool", "source": {"kind": "label", "page": "/x", "label": "Insured"}}, {}, [("http://s.test/x", "Insured\nYes")], {}) is True)

# Cells that differ only in their unit word are different things, so a fact never picks the wrong row by its digits alone
PLANS = ("http://club.test/plans", "Plans\nPlan\tPrice\n1 Month\t₹500\n1 Year\t₹5,000\n\n[table rows with their headings]\n"
         "Plan: 1 Month | Price: ₹500\nPlan: 1 Year | Price: ₹5,000")
plan_price = {"name": "price", "type": "number", "source": {"kind": "table", "page": "/plans", "where": [{"column": "Plan", "op": "eq", "value": "{plan}"}],
                                                            "column": "Price", "aggregate": "first"}}
check("table fact: '1 Year' is not '1 Month' (a unit word is part of the cell)",
      extract_fact(plan_price, {}, [PLANS], {"plan": "1 Year"}) == 5000 and extract_fact(plan_price, {}, [PLANS], {"plan": "1 month"}) == 500
      and not cell_matches("1 King", "eq", "1 Queen") and not cell_matches("2 Adults", "in", "2 Children, 3 Adults")
      and cell_matches("₹2,480", "eq", "2480") and cell_matches("2 nights", "eq", "2 Nights") and not cell_matches("2 weeks", "gt", "10 days")
      and cell_matches("3 nights", "gt", "1 night"))
# A cell with a line break (a name, then its SKU) prints over two lines, in the block and in the tab-separated text
BROKEN_BLOCK = ("http://shop.test/orders/ORD-1", "Order ORD-1\n\n[table rows with their headings]\nItem: Socks\nSKU-11 | Status: Shipped | Price: ₹200\n"
                "Item: Shoe\nSKU-12 | Status: Shipped | Price: ₹2,480\nItem: Belt | Status: Cancelled | Price: ₹300")
BROKEN_TABS = ("http://shop.test/orders/ORD-2", "Order ORD-2\nItem\tStatus\tPrice\nSocks\nSKU-11\tShipped\t₹200\nShoe\nSKU-12\tShipped\t₹2,480\nBelt\tCancelled\t₹300")
order_sum = {"name": "t", "type": "number", "source": {"kind": "table", "page": "/orders/*", "where": [{"column": "Status", "op": "eq", "value": "Shipped"}],
                                                       "column": "Price", "aggregate": "sum"}}
order_count = {"name": "n", "type": "number", "source": {"kind": "table", "page": "/orders/*", "aggregate": "count"}}
check("a cell holding a line break stays in its row (block and tab text): sums and counts are right",
      [r["Item"] for r in table_rows(BROKEN_BLOCK[1])] == ["Socks SKU-11", "Shoe SKU-12", "Belt"]
      and [r["Item"] for r in table_rows(BROKEN_TABS[1])] == ["Socks SKU-11", "Shoe SKU-12", "Belt"]
      and all(extract_fact(order_sum, {}, [page], {}) == 2680 and extract_fact(order_count, {}, [page], {}) == 3 for page in (BROKEN_BLOCK, BROKEN_TABS)))
check("a row that cannot be put back together makes the fact unknown, not wrong",
      extract_fact(order_count, {}, [("http://shop.test/orders/ORD-3", "Order\nItem\tStatus\tPrice\nSocks\tShipped\t₹200\nShoe\nSKU-12\tShipped\tpending\n"
                                                                     "Belt\tCancelled\t₹300")], {}) is None)
# An order page with an items table and a payments table that both have "Amount"
TWO_TABLES = ("http://shop.test/orders/ORD-4", "Order ORD-4\n\n[table rows with their headings]\nItem: Socks | Qty: 1 | Amount: ₹200\n"
              "Item: Shoe | Qty: 1 | Amount: ₹2,480\nDate: 12 Oct 2026 | Method: Card | Amount: ₹2,680")
amount_sum = {"name": "t", "type": "number", "source": {"kind": "table", "page": "/orders/*", "column": "Amount", "aggregate": "sum"}}
item_sum = {"name": "t", "type": "number", "source": {"kind": "table", "page": "/orders/*", "where": [{"column": "Qty", "op": "ge", "value": "1"}],
                                                      "column": "Amount", "aggregate": "sum"}}
check("rows of two tables are never added together; a condition that picks one table is fine",
      extract_fact(amount_sum, {}, [TWO_TABLES], {}) is None and extract_fact(item_sum, {}, [TWO_TABLES], {}) == 2680)
check("label: an empty second cell gives no value (not the next line)", label_value("Discount\t\nTotal\t₹2,480", "Discount") is None
      and label_value("Discount\t\nTotal\t₹2,480", "Total") == "₹2,480")

# ------------------------------------------------------------------ the expression language

F = {"sales": Decimal("125000"), "tier": "Gold", "due": "2026-10-12", "returned": "2026-10-19", "cities": ["Mumbai", "Pune"],
     "city_text": "Mumbai, Pune, Nashik", "missing": None, "staff": True, "code": "120"}
check("arithmetic is exact", evaluate_expr("sales * 7 / 100", F) == 8750 and evaluate_expr("0.1 + 0.2 == 0.3", F) is True)
check("texts compare without case or extra spaces", evaluate_expr("tier == ' gold '", F) is True and evaluate_expr("tier != 'Silver'", F) is True)
check("a number and a numeric text compare as numbers", evaluate_expr("code > 99", F) is True and evaluate_expr("code == 120.0", F) is True
      and evaluate_expr("tier == 5", F) is False)
U = {"plan": "1 Year", "stay": "2 weeks", "limit": "10 days", "nights": "3 nights"}
check("texts with different unit words are not equal, and are never put in order by their digits",
      evaluate_expr("plan == '1 Month'", U) is False and evaluate_expr("plan == '1 year'", U) is True
      and isinstance(raised(lambda: evaluate_expr("plan <= '1 Month' and plan >= '1 Month'", U)), ProgramError)
      and isinstance(raised(lambda: evaluate_expr("stay > limit", U)), ProgramError) and evaluate_expr("nights > '1 night'", U) is True)
check("== agrees with <= and >= for two texts that are numbers or dates",
      evaluate_expr("'₹2,480' == '2480'", {}) is True and evaluate_expr("'2026-10-12' == '12 Oct 2026'", {}) is True
      and evaluate_expr("'INV-2026-10-12' == '2026-10-12'", {}) is False and evaluate_expr("'Gold' < 'silver'", {}) is True)
check("dates: days_between, add_days and ordering", evaluate_expr("days_between(due, returned)", F) == 7 and evaluate_expr("returned > due", F) is True
      and evaluate_expr("add_days(due, 30)", F) == "2026-11-11" and evaluate_expr("days_between(returned, due)", F) == -7)
check("'in' and contains(): substring, list item, whole word",
      evaluate_expr("'pune' in cities", F) is True and evaluate_expr("'pun' in city_text", F) is True and evaluate_expr("contains(city_text, 'Pun')", F) is False
      and evaluate_expr("contains(city_text, 'nashik') and startswith(city_text, 'mum')", F) is True and evaluate_expr("tier in ['Gold', 'Platinum']", F) is True)
check("rounding helpers: halves round up, floor division rounds down",
      evaluate_expr("round(2.5) == 3 and round(1.005, 2) == 1.01 and floor(-7 / 2) == -4 and -7 // 2 == -4 and ceil(2.1) == 3 and abs(-4) == max(1, 4, 2)", F) is True)
check("text helpers", evaluate_expr("join(cities)", F) == "Mumbai, Pune" and evaluate_expr("len(cities)", F) == 2 and evaluate_expr("upper(tier)", F) == "GOLD")
unknown = raised(lambda: evaluate_expr("missing > 5", F))
check("a fact that was not found is reported by name", isinstance(unknown, UnknownFact) and unknown.name == "missing" and str(unknown) == "fact missing is not on the pages")
check("an unknown side does not matter when the other side decides",
      evaluate_expr("missing > 5 or staff", F) is True and evaluate_expr("missing > 5 and not staff", F) is False
      and isinstance(raised(lambda: evaluate_expr("missing > 5 or not staff", F)), UnknownFact))
refused = ["__import__('os')", "tier.upper()", "cities[0]", "lambda: 1", "2 ** 10", "sales > 1,00,000", "open('x')", "nobody > 1", "tier + 1", "1 / 0",
           "sales if staff else 0", "[c for c in cities]", "staff and 'yes'", "round(sales, ndigits=2)", "days_between(tier, due)", "", "sales >"]
errors = [raised(lambda e=e: evaluate_expr(e, F)) for e in refused]
check("everything outside the language is refused", all(isinstance(x, ProgramError) and not isinstance(x, UnknownFact) for x in errors))

check("templates fill facts and expressions", render_template("{tier} agent: {= round(sales * 0.07, 2) } due {due} for {cities}", F)
      == "Gold agent: 8750 due 2026-10-12 for Mumbai, Pune" and render_template("{{= 1 + 2 }} and {{tier}}", F) == "3 and Gold"
      and render_template("a {b c} stays", F) == "a {b c} stays")
check("a template naming an unknown name or a missing fact is refused", isinstance(raised(lambda: render_template("{nobody}", F)), ProgramError)
      and isinstance(raised(lambda: render_template("x {missing}", F)), UnknownFact))

# ------------------------------------------------------------------ a hand-written program: a library's late fees

library = parse_program(LIBRARY)
check("a hand-written program is read and starts untrusted", library is not None and library["trust"] == {"branches": {}, "since_spot": 0} and len(library["rules"]) == 3)
r = run_program(library, *LATE, LIBRARY_FORM)
check("late DVD: 7 days at ₹25, each value written the way its field takes it",
      r.values == {"outcome": "Fee charged", "amount": "175", "note": "7 days late at 25 a day", "notify": "yes"} and r.rule_index == 2)
check("the branch names the rule and the choices", r.branch == "p3: outcome=Fee charged")
check("the pages the facts came from", r.used_pages == {"/loans/*", "/members/*", "/fees"} and r.facts["daily_rate"] == 25)
check("returned on the due day: no fee", run_program(library, *ON_DUE_DAY, LIBRARY_FORM).values == {"outcome": "No fee", "notify": "no"})
check("staff: waived", run_program(library, *STAFF, LIBRARY_FORM).values == {"outcome": "Waived", "notify": "no"})
check("capped at the replacement cost", run_program(library, *CAPPED, LIBRARY_FORM).values["amount"] == "450")
r = run_program(library, *NO_MEMBER_LATE, LIBRARY_FORM)
check("a rule needing a fact that is not on the pages stops the run (never skips to a later rule)",
      r.values is None and r.reason == "fact membership is not on the pages" and r.branch is None and r.rule_index is None)
check("a record decided before that rule needs no member page", run_program(library, *NO_MEMBER_ON_TIME, LIBRARY_FORM).values == {"outcome": "No fee", "notify": "no"})
odd = copy.deepcopy(library)
odd["rules"][0]["set"]["fine_code"] = "F1"
check("a value for a field that is not on the form is refused", "not a field of the form" in run_program(odd, *ON_DUE_DAY, LIBRARY_FORM).reason)
odd = copy.deepcopy(library)
odd["rules"][1]["set"]["outcome"] = "Forgiven"
check("a choice that is not one of the options is refused", run_program(odd, *STAFF, LIBRARY_FORM).values is None)
odd["rules"][1]["set"] = {"outcome": "Waived", "notify": "maybe"}
check("a checkbox value that is neither yes nor no is refused", run_program(odd, *STAFF, LIBRARY_FORM).values is None)
# 13 Oct, run #72: the model wrote the checkbox's own label as its value ("Hotel above policy limit") in 3 programs out of 3, and each
# was refused after three tries, so no program was ever learned live
over_box = {"key": "hotel_over_limit", "tag": "input", "type": "checkbox", "label": "Hotel above policy limit", "visible": True, "options": []}
check("a single checkbox given its own label as the value means tick it", prog.normalise_value(over_box, "Hotel above policy limit") == "yes" and prog.normalise_value(over_box, "hotel above policy limit") == "yes")
check("  yes and no still work, and another text is still refused", prog.normalise_value(over_box, "yes") == "yes" and prog.normalise_value(over_box, "no") == "no" and _raises(lambda: prog.normalise_value(over_box, "Meals")))
check("  the prompt says a checkbox is yes or no, never its label", "never the checkbox" in prog.program_prompt("rules", [over_box], LIBRARY_EXAMPLES[:2]))
odd = copy.deepcopy(library)
odd["rules"] = [{"name": "never", "when": "false", "set": {"outcome": "No fee"}}]
check("no rule that applies: no values", run_program(odd, *STAFF, LIBRARY_FORM).reason == "no rule applies")
group = [{"key": "flags", "tag": "input", "type": "checkbox", "label": "Flags", "visible": True, "options": ["Damaged", "Late", "Lost"]}]
odd["rules"] = [{"name": "flags", "when": "true", "set": {"flags": "late; damaged"}}]
check("a group of checkboxes gets its labels, sorted", run_program(odd, *STAFF, group).values == {"flags": "Damaged, Late"})

ok, problems = validate_program(library, LIBRARY_EXAMPLES, LIBRARY_KINDS, LIBRARY_FORM)
check("the right program reproduces every verified example", ok and problems == [])
wrong = copy.deepcopy(LIBRARY)
wrong["rules"][0]["when"] = "returned < due"  # "on or before" read as "before"
ok, problems = validate_program(parse_program(wrong), LIBRARY_EXAMPLES, LIBRARY_KINDS)
check("validation catches a wrong rule and says which example and field differ",
      not ok and len(problems) >= 1 and problems[0].startswith("example 2: rule 3") and "outcome" in problems[0] and "'No fee'" in problems[0])

pickup_form = [{"key": "decision", "tag": "select", "type": "", "label": "Decision", "visible": True, "options": ["Approve", "Reject"]},
               {"key": "pickup", "tag": "widget", "type": "", "label": "Pickup date", "visible": True, "options": []},
               {"key": "ref", "tag": "input", "type": "text", "label": "Reference", "visible": True, "options": []},
               {"key": "note", "tag": "textarea", "type": "", "label": "Note", "visible": True, "options": []}]
pickup_kinds = {"decision": "select", "pickup": "widget", "ref": "text", "note": "note"}
pickup_examples = [{"fields": {"req": d}, "pages": [], "values": {"decision": "Approve", "pickup": p, "ref": "Approved per policy 7"}}
                   for d, p in (("2026-10-12", "19 Oct 2026"), ("2026-10-15", "2026-10-22"))]
pickup = {"version": 1, "facts": [{"name": "req", "type": "date", "source": {"kind": "field", "field": "req"}}],
          "rules": [{"name": "approve", "when": "true", "set": {"decision": "Approve", "pickup": "{= add_days(req, 7) }", "ref": "approved PER policy 7"}}]}
ok, problems = validate_program(parse_program(pickup), pickup_examples, pickup_kinds, pickup_form)
check("validation accepts the same date written another way and text in another case", ok and problems == [])
wrong_pickup = copy.deepcopy(pickup)
wrong_pickup["rules"][0]["set"].update(pickup="{= add_days(req, 1) }", ref="Rejected per policy 7")
ok, problems = validate_program(parse_program(wrong_pickup), pickup_examples, pickup_kinds, pickup_form)
check("validation compares a custom control's date and a text exactly",
      not ok and len(problems) == 4 and "pickup = '2026-10-13'" in problems[0] and "'Approved per policy 7'" in problems[1])
extra_note = copy.deepcopy(pickup)
extra_note["rules"][0]["set"]["note"] = "on time"
ok, problems = validate_program(parse_program(extra_note), pickup_examples, pickup_kinds, pickup_form)
check("validation counts a field the example left empty, set by the program, as a difference", not ok and "(left empty)" in problems[0])
other_note = copy.deepcopy(LIBRARY)
other_note["rules"][2]["set"]["note"] = "{= days_between(due, returned) } days overdue at {daily_rate} a day"
ok, problems = validate_program(parse_program(other_note), LIBRARY_EXAMPLES, LIBRARY_KINDS, LIBRARY_FORM)
check("validation compares a note's words, not only its numbers", not ok and "note" in problems[0])
check("controls that are not objects are ignored, not a crash", run_program(library, *STAFF, LIBRARY_FORM + [None, "x"]).values == {"outcome": "Waived", "notify": "no"})

# ------------------------------------------------------------------ a hand-written program: a rate by tier and grade

AGENT = "Sales desk\nAgent {id}\nName\t{name}\nTier\t{tier}\nGrade\t{grade}\nQuarterly sales\t{sales}\nRegion: West"
RATES = ("http://sales.test/rates", "Commission rates (% of quarterly sales)\nTier\tA\tB\tC\nGold\t9%\t7%\t5%\nSilver\t6%\t4.5%\t3.5%\nBronze\t4%\t3%\t2%\n\n"
         "[table rows with their headings]\nTier: Gold | A: 9% | B: 7% | C: 5%\nTier: Silver | A: 6% | B: 4.5% | C: 3.5%\nTier: Bronze | A: 4% | B: 3% | C: 2%")


def agent_record(agent, name, tier, grade, sales):
    return {"agent": agent}, [(f"http://sales.test/agents/{agent}", AGENT.format(id=agent, name=name, tier=tier, grade=grade, sales=sales)), RATES]


COMMISSION = parse_program({
    "version": 1,
    "facts": [
        {"name": "tier", "type": "text", "source": {"kind": "label", "page": "/agents/*", "label": "Tier"}},
        {"name": "grade", "type": "text", "source": {"kind": "label", "page": "/agents/*", "label": "Grade"}},
        {"name": "sales", "type": "number", "source": {"kind": "label", "page": "/agents/*", "label": "Quarterly sales"}},
        {"name": "rate", "type": "number", "source": {"kind": "table", "page": "/rates", "where": [{"column": "Tier", "op": "eq", "value": "{tier}"}],
                                                      "column": "{grade}", "aggregate": "first"}},
    ],
    "rules": [
        {"name": "under 50,000 earns nothing", "when": "sales < 50000", "set": {"band": "none"}},
        {"name": "the rate for the tier and grade", "when": "true", "set": {"band": "Standard", "commission": "{= round(sales * rate / 100, 2) }"}},
    ],
})
COMMISSION_FORM = [{"key": "band", "tag": "input", "type": "radio", "label": "Band", "visible": True, "options": ["None", "Standard"]},
                   {"key": "commission", "tag": "input", "type": "number", "label": "Commission", "visible": True, "options": []}]
got = [run_program(COMMISSION, *agent_record(*a), COMMISSION_FORM).values for a in [
    ("AG-301", "Riya Sharma", "Gold", "B", "₹1,25,000"), ("AG-302", "Kabir Das", "Silver", "C", "₹80,000"),
    ("AG-303", "Meera Iyer", "Silver", "B", "₹1,10,500"), ("AG-304", "Arjun Rao", "Bronze", "A", "₹40,000")]]
check("the column picked by a fact: Gold/B 7%, Silver/C 3.5%, Silver/B 4.5%", got[:3] == [{"band": "Standard", "commission": "8750"},
                                                                                         {"band": "Standard", "commission": "2800"},
                                                                                         {"band": "Standard", "commission": "4972.5"}])
check("under the threshold: no commission, the radio's own label", got[3] == {"band": "None"})
missing_grade = run_program(COMMISSION, *agent_record("AG-305", "Isha Jain", "Gold", "D", "₹90,000"), COMMISSION_FORM)
check("a grade with no column stops at the rule that needs the rate", missing_grade.values is None and missing_grade.reason == "fact rate is not on the pages")
older = ("http://sales.test/rates", RATES[1].replace("B: 7%", "B: 6%").replace("Gold\t9%\t7%", "Gold\t9%\t6%"))
fields, pages = agent_record("AG-301", "Riya Sharma", "Gold", "B", "₹1,25,000")
check("the latest page of a type is the one read", run_program(COMMISSION, fields, [older] + pages, COMMISSION_FORM).values["commission"] == "8750"
      and run_program(COMMISSION, fields, pages + [older], COMMISSION_FORM).values["commission"] == "7500")

# ------------------------------------------------------------------ reading the model's program

bad_ref = copy.deepcopy(LIBRARY)
bad_ref["facts"][4]["source"]["where"][0]["value"] = "{cost}"  # refers to a fact read after it
check("a fact may only use facts read before it", parse_program(bad_ref) is None)
bad_call = copy.deepcopy(LIBRARY)
bad_call["rules"][2]["when"] = "eval('1')"
check("a rule calling a function outside the language is refused", parse_program(bad_call) is None)
bad_blank = copy.deepcopy(LIBRARY)
bad_blank["rules"][2]["set"]["note"] = "{days_late} days"
check("a value naming an unknown fact is refused", parse_program(bad_blank) is None)
check("the program may come wrapped as {\"program\": ...}", parse_program({"program": LIBRARY}) == library)
check("page types: the plan's known type first, else the address's shape", page_type("http://x/#/claims/kxqfab", ["/#/claims/*"]) == "/#/claims/*"
      and page_type("http://x/fees", ["/#/claims/*"]) == "/fees")

long_rates = "Rates\n\n[table rows with their headings]\n" + "\n".join(f"Tier: T{n:03d} | A: {n}% | B: {n + 1}%" for n in range(300)) + "\nTier: Platinum | A: 12% | B: 15%"
trimmed = trim_page(long_rates, ["Platinum"])
check("a long page is trimmed for the prompt, keeping the block's headings and the rows that matter",
      len(trimmed) < 3600 and "[table rows with their headings]" in trimmed and "Tier: Platinum | A: 12% | B: 15%" in trimmed and "Tier: T000" in trimmed)

prompt = program_prompt(LIBRARY_RULES, LIBRARY_FORM, LIBRARY_EXAMPLES, LIBRARY_TYPES)
check("the prompt holds the rules, the form's keys and options, and the pages by type",
      LIBRARY_RULES in prompt and "outcome" in prompt and "No fee | Waived | Fee charged" in prompt and "=== /loans/* ===" in prompt
      and "=== /members/* ===" in prompt and "Due date\n12 Oct 2026" in prompt and "Daily rate: ₹25" in prompt)
check("a page that is the same for every record is shown once", prompt.count("Item type: Magazine") == 1 and "the same text as this page in example 1" in prompt)
check("the prompt's worked examples are not about any test site", not re.search(r"\b(?:RMA|claims?|expense|returns desk|invoice|refunds?|restocking)\b", program_prompt("", [], []), re.I))
check("a long page is trimmed to about the limit even when its first lines are long",
      len(trim_page("Terms " * 2000 + "\nTotal\t₹2,480\n" + "\n".join(f"line {n}" for n in range(500)), ["₹2,480"])) <= 3300
      and "Total\t₹2,480" in trim_page("Terms " * 2000 + "\nTotal\t₹2,480\n" + "\n".join(f"line {n}" for n in range(500)), ["₹2,480"])
      and len(trim_page("Policy\n" + "x" * 14000, [])) <= 3300)
tiers = [f"T{n:03d}" for n in range(100)] + ["Silver"] + [f"U{n:03d}" for n in range(100)] + ["Gold"]
BIG_RATES = ("http://sales.test/rates", "Commission rates\nTier\tA\tB\n" + "\n".join(f"{t}\t{n % 9 + 1}%\t{n % 7 + 1}%" for n, t in enumerate(tiers)))
tier_examples = [{"fields": {"agent": a}, "pages": [(f"http://sales.test/agents/{a}", f"Agent {a}\nAgent tier\n{t}\nGrade\nA"), BIG_RATES], "values": {"band": "Standard"}}
                 for a, t in (("AG-1", "Gold"), ("AG-2", "Silver"))]
big_prompt = program_prompt("", COMMISSION_FORM, tier_examples, ["/agents/*", "/rates"])
check("a page shared by examples is trimmed with every example's clues, so each one's row shows",
      "Gold\t" in big_prompt and "Silver\t" in big_prompt and "the same text as this page in example 1" in big_prompt)

# ------------------------------------------------------------------ learning a program (a fake model)

ctx = RunContext(target_url="http://lib.test", workflow_name="test", match_key_field="loan_id", model_name="m", gemini_api_key="x", task_instructions=LIBRARY_RULES)
answers: list = []
prompts: list[str] = []


async def fake_ask_json(ctx, prompt, schema=None, temperature=0.0):
    prompts.append(prompt)
    answer = answers.pop(0)
    if isinstance(answer, Exception):
        raise answer
    return copy.deepcopy(answer)


prog.ask_json = fake_ask_json


def learn(*replies, examples=LIBRARY_EXAMPLES):
    answers[:] = list(replies)
    prompts.clear()
    return asyncio.run(learn_program(ctx, LIBRARY_FORM, examples, LIBRARY_KINDS, LIBRARY_TYPES))


program, why, calls = learn(LIBRARY)
check("a good first answer is kept after one call", program is not None and calls == 1 and program["rules"][2]["name"].startswith("daily rate") and "all 4 examples" in why)
check("the question carries the task instructions", LIBRARY_RULES in prompts[0])
program, why, calls = learn(wrong, LIBRARY)
check("a wrong first answer is retried once with the differences, and the fix is kept",
      program is not None and calls == 2 and "YOUR FIRST PROGRAM" in prompts[1] and "example 2: rule 3" in prompts[1] and "returned < due" in prompts[1])
program, why, calls = learn(wrong, wrong, LIBRARY)
check("wrong twice, right on the third try: the program is kept", program is not None and calls == 3)
program, why, calls = learn(wrong, wrong, wrong)
check("wrong three times: no program", program is None and calls == 3 and "still wrong" in why and not answers)
stray = copy.deepcopy(LIBRARY)
stray["rules"][1]["set"]["fine_code"] = "W"
program, why, calls = learn(bad_call, stray, stray)
check("an answer outside the language, then one naming a field not on the form: no program",
      program is None and calls == 3 and "not allowed" in prompts[1] and "fine_code" in why)
program, why, calls = learn(LIBRARY, examples=LIBRARY_EXAMPLES[:1])
check("one example is not enough to write a program from", program is None and calls == 0 and not prompts)
check("a used-up quota stops learning instead of being swallowed", isinstance(raised(lambda: learn(LlmHalt("quota", quota=True))), LlmHalt))
program, why, calls = learn(RuntimeError("the model call failed: 503"))
check("any other failed call gives no program and says why", program is None and calls == 1 and "failed" in why)

# ------------------------------------------------------------------ trust

p = parse_program(LIBRARY)
late = "p3: outcome=Fee charged"
check("a new program is trusted for nothing", not program_trusted_for(p, late) and not program_trusted_for(p, None))
note_program_check(p, late, True)
check("one agreement is not enough", not program_trusted_for(p, late))
note_program_check(p, late, True)
check("two agreements trust that branch only", program_trusted_for(p, late) and not program_trusted_for(p, "p1: outcome=No fee"))
check("a stricter need can be asked for", not program_trusted_for(p, late, need=3))
note_program_check(p, late, False)
note_program_check(p, late, True)
check("one disagreement distrusts the branch for good", not program_trusted_for(p, late) and p["trust"]["branches"][late] == -1)
p = parse_program(LIBRARY)
check("no spot check is due on a fresh program", not spot_check_due(p, every=3))
note_program_used(p)
note_program_used(p)
check("every Nth trusted use is checked against the model", spot_check_due(p, every=3) and p["trust"]["since_spot"] == 2)
note_program_check(p, late, True, spot=True)
check("a spot check restarts the count", not spot_check_due(p, every=3) and p["trust"]["since_spot"] == 0)
p = parse_program(LIBRARY)
other = "p1: outcome=No fee"
note_program_check(p, late, True)
note_program_check(p, late, True)
spot_checks = 0
for n in range(1, 101):  # a distrusted branch is compared every 9 records; the trusted one must still be spot-checked
    if n % 9 == 0:
        note_program_check(p, other, False)
    elif spot_check_due(p):
        note_program_check(p, late, True, spot=True)
        spot_checks += 1
    else:
        note_program_used(p)
check("checks of another branch do not put off the trusted branch's spot checks", spot_checks >= 8)
p = parse_program(LIBRARY)
note_program_check(p, late, True)
note_program_check(p, late, True)
note_program_used(p)
note_program_check(p, late, True)
check("a check of a branch that is trusted already counts as a spot check", p["trust"]["since_spot"] == 0)
damaged = [{"trust": None}, {"trust": {"branches": {late: "x"}, "since_spot": "?"}}, {"trust": {"branches": [late]}}, {}]
check("a damaged trust record is never trusted and never crashes",
      not any(program_trusted_for(d, late) for d in damaged) and not program_trusted_for({}, late, need=0)
      and [note_program_used(d) for d in copy.deepcopy(damaged)] == [1, 1, 1, 1]
      and [note_program_check(d, late, True) for d in copy.deepcopy(damaged)] == [1, -1, 1, 1]
      and not any(spot_check_due(d) for d in copy.deepcopy(damaged)))

# ------------------------------------------------------------------ 15 Oct review: a program's numbers must be on the pages

REFUND_FORM = [{"key": "disposition", "tag": "select", "type": "", "label": "Disposition", "options": ["Refund", "Replace"], "visible": True},
               {"key": "refund_amount", "tag": "input", "type": "text", "label": "Refund amount", "options": [], "visible": True}]
REFUND_RULES = "Refund price x quantity less the restocking fee printed on the order page."
fee_program = parse_program({"facts": [
    {"name": "price", "type": "number", "source": {"kind": "label", "page": "/orders/*", "label": "Price"}},
    {"name": "qty", "type": "number", "source": {"kind": "label", "page": "/orders/*", "label": "Quantity"}},
    {"name": "fee_pct", "type": "number", "source": {"kind": "const", "value": "15"}}],
    "rules": [{"name": "refund", "when": "true", "set": {"disposition": "Refund", "refund_amount": "{= price*qty*(100-fee_pct)/100 }"}}]})


def order_page(oid, price, qty, fee):
    return {"order_id": oid}, [(f"http://shop.test/orders/{oid}", f"Order {oid}\nPrice\n₹{price}\nQuantity\n{qty}\nRestocking fee {fee}%")]


fields20, pages20 = order_page("B1", 1000, 2, 20)
r20 = run_program(fee_program, fields20, pages20, REFUND_FORM)
check("a program that fixed a fee rate the examples shared still runs (1700 on a 20% page) ...", r20.values == {"disposition": "Refund", "refund_amount": "1700"})
problems = prog.ungrounded(fee_program, r20, fields20, pages20, REFUND_RULES, REFUND_FORM)
check("  but its fixed 15 is not in the instructions: the program's answer is not grounded (the model decides instead)",
      problems and "fee_pct" in problems[0] and "15" in problems[0])
check("  a fixed number the instructions give is fine", prog.const_problems(fee_program, "Refund less a 15% restocking fee.") == [])
read_fee = parse_program({"facts": [
    {"name": "price", "type": "number", "source": {"kind": "label", "page": "/orders/*", "label": "Price"}},
    {"name": "amount", "type": "number", "source": {"kind": "label", "page": "/orders/*", "label": "Price"}}],
    "rules": [{"name": "small refund", "when": "amount < 1500", "set": {"disposition": "Refund", "refund_amount": "{= price * 0.85 }"}},
              {"name": "otherwise replace", "when": "true", "set": {"disposition": "Replace"}}]})
r = run_program(read_fee, fields20, pages20, REFUND_FORM)
problems = prog.ungrounded(read_fee, r, fields20, pages20, REFUND_RULES, REFUND_FORM)
check("a limit (1500) and a rate (0.85) written into a program's rules are checked against the record's pages, like a model's",
      r.rule_index == 0 and any("1500" in p for p in problems) and any("0.85" in p for p in problems))
r = run_program(read_fee, *order_page("B2", 1500, 1, 15), REFUND_FORM)
check("  a program whose numbers this record's pages print is grounded", r.rule_index == 1 and prog.ungrounded(read_fee, r, *order_page("B2", 1500, 1, 15), "", REFUND_FORM) == [])
fixed_rate = copy.deepcopy(LIBRARY)
fixed_rate["facts"].append({"name": "grace_days", "type": "number", "source": {"kind": "const", "value": "37"}})
program, why, calls = learn(fixed_rate, fixed_rate, fixed_rate)
check("learning refuses a program that fixes a number the instructions do not give (asked again, then none)",
      program is None and calls == 3 and "grace_days" in prompts[1] and "37" in why)

# ------------------------------------------------------------------ 15 Oct review: trust kept over a relearn, spot checks claimed

old = parse_program(LIBRARY)
old["trust"]["branches"] = {"p1: outcome=No fee": 3, "p2: outcome=Waived": 2, "p3: outcome=Fee charged": -1}
new = parse_program(LIBRARY)
new["rules"][1]["when"] = "membership == 'staff' or membership == 'volunteer'"  # this rule changed
new["rules"].insert(0, {"name": "lost", "when": "item_type == 'Lost'", "set": {"outcome": "fee charged", "notify": "yes"}})
carried = prog.carry_trust(old, new)
check("a relearned program keeps the trust of each rule it left unchanged (renamed to its new place); a changed rule starts again",
      new["trust"]["branches"] == {"p2: outcome=No fee": 3, "p4: outcome=Fee charged": -1} and sorted(carried) == ["p2: outcome=No fee", "p4: outcome=Fee charged"])
renamed_fact = parse_program(LIBRARY)
renamed_fact["facts"][0]["source"]["label"] = "Due on"  # the same rule over a fact read differently is another rule
check("  a rule whose facts are read differently is not the same rule (only the rule that does not use the due date keeps its trust)",
      prog.carry_trust(old, renamed_fact) == ["p2: outcome=Waived"])

p = parse_program(LIBRARY)
for _ in range(9):
    note_program_used(p)
check("the 10th trusted use is a spot check", spot_check_due(p))
prog.claim_spot_check(p)
check("  claiming it restarts the count at once, so records running alongside use the program", not spot_check_due(p) and p["trust"]["since_spot"] == 0)
note_program_used(p)
note_program_check(p, late, True, spot=True)
check("  booking the claimed check does not restart the count again (uses made meanwhile still count)", p["trust"]["since_spot"] == 1 and "spot_claimed" not in p["trust"])
prog.claim_spot_check(p)
prog.reopen_spot_check(p)
check("  a claimed check never booked (the record failed) is handed back: the next trusted record is the spot check", spot_check_due(p))
prog.reopen_spot_check(p)
check("  handing back without a claim changes nothing", spot_check_due(p) and p["trust"]["since_spot"] == prog.SPOT_EVERY - 1)

print(f"\n{checks} checks passed")
