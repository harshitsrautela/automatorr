"""Reads facts off page text by code: numbers, dates, the value printed next to a label, and rows of tables.

A decision program (recipe/program.py) names the facts it needs and where each one is printed: a record field, the value next
to a label on a page, a cell of a table picked by other cells, or a fixed text. This file finds them in the text the plan
executor reads from each page (plan_exec._quick_read): the page's visible text, then, for tables that have headings, a block
"[table rows with their headings]" with one line per row ("Heading: cell | Heading: cell"). Nothing here opens a browser or
calls a model; everything is a plain function of the text, so it gives the same answer every time."""

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from recipe.plan import shape_matches, url_shape

TABLE_MARK = "[table rows with their headings]"
FACT_TYPES = ("number", "date", "text", "list", "bool")
SOURCE_KINDS = ("field", "label", "table", "const")
# How a table's rows are picked: by a cell's text (eq, contains, in) or by comparing numbers or dates (lt, le, gt, ge)
WHERE_OPS = ("eq", "contains", "in", "lt", "le", "gt", "ge")
AGGREGATES = ("first", "sum", "count", "list", "max", "min")

_CURRENCY = re.compile(r"(?<![A-Za-z])(?:rs\.?|inr|usd|eur|gbp|aed|sgd)(?![A-Za-z])|[₹$€£]", re.I)
# A number with thousands separators in either style (2,480 and 1,25,000) or none, then decimals, then "%" or one unit word
_NUMBER_TEXT = re.compile(r"([+-]?)\s*(\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})+|\d+)?(\.\d+)?\s*(%|[A-Za-z]+\.?)?")
# Words that change a number's size ("2.5 lakh"): reading only the digits would be wrong, so such a text is not read as a number
_MAGNITUDE = {"k", "m", "b", "l", "mn", "bn", "lac", "lacs", "lakh", "lakhs", "cr", "crore", "crores", "thousand", "million", "billion"}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MONTHS = {m: n for n, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_DAY_MONTH_YEAR = re.compile(r"(?<![0-9A-Za-z])(\d{1,2})(?:st|nd|rd|th)?[\s-]+" + _MONTH + r"\.?,?[\s-]+(\d{4})(?!\d)", re.I)
_MONTH_DAY_YEAR = re.compile(r"(?<![0-9A-Za-z])" + _MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I)
_NUMERIC_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
# A weekday's name, which may stand beside a date ("Tue, 19 Oct 2026") without making the text more than that date
_WEEKDAY = re.compile(r"(?<![A-Za-z])(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\.?(?![A-Za-z])", re.I)
_YES_WORDS = {"yes", "true", "y"}
_NO_WORDS = {"no", "false", "n"}
_BLANK = re.compile(r"\{([A-Za-z_]\w*)\}")
# Marks that mean nothing special in a pattern outside brackets, so a value's shape keeps them as they are ("ORD-99751")
_PLAIN_MARKS = set("-_ :/,#@")


# Collapses every run of whitespace into one space and trims the ends. Takes any value; returns text ("" for None).
def norm_text(text: Any) -> str:
    return re.sub(r"\s+", " ", "" if text is None else str(text)).strip()


# Writes a number as plain digits without a pointless ".0" or exponent: Decimal("2480.00") -> "2480", Decimal("12.50") -> "12.5".
def number_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


# Writes a fact's value as text for a template: a number as plain digits, a list as its items sorted and joined by ", ", true
# and false as "yes" and "no", a date as it is (ISO). Takes the value; returns the text.
def fact_text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return number_text(value)
    if isinstance(value, (int, float)):
        return number_text(Decimal(repr(value)))
    if isinstance(value, (list, tuple, set)):
        return ", ".join(sorted(fact_text(v) for v in value))
    return norm_text(value)


# The number in a short text and the unit word after it: "45 days" -> (45, "day"), "₹2,480" -> (2480, ""), "20%" -> (20, "").
# Currency signs and codes and "%" are not units; a unit is kept in lower case without a plural "s", so "1 night" and "2 nights"
# count the same thing. Takes the text (or a number); returns (the Decimal, the unit or ""), or None when parse_number gives None.
def number_and_unit(text: Any) -> tuple[Decimal, str] | None:
    if isinstance(text, bool) or text is None:
        return None
    if isinstance(text, Decimal):
        return (text, "") if text.is_finite() else None
    if isinstance(text, int):
        return Decimal(text), ""
    if isinstance(text, float):
        return (Decimal(repr(text)), "") if text == text and abs(text) != float("inf") else None
    s = norm_text(text).replace("−", "-").replace("–", "-")
    s = _CURRENCY.sub(" ", s)
    s = re.sub(r"/-$", "", s.strip()).strip()  # "₹500/-"
    m = _NUMBER_TEXT.fullmatch(s)
    if not m or not (m.group(2) or m.group(3)):
        return None
    unit = (m.group(4) or "").rstrip(".").lower()
    if unit in _MAGNITUDE:
        return None
    try:
        value = Decimal((m.group(2) or "0").replace(",", "") + (m.group(3) or ""))
    except InvalidOperation:
        return None
    if unit == "%":
        unit = ""
    elif len(unit) > 2 and unit.endswith("s") and not unit.endswith("ss"):
        unit = unit[:-1]
    return (-value if m.group(1) == "-" else value), unit


# The single number in a short text: currency signs and codes, thousands separators of both styles, "%" and one trailing unit
# word ("45 days") are allowed around it; a minus sign and decimals are kept. Takes the text (or a number); returns a Decimal, or
# None when the text holds no number, more than one, other words, or a word that scales it ("2.5 lakh").
def parse_number(text: Any) -> Decimal | None:
    pair = number_and_unit(text)
    return None if pair is None else pair[0]


# Two values as numbers to compare, only when they count the same thing: the same unit word or none on both ("₹2,480" and
# "2480", "1 night" and "2 nights"; not "1 Month" and "1 Year", "1 King" and "1 Queen", "2 weeks" and "10 days", whose digits
# alone would wrongly match or order). Takes the two values; returns the two Decimals, or None.
def same_unit_numbers(a: Any, b: Any) -> tuple[Decimal, Decimal] | None:
    pa, pb = number_and_unit(a), number_and_unit(b)
    if pa is None or pb is None or pa[1] != pb[1]:
        return None
    return pa[0], pb[0]


# Takes a year, a month and a day; returns the ISO date, or None when there is no such day (31 Feb).
def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


# Every date written in a text, each place read once (the first form that reads it wins). Takes the text; returns
# [((start, end), the ISO date or None for an impossible or ambiguous one)].
def _dates_in(s: str) -> list[tuple[tuple[int, int], str | None]]:
    found: list[tuple[tuple[int, int], str | None]] = []

    # Books one date found at a match, unless the place is part of a date already read.
    def add(match: re.Match[str], iso: str | None) -> None:
        if any(a < match.end() and match.start() < b for (a, b), _iso_date in found):
            return
        found.append((match.span(), iso))

    for m in _ISO_DATE.finditer(s):
        add(m, _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DAY_MONTH_YEAR.finditer(s):
        add(m, _iso(int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
    for m in _MONTH_DAY_YEAR.finditer(s):
        add(m, _iso(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2))))
    for m in _NUMERIC_DATE.finditer(s):
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a == b or (a > 12) != (b > 12):
            add(m, _iso(year, b, a) if a > 12 or a == b else _iso(year, a, b))
        else:
            add(m, None)  # 05/06/2026: the 5th of June or the 6th of May
    return found


# The one date in a short text, as ISO "YYYY-MM-DD". Reads 2026-10-19, 19 Oct 2026, 19 October 2026, 05 Sept 2026, Oct 19, 2026,
# October 19 2026, and 19/10/2026 or 10/19/2026 only when the order of day and month cannot be mistaken (one part is over 12, or
# both are the same). Returns None when there is no date, an impossible one, an ambiguous one, or two different dates.
def parse_date(text: Any) -> str | None:
    s = norm_text(text)
    if not s:
        return None
    found = [iso for _span, iso in _dates_in(s)]
    if not found or None in found or len(set(found)) != 1:
        return None
    return found[0]


# The date a text is, when the text is nothing but that date (a weekday's name may stand beside it): "Tue, 19 Oct 2026" ->
# "2026-10-19", but "INV-2026-10-19-0001" or "Due 19 Oct 2026" -> None, so an id or a phrase that holds a date is never taken
# for the date when two texts are compared. Takes the text; returns the ISO date or None.
def date_alone(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    s = norm_text(text)
    iso = parse_date(s)
    if iso is None:
        return None
    rest = s
    for (a, b), _iso_date in sorted(_dates_in(s), reverse=True):
        rest = rest[:a] + " " + rest[b:]
    return None if re.search(r"[^\W_]", _WEEKDAY.sub(" ", rest)) else iso


# --------------------------------------------------------------------- tables and labels

# Splits page text into its plain lines and the lines of its "[table rows with their headings]" block. A later marker line
# ("[frame]") ends the block and what follows is plain text again. Takes the page text; returns (plain lines, row lines).
def _split_block(text: str) -> tuple[list[str], list[str]]:
    plain: list[str] = []
    rows: list[str] = []
    in_block = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == TABLE_MARK:
            in_block = True
            continue
        if in_block and re.fullmatch(r"\[[^\]]{1,60}\]", stripped):
            in_block = False
            continue
        if in_block:
            if stripped:
                rows.append(stripped)
        else:
            plain.append(line)
    return plain, rows


# A part of a table-rows line that starts with its heading: "Status: Shipped" -> ("Status", "Shipped").
_HEADED = re.compile(r"^([^:|]{1,80}?):(?:\s(.*))?$")


# One table read off a page: its headings, its rows ({heading: cell}) and whether it was read cleanly. A table is not readable
# when a cell that holds a line break could not be put back together, so its rows may be cut or mixed up; facts read from it
# are then unknown rather than wrong.
@dataclass
class Table:
    heads: tuple[str, ...]
    rows: list[dict[str, str]] = field(default_factory=list)
    readable: bool = True


# Reads one line of the table-rows block ("Category: Footwear | Opened: 10%") into {heading: cell}. A cell printed without
# a heading gets the key "#n" (its place in the row).
def _block_row(line: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for n, part in enumerate(line.split(" | "), start=1):
        m = _HEADED.match(part)
        key, cell = (norm_text(m.group(1)), norm_text(m.group(2))) if m else (f"#{n}", norm_text(part))
        if key in row:
            key = f"{key} ({n})"
        row[key] = cell
    return row


# Takes one part of a table-rows line; returns the heading it starts with ("Status: Shipped" -> "Status"), or None.
def _heading_of(part: str) -> str | None:
    m = _HEADED.match(part.strip())
    return (norm_text(m.group(1)) or None) if m else None


# The tables of the "[table rows with their headings]" block, in order. The browser prints a cell (or a heading) that holds a
# line break over several lines, so a line that does not start a row is put back onto the row before it: a row starts with a
# heading of the block ("Item: Socks"), or is shaped like a row already seen, or has a heading on every part. A line that
# starts like "Word: text" where Word is no heading of the block may be either, so the table it is put onto is marked
# unreadable; so is a table next to a one-row piece holding only some of its headings (a row cut in two). Takes the block's
# lines; returns the tables (consecutive rows with the same headings are one table).
def _block_tables(lines: list[str]) -> list[Table]:
    sigs = [tuple(_heading_of(p) for p in line.split(" | ")) for line in lines]
    firsts = Counter(sig[0] for sig in sigs if sig[0])
    known = {h for sig in sigs for h in sig[1:] if h} | {h for h, n in firsts.items() if n >= 2}
    if sigs and sigs[0][0]:
        known.add(sigs[0][0])
    texts: list[str] = []
    doubtful: set[int] = set()  # rows that took a line that may have been a row of its own
    row_shapes: set[tuple[str | None, ...]] = set()
    for line, sig in zip(lines, sigs):
        if not texts or sig[0] in known or sig in row_shapes or (len(sig) >= 2 and all(sig)):
            texts.append(line)
            row_shapes.add(sig)
            continue
        if sig[0] is not None:
            doubtful.add(len(texts) - 1)
        texts[-1] += " " + line
    tables: list[Table] = []
    for n, text in enumerate(texts):
        row = _block_row(text)
        if not any(row.values()):
            continue
        if not tables or tables[-1].heads != tuple(row):
            tables.append(Table(tuple(row)))
        tables[-1].rows.append(row)
        if n in doubtful:
            tables[-1].readable = False
    for k, table in enumerate(tables):
        if len(table.rows) != 1:
            continue
        for other in tables[max(0, k - 1):k] + tables[k + 1:k + 2]:
            if set(table.heads) < set(other.heads) and table.heads[:1] == other.heads[:1]:
                table.readable = other.readable = False
    return tables


# Takes one cell of a plain-text table line; returns True when it can be a column heading (it is not a number or a date).
def _is_heading(cell: str) -> bool:
    return parse_number(cell) is None and parse_date(cell) is None


# The keys of a plain-text table's columns: each heading, "#n" for an empty one, "Heading (n)" for a second one of the same name.
def _column_keys(heads: list[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for n, head in enumerate(heads, start=1):
        key = head or f"#{n}"
        keys.append(key if key not in keys else f"{key} ({n})")
    return tuple(keys)


# Puts back a row of a plain-text table that the browser printed over several lines because a cell holds a line break ("Socks"
# then "SKU-11<tab>Shipped<tab>₹200"): joins up to 3 lines from `start`, the end of each to the start of the next, until they
# make `width` cells. A blank line is never part of a row. Returns (the cells, how many lines they took), or (None, 0).
def _rejoin(lines: list[str], start: int, width: int) -> tuple[list[str] | None, int]:
    cells: list[str] = []
    for k in range(start, min(len(lines), start + 3)):
        if not lines[k].strip():
            break
        parts = lines[k].split("\t")
        if cells:
            cells[-1] = cells[-1] + " " + parts[0]
            cells.extend(parts[1:])
        else:
            cells = list(parts)
        if len(cells) == width:
            return [norm_text(c) for c in cells], k - start + 1
        if len(cells) > width:
            break
    return None, 0


# True when put-together cells look like a row of the table: every column whose cells so far are all numbers of one unit gets
# a number of that unit too. Takes the cells and the table.
def _fits(cells: list[str], table: Table) -> bool:
    for key, cell in zip(table.heads, cells):
        units = {p[1] if p else None for p in (number_and_unit(row.get(key)) for row in table.rows)}
        if table.rows and None not in units and len(units) == 1:
            got = number_and_unit(cell)
            if got is None or got[1] not in units:
                return False
    return True


# The tables in a page's plain text, which the browser prints with a tab between cells: a heading line of two or more cells
# (none of them a number or a date) followed by lines with as many cells. A row broken over lines by a cell's line break is put
# back together when the table goes on after it (a whole row, or another broken row) and the joined cells fit; a broken row
# that does not fit, or lines right after
# the last row that would make one more row, make the table unreadable (a broken last row cannot be told from text after the
# table). Takes the plain lines; returns the tables.
def _plain_tables(lines: list[str]) -> list[Table]:
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        heads = [norm_text(c) for c in lines[i].split("\t")] if "\t" in lines[i] else []
        i += 1
        if not (len(heads) >= 2 and sum(1 for c in heads if c) >= 2 and all(_is_heading(c) for c in heads if c)):
            continue
        table = Table(_column_keys(heads))
        while i < len(lines):
            parts = lines[i].split("\t")
            if len(parts) == len(heads):
                cells, used = [norm_text(c) for c in parts], 1
            else:
                cells, used = _rejoin(lines, i, len(heads))
                if cells is None:
                    break  # the table ends here
                fits = _fits(cells, table)
                after = i + used
                goes_on = after < len(lines) and (len(lines[after].split("\t")) == len(heads) or _rejoin(lines, after, len(heads))[0] is not None)
                if not goes_on:
                    table.readable = table.readable and not fits  # a broken last row, or text after the table: cannot tell
                    break
                table.readable = table.readable and fits
            if any(cells):
                table.rows.append(dict(zip(table.heads, cells)))
            i += used
        if table.rows:
            tables.append(table)
    return tables


# Every table in a page's text: the block's tables first, then the plain text's tab-separated tables that the block does not
# hold already (a table with headings is printed both ways; the block's copy is the complete one). Takes the page text;
# returns the tables.
def page_tables(text: str) -> list[Table]:
    plain, block = _split_block(text)
    tables = _block_tables(block)
    in_block = {frozenset(t.heads) for t in tables}
    return tables + [t for t in _plain_tables(plain) if frozenset(t.heads) not in in_block]


# Rows of the tables in a page's text, each as {heading: cell}. Read from the "[table rows with their headings]" block, and also
# from tables in the plain text, which the browser prints with a tab between cells: a heading line of two or more cells (none
# of them a number or a date) followed by lines with as many cells. A table printed both ways is given once; two equal rows of
# one table (two identical order lines) both stay, so sums and counts are right. Rows of a table that could not be read cleanly
# are left out. Takes the page text; returns the rows, the block's first.
def table_rows(text: str) -> list[dict[str, str]]:
    return [row for table in page_tables(text) if table.readable for row in table.rows]


# Takes a label as printed or asked for; returns it in lower case without extra spaces or a trailing ":".
def _bare_label(text: str) -> str:
    return norm_text(text).rstrip(":").strip().lower()


# Every value printed next to a label in a page's text, in order: (a) a line that is the label (a trailing ":" ignored) gives
# the next line that is not empty, unless that line is itself a label ending in ":" (the value is empty); (b) a line
# "Label: value" or "Label<tab>value" gives the rest (nothing when the rest is empty); (c) a table row of two cells whose first
# cell is the label gives the second.
def _values_next_to(text: str, label: str) -> list[str]:
    want = _bare_label(label)
    if not want:
        return []
    plain, _block = _split_block(text)
    lines = [ln for ln in plain if ln.strip()]
    inline = re.compile(r"^\s*" + r"\s+".join(re.escape(w) for w in want.split(" ")) + r"\s*:\s*(\S.*)$", re.I)
    found: list[str] = []
    for i, line in enumerate(lines):
        cells = line.split("\t")
        if _bare_label(line) == want and "\t" not in line:
            if i + 1 < len(lines) and not lines[i + 1].rstrip().endswith(":"):
                found.append(norm_text(lines[i + 1]))
        elif len(cells) == 2 and _bare_label(cells[0]) == want and norm_text(cells[1]):
            found.append(norm_text(cells[1]))
        else:
            m = inline.match(line)
            if m and "\t" not in line:
                found.append(norm_text(m.group(1)))
    for row in table_rows(text):
        cells = list(row.values())
        if len(cells) == 2 and _bare_label(cells[0]) == want and cells[1]:
            found.append(cells[1])
    return found


# The value printed next to a label on a page ("Unit price" -> "₹620"), from a "Label / value" pair of lines (a definition
# list), a "Label: value" or tab-separated line, or a two-cell table row. Takes the page text and the label (case does not
# matter); returns the value, or None when the label is not there or is printed several times with different values.
def label_value(text: str, label: str) -> str | None:
    values = _values_next_to(text, label)
    if not values or len({v.lower() for v in values}) != 1:
        return None
    return values[0]


# The label printed next to a value on a page: the inverse of label_value, used to learn where a known value is printed.
# Looks at the line before a line that is the value, the part before ":" or a tab on the value's line, and the first cell of
# a two-cell row whose second cell is the value. A label is only returned when label_value gives back exactly the value, and
# only when one label does (two labels for the same value, "Quantity ordered" and "Quantity returned", are ambiguous).
# Takes the page text and the value; returns the label or None.
def learn_label(text: str, value: Any) -> str | None:
    want = norm_text(value)
    if not want:
        return None
    plain, _block = _split_block(text)
    lines = [ln for ln in plain if ln.strip()]
    candidates: list[str] = []
    for i, line in enumerate(lines):
        cells = line.split("\t")
        if norm_text(line) == want and i > 0 and "\t" not in lines[i - 1]:
            candidates.append(lines[i - 1])
        if len(cells) == 2 and norm_text(cells[1]) == want:
            candidates.append(cells[0])
        m = re.match(r"^(.{1,80}?)\s*:\s*(.+)$", line)
        if m and "\t" not in line and norm_text(m.group(2)) == want:
            candidates.append(m.group(1))
    for row in table_rows(text):
        cells = list(row.values())
        if len(cells) == 2 and cells[1] == want:
            candidates.append(cells[0])
    labels: dict[str, str] = {}
    for c in candidates:
        label = norm_text(c).rstrip(":").strip()
        if not label or len(label) > 80 or not re.search(r"[^\W\d_]", label):
            continue
        if label_value(text, label) == want:
            labels.setdefault(label.lower(), label)
    return next(iter(labels.values())) if len(labels) == 1 else None


# A pattern that matches values shaped like this one, for checking that a value read for another record is the same kind of
# thing: "ORD-99751" -> ^[A-Z]{3}-\d{5}$. Capital-letter runs and digit runs keep their length (codes have fixed lengths), other
# letter runs match any length (words do not), anything else must be the same character. A free text over 40 characters gives
# ".+" (any text). Takes the value; returns the pattern text.
def value_shape(value: Any) -> str:
    text = norm_text(value)
    if len(text) > 40:
        return ".+"
    parts: list[str] = []
    for m in re.finditer(r"\d+|[A-Z]+(?![A-Za-z])|[A-Za-z]+|[^\W\d_]+|.", text):
        run = m.group(0)
        if run.isdigit() and run.isascii():
            parts.append(rf"\d{{{len(run)}}}")
        elif run.isascii() and run.isalpha() and run.isupper():
            parts.append(f"[A-Z]{{{len(run)}}}")
        elif run.isascii() and run.isalpha():
            parts.append("[A-Za-z]+")
        elif run.isalpha():
            parts.append(r"[^\W\d_]+")
        else:
            parts.append(run if run in _PLAIN_MARKS else re.escape(run))
    return "^" + "".join(parts) + "$"


# --------------------------------------------------------------------- facts

# Fills {name} blanks in a fact's text with facts read before it. Returns None when a blank names a fact that is unknown.
def fill_known(template: Any, known: dict[str, Any]) -> str | None:
    missing = False

    # Takes one {name} blank; returns the named fact's text ("" and a note that it is missing when it is unknown).
    def one(m: re.Match[str]) -> str:
        nonlocal missing
        value = known.get(m.group(1))
        if value is None:
            missing = True
            return ""
        return fact_text(value)

    out = _BLANK.sub(one, "" if template is None else str(template))
    return None if missing else out


# The facts a fact's text refers to with {name} blanks (in its label, its row conditions and its column).
def fact_refs(spec: dict[str, Any]) -> set[str]:
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    where = source.get("where") if isinstance(source.get("where"), list) else []
    texts = [source.get("label"), source.get("column")] + [w.get("value") for w in where if isinstance(w, dict)]
    return {m for t in texts if t is not None for m in _BLANK.findall(str(t))}


# The latest page (url, text) whose address has the given page type ("/orders/*"); every page from the latest back when no type
# is given. Takes the pages and the type; returns the matching pages, latest first.
def pages_of_type(pages: list[tuple[str, str]], page_type: str | None) -> list[tuple[str, str]]:
    latest = [(u, t) for u, t in reversed(list(pages or [])) if t is not None]
    if not page_type:
        return latest
    # every page of that type, newest first: tabs and sections show different text at the same address, so a fact on the
    # Receipts tab must still be found after the plan went back to the Summary tab
    return [(u, t) for u, t in latest if shape_matches(u, page_type)]


# True when a table cell and a condition's value are the same: the same text (case does not matter), the same number counting
# the same thing ("₹2,480" and "2480", but not "1 Month" and "1 Year"), or the same date when each text is only a date (not an
# id like "INV-2026-10-19-01"). Takes the cell and the value, both with spaces tidied.
def _same_cell(cell: str, value: str) -> bool:
    if cell.lower() == value.lower():
        return True
    numbers = same_unit_numbers(cell, value)
    if numbers is not None:
        return numbers[0] == numbers[1]
    d1 = date_alone(cell)
    return d1 is not None and d1 == date_alone(value)


# A table cell and a condition's value put in order: two numbers counting the same thing, else two texts that are only dates.
# Takes the cell and the value; returns the pair to compare, or None when they cannot be put in order (the row does not match).
def _ordered(cell: str, value: str) -> tuple[Any, Any] | None:
    numbers = same_unit_numbers(cell, value)
    if numbers is not None:
        return numbers
    d1, d2 = date_alone(cell), date_alone(value)
    return (d1, d2) if d1 and d2 else None


# True when a table cell meets one row condition: eq (the same text, number or date), contains (the value as a whole word or
# phrase inside the cell, like one city in a list of cities), in (the cell is one item of a comma-separated list), or
# lt / le / gt / ge (the cell is a number or date below, at most, above or at least the value).
def cell_matches(cell: str, op: str, value: str) -> bool:
    c, v = norm_text(cell), norm_text(value)
    if op == "eq":
        return _same_cell(c, v)
    if op == "contains":
        return bool(v) and re.search(r"(?<![0-9A-Za-z])" + re.escape(v) + r"(?![0-9A-Za-z])", c, re.I) is not None
    if op == "in":
        return any(_same_cell(c, norm_text(part)) for part in v.split(",") if norm_text(part))
    pair = _ordered(c, v)
    if pair is None:
        return False
    a, b = pair
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}.get(op, False)


# The heading of a row that a fact names: the same text (case does not matter), else the one heading that is the same once a
# unit in brackets is left out ("Limit" for "Limit (₹)"). Returns None when there is no such heading.
def find_column(keys: list[str], name: str) -> str | None:
    want = norm_text(name).lower()
    exact = [k for k in keys if norm_text(k).lower() == want]
    if exact:
        return exact[0]
    loose = [k for k in keys if norm_text(re.sub(r"\([^)]*\)", "", k)).lower() == want]
    return loose[0] if len(loose) == 1 else None


# Reads one table fact from one page's text: the rows that have every condition's column and meet every condition, then the
# named column of those rows combined by the aggregate. Rows of two tables are never combined (an order's items and its
# payments may both have an "Amount"): when the rows that meet the conditions come from more than one table, or any table
# with the named columns could not be read cleanly, the fact is unknown. Sums, maxima and minima need numbers that count the
# same thing (or dates). Returns the raw result (text, list, Decimal) or None.
def _from_table(text: str, source: dict[str, Any], known: dict[str, Any]) -> Any:
    tables = page_tables(text)
    if not tables:
        return None
    conditions = []
    for w in source.get("where") or []:
        if not isinstance(w, dict):
            return None
        value = fill_known(w.get("value"), known)
        if value is None:
            return None
        conditions.append((str(w.get("column") or ""), str(w.get("op") or "eq").lower(), value))
    aggregate = str(source.get("aggregate") or "first").lower()
    column = fill_known(source.get("column"), known) if source.get("column") is not None else None
    if source.get("column") is not None and column is None:
        return None
    shaped = []  # rows that have every column the fact names: (their table's place, the row, the condition columns, the column)
    for number, table in enumerate(tables):
        for row in table.rows:
            keys = list(row)
            cols = [find_column(keys, c) for c, _op, _v in conditions]
            target = find_column(keys, column) if column is not None else None
            if any(c is None for c in cols) or (column is not None and target is None):
                continue
            shaped.append((number, row, cols, target))
    if not shaped or any(not tables[number].readable for number, _row, _cols, _target in shaped):
        return None
    hits = [(number, row, target) for number, row, cols, target in shaped
            if all(cell_matches(row[c], op, v) for c, (_n, op, v) in zip(cols, conditions))]
    if len({number for number, _row, _target in hits}) > 1:
        return None
    if aggregate == "count":
        return Decimal(len(hits))
    if not hits and aggregate == "list" and column is not None:
        return []  # the table was read and no row matched: the list is known to be empty ("no receipt is missing")
    if not hits or column is None:
        return None
    cells = [row[target] for _number, row, target in hits]
    if aggregate == "first":
        return cells[0] if cells[0] else None
    if aggregate == "list":
        return sorted(c for c in cells if c)
    if aggregate not in ("sum", "max", "min"):
        return None
    pairs = [number_and_unit(c) for c in cells]
    if all(p is not None for p in pairs) and len({unit for _n, unit in pairs}) == 1:
        numbers = [n for n, _unit in pairs]
        return sum(numbers, Decimal(0)) if aggregate == "sum" else max(numbers) if aggregate == "max" else min(numbers)
    dates = [date_alone(c) for c in cells]
    if aggregate == "sum" or not all(dates):
        return None
    return max(dates) if aggregate == "max" else min(dates)


# Turns a raw value into the fact's type: number -> Decimal, date -> ISO text, text -> text, list -> sorted list of texts,
# bool -> True or False from yes/no/true/false. Returns None when the value is empty or is not of that type.
def as_type(raw: Any, kind: str) -> Any:
    if raw is None:
        return None
    if kind == "number":
        return parse_number(raw)
    if kind == "date":
        return parse_date(fact_text(raw) if isinstance(raw, (list, tuple)) else raw)
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        word = norm_text(raw).lower().rstrip(".")
        return True if word in _YES_WORDS else False if word in _NO_WORDS else None
    if kind == "list":
        if isinstance(raw, (list, tuple, set)) and not raw:
            return []  # known to be empty, which is not the same as not found
        items = raw if isinstance(raw, (list, tuple, set)) else [p for p in re.split(r"[,;]", fact_text(raw))]
        items = [fact_text(i) for i in items if fact_text(i)]
        return sorted(items) if items else None
    text = fact_text(raw)
    return text or None


# Reads one fact for a record and says where it came from. Takes the fact (see recipe/program.py for its shape), the record's
# fields, the pages read for the record ([(url, text)], oldest first) and the facts read before it. Returns (the typed value or
# None when it is not there, the page type it was read from or None for a field or a fixed text).
def locate_fact(spec: dict[str, Any], fields: dict[str, Any], pages: list[tuple[str, str]], known: dict[str, Any]) -> tuple[Any, str | None]:
    source = spec.get("source") if isinstance(spec, dict) else None
    if not isinstance(source, dict):
        return None, None
    kind = str(spec.get("type") or "text").lower()
    where = str(source.get("kind") or "").lower()
    if where == "field":
        return as_type((fields or {}).get(str(source.get("field") or "")), kind), None
    if where == "const":
        return as_type(source.get("value"), kind), None
    if where not in ("label", "table"):
        return None, None
    page_type = norm_text(source.get("page")) or None
    label = fill_known(source.get("label"), known) if where == "label" else None
    if where == "label" and not label:
        return None, None
    seen = None
    for url, text in pages_of_type(pages, page_type):
        seen = seen or url
        raw = label_value(text, label) if where == "label" else _from_table(text, source, known)
        value = as_type(raw, kind)
        if value is not None:
            return value, page_type or url_shape(url)
    if seen is not None and source.get("default") is not None:
        # The page was read but holds no such row or label, and the instructions give what that means (no rate row for the
        # home currency: 1). Never used when the page itself was not read.
        return as_type(source.get("default"), kind), page_type or url_shape(seen)
    return None, None


# Reads one fact for a record: like locate_fact, without saying where it came from. Returns the typed value or None.
def extract_fact(spec: dict[str, Any], fields: dict[str, Any], pages: list[tuple[str, str]], known: dict[str, Any]) -> Any | None:
    return locate_fact(spec, fields, pages, known)[0]
