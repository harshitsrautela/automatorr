"""Read-back: after a plan saves a record, opens the record's own page again and checks that the values it entered are shown
there. A success message only says so; the saved record is the proof.

Values are compared by what they mean, not by how they are written: a number by its value (₹1,984 and €1.984,00 are 1984), a
date by the day it names (05 Nov 2026 and 05-Nov-26 are 2026-11-05), the label of a choice by the stems of its words (Approve
with deduction ~ Approved with deduction) and a short text by its words in order. When the form's options are known, a shown
choice must fit the saved option better than any other option ("Approve" on the page is not "Approve partially"), and a
shown choice that is turned around ("Not approved", "Approval pending") does not count. Some values cannot be checked (an empty
one, a yes/no checkbox, a long free text); they are reported apart.

The page's text is read with its labels, headings, buttons and the options its forms offer left out (hidden for a moment in the
page itself, by a style sheet the page's own security rules cannot block), so a form that still lists every choice, or a
"Reviewer" label, does not count as showing the value that was saved. What the page's form controls hold now (the chosen
option, a ticked box, a field's value) is added as lines of its own: a record page that is the saved form, filled in, shows its
values that way. Frames are read too. A success screen is not trusted either: the record's page is always loaded afresh from
the site, and a page the site sent the browser to instead (a login page, a list) is not taken for it. Many sites never show a
saved decision at all: `site_shows_decisions` tells, from a record the live agent did, whether a read-back on this site means
anything."""

import asyncio
import datetime
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urljoin, urlsplit

from playwright.async_api import Frame, Page

from recipe.plan import _norm

# Free text longer than this is not checked: a site may shorten, wrap or reformat it
_TEXT_LIMIT = 60
# How much of the page's text a report keeps
_REPORT_CHARS = 2000
# Where the plan executor's reader cuts a page's text, and the heading of the table rows it adds after the cut
_READ_CHARS = 14000
_ROWS_MARK = "\n\n[table rows with their headings]\n"
# The line a frame's text starts after (as recipe/webkit.read_frames_text writes it), and how many frames are read
_FRAME_MARK = "\n\n[frame]\n"
_MAX_FRAMES = 10
_OPEN_TIMEOUT_MS = 15000
# The longest a page (or a frame) may take to be read without its labels; a page that takes longer is not checked
_SHOWN_TIMEOUT_S = 20.0
# Short words that carry no meaning in a choice's label ("Approve with deduction" is about approving and a deduction)
_STOP = {
    "with", "from", "that", "this", "than", "then", "into", "onto", "upon", "also", "have", "been", "were", "will", "your",
    "their", "there", "they", "them", "what", "when", "where", "which", "while", "only", "some", "such", "each", "more", "most",
    "very", "just", "does", "done", "being", "other", "these", "those", "should", "would", "could", "please",
}
# Words that turn a shown choice around: "Not approved", "Approval pending", "No refund"
_NEGATION = {"not", "no", "non", "none", "never", "neither", "nor", "pending", "awaiting", "cannot"}
# Words a sentence of help text or policy puts before the choice it talks about ("Staff may waive...", "Claims that were...")
_HYPOTHETICAL = {
    "if", "when", "whenever", "unless", "until", "may", "might", "can", "could", "must", "should", "would", "will", "shall",
    "that", "which", "who", "whose", "where",
}
# Words just before a choice in a sentence that reports what happened to this record ("Your claim has been approved!")
_DONE = {"was", "were", "been", "has", "have", "had", "got"}

# A number as pages write it: grouping dots and a decimal comma (European 1.984,00, or 1.250.000 with two groups or more, which
# cannot be a decimal), digits with grouping commas (Western 1,250,000 or Indian 12,50,000) and a decimal part, or plain digits
_EURO = r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:\.\d{3}){2,}"
_NUM = _EURO + r"|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# A number standing on its own: not part of an id ("INV-41142", "PO-26-0012"), a word ("2nd", "A12"), a time ("10:30") or
# the decimal part of another number. Atomic, so "1,250x" is not read as 1.
_PAGE_NUMBER = re.compile(r"(?<![\w:])(?<!\w-)(?<!\d\.)(?>" + _NUM + r")(?![\w]|-\w|:\d|\.\d)")
# The words and numbers of a text, in order
_TOKEN = re.compile(r"(?>" + _NUM + r")|[^\W\d_]+")
# Words alone (any length), and words with the "/" or apostrophe inside them ("N/A", "don't")
_WORD = re.compile(r"[^\W\d_]+")
_PLAIN = re.compile(r"\w+(?:[/'’]\w+)*")
# Currency codes written before an amount; pages also write them against the digits ("Rs.1,984", "INR1984")
_CODES = r"rs\.?|inr|usd|eur|gbp|aed|sgd"
_GLUED_CODE = re.compile(r"(?<![^\W\d_])(?:" + _CODES + r")(?=\d)", re.I)

_MONTH_NUMBER = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = "(" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + r")\b\.?"
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_DAY_MONTH_YEAR = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-./]*" + _MONTH + r",?[\s\-./]*(\d{4})(?!\d)", re.I)
_MONTH_DAY_YEAR = re.compile(r"\b" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I)
# 05/11/2026: day first in most of the world, month first in the US. Both readings are kept when both are real days.
_NUMERIC_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)")
# Two-digit years, read as 20xx: 05-Nov-26 or 05 Nov 26 (the same mark on both sides of the month), and 05/11/26 or 05-11-26
_DAY_MONTH_YY = re.compile(r"(?<![\d/.-])(\d{1,2})([\-/. ])" + _MONTH + r"\2(\d{2})(?![\w/-]|\.\d)", re.I)
_NUMERIC_DATE_YY = re.compile(r"(?<![\d/.-])(\d{1,2})([/-])(\d{1,2})\2(\d{2})(?![\w/-]|\.\d)")

# Where a line of page text breaks into separate values: table cells (tabs), the "Heading: cell | Heading: cell" rows the
# page reader adds, and values written side by side with a dot or bullet between them ("Approved · pay ₹1,984")
_PIECE_SPLIT = re.compile(r"\t|\s+[|·•]\s+")
# "Heading: value": only the value part can show a value (a "Rejection reason" heading does not show "Reject")
_HEADED = re.compile(r"^[^:]{1,40}:\s+(.+)$")
# A list of ticked boxes written in one piece ("Damaged, Late")
_LIST_SPLIT = re.compile(r"\s*;\s*|,\s+")
# A line this module or the page reader adds to mark a part of the text ("[frame]", "[table rows with their headings]")
_MARK_LINE = re.compile(r"^\[[^\]]*\]$")


# ----------------------------------------------------------------------------- numbers and dates

# Takes a number as written ("2,480.00", "1.984,00"); returns its value.
def _decimal(text: str) -> Decimal:
    if re.fullmatch(_EURO, text):
        return Decimal(text.replace(".", "").replace(",", "."))
    return Decimal(text.replace(",", ""))


# Turns a number as written ("2,480.00") into a key that is the same for every way of writing it ("2480").
def _number_key(text: str) -> str:
    return format(_decimal(text).normalize(), "f")


# Takes a value; returns it as a number when the whole value is one number (an optional currency, sign, "%" or "/-" around
# the digits), else None. The sign is dropped: pages show a deduction as "-200", "−₹200" or "(200)" alike.
def _number_of(value: str) -> Decimal | None:
    text = _norm(value).replace("−", "-")
    text = re.sub(r"^[-+(]?\s*(?:[₹$€£¥]|" + _CODES + r")?\s*[-+]?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*(?:%|/-|\)|€)$", "", text)
    if not re.fullmatch(r"(?>" + _NUM + r")", text):
        return None
    try:
        return abs(_decimal(text))
    except InvalidOperation:
        return None


# Takes a year, month and day; returns the date as YYYY-MM-DD, or None when it is not a real day.
def _iso(year: int, month: int, day: int) -> str | None:
    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


# Finds the dates written in a text, in any of the usual ways. Takes the text; returns [(start, end, {YYYY-MM-DD, ...})], one
# entry per date as written (a numeric date that reads both day-first and month-first gives both days).
def _date_spans(text: str) -> list[tuple[int, int, set[str]]]:
    found: list[tuple[int, int, set[str]]] = []
    for m in _ISO_DATE.finditer(text):
        found.append((m.start(), m.end(), {d for d in [_iso(int(m[1]), int(m[2]), int(m[3]))] if d}))
    for m in _DAY_MONTH_YEAR.finditer(text):
        found.append((m.start(), m.end(), {d for d in [_iso(int(m[3]), _MONTH_NUMBER[m[2].lower()], int(m[1]))] if d}))
    for m in _MONTH_DAY_YEAR.finditer(text):
        found.append((m.start(), m.end(), {d for d in [_iso(int(m[3]), _MONTH_NUMBER[m[1].lower()], int(m[2]))] if d}))
    for m in _NUMERIC_DATE.finditer(text):
        a, b, year = int(m[1]), int(m[2]), int(m[3])
        found.append((m.start(), m.end(), {d for d in (_iso(year, b, a), _iso(year, a, b)) if d}))
    for m in _DAY_MONTH_YY.finditer(text):
        found.append((m.start(), m.end(), {d for d in [_iso(2000 + int(m[4]), _MONTH_NUMBER[m[3].lower()], int(m[1]))] if d}))
    for m in _NUMERIC_DATE_YY.finditer(text):
        a, b, year = int(m[1]), int(m[3]), 2000 + int(m[4])
        found.append((m.start(), m.end(), {d for d in (_iso(year, b, a), _iso(year, a, b)) if d}))
    return [f for f in found if f[2]]


# Takes a text; returns every day a date in it names, as YYYY-MM-DD.
def _dates_in(text: str) -> set[str]:
    return {d for _s, _e, days in _date_spans(text) for d in days}


# Takes a value; returns the days it names when the whole value is a date ("2026-11-05", "05 Nov 2026"), else an empty set.
def _date_of(value: str) -> set[str]:
    spans = _date_spans(value)
    rest = value
    for start, end, _days in spans:
        rest = rest[:start] + " " * (end - start) + rest[end:]
    if not spans or rest.strip(" \t,.;:/-"):
        return set()
    return {d for _s, _e, days in spans for d in days}


# Takes a page's text; returns the numbers shown on it (without their signs), leaving out the digits of its dates.
def _numbers_in(text: str) -> set[Decimal]:
    for start, end, _days in _date_spans(text):
        text = text[:start] + " " * (end - start) + text[end:]
    text = _GLUED_CODE.sub(" ", text)
    out: set[Decimal] = set()
    for m in _PAGE_NUMBER.finditer(text):
        try:
            out.add(abs(_decimal(m.group(0))))
        except InvalidOperation:
            continue
    return out


# ----------------------------------------------------------------------------- choices and short texts

# Takes a text; returns its words (lowercase, two letters or more) and numbers (as "2480" for "₹2,480.00"), in order.
def _tokens(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text):
        if tok[0].isdigit():
            out.append(_number_key(tok))
        elif len(tok) >= 2:
            out.append(tok.lower())
    return out


# Takes a choice's label (or a piece of a page); returns its content words (four letters or more, not "with", "from"...) and
# its numbers (as "#30"), in order.
def _stems(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text):
        if tok[0].isdigit():
            out.append("#" + _number_key(tok))
        elif len(tok) >= 4 and tok.lower() not in _STOP:
            out.append(tok.lower())
    return out


# Takes a text; returns its words, lowercase, every length ("Not approved" -> ["not", "approved"]).
def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


# Takes a text; returns its words and numbers without the marks around them, lowercase ("OK ✓" -> "ok", "N/A." -> "n/a").
def _plain(text: str) -> str:
    return " ".join(_PLAIN.findall(text.lower()))


# True when two content words are the same word in another form: their first five letters agree ("approve" / "approved",
# "deduction" / "deductions"; a word shorter than five letters may gain up to two: "meal" / "meals"), or they differ only in a
# last d / t (an irregular past: "send" / "sent", "spend" / "spent", "build" / "built"). Numbers must be equal.
def _same_word(a: str, b: str) -> bool:
    if a.startswith("#") or b.startswith("#"):
        return a == b
    n = min(5, len(a), len(b))
    if a[:n] == b[:n] and (n == 5 or abs(len(a) - len(b)) <= 2):
        return True
    return len(a) == len(b) >= 4 and a[:-1] == b[:-1] and {a[-1], b[-1]} == {"d", "t"}


# True when every word of `small` has its match among `big`'s words, all within `window` neighbouring words of `big` (all of
# `big` when no window is given).
def _covered(small: list[str], big: list[str], window: int | None = None) -> bool:
    size = window or len(big)
    for start in range(max(1, len(big) - size + 1)):
        part = big[start: start + size]
        if all(any(_same_word(a, b) for b in part) for a in small):
            return True
    return False


# Takes an option's content words and a piece's; returns how well the option fits the piece: (its words the piece shows,
# minus its words the piece does not show). A larger fit is a better one.
def _fit(option: list[str], piece: list[str]) -> tuple[int, int]:
    hit = sum(1 for a in option if any(_same_word(a, b) for b in piece))
    return hit, hit - len(option)


# Splits a page's text into the pieces a value can be shown in: lines, table cells, values written side by side, and only the
# value part of "Heading: value". With `lists`, each part of a list ("Damaged, Late") is a piece too. Takes the text; returns the
# pieces, tidied, without the lines that only mark a part of the text.
def _segments(text: str, lists: bool = False) -> list[str]:
    out = []
    for line in text.splitlines():
        if _MARK_LINE.match(line.strip()):
            continue
        for piece in _PIECE_SPLIT.split(line):
            piece = _norm(piece)
            headed = _HEADED.match(piece)
            if headed:
                piece = _norm(headed.group(1))
            if not piece:
                continue
            out.append(piece)
            if lists:
                parts = [_norm(p) for p in _LIST_SPLIT.split(piece)]
                out.extend(p for p in parts if p and len(parts) > 1)
    return out


# True when a piece of a page is a sentence of prose (help text, a policy, a message) rather than a shown value: five words or
# more that end like a sentence.
def _prose(piece: str) -> bool:
    return len(piece.split()) >= 5 and piece.rstrip()[-1:] in ".!?"


# True when a sentence reports that this record got the choice whose content words are given: the choice's first word comes
# right after "was", "were", "been", "has"... ("Your claim has been approved!"), and no "if", "may", "that"... comes before it.
# "Late fees are waived for members over 65." and "Staff may waive the fee..." are help text, not a shown choice.
def _reports(piece: str, want: list[str]) -> bool:
    if want[0].startswith("#"):
        return False
    words = _words(piece)
    for i, word in enumerate(words):
        if len(word) >= 4 and _same_word(want[0], word):
            if any(w in _DONE for w in words[max(0, i - 2): i]) and not any(w in _HYPOTHETICAL for w in words[:i]):
                return True
    return False


# True when a piece of a page turns a choice around: it holds "not", "no", "pending", "awaiting", a "n't"... that the choice's
# own label does not ("Not approved" and "Approval pending" do not show "Approve"; "No" shows "No").
def _negated(piece: str, label: str) -> bool:
    own = set(_words(label))
    if any(w in _NEGATION and w not in own for w in _words(piece)):
        return True
    return bool(re.search(r"[^\W\d_]n['’]t\b", piece, re.I)) and not re.search(r"n['’]t\b", label, re.I)


# True when one piece of a page shows a choice. Takes the label, its content words and the piece. The label and the piece agree
# when every content word of the shorter of the two matches a word of the other (see _same_word); when the label is the shorter,
# its words must also sit close together; when the piece is the shorter, it must also hold the label's first content word (a
# shown "Approved" is "Approve with deduction" cut short, a table cell "Deduction" or a "Back" link is not a shown choice). A
# label with no content word ("Yes", "N/A", "Bus") must be the whole piece: a cell, a line, or the value of "Heading: value".
def _piece_shows(label: str, want: list[str], piece: str) -> bool:
    if not want:
        return _plain(piece) == _plain(label)
    have = _stems(piece)
    if not have or (_prose(piece) and not _reports(piece, want)):
        return False
    if len(want) <= len(have):
        return _covered(want, have, window=len(want) + 3)
    return _covered(have, want) and any(_same_word(want[0], word) for word in have)


# True when a choice's label is shown on a page; None when the label has no word to look for. Takes the label, the page's text,
# and optionally the options the control offers. A piece of the page shows the label (see _piece_shows) unless it turns it
# around (see _negated) or another option that shares a word with it fits that piece as well or better ("Approve" shown is not
# "Approve partially", and "Approve partially" shown is not "Approve"). With `lists`, the parts of a list are pieces too.
def _choice_seen(label: str, page_text: str, options: list[str] | None = None, lists: bool = False) -> bool | None:
    want = _stems(label)
    if not want and not _plain(label):
        return None
    rivals = []
    for option in options or []:
        theirs = _stems(str(option))
        if _norm(option).lower() != _norm(label).lower() and theirs and want and any(_same_word(a, b) for a in want for b in theirs):
            rivals.append((str(option), theirs))
    for piece in _segments(page_text, lists):
        if not _piece_shows(label, want, piece) or _negated(piece, label):
            continue
        have = _stems(piece)
        mine = _fit(want, have)
        if any(_piece_shows(option, theirs, piece) and _fit(theirs, have) >= mine for option, theirs in rivals):
            continue
        return True
    return False


# True when a short text's words and numbers appear on the page in the same order, with at most two other words between
# them in all ("Invoiced 740, received 738" is shown by "Invoiced ₹740, received ₹738."). Takes the text and the page's text.
def _text_seen(value: str, page_text: str) -> bool | None:
    want = _tokens(value)
    if not want:
        return None
    have = _tokens(page_text)
    window = len(want) + 2
    for start, tok in enumerate(have):
        if tok != want[0]:
            continue
        k, j = 1, start + 1
        while k < len(want) and j < min(len(have), start + window):
            if have[j] == want[k]:
                k += 1
            j += 1
        if k == len(want):
            return True
    return False


# ----------------------------------------------------------------------------- the checks

# Checks one value (not a group of ticked boxes) against a page's text. Takes the value, the control's kind, the text and the
# options the control offers (or None); returns True / False, or None when it cannot be checked.
def _one_seen(value: str, kind: str, page_text: str, options: list[str] | None) -> bool | None:
    days = _date_of(value)
    if days:
        return bool(days & _dates_in(page_text))
    number = _number_of(value)
    if number is not None:
        return number in _numbers_in(page_text)
    if kind in ("select", "checks", "widget"):
        return _choice_seen(value, page_text, options, lists=kind == "checks")
    if len(value) > _TEXT_LIMIT:
        return None
    return _text_seen(value, page_text)


# Tells whether a value that was saved is shown in a page's text. Takes the value as the plan entered it, the control's kind
# (plan_exec._kind_of: select, check, checks, note, text, widget, other), the text, and optionally the options the control
# offers (the scraped control's "options": then a choice is told apart from the other options). Returns True when it is shown,
# False when it is not, and None when it cannot be checked: an empty value, a single yes/no checkbox, free text over 60
# characters. Dates are compared by day, numbers by value, choices (a select, a radio group, the labels of ticked boxes, a
# custom control) by the stems of their words, and other short texts by their words in order.
def value_seen(value: Any, kind: str, page_text: str, options: list[str] | None = None) -> bool | None:
    text = _norm(value)
    if not text or kind == "check":
        return None
    if kind == "checks":
        labels = [label for label in (_norm(p) for p in text.split(",")) if label]
        seen = [_one_seen(label, kind, page_text, options) for label in labels]
        return None if not labels or None in seen else all(seen)
    return _one_seen(text, kind, page_text, options)


# Takes a line of page text; returns it as a key that is the same however its cells are set apart (tabs, " | ", " · ").
def _line_key(line: str) -> str:
    return "\t".join(p for p in (_norm(p).lower() for p in _PIECE_SPLIT.split(line)) if p)


# Takes a page's text and the same page's text from before saving; returns the lines that are new (a line that was there
# before, as often as it was, is dropped), so only what the save changed is compared.
def _new_lines(text: str, before: str) -> str:
    old = Counter(_line_key(line) for line in before.splitlines() if _line_key(line))
    out = []
    for line in text.splitlines():
        key = _line_key(line)
        if key and old[key] > 0:
            old[key] -= 1
            continue
        out.append(line)
    return "\n".join(out)


# Checks every saved value against a page's text. Takes {control key: value}, {control key: kind} and the text, optionally
# the same page's text from before saving (then only what changed counts as showing a value) and {control key: the options it
# offers}. Returns {"seen": [keys], "missing": [keys], "unchecked": [keys]}, each in the order of `values`.
def readback_report(
    values: dict[str, Any],
    kinds: dict[str, str],
    page_text: str,
    before: str = "",
    options: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    shown = _new_lines(page_text, before) if before else page_text
    report: dict[str, Any] = {"seen": [], "missing": [], "unchecked": []}
    for key, value in values.items():
        seen = value_seen(value, kinds.get(key, "other"), shown, (options or {}).get(key))
        report["unchecked" if seen is None else "seen" if seen else "missing"].append(key)
    return report


# ----------------------------------------------------------------------------- opening the record again

# The page's text without the texts that are never a saved value: labels, legends, a definition list's terms, table headings,
# buttons, tabs, the controls that offer choices (a <select> lists every option in the page's text, a row of radio buttons
# shows every label), and the text written next to a radio button or checkbox that has no <label>. They are hidden for a
# moment while the text is read, by a mark and a style sheet of its own (an adopted one: a page's Content-Security-Policy
# blocks a <style> element but not this), both taken away after, so the page is left exactly as it was; the page's own layout
# decides what is left: a text match cannot tell a saved "Late" from a form's "Late" box. Only shown ones, and only short
# one-line ones, so a whole clickable row is never taken for a label. The page is measured once before anything changes, then
# once per level of nesting (a label is measured once the select inside it is hidden), never once per element.
# Returns {"text": the rest of the page's text, "labels": the texts taken out, "leaked": the texts of the hidden ones the page's
# own styles kept shown, "state": what the form controls hold now (the chosen option of a select, the label of a ticked box or
# radio button, a field's value, an ARIA option or combobox's shown value), "rows": every body row of every table with a
# heading row as "Heading: cell | ..." (as the plan executor's reader writes them)}.
_SHOWN_TEXT = """() => {
  const SEL = 'label, legend, dt, th, summary, select, button, [role=button], [role=listbox], [role=option], [role=radio], '
    + '[role=checkbox], [role=switch], [role=tab], [role=menuitem], [role=menuitemradio], [role=menuitemcheckbox], '
    + '[role=columnheader], [role=rowheader]';
  const MARK = 'data-readback-hidden';
  const RULE = '[' + MARK + '] { display: none !important; }';
  const STOP = 'input, select, textarea, button, label, br, hr';
  const SKIP = ['hidden', 'password', 'submit', 'button', 'reset', 'image', 'file', 'range', 'color', 'radio', 'checkbox'];
  const out = {text: '', labels: [], leaked: [], state: [], rows: ''};
  const body = document.body;
  if (!body) return out;
  const one = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const rendered = (el) => el.getClientRects().length > 0;

  // --- read: nothing on the page has changed yet, so it is laid out once
  // the text written next to a radio button or checkbox without a label: up to the next control, line break or block
  const claimed = new Set();
  const beside = (c, step) => {
    const nodes = [];
    for (let n = c[step]; n; n = n[step]) {
      if (claimed.has(n)) break;
      if (n.nodeType === Node.TEXT_NODE) { nodes.push(n); continue; }
      if (n.nodeType !== Node.ELEMENT_NODE) continue;
      const display = getComputedStyle(n).display;
      if (n.matches(STOP) || n.querySelector(STOP) || !(display.startsWith('inline') || display === 'contents')) break;
      nodes.push(n);
    }
    return step === 'previousSibling' ? nodes.reverse() : nodes;
  };
  const textOf = (nodes) => one(nodes.map((n) => n.textContent).join(' '));
  // each control's first label, found once from the labels (a control's own .labels searches the whole page every time)
  const labelOf = new Map();
  for (const l of body.querySelectorAll('label')) {
    const c = l.control;
    if (c && !labelOf.has(c)) labelOf.set(c, l);
  }
  const bare = [];
  for (const c of body.querySelectorAll('select, input, textarea')) {
    if (!rendered(c)) continue;
    const type = (c.getAttribute('type') || '').toLowerCase();
    if (c.tagName === 'SELECT') {
      // a select shows its first option when nothing was chosen: that one counts only when the page chose it
      Array.from(c.options).forEach((o, i) => {
        if (o.selected && o.value !== '' && (c.multiple || i > 0 || o.defaultSelected)) out.state.push(one(o.textContent));
      });
    } else if (type === 'radio' || type === 'checkbox') {
      const label = labelOf.get(c);
      let text = label ? one(label.innerText) : '';
      if (!label) {
        let nodes = beside(c, 'nextSibling');
        if (!textOf(nodes)) nodes = beside(c, 'previousSibling');
        text = textOf(nodes);
        if (text && text.length <= 80) { nodes.forEach((n) => claimed.add(n)); bare.push([nodes, text]); }
      }
      if (c.checked && text) out.state.push(text);
    } else if (c.tagName === 'TEXTAREA' || !SKIP.includes(type)) {
      const value = one(c.value);
      if (value) out.state.push(value.slice(0, 1000));
    }
  }
  for (const el of body.querySelectorAll('[role=radio][aria-checked=true], [role=checkbox][aria-checked=true], '
      + '[role=menuitemradio][aria-checked=true], [role=option][aria-selected=true], [aria-haspopup=listbox], '
      + '[role=combobox]:not(input)')) {
    const text = rendered(el) ? one(el.innerText) : '';
    if (text && text.length <= 80) out.state.push(text);
  }
  const tables = [];
  for (const t of body.querySelectorAll('table')) {
    const heads = Array.from(t.querySelectorAll('thead th'), (h) => (h.innerText || '').trim());
    if (heads.length) tables.push([t, heads]);
  }
  // the elements to hide, by level: 0 holds none of the others, 1 holds some of level 0, ...
  const found = Array.from(body.querySelectorAll(SEL));
  const isFound = new Set(found);
  const level = new Map();
  for (let i = found.length - 1; i >= 0; i--) {
    const el = found[i];
    const h = level.get(el) || 0;
    level.set(el, h);
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (isFound.has(p)) { if ((level.get(p) || 0) < h + 1) level.set(p, h + 1); break; }
    }
  }
  const levels = [];
  for (const el of found) (levels[level.get(el)] = levels[level.get(el)] || []).push(el);

  // --- hide, read, and put everything back
  let sheet = null;
  let styleEl = null;
  try {
    sheet = new CSSStyleSheet();
    sheet.replaceSync(RULE);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  } catch (e) {
    sheet = null;
    styleEl = document.createElement('style');
    styleEl.textContent = RULE;
    (document.head || document.documentElement).appendChild(styleEl);
  }
  const hidden = new Map();
  const wrapped = [];
  try {
    for (const [nodes, text] of bare) {
      out.labels.push(text);
      for (const n of nodes) {
        if (n.nodeType === Node.TEXT_NODE) {
          if (!n.textContent.trim()) continue;
          const span = document.createElement('span');
          span.setAttribute(MARK, '');
          n.parentNode.insertBefore(span, n);
          span.appendChild(n);
          wrapped.push([span, n]);
          hidden.set(span, [text]);
        } else {
          n.setAttribute(MARK, '');
          hidden.set(n, [text]);
        }
      }
    }
    for (const group of levels) {
      if (!group) continue;
      const take = [];
      for (const el of group) {
        if (!rendered(el)) continue;
        let texts;
        if (el.tagName === 'SELECT') {
          texts = Array.from(el.options, (o) => one(o.textContent)).filter(Boolean);
        } else {
          const text = (el.innerText || '').trim();
          if (!text || text.length > 80 || /[\\n\\t]/.test(text)) continue;
          texts = [text];
        }
        take.push([el, texts]);
      }
      for (const [el, texts] of take) {
        out.labels.push(...texts);
        el.setAttribute(MARK, '');
        hidden.set(el, texts);
      }
    }
    // a page's own styles can still show a hidden one (an inline "display: ... !important"): its texts are taken out later
    for (const [el, texts] of hidden) if (rendered(el)) out.leaked.push(...texts);
    out.text = body.innerText || '';
    const rows = [];
    for (const [t, heads] of tables) {
      for (const tr of t.querySelectorAll('tbody tr')) {
        const cells = Array.from(tr.children, (c) => (c.innerText || '').trim());
        rows.push(cells.map((c, i) => (heads[i] ? heads[i] + ': ' : '') + c).join(' | '));
      }
    }
    out.rows = rows.join('\\n');
    return out;
  } finally {
    for (const el of hidden.keys()) el.removeAttribute(MARK);
    for (const [span, n] of wrapped) if (span.parentNode) span.parentNode.replaceChild(n, span);
    if (sheet) document.adoptedStyleSheets = Array.from(document.adoptedStyleSheets).filter((s) => s !== sheet);
    if (styleEl) styleEl.remove();
  }
}"""


# Takes a page's text out of the texts of its labels, buttons and offered options. Takes the text and those texts (as
# _SHOWN_TEXT gives them); returns the text without them. Each is taken out once, where the text shows it as a line of its own,
# else as a table cell, else from a line made only of such texts side by side (a row of radio buttons, a bar of buttons).
def _drop_labels(text: str, labels: list[str]) -> str:
    left = Counter(_norm(t).lower() for t in labels if _norm(t))
    lines = text.splitlines()
    for i, line in enumerate(lines):
        key = _norm(line).lower()
        if key and left[key] > 0:
            left[key] -= 1
            lines[i] = ""
    for i, line in enumerate(lines):
        pieces = _PIECE_SPLIT.split(line)
        if len(pieces) < 2:
            continue
        kept = []
        for piece in pieces:
            key = _norm(piece).lower()
            if key and left[key] > 0:
                left[key] -= 1
            else:
                kept.append(piece)
        if len(kept) < len(pieces):
            lines[i] = "\t".join(kept)
    side_by_side = None
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if side_by_side is None:
            rest = sorted((k for k, n in left.items() if n > 0), key=len, reverse=True)
            if not rest:
                break
            side_by_side = re.compile(r"(?<!\w)(?:" + "|".join(r"\s+".join(re.escape(w) for w in k.split()) for k in rest) + r")(?!\w)", re.I)
        found = Counter(_norm(f).lower() for f in side_by_side.findall(line))
        if not found or side_by_side.sub(" ", line).strip(" \t·|•,;:/-()") or any(n > left[k] for k, n in found.items()):
            continue
        left.subtract(found)
        lines[i] = ""
        side_by_side = None
    return "\n".join(line for line in lines if line.strip())


# Turns a record page's text from before saving into the old lines a read-back compares with. Takes that text and the texts
# of the labels, buttons and offered options of the page as it is now; returns the text without them (see _drop_labels), plus,
# for a line that starts with a label and goes on ("Fee charged ₹620", when the before text is the reader's), the rest of it
# as a line of its own: that is how the label-less page shows it.
def _old_text(before: str, labels: list[str]) -> str:
    text = _drop_labels(before, labels)
    heads = sorted({_norm(t) for t in labels if 0 < len(_norm(t)) <= 80}, key=len, reverse=True)
    if not heads:
        return text
    lead = re.compile(r"^\s*(?:" + "|".join(r"\s+".join(re.escape(w) for w in h.split()) for h in heads) + r")(?!\w)[\s:]*(\S.*)$", re.I)
    rests = [m.group(1) for line in before.splitlines() if (m := lead.match(line))]
    return "\n".join([text, *rests]) if rests else text


# Reads one frame's text without its labels, buttons and offered choices (see _SHOWN_TEXT), giving up after
# _SHOWN_TIMEOUT_S. Takes the frame; returns (the text, cut where the plan executor's reader cuts, then what the form controls
# hold and the table rows with their headings; the texts that were taken out).
async def _shown_in(frame: Frame) -> tuple[str, list[str]]:
    got = await asyncio.wait_for(frame.evaluate(_SHOWN_TEXT), _SHOWN_TIMEOUT_S)
    text = str(got.get("text") or "").strip()[:_READ_CHARS]
    leaked = [str(t) for t in got.get("leaked") or []]
    if leaked:
        text = _drop_labels(text, leaked)
    state = [str(t) for t in got.get("state") or [] if str(t).strip()]
    if state:
        text += "\n\n" + "\n".join(state)
    rows = str(got.get("rows") or "")
    if rows:
        text += _ROWS_MARK + rows
    return text, [str(t) for t in got.get("labels") or []]


# Reads the page that is open, and each frame shown in it (after a "[frame]" line), without their labels, buttons and offered
# choices. Takes the page; returns (that text, the texts that were taken out). Raises when the page itself cannot be read; a
# frame that cannot be read is left out.
async def _shown(page: Page) -> tuple[str, list[str]]:
    text, labels = await _shown_in(page.main_frame)
    read = 0
    for frame in page.frames:
        if frame is page.main_frame or frame.is_detached() or read >= _MAX_FRAMES:
            continue
        try:
            if not await (await frame.frame_element()).is_visible():
                continue
            more, more_labels = await _shown_in(frame)
        except Exception:  # noqa: BLE001 - a frame that is loading, gone or has no body
            continue
        read += 1
        if more.strip():
            text += _FRAME_MARK + more
            labels += more_labels
    return text, labels


# The text of the page that is open as read_back compares it: without its labels, buttons and the options its forms offer,
# with what its form controls hold, its table rows with their headings, and its frames. Takes the page; returns the text. A
# caller that wants only what a save changed reads this on the record's page before saving and passes it to read_back as
# `before`.
async def shown_text(page: Page) -> str:
    return (await _shown(page))[0]


# True when two addresses are the same document and differ at most in the part after "#" (two routes of one single-page app).
def _same_document(a: str, b: str) -> bool:
    pa, pb = urlsplit(a or ""), urlsplit(b or "")
    return (pa.scheme, pa.netloc, pa.path or "/", pa.query) == (pb.scheme, pb.netloc, pb.path or "/", pb.query)


# True when a path is the wanted one or a page under it ("/claims/7/summary" is under "/claims/7"; "/claims" is not).
def _under(path: str, want: str) -> bool:
    path, want = path.rstrip("/") or "/", want.rstrip("/") or "/"
    return path == want or (want != "/" and path.startswith(want + "/"))


# True when the page that opened is the record's page that was asked for: the same site, the same path (or a page under it),
# every part of the asked query (more may be added), and for a single-page app the same route after "#". A login page
# (/login?next=/claims/7) or a list (/claims) the site sent the browser to instead is not. Takes both addresses.
def _same_record(landed: str, target: str) -> bool:
    a, b = urlsplit(landed or ""), urlsplit(target or "")
    if (a.scheme, a.netloc.lower()) != (b.scheme, b.netloc.lower()) or not _under(a.path, b.path):
        return False
    if not set(parse_qsl(b.query)) <= set(parse_qsl(a.query)):
        return False
    if b.fragment.startswith(("/", "!/")):
        route, _, query = a.fragment.partition("?")
        want, _, want_query = b.fragment.partition("?")
        return _under(route, want) and set(parse_qsl(want_query)) <= set(parse_qsl(query))
    return True


# Opens a record's own page again and checks the saved values are shown. Takes the open page, the record page's address (a
# hash-route address works too), {control key: value}, {control key: kind}, the reader that waits for the page and returns its
# text (the executor passes plan_exec._quick_read), optionally the record page's text from before saving (read it with
# shown_text and pass before_shown=True; a reader's text works on most pages), and optionally {control key: the options it offers} (the scraped
# controls' "options"), which tells a saved choice apart from the other options. The page is always loaded afresh from the
# site, never shown from what the browser still holds: an address in the same document (a single-page app's route) is reached
# by way of a blank page. The values are looked for in shown_text's text. Returns readback_report's report plus "text" (the
# page's first 2,000 characters, as the reader read it); when the page cannot be opened or read, answers with an error, or is
# not the record's page (the site sent the browser to a login page or a list instead), every value is "unchecked" and "error"
# says why.
async def read_back(
    page: Page,
    url: str,
    values: dict[str, Any],
    kinds: dict[str, str],
    reader: Callable[[Page], Awaitable[str]],
    before: str = "",
    options: dict[str, list[str]] | None = None,
    before_shown: bool = False,
) -> dict[str, Any]:
    failed: dict[str, Any] = {"seen": [], "missing": [], "unchecked": list(values), "text": ""}
    if not url:
        return {**failed, "error": "no address to open the record again"}
    target = urljoin(page.url, url) if page.url.startswith("http") else url
    try:
        if _same_document(page.url, target):
            await page.goto("about:blank", timeout=_OPEN_TIMEOUT_MS)
        response = await page.goto(target, wait_until="domcontentloaded", timeout=_OPEN_TIMEOUT_MS)
        text = await reader(page)
    except Exception as exc:  # noqa: BLE001 - a page that will not open is reported, not raised
        return {**failed, "error": f"could not open {target}: {type(exc).__name__}: {str(exc)[:200]}"}
    failed["text"] = text[:_REPORT_CHARS]
    if response is not None and response.status >= 400:
        return {**failed, "error": f"the record's page answered {response.status}"}
    if not _same_record(page.url, target):
        return {**failed, "error": f"landed on {page.url}, not on the record's page {target}"}
    try:
        shown, labels = await _shown(page)
    except Exception as exc:  # noqa: BLE001 - the reader's text still holds every option the forms offer: not compared
        return {**failed, "error": f"could not read the page without its forms: {type(exc).__name__}: {str(exc)[:200]}"}
    if not _same_record(page.url, target):
        return {**failed, "error": f"landed on {page.url}, not on the record's page {target}"}
    old = before if before_shown else _old_text(before, labels) if before else ""  # shown_text's text has no labels to take out
    report = readback_report(values, kinds, shown, before=old, options=options)
    report["text"] = text[:_REPORT_CHARS]
    return report


# True when a read-back shows that this site displays the values that were saved: at least one value seen and no more than one
# missing. Takes a report from a record the live agent did; on a site where this is False, a read-back that misses values
# means nothing.
def site_shows_decisions(report: dict[str, Any]) -> bool:
    if report.get("error"):
        return False
    return len(report.get("seen", [])) >= 1 and len(report.get("missing", [])) <= 1
