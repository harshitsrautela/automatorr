"""Per-field-type normalizers applied before comparing expected and actual values."""

import re
from datetime import datetime
from typing import Any, Callable

# Keyword hints used to infer a field's type from its name

DATE_KEYWORDS = ("date", "dob", "birth", "created_at", "updated_at", "timestamp")
PHONE_KEYWORDS = ("phone", "mobile", "cell", "contact_number")
MONEY_KEYWORDS = ("salary", "price", "amount", "cost", "revenue", "wage", "pay")
EMAIL_KEYWORDS = ("email",)

# Date formats tried in order
_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
)

# Converts any value to a string with runs of whitespace collapsed and the ends trimmed.
# Takes a value and returns the cleaned string.
def _clean_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()

# Default normalizer: trims, collapses whitespace and lowercases.
# Takes a value and returns the normalized string, or "" if the value is None.
def normalize_string(value: Any) -> str:
    if value is None:
        return ""
    return _clean_str(value).lower()

# Parses common date formats into YYYY-MM-DD, falling back to the normalized string if none parse.
# Takes a value (string or datetime) and returns the date string, or "" if the value is None.
def normalize_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()

    raw = _clean_str(value)
    # A trailing "Z" is not parsed by %z directly
    candidate = raw.replace("Z", "+00:00")

    # First format that parses wins
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(candidate, fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue

    try:
        # Last resort: python's flexible ISO parser
        parsed = datetime.fromisoformat(candidate)
        return parsed.date().isoformat()
    except ValueError:
        # Unparseable: compare as a plain string
        return normalize_string(raw)

# Keeps only digits and drops a leading US country code '1' on 11-digit numbers.
# Takes a value and returns the digit string, or "" if the value is None.
def normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits

# Strips currency symbols and commas from a value and converts it to a float.
# Returns the float, or the normalized string if it is not numeric, or "" if the value is None.
def normalize_money(value: Any) -> float | str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    raw = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(raw)
    except ValueError:
        return normalize_string(value)

# Normalizes an email address using the default string normalizer.
# Takes a value and returns the trimmed, lowercased string.
def normalize_email(value: Any) -> str:
    return normalize_string(value)

FIELD_TYPE_NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "date": normalize_date,
    "phone": normalize_phone,
    "money": normalize_money,
    "email": normalize_email,
    "default": normalize_string,
}

# Exact field-name overrides, e.g. {"start_date": "date"}
FIELD_NAME_OVERRIDES: dict[str, str] = {}


# Guesses a field's type from its name; exact overrides win, then the keyword lists in order.
# Takes a field name and returns "date", "phone", "money", "email" or "default".
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


# Normalizes both values using the field's inferred type and checks whether they are equal.
# Takes a field name, the expected value and the actual value, and returns True if they match.
def values_match(field_name: str, expected: Any, actual: Any) -> bool:
    field_type = infer_field_type(field_name)
    normalizer = FIELD_TYPE_NORMALIZERS[field_type]
    return normalizer(expected) == normalizer(actual)
