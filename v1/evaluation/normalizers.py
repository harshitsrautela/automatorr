"""
Per-field-type normalizers used before comparing expected vs. actual values.

The core idea: two values can be "the same" while looking completely
different as strings (a date as "1990-05-14" vs a Postgres timestamp, a phone
number with or without punctuation, "$75,000.00" vs 75000.0). Comparing raw
values would produce constant false failures that have nothing to do with
whether the agent actually did its job correctly.

Instead of one big comparison function, each field type gets its own
normalizer. Field type is guessed from the field name (see infer_field_type),
with a generic string-normalize fallback for anything unrecognized — since
the exact field set isn't fixed ahead of time (it comes from whatever source
JSON you load).

To override behavior for a specific field, just add its name to
FIELD_NAME_OVERRIDES below, or add a smarter keyword to the *_KEYWORDS lists.
"""

import re
from datetime import datetime
from typing import Any, Callable

# --- keyword hints used to infer a field's type from its name ---

DATE_KEYWORDS = ("date", "dob", "birth", "created_at", "updated_at", "timestamp")
PHONE_KEYWORDS = ("phone", "mobile", "cell", "contact_number")
MONEY_KEYWORDS = ("salary", "price", "amount", "cost", "revenue", "wage", "pay")
EMAIL_KEYWORDS = ("email",)

# Formats we'll try, in order, when parsing a date-like string.
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
)


def _clean_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_string(value: Any) -> str:
    """Default normalizer: strip, collapse whitespace, lowercase."""
    if value is None:
        return ""
    return _clean_str(value).lower()


def normalize_date(value: Any) -> str:
    """Parses common date/timestamp formats into a canonical YYYY-MM-DD (or
    full ISO if time-of-day is present and non-zero). Falls back to the
    cleaned raw string if nothing parses, so it still compares *something*
    rather than silently passing."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()

    raw = _clean_str(value)
    # ISO strings with a trailing "Z" aren't parsed by %z directly.
    candidate = raw.replace("Z", "+00:00")

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue

    try:
        # last resort: python's flexible ISO parser
        parsed = datetime.fromisoformat(candidate)
        return parsed.date().isoformat()
    except ValueError:
        return normalize_string(raw)


def normalize_phone(value: Any) -> str:
    """Strips everything but digits, and drops a leading country-code '1'
    on 11-digit US numbers so formatted/unformatted/with-country-code
    variants all compare equal."""
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_money(value: Any) -> float | str:
    """Strips currency symbols/commas and casts to float. Falls back to a
    normalized string if it truly isn't numeric."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    raw = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(raw)
    except ValueError:
        return normalize_string(value)


def normalize_email(value: Any) -> str:
    return normalize_string(value)


FIELD_TYPE_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "date": normalize_date,
    "phone": normalize_phone,
    "money": normalize_money,
    "email": normalize_email,
    "default": normalize_string,
}

# Exact field-name overrides, if the keyword inference gets something wrong
# for your specific form. e.g. {"start_date": "date", "office_phone": "phone"}
FIELD_NAME_OVERRIDES: dict[str, str] = {}


def infer_field_type(field_name: str) -> str:
    name = field_name.lower()

    if field_name in FIELD_NAME_OVERRIDES:
        return FIELD_NAME_OVERRIDES[field_name]

    if any(kw in name for kw in DATE_KEYWORDS):
        return "date"
    if any(kw in name for kw in PHONE_KEYWORDS):
        return "phone"
    if any(kw in name for kw in MONEY_KEYWORDS):
        return "money"
    if any(kw in name for kw in EMAIL_KEYWORDS):
        return "email"
    return "default"


def values_match(field_name: str, expected: Any, actual: Any) -> bool:
    """Normalizes both values according to the field's inferred type, then
    compares. This is the single entry point evaluator.py should use."""
    field_type = infer_field_type(field_name)
    normalizer = FIELD_TYPE_NORMALIZERS[field_type]
    return normalizer(expected) == normalizer(actual)
