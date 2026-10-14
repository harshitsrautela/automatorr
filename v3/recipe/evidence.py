"""Checks that a model's decision rests on what the record's pages show, not on what the model assumed.

When the plan executor asks the model to decide a record, the model writes each rule of the task as a formula with the real
numbers put in ("17 <= 30", "4*620*(100-20)/100") and gives the values to enter, a worked-out number written as {= formula }.
A model that cannot see a number it needs sometimes writes one anyway (a percentage no page showed) and still says it could
see everything. Code catches that before anything is typed:

- every number in the formulas of the rules it checked, and in the values it enters (inside {= } or written out), must be
  printed on the record's pages, in the record itself or in the task instructions, or be one step of arithmetic from such
  numbers (2480 = 4 x 620, 0.2 = 20 / 100). A percentage or a rate (the 20 of "100 - 20" or "20 / 100", the 0.8 of
  "2480 * 0.8", a "20%" in a note) must itself be printed, because the small numbers every page has (a pager's 4, 5 and 10)
  put almost any guess one step away (15 = 10 + 5, 0.8 = 4 / 5);
- every date it enters must be printed on the pages or in the record, as one clear calendar day;
- every quote it gives as evidence must really be on a page: line breaks and spacing aside, but never joining a label to the
  value on another line and never skipping a number (so one table row's label cannot borrow the next row's value).

Page text is what plan_exec._quick_read reads: the visible text, then a "[table rows with their headings]" block. Dates, times
of day and ids (19 Oct 2026, 2026-10-19, 10:15, SKU-4456) are not numbers here. Nothing in this file opens a browser or calls
a model; every function is a plain function of the text, and takes time in proportion to its length."""

import datetime
import re
from collections.abc import Callable, Collection
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import Any

# Numbers any rule may use without a page showing them. In one step of arithmetic they only change a number's scale (x or / 10,
# 100, 1000) or take a percentage from 100, so a guess that happens to be half of a page's number (30 / 2 = 15) is not "read"
CONSTANTS: frozenset[Decimal] = frozenset(Decimal(n) for n in (0, 1, 2, 10, 100, 1000))
_SCALES = (Decimal(10), Decimal(100), Decimal(1000))
_HUNDRED = Decimal(100)
_DECIMAL_ERRORS = (InvalidOperation, DivisionByZero, Overflow)

# Dates, read the same way as the decision programs read them (recipe/facts.parse_date), but found anywhere in a long text:
# 2026-10-19, 19 Oct 2026, 19 October 2026, 05 Sept 2026, Oct 19, 2026, October 19 2026, 19/10/2026. A date never runs across
# a line break or a table cell, so only spaces may separate its parts
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MONTHS = {m: n for n, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_ISO_DATE = re.compile(r"(?<![0-9])([0-9]{4})[-/.]([0-9]{1,2})[-/.]([0-9]{1,2})(?![0-9])")
_DAY_MONTH_YEAR = re.compile(r"(?<![0-9A-Za-z])([0-9]{1,2})(?:st|nd|rd|th)?[  -]+" + _MONTH + r"\.?,?[  -]+([0-9]{4})(?![0-9])", re.I)
_MONTH_DAY_YEAR = re.compile(r"(?<![0-9A-Za-z])" + _MONTH + r"\.?[  ]+([0-9]{1,2})(?:st|nd|rd|th)?(?:,[  ]*|[  ]+)([0-9]{4})(?![0-9])", re.I)
_NUMERIC_DATE = re.compile(r"(?<![0-9/.-])([0-9]{1,2})[/.-]([0-9]{1,2})[/.-]([0-9]{4})(?![0-9])")
_NOT_A_DAY = "which is not a real date"
_UNCLEAR_DAY = "whose day and month cannot be told apart (write it as YYYY-MM-DD)"
# Text in quotes inside a formula ('2026-10-19'): recipe/formula.py reads it as text, so only there is a numeric date a date;
# outside quotes it computes 2026-10-19 as two subtractions
_STRING = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
# A time of day (10:15, 09:30:00): the minutes of a pick-up slot are not a number a rule would use
_TIME = re.compile(r"(?<![0-9:.])(?:[01]?[0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?(?![0-9]|[:.,][0-9])")

# A number token: thousands separators of both styles (2,480 and 1,25,000) on a page, decimals, never part of a word ("A12",
# "45days") or of a longer dotted run ("2.5.1"). In a formula commas are read the way recipe/formula.py reads them, and an
# integer may be written in hex, octal or binary (0xF, 0o17, 0b1111), which recipe/formula.py computes like any other number
_GROUPED = r"[0-9]{1,3}(?:,[0-9]{3})+(?![0-9])|[0-9]{1,3}(?:,[0-9]{2})+,[0-9]{3}(?![0-9])"
_PAGE_NUMBER = re.compile(r"(?<![\w.])\.[0-9]+(?![\w.])|(?<!\w)(?<![0-9]\.)(?:" + _GROUPED + r"|[0-9]+)(?:\.[0-9]+)?(?!\w)(?!\.[0-9])")
_FORMULA_NUMBER = re.compile(r"(?<![\w.])0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)(?![\w.])"
                             r"|(?<![\w.])\.[0-9]+(?:[eE][+-]?[0-9]+)?(?![\w.])|(?<!\w)(?<![0-9]\.)[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?(?!\w)(?!\.[0-9])")
# Digits that end an id joined by hyphens or slashes to letters before them: SKU-4456, ORD-10128, AB-26-04123 (not "30-day")
_ID_BEFORE = re.compile(r"[A-Za-z]\w*(?:[-/]\w+)*[-/]$")
# A currency code written straight onto the digits ("Rs.500", "INR2,480"): the code is dropped so the number stays a number
_CODE_ON_DIGITS = re.compile(r"(?<![A-Za-z])(?:rs|inr|usd|eur|gbp|aed|sgd)\.?(?=[0-9])", re.I)
# The number of a numbered paragraph or list item at the start of a line ("3. Payments are issued ..."): a place, not a fact
_LIST_MARK = re.compile(r"^[ \t]*[0-9]{1,2}[.)](?=[ \t]+\S)", re.M)
# A call to abs, min or max: recipe/formula.py then reads a comma between digits as separating arguments, not thousands
_CALL = re.compile(r"\b(abs|min|max)\s*\(", re.I)
# A worked-out number inside a value: {= formula }, doubled braces read the same (as recipe/formula.render_inline reads them)
_INLINE = re.compile(r"\{+=\s*([^{}]*?)\s*\}+")
# A number used as a percentage. In a formula: the 15 of "100 - 15" or "100 + 15" (not of "x*100 - 15") and of "15 / 100"; any
# fraction between 0 and 1 (the 0.85 of "2480 * 0.85") is a rate too. In a page's or a value's words: a number followed by % or
# "percent"
_HUNDRED_BEFORE = re.compile(r"(?:^|[^\w.*/])100(?:\.0*)?\s*[-+]\s*\(?\s*$")
_BY_HUNDRED_AFTER = re.compile(r"\s*/\s*100(?:\.0*)?(?![\w.])")
_PERCENT_AFTER = re.compile(r"\s*(?:%|per\s?cent\b)", re.I)
# Words and numbers of a quote or a page, for matching quotes: a number with separators becomes its plain digits
_TOKEN = re.compile(r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?(?![0-9])|[0-9]{1,3}(?:,[0-9]{2})+,[0-9]{3}(?:\.[0-9]+)?(?![0-9])|[0-9]+(?:\.[0-9]+)?|[^\W\d_]+")
_QUOTE_SLACK = 6  # a quote may be spread over this many extra tokens of a page (a label and its value on separate lines)

_NOT_SEEN = "which is not on the pages, in the record or in the task instructions, nor one step of arithmetic from numbers that are"
_RATE_NOT_SEEN = "which is not printed on the pages, in the record or in the task instructions (a percentage or a rate must be read, not worked out from other numbers)"

Span = tuple[int, int, tuple[str | None, str | None]]  # a date found in a text: start, end, its (day-first, month-first) readings
Dated = tuple[int, int, frozenset[str], str]  # a date as read: start, end, its ISO date (or none) and why it has none


class Grounded(set):
    """The numbers a decision may use (a set of Decimals: the numbers seen plus CONSTANTS) that also remembers which of them were
    really printed somewhere (`seen`), so derivable lets a constant no page showed only change a number's scale."""

    # Takes the numbers really seen; the set holds them and CONSTANTS.
    def __init__(self, seen: Collection[Decimal] = ()) -> None:
        super().__init__(set(seen) | CONSTANTS)
        self.seen = frozenset(seen)


# --------------------------------------------------------------------- reading text

# Turns any value (a page's text, a field's value, None) into text. Takes the value; returns text ("" for None).
def _text(value: Any) -> str:
    return "" if value is None else str(value)


# Writes a number as plain digits without a pointless ".0", using no decimal context (so an absurd size cannot fail); a number
# of absurd size keeps its exponent. Takes a Decimal; returns text ("2480", "0.2", "1E+9999999").
def _plain(n: Decimal) -> str:
    if not n.is_finite() or abs(n.adjusted()) > 40:
        return str(n)
    text = format(n, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


# Turns a number token of a quote or a page into plain digits with string steps only, so long ids stay exact ("1,25,000" ->
# "125000", "2,480.00" -> "2480", "05" -> "5"). Takes the token; returns its digits.
def _digits(token: str) -> str:
    whole, _dot, fraction = token.replace(",", "").partition(".")
    whole, fraction = whole.lstrip("0") or "0", fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


# Replaces every match of a pattern with spaces, keeping the text's length. Takes the pattern and the text; returns the text.
def _blank_all(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


# Blanks out parts of a text in one pass, keeping its length. Takes the text and spans (start, end, ...) in order and not
# overlapping; returns the text with spaces there.
def _blank(text: str, spans: list[Any]) -> str:
    pieces: list[str] = []
    at = 0
    for span in spans:
        start, end = span[0], span[1]
        pieces.append(text[at:start])
        pieces.append(" " * (end - start))
        at = end
    pieces.append(text[at:])
    return "".join(pieces)


# --------------------------------------------------------------------- dates

# Checks a day exists in the calendar. Takes year, month and day; returns the ISO date text, or None for an impossible date.
def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


# Reads a 2026-10-19 date. Takes the match; returns its (day-first, month-first) readings, the same one twice.
def _read_iso(m: re.Match[str]) -> tuple[str | None, str | None]:
    iso = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return iso, iso


# Reads a 19 Oct 2026 date. Takes the match; returns its (day-first, month-first) readings, the same one twice.
def _read_day_month(m: re.Match[str]) -> tuple[str | None, str | None]:
    iso = _iso(int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
    return iso, iso


# Reads an Oct 19, 2026 date. Takes the match; returns its (day-first, month-first) readings, the same one twice.
def _read_month_day(m: re.Match[str]) -> tuple[str | None, str | None]:
    iso = _iso(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
    return iso, iso


# Reads a 19/10/2026 date, which may be day first or month first. Takes the match; returns the (day-first, month-first)
# readings, None for a reading that is not a real date (19/10/2026 has only the first; 05/06/2026 has two).
def _read_numeric(m: re.Match[str]) -> tuple[str | None, str | None]:
    a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return _iso(year, b, a), _iso(year, a, b)


# The kinds of date in the order they are tried: a part of the text an earlier kind read is not read again
_DATE_KINDS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], tuple[str | None, str | None]]], ...] = (
    (_ISO_DATE, _read_iso), (_DAY_MONTH_YEAR, _read_day_month), (_MONTH_DAY_YEAR, _read_month_day), (_NUMERIC_DATE, _read_numeric))


# Finds every date written in a text, in one left-to-right pass per kind of date (each kind's matches are merged into the dates
# already found, so a long table of dates takes time in proportion to its length). Takes the text; returns (start, end,
# (day-first, month-first)) per date in order of position, impossible dates included (their readings are None).
def _date_candidates(text: str) -> list[Span]:
    spans: list[Span] = []
    for pattern, read in _DATE_KINDS:
        merged: list[Span] = []
        i = 0
        for m in pattern.finditer(text):
            while i < len(spans) and spans[i][1] <= m.start():
                merged.append(spans[i])
                i += 1
            if i < len(spans) and spans[i][0] < m.end():
                continue  # part of a date an earlier kind already read
            merged.append((m.start(), m.end(), read(m)))
        merged.extend(spans[i:])
        spans = merged
    return spans


# The order of day and month that a text's numeric dates show: "d" when some can only be day first (19/10/2026) and none only
# month first, "m" the other way round, "x" when both occur, "" when no date settles it. Takes the dates found; returns the order.
def _date_order(spans: list[Span]) -> str:
    orders = {"d" if day else "m" for _start, _end, (day, month) in spans if day != month and not (day and month)}
    return orders.pop() if len(orders) == 1 else ("x" if orders else "")


# Reads the dates found in a text as calendar days. A numeric date whose day and month order cannot be told (05/06/2026) takes
# the order its own text shows, else the given fallback order (the one the site's pages show), else it gives no day at all.
# Takes the dates found and the fallback order; returns (start, end, {ISO date} or empty, why it has none) per date.
def _settle(spans: list[Span], fallback: str = "") -> list[Dated]:
    order = _date_order(spans) or fallback
    out: list[Dated] = []
    for start, end, (day, month) in spans:
        if day == month:
            iso, why = day, ("" if day else _NOT_A_DAY)
        elif day and month:
            iso = day if order == "d" else month if order == "m" else None
            why = "" if iso else _UNCLEAR_DAY
        else:
            iso, why = day or month, ""
        out.append((start, end, frozenset([iso]) if iso else frozenset(), why))
    return out


# The dates of a formula: those inside quotes, and those spelled with a month name (never arithmetic); 2026-10-19 or 19/10/2026
# outside quotes is arithmetic to recipe/formula.py, so its parts are numbers. Takes the formula; returns the dates found, in order.
def _formula_date_candidates(text: str) -> list[Span]:
    strings = [m.span() for m in _STRING.finditer(text)]
    out: list[Span] = []
    j = 0
    for span in _date_candidates(text):
        start, end = span[0], span[1]
        while j < len(strings) and strings[j][1] <= start:
            j += 1
        quoted = j < len(strings) and strings[j][0] < start and end < strings[j][1]
        if quoted or re.search(r"[A-Za-z]", text[start:end]):
            out.append(span)
    return out


# The ISO dates written in a text: every date that reads as one calendar day. Takes the text and the fallback order of day and
# month (see _settle); returns a set of ISO dates.
def _dates_in(text: str, fallback: str = "") -> set[str]:
    return {iso for _start, _end, days, _why in _settle(_date_candidates(_text(text)), fallback) for iso in days}


# Every date on the pages and in the record's field values, and the order of day and month the pages show. Each page settles an
# unclear 05/06/2026 by its own clear numeric dates, else by those of all the pages (a site writes its dates one way); a field
# value only by its own. Takes the pages as [(address, text)] and the fields; returns (set of ISO dates, the pages' order).
def _page_dates(pages: list[tuple[str, str]], fields: dict[str, Any] | None) -> tuple[set[str], str]:
    per_page = [_date_candidates(_text(text)) for _url, text in pages or []]
    order = _date_order([span for spans in per_page for span in spans])
    found: set[str] = set()
    for spans in per_page:
        found |= {iso for _start, _end, days, _why in _settle(spans, order) for iso in days}
    for value in (fields or {}).values():
        found |= _dates_in(_text(value))
    return found, order


# Every date printed on the pages or in the record's field values, as ISO "YYYY-MM-DD" (19 Oct 2026, Oct 19, 2026, 2026-10-19,
# 19/10/2026). Like recipe/facts.parse_date, a numeric date whose day and month order cannot be told (05/06/2026) gives no date,
# unless clear numeric dates on its page (or, failing that, on the other pages) show the order the site uses. Takes the pages
# as [(address, text)] and the fields; returns a set of ISO dates.
def grounded_dates(pages: list[tuple[str, str]], fields: dict[str, Any] | None) -> set[str]:
    return _page_dates(pages, fields)[0]


# --------------------------------------------------------------------- numbers

# Reads one number token. Takes the token (digits with separators, a decimal, an exponent, or 0x/0o/0b digits); returns it as
# a non-negative Decimal, or None when it is not a finite number.
def _read_number(token: str) -> Decimal | None:
    try:
        if token[1:2] in ("x", "X", "o", "O", "b", "B"):
            value = Decimal(int(token, 0))
        else:
            value = Decimal(token.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return value.copy_abs() if value.is_finite() else None  # copy_abs needs no decimal context, so 1e999999999 cannot fail


# How a number is used where it is written: "percent" for a percentage between 0 and 100 (the 15 of "100 - 15", "15 / 100" or
# "15%"), "fraction" for a rate between 0 and 1 in a formula (the 0.85 of "2480 * 0.85"), else "". Takes the prepared text, the
# number's match, its value and whether the text is a formula; returns the use.
def _use_of(text: str, m: re.Match[str], value: Decimal, formula: bool) -> str:
    if not 0 < value < _HUNDRED:
        return ""
    if not formula:
        return "percent" if _PERCENT_AFTER.match(text, m.end()) else ""
    if _HUNDRED_BEFORE.search(text, max(0, m.start() - 20), m.start()) or _BY_HUNDRED_AFTER.match(text, m.end()):
        return "percent"
    return "fraction" if value < 1 else ""


# Reads the numbers of a text and how each is used, skipping dates, times, ids and paragraph numbers. Takes the text and whether
# it is a formula (commas then follow recipe/formula.py: they separate the arguments of abs/min/max, else they group thousands;
# a numeric date outside quotes is arithmetic, so its parts are numbers); returns (number, use) pairs in order (see _use_of),
# the numbers non-negative (a minus sign is an operator or a direction, not part of what was read).
def _number_uses(text: str, formula: bool = False) -> list[tuple[Decimal, str]]:
    s = _blank_all(_CODE_ON_DIGITS, _text(text))
    if formula:
        s = re.sub(r"(?<=[0-9A-Fa-f])_(?=[0-9A-Fa-f])", "", s)  # 1_000 is how Python writes 1000
        if not _CALL.search(s):
            s = re.sub(r"(?<=[0-9]),(?=[0-9])", "", s)  # 6,50,000 -> 650000, exactly as the formula is computed
        s = _blank(s, _formula_date_candidates(s))
    else:
        s = _blank_all(_LIST_MARK, _blank_all(_TIME, _blank(s, _date_candidates(s))))
    out: list[tuple[Decimal, str]] = []
    for m in (_FORMULA_NUMBER if formula else _PAGE_NUMBER).finditer(s):
        if _ID_BEFORE.search(s, max(0, m.start() - 40), m.start()):
            continue
        value = _read_number(m.group(0))
        if value is not None:
            out.append((value, _use_of(s, m, value, formula)))
    return out


# Every number printed on the pages, in the record's field values and in the task instructions (no constants). Takes the pages
# as [(address, text)], the record's fields and the instructions; returns the set of numbers.
def _seen_numbers(pages: list[tuple[str, str]], fields: dict[str, Any] | None, instructions: str | None) -> set[Decimal]:
    texts = [_text(text) for _url, text in pages or []] + [_text(v) for v in (fields or {}).values()] + [_text(instructions)]
    return {n for text in texts for n, _use in _number_uses(text)}


# The numbers a decision may use as they are: every number token on the pages (their text and their table rows; ₹1,25,000 ->
# 125000, 20% -> 20; dates, times and ids give none), in the record's field values and in the task instructions, plus CONSTANTS.
# Takes the pages as [(address, text)], the fields and the instructions; returns a set of Decimals (a Grounded set, which
# also remembers which numbers were really seen).
def grounded_numbers(pages: list[tuple[str, str]], fields: dict[str, Any] | None, instructions: str | None) -> Grounded:
    return Grounded(_seen_numbers(pages, fields, instructions))


# Whether a number is grounded or one step of arithmetic from grounded numbers: a+b, a-b, a*b or a/b for two numbers that
# were really seen, or a seen number changed in scale (x or / 10, 100, 1000) or taken from 100 as a percentage (85 = 100 - 15,
# 0.8 = 1 - 20/100). A bare constant takes part only in those last steps. Takes the number, the grounded set and, optionally,
# the numbers really seen (default: what a Grounded set remembers, else the set without CONSTANTS); returns True or False.
# O(len(seen)) set lookups.
def derivable(n: Decimal, grounded: Collection[Decimal], seen: Collection[Decimal] | None = None) -> bool:
    try:
        n = (n if isinstance(n, Decimal) else Decimal(str(n))).copy_abs()
    except (InvalidOperation, ValueError):
        return False
    if not n.is_finite():
        return False
    pool = grounded if isinstance(grounded, (set, frozenset)) else set(grounded)
    if n in pool:
        return True
    if n == 0:
        return False  # no sum, product or ratio of numbers that were read makes a zero that was not read itself
    seen = seen if seen is not None else getattr(grounded, "seen", None)
    if seen is None:
        data: Collection[Decimal] = set(pool) - CONSTANTS
    else:
        data = seen if isinstance(seen, (set, frozenset)) else set(seen)
    for a in data:
        try:
            # n = a + b, n = a - b, n = a * b, n = a / b; a zero never divides, and 0 / b is never a nonzero n
            if n - a in data or a - n in data or (a != 0 and (n / a in data or a / n in data)):
                return True
        except _DECIMAL_ERRORS:
            continue
    try:
        if any(n * c in data or n / c in data for c in _SCALES):
            return True
        if _HUNDRED - n in data:
            return True  # what is left of 100 after a percentage
        return n < 2 and abs(n * _HUNDRED - _HUNDRED) in data  # 1 minus or plus a percentage, as a fraction
    except _DECIMAL_ERRORS:
        return False


# Whether a percentage or a rate is printed somewhere, with no other number's help: a percentage as it is, as a fraction, or as
# what is left of 100 (a 20 or a 0.2 printed lets a formula use 20 or 80); a fraction also changed in scale (a 500 printed lets
# it use 0.5). Takes the number, its use ("percent" or "fraction") and the numbers really seen; returns True or False.
def _rate_printed(n: Decimal, use: str, seen: Collection[Decimal]) -> bool:
    try:
        if use == "percent":
            forms = [n, n / _HUNDRED, _HUNDRED - n, (_HUNDRED - n) / _HUNDRED]
        else:
            forms = [n, *(n * c for c in _SCALES), _HUNDRED - n * _HUNDRED, 1 - n]
    except _DECIMAL_ERRORS:
        return False
    return any(form in seen for form in forms)


# The numbers inside a formula: a rule's expr, or the inside of one {= } part of a value. Digits of ids (SKU-4456) and of dates
# in quotes ('2026-10-19') are not numbers, but 2026-10-15 outside quotes is (recipe/formula.py subtracts it); commas follow
# recipe/formula.py ("6,50,000" is 650000, "max(1,2)" is 1 and 2); 0xF is 15. Takes the formula text; returns the numbers in
# the order written, as non-negative Decimals.
def formula_numbers(text: str) -> list[Decimal]:
    return [n for n, _use in _number_uses(_text(text), formula=True)]


# The worked-out parts of a value: the inside of each {= formula } ("received {=20+15+5}" -> ["20+15+5"]). Takes the value;
# returns the formulas in order (an empty list for a value without any).
def inline_formulas(value: Any) -> list[str]:
    return [m.group(1) for m in _INLINE.finditer(_text(value))]


# --------------------------------------------------------------------- quotes

# Splits a text into lower-case words and numbers for matching quotes; a number with separators or trailing zeros becomes its
# plain digits ("1,25,000" -> "125000", "2,480.00" -> "2480", "05" -> "5"). Takes the text; returns the tokens in order.
def _tokens(text: str) -> list[str]:
    return [_digits(t) if t[0].isdigit() else t for t in (m.group(0) for m in _TOKEN.finditer(_text(text).lower()))]


# Prepares one text for finding quotes in. Takes the text; returns its tokens, the line each token is on, and where each token
# occurs ({token: positions}).
def _stream(text: str) -> tuple[list[str], list[int], dict[str, list[int]]]:
    low = _text(text).lower()
    tokens: list[str] = []
    lines: list[int] = []
    where: dict[str, list[int]] = {}
    line, at = 0, 0
    for m in _TOKEN.finditer(low):
        line += low.count("\n", at, m.start())
        at = m.start()
        token = _digits(m.group(0)) if m.group(0)[0].isdigit() else m.group(0)
        where.setdefault(token, []).append(len(tokens))
        tokens.append(token)
        lines.append(line)
    return tokens, lines, where


# Prepares the pages and the record's field values for finding quotes in. Takes the pages as [(address, text)] and the fields;
# returns one prepared stream (see _stream) per page and per field value.
def _streams(pages: list[tuple[str, str]], fields: dict[str, Any] | None) -> list[tuple[list[str], list[int], dict[str, list[int]]]]:
    return [_stream(_text(text)) for _url, text in pages or []] + [_stream(_text(v)) for v in (fields or {}).values()]


# Whether a quote's tokens occur in a text starting at one token, in order and within len(quote) + 6 tokens. Between two parts
# of the quote the text may hold only extra words on the same line ("Opened:" inside one table row); a number or a line break
# there means the quote joins things that do not belong together (a label and the next row's or the next pair's value). Two
# parts may sit on neighbouring lines when nothing lies between them ("Final sale\nNo"). Takes the quote's tokens, the text's
# tokens and lines, and the start; returns True or False.
def _matches_at(quote: list[str], tokens: list[str], lines: list[int], start: int) -> bool:
    matched, last = 1, start
    for i in range(start + 1, min(len(tokens), start + len(quote) + _QUOTE_SLACK)):
        if matched == len(quote):
            break
        if tokens[i] == quote[matched]:
            if i > last + 1 and lines[i] != lines[last]:
                return False  # skipped words on one line, then went on to another
            matched, last = matched + 1, i
        elif tokens[i][0].isdigit() or lines[i] != lines[last]:
            return False  # a number, or a word of another line, between two parts of the quote
    return matched == len(quote)


# Whether a quote's tokens occur in one of the prepared texts (see _matches_at). Takes the quote's tokens and the prepared
# texts; returns True or False (False for a quote without words or numbers).
def _quote_in(quote: list[str], streams: list[tuple[list[str], list[int], dict[str, list[int]]]]) -> bool:
    if not quote:
        return False
    return any(_matches_at(quote, tokens, lines, start) for tokens, lines, where in streams for start in where.get(quote[0], []))


# The cells of each line of a text that is a table row with its headings ("Heading: cell | Heading: cell | ..."), each cell
# as (its tokens, the tokens of its value: what follows "Heading:", or the whole cell). Takes the text; returns one list of
# cells per such line.
def _row_cells(text: str) -> list[list[tuple[list[str], list[str]]]]:
    rows: list[list[tuple[list[str], list[str]]]] = []
    for line in _text(text).splitlines():
        parts = [p for p in re.split(r"\s+\|\s+", line) if p.strip()]
        if len(parts) < 3:
            continue
        cells = []
        for part in parts:
            head, colon, value = part.partition(":")
            cells.append((_tokens(part), _tokens(value) if colon and value.strip() else _tokens(part)))
        rows.append(cells)
    return rows


# Whether a quote is whole cells of one table row that leaves out cells between them ("City tier: Tier 1 | L2: 5,500" of the
# row "City tier: Tier 1 | L1: 4,000 | L2: 5,500"): the quote must start with the row's first cell (its name: the whole cell
# or its value) and go on with whole cells of the same row, in order (a cell as printed, or just its value), never a part of
# a cell and never a cell of another row. Takes the quote's tokens and the texts (pages and field values); returns True or False.
def _row_quote_in(quote: list[str], texts: list[str]) -> bool:
    if not quote:
        return False
    for text in texts:
        for cells in _row_cells(text):

            # Whether quote[at:] is made of whole cells taken in order from cells[start:].
            def rest(at: int, start: int) -> bool:
                if at == len(quote):
                    return True
                for c in range(start, len(cells)):
                    for piece in {tuple(cells[c][0]), tuple(cells[c][1])}:
                        if piece and tuple(quote[at:at + len(piece)]) == piece and rest(at + len(piece), c + 1):
                            return True
                return False

            first = cells[0]
            for piece in {tuple(first[0]), tuple(first[1])}:
                if piece and tuple(quote[:len(piece)]) == piece and rest(len(piece), 1):
                    return True
    return False


# Whether a quote the model gave as evidence is on one of the pages or in a field value: its lower-case words and numbers
# (separators aside) in order, close together, never across a number or from one line's label to another line's value
# ("Final sale No" matches "Final sale\nNo", but not "Final sale\nYes" two lines further); or whole cells of one table row
# with its headings, starting with the row's first cell, that leave out the cells between them ("City tier: Tier 1 | L2:
# 5,500"; see _row_quote_in). Takes the quote, the pages as [(address, text)] and the fields; returns True or False.
def quote_found(quote: str, pages: list[tuple[str, str]], fields: dict[str, Any] | None) -> bool:
    tokens = _tokens(quote)
    return _quote_in(tokens, _streams(pages, fields)) or _row_quote_in(tokens, [_text(t) for _u, t in pages or []])


# --------------------------------------------------------------------- one answer

# A 0 taken away in a formula ("48000 - 0", "x - 0.00"), not part of a longer number
_ZERO_TAKEN = re.compile(r"-\s*0+(?:\.0*)?(?![\d.])")
# The words that start an "otherwise" rule (the one that applies when no other does): it rests on no fact of its own
_OTHERWISE = re.compile(r"\b(?:otherwise|else|in (?:all |any )?other cases?|by default|none of the (?:above|other))\b", re.I)


# Whether a check is an "otherwise" rule: its rule text says so. Takes the check; returns True or False.
def _otherwise(check: dict[str, Any]) -> bool:
    return _OTHERWISE.search(_text(check.get("rule"))) is not None


# Names a rule in a problem: its place and the start of its text. Takes the rule's index and its check; returns the name.
def _rule_label(index: int, check: dict[str, Any]) -> str:
    rule = re.sub(r"\s+", " ", _text(check.get("rule"))).strip()
    if not rule:
        return f"rule {index + 1}"
    return f'rule {index + 1} ("{rule[:60]}{"..." if len(rule) > 60 else ""}")'


# The values of the rule that applies, as (control key, raw value) pairs. Takes the values as given ({key: value}, or the
# model's list of {"field", "value"}; None to take them from the applying check) and the checks; returns the pairs.
def _value_items(values_raw: Any, checks: list[Any], applying_index: int | None) -> list[tuple[str, str]]:
    raw = values_raw
    if raw is None and applying_index is not None and 0 <= applying_index < len(checks) and isinstance(checks[applying_index], dict):
        raw = checks[applying_index].get("values")
    if isinstance(raw, dict):
        return [(str(k), _text(v)) for k, v in raw.items()]
    items: list[tuple[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            items.append((_text(item.get("field", item.get("key", ""))).strip(), _text(item.get("value"))))
    return items


# The evidence quotes on a check: a list of texts (one text is read as a list of one). Takes the check; returns the quotes.
def _quotes(check: dict[str, Any]) -> list[str]:
    evidence = check.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence]
    return [q for q in (evidence or []) if isinstance(q, str) and q.strip()]


# The option labels of each control, so a chosen option ("3-5 days") is not read as numbers the model wrote. Takes the controls
# as read at run time ([{"key", "options", ...}], or None); returns {control key: {option label, lower case, spaces collapsed}}.
def _options_by_key(controls: list[dict[str, Any]] | None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for control in controls or []:
        if isinstance(control, dict) and isinstance(control.get("options"), list):
            out.setdefault(_text(control.get("key")), set()).update(" ".join(_text(o).split()).lower() for o in control["options"])
    return out


# Whether a value is one of its control's options, or (a group of checkboxes) a ", "-joined list of them. Takes the value and
# the control's option labels (as _options_by_key gives them); returns True or False.
def _is_choice(value: str, options: set[str]) -> bool:
    text = " ".join(value.split()).lower()
    return bool(options) and bool(text) and (text in options or all(p.strip() in options for p in text.split(",")))


# The problems with the numbers of one formula or one value's words. Takes how a problem starts ("rule 4 (...): its formula
# uses"), the text, whether it is a formula, the grounded numbers and what a problem about a written-out number adds; returns
# the problems: a number that is not derivable, or a percentage or rate that is not printed.
def _number_problems(start: str, text: str, formula: bool, grounded: Grounded, advice: str = "") -> list[str]:
    problems: list[str] = []
    for n, use in dict.fromkeys(_number_uses(text, formula)):
        if not derivable(n, grounded):
            problems.append(f"{start} {_plain(n)}, {_NOT_SEEN}{advice}")
        elif use and not _rate_printed(n, use, grounded.seen):
            what = f"the percentage {_plain(n)}%" if use == "percent" else f"the rate {_plain(n)}"
            problems.append(f"{start} {what}, {_RATE_NOT_SEEN}")
    return problems


# Checks a model's answer for one record against its pages, for the rules up to and including the one that applies: every
# number in their formulas (expr) must be grounded or one step from grounded numbers (derivable), as must every number in the
# applying rule's values, inside {= } or written out (a value that is one of its control's options is a choice, not a number);
# a number used as a percentage must be printed itself; a date in those values must be on the pages or in the record as one
# clear day (a date in a formula, in quotes, may also come from the instructions); every evidence quote must be found; a 0
# taken away in a value's formula ("48000 - 0") must be printed; with require_quotes, a rule (other than an "otherwise" one)
# whose formula has no number or date (true or false: a status) must give a quote that is on a page. Takes
# the checks (the model's list of {"rule", "expr", "values", "evidence"?}), the index of the rule that applies (None: all of
# them), that rule's raw values ({key: value} or the model's list; None: taken from its check), the pages as [(address,
# text)], the fields, the task instructions, optionally the form's controls as read at run time (for their options) and
# require_quotes; returns the problems in plain words (an empty list = grounded).
def check_answer(
    checks: list[dict[str, Any]],
    applying_index: int | None,
    values_raw: dict[str, Any] | list[dict[str, Any]] | None,
    pages: list[tuple[str, str]],
    fields: dict[str, Any] | None,
    instructions: str | None,
    controls: list[dict[str, Any]] | None = None,
    require_quotes: bool = False,
) -> list[str]:
    checks = list(checks or [])
    grounded = grounded_numbers(pages, fields, instructions)
    dates, order = _page_dates(pages, fields)
    rule_dates = dates | _dates_in(_text(instructions))
    options = _options_by_key(controls)
    streams: list[tuple[list[str], list[int], dict[str, list[int]]]] | None = None  # prepared only when a quote needs them
    stop = len(checks) if applying_index is None else max(0, min(applying_index + 1, len(checks)))
    problems: list[str] = []
    page_streams: list[tuple[list[str], list[int], dict[str, list[int]]]] | None = None
    for index, check in enumerate(checks[:stop]):
        if not isinstance(check, dict):
            continue
        label = _rule_label(index, check)
        expr = _text(check.get("expr"))
        problems += _number_problems(f"{label}: its formula uses", expr, True, grounded)
        if require_quotes and not _otherwise(check) and not _number_uses(expr, formula=True) and not _formula_date_candidates(expr):
            # a condition written as true or false (a status, a yes/no) has nothing code can check but its quotes: at least
            # one of them must be on a page (a page that was never read cannot be quoted)
            if page_streams is None:
                page_streams = _streams(pages, None)
            if not any(_quote_in(_tokens(q), page_streams) or _row_quote_in(_tokens(q), [_text(t) for _u, t in pages or []]) for q in _quotes(check)):
                problems.append(f"{label}: it is decided as {expr.strip() or 'true or false'} without a quote of the page text it rests on")
        for start, end, days, why in _settle(_formula_date_candidates(expr), order):
            if why:
                problems.append(f"{label}: its formula uses the date {expr[start:end]}, {why}")
            elif not days & rule_dates:
                problems.append(f"{label}: its formula uses the date {expr[start:end]}, which is not on the pages, in the record or in the task instructions")
        for quote in _quotes(check):
            if streams is None:
                streams = _streams(pages, fields)
            if not _quote_in(_tokens(quote), streams) and not _row_quote_in(_tokens(quote), [_text(t) for _u, t in pages or []]):
                problems.append(f'{label}: the evidence "{quote.strip()[:80]}" is not on the pages or in the record')
    for key, raw in _value_items(values_raw, checks, applying_index):
        for part in inline_formulas(raw):
            problems += _number_problems(f"the value for {key} is worked out with", part, True, grounded)
            if _ZERO_TAKEN.search(part) and Decimal(0) not in grounded.seen:
                # "48000 - 0": a deductible or a fee of 0 is a number like any other, to be read, not assumed
                problems.append(f"the value for {key} is worked out by taking away 0, which is not printed on the pages, in the record or in the task instructions (an amount taken away, such as a deductible, must be read, not assumed)")
        written = _INLINE.sub(" ", raw)
        if _is_choice(written, options.get(key, set())):
            continue
        problems += _number_problems(f"the value for {key} has", written, False, grounded, "; a worked-out number is written as {= formula }")
        for start, end, days, why in _settle(_date_candidates(written), order):
            if why:
                problems.append(f"the value for {key} has the date {written[start:end]}, {why}")
            elif not days & dates:
                problems.append(f"the value for {key} has the date {written[start:end]}, which is not on the pages or in the record")
    return list(dict.fromkeys(problems))
