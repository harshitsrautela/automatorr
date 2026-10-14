"""Checks for grounding (recipe/evidence.py): a model's decision must rest on numbers, dates and quotes that are on the record's
pages, in the record or in the task instructions.

    python tests/test_evidence.py

Needs nothing: no browser, no model, no site. The pages are small copies of what the plan executor reads (plan_exec._quick_read):
a description list read as "Label\\nValue", a table read as tab-separated cells, then the "[table rows with their headings]"
block. The main case is the 9 Oct failure: a trusted plan decided a change-of-mind refund with a guessed 15% restocking fee
although the policy page (with the real 20%) was never read. It is checked twice: with the list page's pager glued into one
word, and with an ordinary pager whose 5 puts 15 = 10 + 5 one step away. The guess is also checked in other shapes: a
date-shaped formula, hex digits, a number written out without {= }, a note."""

import os
import sys
import time
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from recipe.evidence import CONSTANTS, check_answer, derivable, formula_numbers, grounded_dates, grounded_numbers, inline_formulas, quote_found

checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


D = Decimal
NAV = "Returns Desk Warehouse returns\nDashboardReturnsReturn policy\n"

LIST_PAGE = ("http://localhost:5181/returns?page=3", NAV + "Returns\n\n100 returns, 10 per page.\n\nRMA\tCustomer\tReason\tDelivered\tStatus\n"
             "RMA-71519\tRohan Gupta\tChanged mind\t22 Sept 2026\tPending\nRMA-71660\tDiya Kapoor\tChanged mind\t30 Sept 2026\tPending\n"
             "RMA-71683\tTara Sethi\tWrong item\t25 Sept 2026\tPending\n‹ Prev12345678910Next ›")
RETURN_PAGE = ("http://localhost:5181/returns/5b1f0c2e", NAV + "\nReturns / RMA-71519\n\nReturn RMA-71519\nCustomer\nRohan Gupta\nOrder\nORD-10342\n"
               "Item\nSKU-4456\nQuantity returned\n4\nReason\nChanged mind\nCondition\nOpened\nDelivered on\n22 Sept 2026\nDays since delivery\n17\n"
               "Next pick-up slot\n19 Oct 2026\nStatus\nPending\nYour decision\nDecision\nChoose…\nRefund\nReplace\nReject\nSave decision")
ORDER_PAGE = ("http://localhost:5181/orders/ORD-10342", NAV + "\nReturns / ORD-10342\n\nOrder ORD-10342\nOrdered SKU\nSKU-4456\nShipped SKU\nSKU-4456\n"
              "Unit price\n₹620\nQuantity ordered\n4\nDelivered on\n22 Sept 2026\nPayment\nUPI")
PRODUCT_PAGE = ("http://localhost:5181/products/SKU-4456", NAV + "\nReturns / SKU-4456\n\nOxford shoes item 4456\nSKU\nSKU-4456\nCategory\n"
                "Footwear - Formal\nFinal sale\nNo\nWarranty period (days)\n180")
POLICY_PAGE = ("http://localhost:5181/policy", NAV + "Return policy\nGeneral terms\n\n"
               "1. A return request is considered open from the moment a return number is issued. Inspection findings are final.\n\n"
               "2. Customers keep the original packaging, accessories and documentation until the return has been decided.\n\n"
               "3. Refunds are issued to the original payment method.\n\nReturn window\n\n"
               "A change-of-mind return must be requested within 30 days of delivery. The 30th day is still inside the window.\n\n"
               "Restocking fees\n\nFor change-of-mind returns the restocking fee is a percentage of the refund, set by the item's category and "
               "its condition on return. Unopened items carry no fee.\n\nCategory\tOpened\tDamaged\nFootwear - Running\t10%\t40%\n"
               "Footwear - Formal\t20%\t40%\nFootwear - Casual\t10%\t25%\nFootwear - Boots\t5%\t30%\n\nFurther terms\n\n"
               "31. Disputes about inspection results are handled by the quality team within ten working days.\n\n"
               "[table rows with their headings]\nCategory: Footwear - Running | Opened: 10% | Damaged: 40%\n"
               "Category: Footwear - Formal | Opened: 20% | Damaged: 40%\nCategory: Footwear - Casual | Opened: 10% | Damaged: 25%\n"
               "Category: Footwear - Boots | Opened: 5% | Damaged: 30%")
FIELDS = {"rma": "RMA-71519"}
INSTRUCTIONS = """Decide each pending return. The first rule that applies decides:

1. Reason is Wrong item: Replace, replacement SKU = the SKU the customer ordered, pick-up date = the next pick-up slot.
2. The item is final sale (item page): Reject, reason Final sale.
3. Reason is Defective: days since delivery more than the item's warranty days: Reject, Out of warranty; otherwise Refund
   quantity x unit price, pick-up date = the next slot.
4. Reason is Changed mind: more than 30 days: Reject, Outside window; otherwise Refund quantity x unit price minus the
   restocking fee (none when Unopened; otherwise the Opened or Damaged percentage for the item's category in the policy
   table), pick-up date = the next slot."""

SEEN_9_OCT = [LIST_PAGE, RETURN_PAGE, ORDER_PAGE, PRODUCT_PAGE]  # what the plan read on 9 Oct: never the policy page
WITH_POLICY = SEEN_9_OCT + [POLICY_PAGE]


# The model's checks for the change-of-mind record, as plan_exec asks for them: one per rule, in order
def answer(fee_formula: str, evidence: list[str] | None = None, pickup: str = "2026-10-19") -> list[dict]:
    rows = [
        {"rule": "Reason is Wrong item: Replace", "known": True, "expr": "false", "values": [{"field": "decision", "value": "Replace"}]},
        {"rule": "The item is final sale: Reject", "known": True, "expr": "false", "values": [{"field": "decision", "value": "Reject"}],
         "evidence": ["Final sale No"]},
        {"rule": "Reason is Defective", "known": True, "expr": "false and 17 > 180", "values": [{"field": "decision", "value": "Reject"}]},
        {"rule": "Changed mind within 30 days: refund less the restocking fee", "known": True, "expr": "17 <= 30",
         "values": [{"field": "decision", "value": "Refund"}, {"field": "refund_amount", "value": fee_formula}, {"field": "pickup_date", "value": pickup}]},
    ]
    if evidence is not None:
        rows[3]["evidence"] = evidence
    return rows


# ------------------------------------------------------------------ numbers on the pages

G = grounded_numbers(SEEN_9_OCT, FIELDS, INSTRUCTIONS)
check("numbers are read off the pages, a dl's values and a table's cells", {D(4), D(17), D(620), D(180), D(100), D(10)} <= G)
check("the instructions' numbers count ('more than 30 days')", D(30) in G)
check("the constants are always there", CONSTANTS <= G and CONSTANTS == {D(0), D(1), D(2), D(10), D(100), D(1000)})
check("ids are not numbers (RMA-71519, ORD-10342, SKU-4456, a pager's run of digits)", not {D(71519), D(10342), D(12345678910)} & G)
check("dates are not split into numbers (22 Sept 2026, 19 Oct 2026)", not {D(22), D(19), D(2026)} & G)
check("no 15 and no 20 anywhere on the pages the plan read", D(15) not in G and D(20) not in G)
odd = [("http://x/a", "Limit\n₹1,25,000\nFee\t20%\nRate\t2.5\nDue 2026-10-19\nEUR 1,234.50\nRs.500\nOn 05 Sept 2026 and Oct 19, 2026")]
G2 = grounded_numbers(odd, {"advance": 5000}, "")
check("₹1,25,000 is 125000, 20% is 20, 2.5 and 1,234.50 are themselves, Rs.500 is 500", {D(125000), D(20), D("2.5"), D("1234.5"), D(500)} <= G2)
check("a record field's value counts", D(5000) in G2)
check("ISO and spelled-out dates give no numbers", not {D(2026), D(5), D(19)} & G2)
GP = grounded_numbers([POLICY_PAGE], {}, "")
check("the policy table's percentages count, from both the cells and the table-rows block", {D(5), D(10), D(20), D(25), D(30), D(40)} <= GP)
check("numbered paragraphs (1. 2. 3. 31.) are places, not facts", D(31) not in GP and D(3) not in GP)
check("a time of day gives no numbers (the 15 of a 10:15 pick-up slot)",
      not {D(10), D(15), D(9), D(30)} & grounded_numbers([("http://x/r", "Pick-up 10:15\nSlot 09:30:00")], {}, "").seen)

# ------------------------------------------------------------------ one step of arithmetic

check("one level: 2480 = 4 x 620 is derivable", derivable(D(2480), G))
check("two levels are not one: 1984 = 4 x 620 x 0.8 is not derivable without the fee", not derivable(D(1984), G))
check("the guessed 15 is not derivable from the 9 Oct pages", not derivable(D(15), G))
check("a bare constant is not a fact: 15 = 30 / 2 does not count", not derivable(D(15), {D(30)} | CONSTANTS))
check("but a 2 that is on a page takes part like any number: 620 = 1240 / 2",
      derivable(D(620), grounded_numbers([("http://x/o", "Quantity\n2\nTotal\n₹1,240")], {}, "")) and not derivable(D(620), {D(1240)} | CONSTANTS)
      and derivable(D(620), {D(1240)} | CONSTANTS, seen={D(2), D(1240)}))
check("the grounded set remembers what was seen, so a 10 printed on a list page counts as a fact", G.seen >= {D(10), D(100)} and derivable(D(20), G))
check("percentages as fractions and what is left of them: 0.2, 80, 0.8 from a 20% on a page",
      derivable(D("0.2"), {D(20)} | CONSTANTS) and derivable(D(80), {D(20)} | CONSTANTS) and derivable(D("0.8"), {D(20)} | CONSTANTS))
check("a zero on a page does not make every number derivable", not derivable(D(15), {D(0), D(30), D(4)} | CONSTANTS, seen={D(0), D(30), D(4)}))
check("a sum and a difference of two page numbers are derivable", derivable(D(624), G) and derivable(D(616), G) and derivable(D(13), G))

# ------------------------------------------------------------------ numbers inside formulas

check("a formula's numbers in order: 4*620*(100-15)/100", formula_numbers("4*620*(100-15)/100") == [D(4), D(620), D(100), D(15), D(100)])
check("max(1,2) gives 1 and 2; 6,50,000 gives 650000", formula_numbers("max(1,2) > 0") == [D(1), D(2), D(0)] and formula_numbers("6,50,000 > 5,00,000") == [D(650000), D(500000)])
check("ids and dates inside a formula are not numbers", formula_numbers("'SKU-4456' == 'SKU-4456' and '2026-10-19' <= '2026-10-30' and 17 <= 30") == [D(17), D(30)])
check("currency signs, decimals and a leading dot are read like recipe/formula.py reads them", formula_numbers("₹2,480 * 0.8 + .5") == [D(2480), D("0.8"), D("0.5")])
check("a numeric date outside quotes is arithmetic, as recipe/formula.py computes it: 1240-10-15 is three numbers",
      formula_numbers("1240-10-15") == [D(1240), D(10), D(15)] and formula_numbers("4960/2/15") == [D(4960), D(2), D(15)]
      and formula_numbers("'2026-10-19' <= \"19/10/2026\"") == [] and formula_numbers("19 Oct 2026 > 22 Sept 2026") == [])
check("hex, octal and binary integers are read as recipe/formula.py reads them (all 15)", formula_numbers("0xF + 0o17 - 0b1111") == [D(15)] * 3)
check("an absurd exponent is read without failing", formula_numbers("17 <= 1e999999999") == [D(17), D("1e999999999")])
check("the {= } parts of a value are found, doubled braces too", inline_formulas("Refund {=4*620} less {{= 2480*20/100 }}") == ["4*620", "2480*20/100"] and inline_formulas("Refund") == [])

# ------------------------------------------------------------------ dates

DATES = grounded_dates(SEEN_9_OCT, {"delivered": "Oct 2, 2026"})
check("dates on the pages and in the record are ISO", {"2026-09-22", "2026-10-19", "2026-09-30", "2026-09-25", "2026-10-02"} <= DATES)
check("every written form of a date is read",
      grounded_dates([("http://x/d", "a 19 October 2026\nb 05 Sept 2026\nc 2026-11-05\nd October 21 2026\ne 23/10/2026\nf 10/24/2026")], {}) ==
      {"2026-10-19", "2026-09-05", "2026-11-05", "2026-10-21", "2026-10-23", "2026-10-24"})
check("a day/month order that cannot be told gives no date (as parse_date); an impossible date gives none",
      grounded_dates([("http://x/d", "on 05/06/2026, not 31/02/2026")], {}) == set())
check("a clear numeric date on the same page settles the order: 22/05/2026 says day first",
      grounded_dates([("http://x/d", "Next pick-up slot\n05/06/2026\nDelivered on\n22/05/2026")], {}) == {"2026-06-05", "2026-05-22"})
check("so do the record's other pages (a site writes dates one way); a field value is settled only by itself",
      grounded_dates([("http://x/r", "Slot\n05/06/2026"), ("http://x/l", "Delivered\n05/22/2026")], {"due": "07/08/2026"}) == {"2026-05-06", "2026-05-22"})

# ------------------------------------------------------------------ quotes

check("a quote across a line break is found ('Final sale No' vs 'Final sale\\nNo')", quote_found("Final sale No", SEEN_9_OCT, FIELDS))
check("case, punctuation and spacing do not matter", quote_found("final sale: no", SEEN_9_OCT, FIELDS) and quote_found("Unit price ₹620", SEEN_9_OCT, FIELDS))
check("a quote from a table row is found (tab cells and the table-rows block)", quote_found("Footwear - Formal 20%", [POLICY_PAGE], {}) and quote_found("Category: Footwear - Formal | Opened: 20%", [POLICY_PAGE], {}))
check("a quote that is not on the pages is not found", not quote_found("Final sale Yes", SEEN_9_OCT, FIELDS) and not quote_found("Restocking fee 15%", WITH_POLICY, FIELDS))
check("words far apart on a page are not a quote", not quote_found("Customer Changed mind", SEEN_9_OCT, FIELDS))
check("numbers match whatever their separators", quote_found("limit 125000", [("http://x/a", "Limit\n₹1,25,000")], {}) and quote_found("₹2,480.00", [("http://x/a", "Total 2480")], {}))
check("a quote from a field value is found; an empty quote never is", quote_found("RMA-71519", [], FIELDS) and not quote_found(" ", SEEN_9_OCT, FIELDS))
check("a label never borrows the next row's value: 'Footwear - Running 20%' and 'Footwear - Formal 10%' are not on the policy page",
      not quote_found("Footwear - Running 20%", [POLICY_PAGE], {}) and not quote_found("Footwear - Formal 10%", [POLICY_PAGE], {}))
DL = [("http://x/p", "Item\nFinal sale\nNo\nGift wrapped\nYes")]
check("nor the next pair's value in a description list", not quote_found("Final sale Yes", DL, {}) and quote_found("Final sale No", DL, {}) and quote_found("Gift wrapped Yes", DL, {}))
ROWS = [("http://x/p", "[table rows with their headings]\nCategory: Footwear - Formal | Opened: 20% | Damaged: 40%\n"
                       "Category: Footwear - Casual | Opened: 10% | Damaged: 25%")]
check("label words on the same row may be skipped, a number never ('Formal Opened 40%' would borrow the Damaged cell)",
      quote_found("Footwear - Formal 20%", ROWS, {}) and not quote_found("Footwear - Formal Opened 40%", ROWS, {})
      and not quote_found("Footwear - Formal Opened 10%", ROWS, {}) and quote_found("Footwear - Formal | Opened: 20% | Damaged: 40%", ROWS, {}))
check("a whole cell of the row, with its heading, may be quoted after the row's name, the cells between left out",
      quote_found("Footwear - Formal Damaged 40%", ROWS, {}) and quote_found("Category: Footwear - Formal | Damaged: 40%", ROWS, {})
      and not quote_found("Category: Footwear - Formal | Damaged: 25%", ROWS, {}) and not quote_found("Category: Footwear - Casual | Damaged: 40%", ROWS, {}))
TIERS = [("http://x/policy", "Hotel limits\n\n[table rows with their headings]\nCity tier: Tier 1 | L1: 4,000 | L2: 5,500 | L3: 7,000 | L4: 9,000\n"
                             "City tier: Tier 2 | L1: 3,000 | L2: 4,000 | L3: 5,500 | L4: 7,000")]
check("15 Oct review: a grade's cell of a tier's row is found without the cells between ('City tier: Tier 1 | L2: 5,500')",
      quote_found("City tier: Tier 1 | L2: 5,500", TIERS, {}) and quote_found("Tier 1 L2: 5,500", TIERS, {})
      and not quote_found("City tier: Tier 1 | L2: 4,000", TIERS, {}) and not quote_found("L1: 4,000 | L3: 7,000", TIERS, {}))
check("  and the answer that quotes it is grounded", check_answer(
    [{"rule": "3. Hotel cost above the limit", "expr": "6200 > 5500", "evidence": ["City tier: Tier 1 | L2: 5,500"]}], 0, {"outcome": "Approve with deduction"},
    TIERS + [("http://x/claim", "Hotel 6,200")], {}, "", None) == [])
check("long numbers in quotes are compared digit by digit, not rounded",
      not quote_found("Account 1234567890123456789012345678901234", [("http://x/a", "Account 1234567890123456789012345678909999")], {})
      and quote_found("Account 1234567890123456789012345678901234", [("http://x/a", "Account\n1234567890123456789012345678901234")], {}))

# ------------------------------------------------------------------ whole answers

problems = check_answer(answer("{=4*620*(100-15)/100}"), 3, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
check("the 9 Oct failure is caught: the guessed 15 in the refund's formula", len(problems) == 1 and "refund_amount" in problems[0] and " 15," in problems[0])
check("written as a fraction (x 0.85) it is caught too", any("0.85" in p for p in check_answer(answer("{=2480*0.85}"), 3, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS)))
guess_in_rule = answer("{=1984}")
guess_in_rule[3]["expr"] = "17 <= 30 and 4*620*(100-15)/100 > 0"
check("a guessed number in a rule's formula is caught", any(p.startswith("rule 4") and " 15," in p for p in check_answer(guess_in_rule, 3, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS)))
check("the honest answer, with the fee on the policy page it read, is grounded",
      check_answer(answer("{=4*620*(100-20)/100}", evidence=["Footwear - Formal 20%"]), 3, None, WITH_POLICY, FIELDS, INSTRUCTIONS) == [])
check("so is the same refund written with a pre-multiplied total and a fraction", check_answer(answer("{=2480*0.8}"), 3, None, WITH_POLICY, FIELDS, INSTRUCTIONS) == [])
missing = check_answer(answer("{=4*620*(100-20)/100}", evidence=["Footwear - Formal 20%"]), 3, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
# 20 is one step from the 10 and 30 these pages show, so derivable alone cannot catch a lucky guess: a percentage must be
# printed itself, and the quote must be on a page
check("claiming the 20% without the policy page is caught twice: the percentage is not printed, and neither is its quote",
      len(missing) == 2 and derivable(D(20), G) and any('evidence "Footwear - Formal 20%"' in p for p in missing)
      and any("refund_amount" in p and "percentage 20%" in p for p in missing))
check("an evidence quote across a line break passes; a wrong one is named", check_answer(answer("{=4*620}"), 1, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS) == []
      and any("Final sale Yes" in p for p in check_answer([{"rule": "final sale", "expr": "true", "evidence": "Final sale Yes"}], 0, {"decision": "Reject"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)))
check("a date to enter must be on the pages", any("pickup_date" in p and "2026-10-26" in p for p in check_answer(answer("{=4*620}", pickup="2026-10-26"), 3, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS)))
check("a date written another way, or inside a note, is matched by its day", check_answer([{"rule": "r", "expr": "true"}], 0, {"pickup_date": "19 Oct 2026", "note": "Pick-up on 2026-10-19"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS) == [])
later = answer("{=4*620*(100-20)/100}") + [{"rule": "otherwise: Reject", "known": True, "expr": "15 > 99", "evidence": ["nowhere at all"], "values": []}]
check("rules after the one that applies are not checked", check_answer(later, 3, None, WITH_POLICY, FIELDS, INSTRUCTIONS) == [] and len(check_answer(later, None, {}, WITH_POLICY, FIELDS, INSTRUCTIONS)) == 2)
warranty = check_answer([{"rule": "Defective past the warranty", "expr": "17 > 365"}, {"rule": "otherwise", "expr": "true"}], 1, {"decision": "Refund"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
check("a rule before the one that applies is checked (a warranty it never read)", len(warranty) == 1 and warranty[0].startswith('rule 1 ("Defective') and " 365," in warranty[0])
check("ids in a formula or a value are not numbers", check_answer([{"rule": "r", "expr": "'SKU-4456' == 'SKU-4456' and 'RMA-71519' != ''"}], 0, {"sku": "SKU-5621 {=4*620}"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS) == [])
check("values as a dict and as the model's list give the same verdict", check_answer(answer("x"), 3, {"refund_amount": "{=4*620*(100-15)/100}"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
      == check_answer(answer("x"), 3, [{"field": "refund_amount", "value": "{=4*620*(100-15)/100}"}], SEEN_9_OCT, FIELDS, INSTRUCTIONS) != [])
check("a date in a rule's formula may come from the instructions, not from nowhere",
      check_answer([{"rule": "r", "expr": "'2026-09-22' >= '2026-09-01'"}], 0, {}, SEEN_9_OCT, FIELDS, "Returns delivered before 01 Sept 2026 are out of policy.") == []
      and len(check_answer([{"rule": "r", "expr": "'2026-09-22' >= '2026-09-01'"}], 0, {}, SEEN_9_OCT, FIELDS, "")) == 1)
check("an answer with nothing to check is grounded", check_answer([], None, None, SEEN_9_OCT, FIELDS, INSTRUCTIONS) == [])

# ------------------------------------------------------------------ guesses in other shapes


# The problems of the change-of-mind answer with this refund value. Takes the value and the pages; returns the problems.
def refund_problems(value: str, pages: list = SEEN_9_OCT, evidence: list[str] | None = None) -> list[str]:
    return check_answer(answer(value, evidence=evidence), 3, None, pages, FIELDS, INSTRUCTIONS)


check("a date-shaped formula is arithmetic, so the 15 hidden in it is caught (1240-10-15 types 1215)",
      any(" 15," in p for p in refund_problems("{=1240-10-15}")) and any(" 15," in p for p in refund_problems("{=4960/2/15}")))
check("so is a 15 written in hex, octal or binary", all(any(" 15," in p for p in refund_problems(f"{{=4*620*(100-{x})/100}}")) for x in ("0xF", "0o17", "0b1111")))
huge = check_answer([{"rule": "r", "expr": "17 <= 1e9999999"}], 0, {}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
check("an absurd exponent is a problem, not a crash", len(huge) == 1 and "1E+9999999" in huge[0])
check("a worked-out number written without {= } is checked too (2108 is 2480 less a guessed 15%)",
      len(refund_problems("2108")) == 1 and "refund_amount has 2108" in refund_problems("2108")[0] and refund_problems("2480") == [])
note = check_answer([{"rule": "r", "expr": "17 <= 30"}], 0, {"decision": "Refund", "note": "Refund less 15% restocking fee"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)
check("so is a guessed figure in a note", len(note) == 1 and "note has 15" in note[0])
check("an honest note passes (17 days, the 20% fee, a {= } amount, a date, a quantity and a SKU)",
      check_answer([{"rule": "r", "expr": "17 <= 30"}], 0, {"note": "Changed mind after 17 days; Formal, opened: 20% fee; refund {=4*620*(100-20)/100}",
                                                           "pickup": "19 Oct 2026", "quantity": "4", "sku": "SKU-4456"}, WITH_POLICY, FIELDS, INSTRUCTIONS) == [])
CONTROLS = [{"key": "fit", "tag": "select", "type": "", "label": "Fit", "visible": True, "options": ["Size 43 wide", "Size 41"]},
            {"key": "extras", "tag": "input", "type": "checkbox", "label": "Extras", "visible": True, "options": ["Gift box 99", "Size 43 wide"]}]
check("a value that is one of its control's options is a choice, not a number the model wrote",
      check_answer([{"rule": "r", "expr": "true"}], 0, {"fit": "Size 43 wide", "extras": "Gift box 99, Size 43 wide"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS, CONTROLS) == []
      and len(check_answer([{"rule": "r", "expr": "true"}], 0, {"fit": "Size 43 wide"}, SEEN_9_OCT, FIELDS, INSTRUCTIONS)) == 1)
check("the fee taken from the wrong row of the policy table is caught by its quote",
      [p for p in refund_problems("{=4*620*(100-10)/100}", WITH_POLICY, ["Footwear - Formal 10%"]) if 'evidence "Footwear - Formal 10%"' in p] != [])

# An ordinary pager and a page count put 1-5 and 10 on the list page, so 15 = 10 + 5, 85 = 17 x 5 and 0.8 = 4 / 5 are all
# one step away: a percentage or a rate in a formula must be printed itself
PAGER_LIST = (LIST_PAGE[0], LIST_PAGE[1].replace("‹ Prev12345678910Next ›", "‹ Prev\n1\n2\n3\n4\n5\nNext ›\nPage 3 of 10"))
PAGER = [PAGER_LIST, RETURN_PAGE, ORDER_PAGE, PRODUCT_PAGE]
check("with an ordinary pager 15 is one step away, yet the 9 Oct failure is still caught",
      derivable(D(15), grounded_numbers(PAGER, FIELDS, INSTRUCTIONS)) and any("percentage 15%" in p for p in refund_problems("{=4*620*(100-15)/100}", PAGER)))
check("and so are 85 / 100, 2480 - 2480*15/100 and a 0.8 the pages never printed",
      any("percentage 85%" in p for p in refund_problems("{=2480*85/100}", PAGER)) and any("percentage 15%" in p for p in refund_problems("{=2480 - 2480*15/100}", PAGER))
      and any("rate 0.8" in p for p in refund_problems("{=2480*0.8}", PAGER)))
check("the honest answer still passes with the pager, however the fee is written",
      all(refund_problems(v, PAGER + [POLICY_PAGE], ["Footwear - Formal 20%"]) == [] for v in ("{=4*620*(100-20)/100}", "{=2480*80/100}", "{=2480*0.8}", "{=2480 - 2480*20/100}")))
check("a fraction printed at another scale is fine (0.5 from a 500 g limit)",
      check_answer([{"rule": "r", "expr": "0.42 <= 0.5"}], 0, {}, [("http://x/p", "Weight\n0.42 kg\nLimit\n500 g")], {}, "") == [])

# ------------------------------------------------------------------ dates to enter

AMBIGUOUS = [("http://x/r", "Next pick-up slot\n05/06/2026")]


# The problems of an answer that enters this pick-up date. Takes the date as written and the pages; returns the problems.
def pick(value: str, pages: list = AMBIGUOUS) -> list[str]:
    return check_answer([{"rule": "r", "expr": "true"}], 0, {"pickup": value}, pages, {}, "")


check("an unclear 05/06/2026 grounds neither reading, so neither guess passes", len(pick("2026-05-06")) == 1 and len(pick("2026-06-05")) == 1)
check("a clear date on the page settles it, and then only the right reading passes",
      pick("2026-06-05", [("http://x/r", AMBIGUOUS[0][1] + "\nDelivered on\n22/05/2026")]) == [] and len(pick("2026-05-06", [("http://x/r", AMBIGUOUS[0][1] + "\nDelivered on\n22/05/2026")])) == 1)
check("a value with an unclear or impossible date says so", "cannot be told apart" in pick("05/06/2026")[0] and "not a real date" in pick("2026-02-30", SEEN_9_OCT)[0])

# ------------------------------------------------------------------ long pages

ROW_COUNT = 30_000
BIG = [("http://x/list", "x" * 14_000 + "\n\n[table rows with their headings]\n" + "\n".join(
    f"Order: ORD-{10000 + i} | Date: {1 + i % 28} Oct 2026 | Amount: ₹{1000 + i:,} | Due: {1 + i % 12:02d}/{1 + i % 28:02d}/2026" for i in range(ROW_COUNT)))]
began = time.perf_counter()
big = check_answer([{"rule": "r", "expr": "17 <= 30", "evidence": ["Amount ₹30,999"]}], 0, {"due": "2026-10-05", "total": "{=4*620}"}, BIG, {}, "17 days, 620, 4")
check(f"a page with a {ROW_COUNT:,}-row table of dates ({len(BIG[0][1]):,} chars) is checked in time proportional to its length",
      big == [] and time.perf_counter() - began < 5)

# ---- 15 Oct review: a status rule needs a quote from a page; a 0 taken away must be read
CLAIM_ONLY = [("http://x/claims/CL-1", "Claim CL-1\nPolicy: POL-77\nLoss date: 2026-03-02\nClaimed amount: 48000")]
WITH_POLICY_PAGE = CLAIM_ONLY + [("http://x/policies/POL-77", "Policy POL-77\nStatus\nLapsed\nDeductible\n₹0")]
INS_CLAIMS = "Open the claim, then its policy. (1) If the policy is not Active, Decline. (5) Otherwise Approve: payout = amount claimed minus the deductible."
status_rule = [{"rule": "(1) policy not Active", "expr": "true", "evidence": [], "values": {"outcome": "Decline"}}]
check("a status rule decided as true with no quote is refused (the policy page was never read)",
      any("without a quote" in p for p in check_answer(status_rule, 0, None, CLAIM_ONLY, {}, INS_CLAIMS, require_quotes=True)))
check("  its quote must be on a page: a quote that only a page not read would show does not help",
      any("not on the pages" in p for p in check_answer([dict(status_rule[0], evidence=["Status Lapsed"])], 0, None, CLAIM_ONLY, {}, INS_CLAIMS, require_quotes=True)))
check("  with the policy page read and quoted, it is grounded",
      check_answer([dict(status_rule[0], evidence=["Status Lapsed"])], 0, None, WITH_POLICY_PAGE, {}, INS_CLAIMS, require_quotes=True) == [])
skipped = [{"rule": "(1) policy not Active", "expr": "false", "evidence": []}, {"rule": "(5) otherwise approve", "expr": "true", "evidence": []}]
check("a status rule decided as false before the one that applies needs its quote too; the 'otherwise' rule does not",
      [p[:6] for p in check_answer(skipped, 1, {"payout": "{= 48000 - 1000 }"}, CLAIM_ONLY, {}, INS_CLAIMS + " 1000", require_quotes=True)] == ["rule 1"])
check("  a rule with numbers is checked by its numbers, not by quotes; without require_quotes nothing changes",
      check_answer([{"rule": "(2) amount over 1000", "expr": "48000 > 1000", "evidence": []}], 0, None, CLAIM_ONLY, {}, INS_CLAIMS + " 1000", require_quotes=True) == []
      and check_answer(status_rule, 0, None, CLAIM_ONLY, {}, INS_CLAIMS) == [])
zero = [{"rule": "(5) otherwise approve", "expr": "true", "evidence": []}]
check("a deductible of 0 that no page prints is refused ('48000 - 0'); printed, it is fine",
      any("taking away 0" in p for p in check_answer(zero, 0, {"payout": "{= 48000 - 0 }"}, CLAIM_ONLY, {}, INS_CLAIMS))
      and check_answer(zero, 0, {"payout": "{= 48000 - 0 }"}, WITH_POLICY_PAGE, {}, INS_CLAIMS) == []
      and check_answer(zero, 0, {"payout": "{= 48000 - 1000 + 0.5 - 0.5 }"}, CLAIM_ONLY, {}, INS_CLAIMS + " 1000 0.5") == [])

print(f"\n{checks} checks passed")
