"""Turns the AI's traces for several records into one recipe: the same steps with the record's values left as blanks."""

import re
from typing import Any

# Element attributes that name a thing stably, best first
_ATTR_PRIORITY = ("data-testid", "data-test", "data-cy", "id", "name", "aria-label", "placeholder", "href", "title")
# Values that look machine-generated and change between page loads
_DYNAMIC_ID = re.compile(r"^(:r[0-9a-z]+:|[0-9a-f]{8,}|\w*-?\d{5,}\w*)$", re.I)
# The most records' worth of traces compared against each other
_MAX_PAIR_TRACES = 4


# Replaces a record's values inside a text with {field} blanks, matching whole tokens only.
# Takes the text and the record's fields; returns the text with blanks.
def templatize(text: str, fields: dict[str, Any]) -> str:
    out = text
    pairs = sorted(
        ((name, str(value)) for name, value in fields.items() if value not in (None, "") and len(str(value)) <= 60),
        key=lambda p: -len(p[1]),
    )
    for name, value in pairs:
        pattern = r"(?<![0-9A-Za-z{])" + re.escape(value) + r"(?![0-9A-Za-z}])"
        out = re.sub(pattern, "{" + name + "}", out)
    return out


# Fills {field} blanks in a template with a record's values.
def fill(template: str, fields: dict[str, Any]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(fields.get(m.group(1), m.group(0))), template)


# Lists the ways to find an element again, best first, with the record's values turned into blanks.
# Takes the element info from a trace and the record's fields; returns a list of locator dicts.
def locator_candidates(el: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    node = el.get("node") or "*"
    attrs = el.get("attrs") or {}
    out: list[dict[str, Any]] = []
    for attr in _ATTR_PRIORITY:
        value = attrs.get(attr)
        if not value or (attr == "id" and _DYNAMIC_ID.match(value)):
            continue
        out.append({"node": node, "attr": attr, "value": templatize(value, fields)})
    if el.get("text"):
        out.append({"node": node, "text": templatize(el["text"], fields)})
    if el.get("xpath"):
        out.append({"node": node, "xpath": el["xpath"]})
    return out


# Builds a pattern that matches both labels, leaving the words that differ open (for "Approve request {id}" and "Reject request {id}").
# Takes two templated labels; returns the pattern, or None when they do not differ word-for-word.
def family_pattern(a: str, b: str) -> str | None:
    ta, tb = a.split(), b.split()
    if len(ta) != len(tb) or a == b:
        return None
    # Escape the literal words but keep {field} blanks readable so they can be filled in later
    parts = [re.sub(r"\\\{(\w+)\\\}", r"{\1}", re.escape(x)) if x == y else r"\S+" for x, y in zip(ta, tb)]
    return "^" + r"\s+".join(parts) + "$"


# Returns the clicks of a trace that point at something belonging to this record (the locator holds the record's values).
def _record_clicks(trace: list[dict[str, Any]], fields: dict[str, Any]) -> list[int]:
    found = []
    for i, entry in enumerate(trace):
        if entry["kind"] != "click" or not entry.get("el"):
            continue
        if any("{" in str(c.get("value", c.get("text", ""))) for c in locator_candidates(entry["el"], fields)):
            found.append(i)
    return found


# The pages a trace looked at before its act click, in the order first visited, with the record's values as blanks.
def _pages_before(trace: list[dict[str, Any]], upto: int, fields: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for entry in trace[: upto + 1]:
        url = entry.get("url")
        if url:
            url = templatize(url, fields)
            if url not in seen:
                seen.append(url)
    return seen


# Compares the AI's work on two records and builds a recipe: which pages to read, and which button to click at the end.
# The AI's route may differ between the records (a search box here, an extra click there); only the pages it looked at
# and the one thing it finally clicked have to agree. Takes (fields, trace) twice; returns (recipe or None, reason).
def compile_pair(fields_a: dict[str, Any], trace_a: list[dict[str, Any]], fields_b: dict[str, Any], trace_b: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    if not trace_a or not trace_b:
        return None, "a trace is empty"

    acts_a, acts_b = _record_clicks(trace_a, fields_a), _record_clicks(trace_b, fields_b)
    if not acts_a or not acts_b:
        return None, "the AI never clicked anything that belongs to the record (for example its own button)"
    ia, ib = acts_a[-1], acts_b[-1]
    ea, eb = trace_a[ia], trace_b[ib]

    # A position in the page (xpath) is never trusted for a record's own button: the same position can be a different row next time
    cands_a = [c for c in locator_candidates(ea["el"], fields_a) if "xpath" not in c]
    cands_b = [c for c in locator_candidates(eb["el"], fields_b) if "xpath" not in c]
    shared = next((ca for ca in cands_a if any(ca == cb for cb in cands_b)), None)
    act: dict[str, Any] = {}
    if shared is not None:
        act["locator"] = shared
    else:
        for ca in cands_a:
            if "attr" not in ca:
                continue
            cb = next((c for c in cands_b if c.get("attr") == ca["attr"] and c.get("node") == ca["node"]), None)
            pattern = family_pattern(ca["value"], cb["value"]) if cb else None
            if pattern:
                act["choose"] = {"node": ca["node"], "attr": ca["attr"], "pattern": pattern}
                break
        if not act:
            return None, "the final clicks have no label that both records share"

    page_a, page_b = templatize(ea.get("url") or "", fields_a), templatize(eb.get("url") or "", fields_b)
    if not page_a or page_a != page_b:
        return None, "the final click happened on different pages"

    pages_a, pages_b = _pages_before(trace_a, ia, fields_a), _pages_before(trace_b, ib, fields_b)
    read = [p for p in pages_a if p in pages_b and p != page_a]
    # The page with the final button is read first (the record's own row is there), then the others
    read = [page_a] + read
    return {
        "version": 2,
        "read": read,
        "act": {"page": page_a, "goal": eb.get("goal") or ea.get("goal") or "", **act},
    }, "ok"


# Builds a recipe from ONE record's trace. What differs between records (the other buttons in the same row, such as
# Reject and Hold next to Approve) is found on the page at run time, so one example is enough.
# Takes the record's fields and its trace; returns (recipe or None, reason).
def compile_single(fields: dict[str, Any], trace: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    acts = _record_clicks(trace, fields)
    if not acts:
        return None, "the AI never clicked anything that belongs to the record (for example its own button)"
    entry = trace[acts[-1]]
    cands = [c for c in locator_candidates(entry["el"], fields) if "attr" in c]
    pick = next((c for c in cands if "{" in c["value"]), None)
    if pick is None:
        return None, "the final button has no label that names the record"
    page = templatize(entry.get("url") or "", fields)
    if not page:
        return None, "the page of the final click is unknown"
    read = [page] + [p for p in _pages_before(trace, acts[-1], fields) if p != page]
    return {"version": 2, "read": read, "act": {"page": page, "goal": entry.get("goal") or "", "locator": pick, "siblings": "auto"}}, "ok"


# Builds a recipe from the saved traces (newest first as (record_id, fields, trace)).
# Two records that agree make the most reliable recipe; one record is enough when that is all there is.
# Returns (recipe or None, reason).
def compile_recipe(traces: list[tuple[str, dict[str, Any], list[dict[str, Any]]]]) -> tuple[dict[str, Any] | None, str]:
    recent = traces[:_MAX_PAIR_TRACES]
    if not recent:
        return None, "no successful record to learn from yet"
    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            recipe, _reason = compile_pair(recent[j][1], recent[j][2], recent[i][1], recent[i][2])
            if recipe is not None:
                return recipe, "ok"
    return compile_single(recent[0][1], recent[0][2])
