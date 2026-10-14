"""Element fingerprint used to sanity-check a located element during replay."""

import hashlib
import re

STATIC_ATTRIBUTES = {
    "class",
    "id",
    "name",
    "type",
    "placeholder",
    "aria-label",
    "title",
    "role",
    "data-testid",
    "data-test",
    "data-cy",
    "data-selenium",
    "for",
    "required",
}

# Transient class-name fragments that should not affect the fingerprint
_DYNAMIC_CLASS_PATTERNS = ("hover", "focus", "active", "animat", "transition", "open", "closed")


# Drops transient state classes (hover, focus, open and similar) from a class string.
# Takes the class attribute text and returns the remaining classes sorted and space-joined.
def _filter_dynamic_classes(class_str: str | None) -> str:
    if not class_str:
        return ""
    classes = class_str.split()
    stable = [c for c in classes if not any(p in c.lower() for p in _DYNAMIC_CLASS_PATTERNS)]
    return " ".join(sorted(stable))


# Returns a SHA-256 hash of an element's tag name and static attributes.
# Takes a node name and an attributes dict; the same function must be used at write and replay time.
def compute_fingerprint(node_name: str, attributes: dict[str, str] | None) -> str:
    attributes = attributes or {}
    filtered: dict[str, str] = {}
    for key, value in attributes.items():
        if key not in STATIC_ATTRIBUTES:
            continue
        if key == "class":
            value = _filter_dynamic_classes(value)
            if not value:
                continue
        filtered[key] = value

    attributes_string = "".join(f"{k}={v}" for k, v in sorted(filtered.items()))
    normalized_node_name = re.sub(r"\s+", "", (node_name or "").lower())
    combined = f"{normalized_node_name}|{attributes_string}"
    return hashlib.sha256(combined.encode()).hexdigest()
