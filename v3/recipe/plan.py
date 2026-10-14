"""Turns the live AI's trace on one record into a plan: the same route through the website, with every value that
changes from record to record marked by where it comes from.

A value is one of three things:
  field     copied from the record ("{invoice_no}")
  slot      text the agent read on an earlier page and typed or clicked later ("{s1}", found again by a small model call)
  computed  worked out by the agent (the options it picked, a date, a note): decided again for every record

The route up to the first computed value is replayed by code ("gather"). From there on the form is filled with the
decided values and submitted ("commit"). A plan is only trusted after it has agreed with the live agent on a few
records (see orchestrator.py), because one trace cannot prove which parts of a route are fixed and which vary."""

import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from recipe.compile import fill, templatize
from recipe.webkit import DISMISS

PLAN_VERSION = 4
# How many records the plan must agree with the live agent on before it is trusted, and the most tried to find varied ones
PROOF_NEEDED = 2
PROOF_MAX = 3

# Path pieces that look like ids (hex ids, numbers, "V-108", "po001") and are not part of a page's identity
_ID_SEGMENT = re.compile(
    r"^(?:[0-9a-f]{6,}|\d+|[A-Za-z]{1,4}-\d+|[A-Za-z0-9]*\d{3,}[A-Za-z0-9]*"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # a UUID
    r"|(?=(?:[A-Za-z]*\d){2})(?=\d*[A-Za-z])[A-Za-z0-9]{5,})$",  # a short random id ("k3f9a2"): letters and two or more digits
    re.I,
)
# Attribute values that name one particular thing (an id with digits in it) and so cannot be reused for another record
_IDENTITY_LIKE = re.compile(r"\d{3,}|[0-9a-f]{8,}", re.I)
_STABLE_ATTRS = ("data-testid", "data-test", "data-cy", "id", "name", "aria-label", "title", "placeholder", "href")
_ROLES = {"a": "link", "button": "button", "select": "combobox", "textarea": "textbox", "input": "textbox"}
_INPUT_ROLES = {"search": "searchbox", "checkbox": "checkbox", "radio": "radio", "button": "button", "submit": "button", "reset": "button", "number": "spinbutton", "range": "slider"}
# Roles an element can be given with role="..." that the page's accessibility tree then reports instead of its tag's
_ARIA_ROLES = {"button", "link", "tab", "menuitem", "menuitemradio", "menuitemcheckbox", "option", "radio", "checkbox", "switch", "treeitem", "row", "cell",
               "gridcell", "combobox", "textbox", "searchbox"}
# Controls that move through a paged list: page numbers, Next, Prev. The record is found by paging at run time instead.
_PAGER_TEXT = re.compile(r"^(?:\d{1,3}|(?:next|next page|previous|previous page|prev|first|last)(?:\s*[›»>→])?|[‹«<←]\s*(?:prev\w*)?|[›»>→])$", re.I)
_PAGER_HREF = re.compile(r"[?&](?:page|p|pg|pageno)=\d+", re.I)
# Buttons that show more rows of a list ("Load more"): like a pager, the record is found by pressing them at run time
_LOAD_MORE_TEXT = re.compile(r"^(?:(?:load|show|see|view)\s+more(?:\s+(?:rows|results|items|entries|records))?|more\s+results)\s*[.…›»>↓]*$", re.I)
# Buttons on an error box that load the page again: only needed when the error shows, so such a step is optional
_RETRY_TEXT = re.compile(r"^(?:retry|try again|reload)$", re.I)
# Buttons that save the form, as opposed to buttons that only help to fill it in (a calendar's day buttons)
_SUBMIT_TEXT = re.compile(r"^(?:save|submit|confirm|apply|update|ok|done|finish|send|continue|approve|reject)\b", re.I)


# Reduces an address to its page type: the path with ids replaced by "*", plus the names (not values) of its query parameters.
# "/invoices/e57b1a67" and "/invoices/0a1b2c3d" give the same shape; so do "?q=A&status=" and "?status=&q=B".
# own_page: the address was reached by clicking the record's own link (or a value read for it), so its last part names the
# record even when it does not look like an id ("/#/claims/kxqfab"); it is replaced by "*" too.
def url_shape(url: str | None, own_page: bool = False) -> str:
    parts = urlsplit(url or "")
    # A single-page app that routes in the address's fragment ("/#/claims/a1b2c3") has its pages there
    route = re.match(r"^!?(/[^?]*)(?:\?(.*))?$", parts.fragment or "")
    shape = _path_shape(parts.path, parts.query, own_page and not route)
    if route:
        shape += "#" + _path_shape(route.group(1), route.group(2) or "", own_page)
    return shape


def _path_shape(path: str, query: str, own_page: bool = False) -> str:
    segments = ["*" if _ID_SEGMENT.match(s) else s for s in path.rstrip("/").split("/")]
    if own_page and len(segments) > 1 and segments[-1]:
        segments[-1] = "*"
    out = "/".join(segments) or "/"
    names = sorted({k for k, _v in parse_qsl(query, keep_blank_values=True)})
    return out + ("?" + "&".join(names) if names else "")


# True when an address has the page type a step expects. A "*" in the expected type stands for any one part of the address.
def shape_matches(url: str | None, expect: str) -> bool:
    actual = url_shape(url)
    if actual == expect:
        return True
    a_parts, e_parts = re.split(r"([#?])", actual), re.split(r"([#?])", expect)
    if len(a_parts) != len(e_parts):
        return False
    for a, e in zip(a_parts, e_parts):
        if a == e:
            continue
        a_seg, e_seg = a.split("/"), e.split("/")
        if len(a_seg) != len(e_seg) or any(x != y and y != "*" for x, y in zip(a_seg, e_seg)):
            return False
    return True


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# True when the value occurs in the text as a whole token (not inside a longer word or number).
def _occurs(text: str, value: str) -> bool:
    return bool(value) and re.search(r"(?<![0-9A-Za-z])" + re.escape(value) + r"(?![0-9A-Za-z])", text) is not None


# Fills {field} and {slot} blanks of a template in one pass; leaves unknown blanks untouched.
def render(template: str, fields: dict[str, Any], slots: dict[str, str]) -> str:
    return fill(template, {**fields, **slots})


def has_blank(text: str) -> bool:
    return re.search(r"\{\w+\}", text) is not None


# Replaces known values inside a text with their blanks: the record's own values first, then the plan's slot values.
def _blank_out(text: str, fields: dict[str, Any], slot_values: dict[str, str]) -> str:
    out = templatize(text, fields)
    for name, value in sorted(slot_values.items(), key=lambda kv: -len(kv[1])):
        if value and len(value) >= 2:
            out = re.sub(r"(?<![0-9A-Za-z{])" + re.escape(value) + r"(?![0-9A-Za-z}])", "{" + name + "}", out)
    return out


# The ways to find an element again, best first. A way whose value names one particular thing (an id with digits) is dropped
# unless the value was turned into a blank, because it would point at the first record's item for every record.
def element_locators(el: dict[str, Any], fields: dict[str, Any], slot_values: dict[str, str], hint: str = "") -> list[dict[str, Any]]:
    node = (el.get("node") or "").lower()
    attrs = el.get("attrs") or {}
    found: list[dict[str, Any]] = []
    for attr in _STABLE_ATTRS:
        value = attrs.get(attr)
        if not value:
            continue
        blanked = _blank_out(str(value), fields, slot_values)
        if not has_blank(blanked) and _IDENTITY_LIKE.search(blanked):
            continue
        found.append({"css": node or "*", "attr": attr, "value": blanked})
    text = _norm(el.get("text"))
    explicit = (attrs.get("role") or "").strip().lower()
    if text and (node in _ROLES or explicit in _ARIA_ROLES):
        # an element's own role attribute wins (a <button role="tab"> is a tab to the page's accessibility tree)
        itype = (attrs.get("type") or "").lower()
        role = explicit if explicit in _ARIA_ROLES else _INPUT_ROLES.get(itype, "textbox") if node == "input" else _ROLES[node]
        blanked = _blank_out(text, fields, slot_values)
        if has_blank(blanked) or not _IDENTITY_LIKE.search(blanked):
            found.append({"role": role, "name": blanked})
    elif text and len(text) <= 80:
        # A plain element (a table cell, a span) the agent clicked: found again by its exact visible text
        blanked = _blank_out(text, fields, slot_values)
        if has_blank(blanked) or not _IDENTITY_LIKE.search(blanked):
            found.append({"text": blanked})
    if not found and not text:
        # A container with no name of its own (a clickable table row): the one of its kind that contains the record's value. The
        # value comes from the text inside it, or, for a trace recorded without that, from the agent's own goal for the step
        # ("click the row for claim EXP-41142") when the element is a container
        source = _norm(el.get("inner")) or (_norm(hint) if node in _CONTAINERS or (attrs.get("role") or "") in ("row", "listitem", "option", "gridcell") else "")
        blanks = re.findall(r"\{(\w+)\}", _blank_out(source, fields, slot_values)) if source else []
        if blanks and node:
            found.append({"css": node, "contains": "{" + _naming_blank(blanks, fields, slot_values, hint) + "}"})
    return found


# Of the record's values printed inside a clicked container (a table row), the one that names the record: the one the agent's
# goal for the step mentions ("open invoice INV-1001"), else one that looks like an id (a vendor's name is shared by many
# rows), else the first. Takes the blanks found in the container's text (in order), the fields, the slot values and the
# step's goal; returns the chosen blank's name.
def _naming_blank(blanks: list[str], fields: dict[str, Any], slot_values: dict[str, str], hint: str) -> str:
    in_goal = set(re.findall(r"\{(\w+)\}", _blank_out(_norm(hint), fields, slot_values))) if hint else set()
    for name in blanks:
        if name in in_goal:
            return name
    values = {**{k: str(v) for k, v in fields.items()}, **slot_values}
    for name in blanks:
        if _IDENTITY_LIKE.search(values.get(name, "")):
            return name
    return blanks[0]


# Elements that hold other content and are made clickable as a whole (a table row, a card)
_CONTAINERS = {"tr", "li", "div", "article", "section", "td", "span", "p", "dl", "label"}


# The text of a way of finding an element that may hold blanks (an attribute value, an accessible name or a visible text).
def spec_text(spec: dict[str, Any]) -> str:
    return str(spec.get("value", spec.get("name", spec.get("text", spec.get("contains", "")))))


# True when a page's text says it is an error page ("Page not found", "404"): short, and saying so.
def _dead_end(page_text: str) -> bool:
    text = _norm(re.sub(r"\*?\[\d+\]<[^>]*>", " ", page_text or ""))
    return bool(text) and len(text) < 400 and re.search(r"\b(?:404|not found|does ?n[o']t exist|no such page|page unavailable)\b", text, re.I) is not None


# True when a click only moves through a paged list (a page number, Next, Prev, or a "Load more" that stays on the page).
# Takes the trace entry and the address the next entry was on (None when unknown).
def _is_pager(e: dict[str, Any], landing: str | None = None) -> bool:
    el = e.get("el") or {}
    if e.get("kind") == "navigate":
        return bool(_PAGER_HREF.search(str(e.get("value") or "")))  # the agent typed the address of a list's page
    if e.get("kind") != "click":
        return False
    if _LOAD_MORE_TEXT.match(_norm(el.get("text"))) and (el.get("node") in ("a", "button", "input") or _role(el) == "button"):
        # a "Load more" button: the list is grown at run time until the record shows. One that opened another page ("View
        # more" on a record that leads to its details) is a step of the route
        return landing is None or landing == e.get("url")
    if el.get("node") not in ("a", "button"):
        return False
    return bool(_PAGER_TEXT.match(_norm(el.get("text")))) or bool(_PAGER_HREF.search((el.get("attrs") or {}).get("href", "")))


# The key a form control is found by: its name (stable; a group of radio buttons or checkboxes shares one), else its id. Many UI
# libraries give controls a new random id every time they are drawn, so the name comes first.
def control_key(attrs: dict[str, Any]) -> str | None:
    return attrs.get("name") or attrs.get("id") or None


# The role an element's markup gives it (its role attribute), in lower case; "" when it has none.
def _role(el: dict[str, Any] | None) -> str:
    return str(((el or {}).get("attrs") or {}).get("role") or "").strip().lower()


# A label as a page prints it, without trailing colons, stars and spaces ("Outcome *:" -> "Outcome").
def _tidy(text: Any) -> str:
    return re.sub(r"[\s:*]+$", "", _norm(text))


# The id of an element when it stays the same from one page load to the next; "" for an id a framework generated (":r3:",
# "f-k3j9x2", "field-20931"). Takes the id; returns it or "". Matches the page-side rule in recipe/webkit.py.
def _stable_id(value: Any) -> str:
    text = str(value or "")
    if not text or ":" in text or re.search(r"\d{3,}", text):
        return ""
    for part in re.split(r"[-_.]", text):
        if len(part) >= 5 and re.search(r"\d", part) and re.search(r"[A-Za-z]", part) and not re.match(r"^[A-Za-z]+\d{1,2}$", part):
            return ""
    return text


# True when an element is a box whose choices show in a list as you type or click (role=combobox, aria-haspopup=listbox, or
# aria-autocomplete). A native <select> is not one. Takes the trace's element; returns True or False.
def _is_combo(el: dict[str, Any] | None) -> bool:
    attrs = (el or {}).get("attrs") or {}
    if (el or {}).get("node") == "select":
        return False
    autocomplete = str(attrs.get("aria-autocomplete") or "").lower()
    return _role(el) == "combobox" or str(attrs.get("aria-haspopup") or "").lower() == "listbox" or autocomplete not in ("", "none")


# True when an element is a rich-text box (contenteditable) rather than a native field. Takes the trace's element.
def _editable(el: dict[str, Any] | None) -> bool:
    attrs = (el or {}).get("attrs") or {}
    return "contenteditable" in attrs and str(attrs.get("contenteditable")).strip().lower() != "false"


# True when an element is a choice made with ARIA roles (a <div role="radio">), not a native radio button or checkbox.
def _aria_choice(el: dict[str, Any] | None) -> bool:
    return choice_type(el) is not None and (el or {}).get("node") != "input"


# The key a form control the agent touched is known by, the same one the run-time form readers give it: an ARIA radio button
# is "aria:" + its group's name, and so is an ARIA checkbox or switch whose group holds only boxes (the recorder notes it);
# any other ARIA box is "aria:" + its own name; a combobox its input's name, a stable id, or "combo:" + its name; a rich-text box a stable id or
# "edit:" + its name; any other field its name or id (control_key). Takes the trace's element; returns the key or None.
def trace_key(el: dict[str, Any] | None) -> str | None:
    attrs = (el or {}).get("attrs") or {}
    if _aria_choice(el):
        group = _tidy((el or {}).get("group"))
        if choice_type(el) == "checkbox" and _role(el) in ("checkbox", "switch") and not (el or {}).get("group_of_boxes"):
            own = _tidy((el or {}).get("text"))  # a box on its own: the run-time reader names it by its own words
            return "aria:" + own if own else None
        return "aria:" + group if group else None
    if _is_combo(el):
        label = _tidy((el or {}).get("text"))
        return attrs.get("name") or _stable_id(attrs.get("id")) or ("combo:" + label if label else None)
    if _editable(el):
        return _stable_id(attrs.get("id")) or "edit:" + (_tidy((el or {}).get("text")) or "note")
    return control_key(attrs)


# True when two of the trace's elements are the same element (the same place on the page, or the same name or id).
def _same_element(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not a or not b:
        return False
    if a.get("xpath") and a.get("xpath") == b.get("xpath"):
        return True
    aa, ba = a.get("attrs") or {}, b.get("attrs") or {}
    return any(aa.get(k) and aa.get(k) == ba.get(k) for k in ("name", "id"))


# Finds a choice made in a combobox: from a click on (or typing into) the box at entry i, through more clicks, typing and
# keys on the same box, to a click on one of its options (role=option). Typing the record's own value (a search) is not a
# choice. Takes the entries, the index and the record's fields; returns the index of the option's click, or None.
def _combo_choice(entries: list[dict[str, Any]], i: int, fields: dict[str, Any]) -> int | None:
    el = entries[i].get("el")
    if entries[i].get("kind") not in ("click", "input") or not _is_combo(el):
        return None
    j = i
    while j < len(entries):
        e = entries[j]
        if e.get("kind") == "input" and has_blank(templatize(_norm(e.get("value")), fields)):
            return None  # the record's own value typed in: a search, part of the route
        if j == i or (e.get("kind") == "keys" and not e.get("el")) or (e.get("kind") in ("click", "input", "keys") and _same_element(e.get("el"), el)):
            j += 1
            continue
        break
    if j < len(entries) and entries[j].get("kind") == "click" and _role(entries[j].get("el")) == "option":
        return j
    return None


# "radio" or "checkbox" when an element is a choice control (a native one, or one marked up with a role); else None.
def choice_type(el: dict[str, Any] | None) -> str | None:
    attrs = (el or {}).get("attrs") or {}
    if (el or {}).get("node") == "input" and (attrs.get("type") or "").lower() in ("radio", "checkbox"):
        return attrs["type"].lower()
    role = (attrs.get("role") or "").lower()
    return {"radio": "radio", "checkbox": "checkbox", "switch": "checkbox", "menuitemradio": "radio", "menuitemcheckbox": "checkbox"}.get(role)


# Buttons that move a form that has several steps on to its next step, without saving it
# Buttons that take a form in several steps back one step
_BACK_TEXT = re.compile(r"^(?:back|previous|prev|go back|previous step|‹ back|← back)$", re.I)
_ADVANCE_TEXT = re.compile(r"^(?:next|next step|continue|proceed|go on|forward)\b", re.I)


def _advance_like(op: dict[str, Any]) -> bool:
    el = op.get("el") or {}
    return el.get("node") in ("button", "a", "input") and bool(_ADVANCE_TEXT.match(_norm(el.get("text")))) and ((el.get("attrs") or {}).get("type") or "").lower() != "submit"


# True when a click saves the form (a submit button, or one named Save / Confirm / ...).
def _submit_like(op: dict[str, Any]) -> bool:
    el = op.get("el") or {}
    return (((el.get("attrs") or {}).get("type") or "").lower() == "submit") or bool(_SUBMIT_TEXT.match(_norm(el.get("text"))))


# Words of a button that changes something on the site when pressed (saves, decides, assigns, starts a review), as opposed to
# one that only shows something; and of a submit button that only searches or signs in
_SAVES_TEXT = re.compile(r"^(?:save|submit|confirm|send|approve|reject|decline|finish|complete|assign|claim|start|resolve|escalate|pay|"
                         r"delete|remove|archive|refund|issue|accept|place|mark|record|update)\b", re.I)
_LOOKS_TEXT = re.compile(r"^(?:search|find|go|filter|apply(?: filters?)?|show|view|open|look ?up|sign ?in|log ?in|next|continue|refresh)\b", re.I)
# Words of an instruction that make a choice between outcomes: then one record's button cannot stand for every record's
_CHOOSES = re.compile(r"\b(?:if|otherwise|else|unless|depending|whether)\b", re.I)


# True when a route's click may change something on the site: a button (not a plain link to another page) named like one that
# saves or decides (Save, Approve, Assign to me, Start review), or a submit button that does not only search or sign in. A
# pop-up's optional button never is. Takes the step (with its trace element).
def _saving_click(op: dict[str, Any]) -> bool:
    if op.get("op") != "click" or op.get("optional"):
        return False
    el = op.get("el") or {}
    attrs = el.get("attrs") or {}
    href = str(attrs.get("href") or "")
    if (el.get("node") == "a" or _role(el) == "link") and href and not href.startswith(("#", "javascript")):
        return False  # a link opens a page: it changes nothing
    if _role(el) not in ("", "button", "link") or el.get("node") not in ("button", "input", "a"):
        return False  # an option, a tab, a choice or a row: it picks or shows something, it is not a button
    text = _norm(el.get("text"))
    if _SAVES_TEXT.match(text):
        return True
    return str(attrs.get("type") or "").lower() == "submit" and bool(text) and not _LOOKS_TEXT.match(text)


# Removes the way back from side trips. When the route returns to the very address it reached earlier (the record's page, after
# a look at its order) through pages it had already been through (the list, paged again), those steps are replaced by one
# "revisit" step that opens the address the plan itself reached at that point for this record. The side trip itself stays: its
# pages are read. Takes the route's steps (each with the address it landed on in next_url) and the start address.
def _cut_detours(ops: list[dict[str, Any]], start: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for op in ops:
        out.append(op)
        land = op.get("next_url")
        if op["op"] not in ("click", "back", "goto") or not land or op.get("url") == land:
            continue  # only a step that moved to another address can be the way back
        for k in range(len(out) - 3, -1, -1):
            if out[k].get("next_url") != land:
                continue
            if all(o.get("next_url") == land for o in out[k + 1: -1]):
                break  # never left that address (a tab, a section opened in place): not a side trip
            known = {url_shape(start)} | {url_shape(o.get("next_url")) for o in out[: k + 1]}
            q = len(out) - 1
            while q - 1 > k + 1 and url_shape(out[q - 1].get("next_url")) in known:
                q -= 1
            out[q:] = [{"op": "revisit", "to": k, "el": None, "i": out[q]["i"], "url": out[q].get("url"), "next_url": land}]
            break
    return out


def _blanks_in(candidates: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for c in candidates:
        names.update(re.findall(r"\{(\w+)\}", spec_text(c)))
    return names


# Builds a plan from one successful record's trace. Takes the record's fields, the trace entries and the task's instructions
# (a typed value written there word for word, such as a username, is a constant of the plan). Returns (plan or None, reason).
# The reason says in plain words why no plan could be made.
def compile_plan(fields: dict[str, Any], trace: list[dict[str, Any]], instructions: str = "") -> tuple[dict[str, Any] | None, str]:
    entries = [e for e in trace if e.get("kind") in ("click", "input", "select", "keys", "navigate", "back")]
    if not entries:
        return None, "the AI did nothing that can be repeated"
    if any(e["kind"] in ("click", "input", "select") and not e.get("el") for e in entries):
        return None, "the AI acted on something that could not be identified"
    # A value written in the instructions is only a constant on a page the route leaves before the form (a sign-in, a list's
    # filter): on the form itself it is one of the decided values. Each pass finds such values that turned out to be on the
    # form, and the next pass decides them instead
    decided: set[int] = set()
    while True:
        plan, why, more = _compile(fields, entries, instructions, decided)
        if not more - decided:
            return plan, why
        decided |= more


# One pass of compile_plan over the trace's repeatable entries. decided holds the entries whose value, though written in the
# instructions, is decided for every record (it is on the form). Returns (plan or None, reason, the entries typed or picked
# as constants that are on the form after all: when not empty, the plan is to be made again with them decided).
def _compile(fields: dict[str, Any], entries: list[dict[str, Any]], instructions: str, decided: set[int]) -> tuple[dict[str, Any] | None, str, set[int]]:

    slot_values: dict[str, str] = {}  # slot name -> the first record's value (only used to find blanks)
    slots: dict[str, dict[str, Any]] = {}
    seen_pages: list[tuple[str, str]] = []  # (url, text) of the pages the agent had seen before the current step
    page_texts: dict[str, str] = {}
    ops: list[dict[str, Any]] = []  # in trace order; each op also remembers its entry index
    computed_at: int | None = None
    after_pager = False  # the clicks just before this one only moved through a paged list
    consumed: set[int] = set()  # entries already taken into an earlier step (the clicks and typing of a combobox choice)

    first_text: dict[str, str] = {}
    for e in entries:
        if e.get("page_text") and e.get("url") and e["url"] not in first_text:
            first_text[e["url"]] = e["page_text"]

    for i, e in enumerate(entries):
        url = e.get("url") or ""
        if e.get("page_text") and url not in page_texts:
            page_texts[url] = e["page_text"]
        landing = entries[i + 1].get("url") if i + 1 < len(entries) else None
        if e["kind"] in ("navigate", "click", "back") and computed_at is None and landing and _dead_end(first_text.get(landing, "")):
            continue  # the step only reached an error page (an address the agent guessed): not part of the route
        if i in consumed:
            continue
        before = list(seen_pages)
        el = e.get("el")
        kind = e["kind"]
        value = e.get("value")
        last = i  # the last entry this step takes in (a combobox choice takes several)
        option_at = _combo_choice(entries, i, fields)
        option_text = _norm((entries[option_at].get("el") or {}).get("text")) if option_at is not None else ""

        if option_at is not None and computed_at is None and i not in decided and option_text and _occurs(instructions or "", option_text):
            # a choice written in the instructions, before the form (a list's status filter): steps of the route. The box is
            # opened here and the option's own click is the next step (text typed to narrow the list is not needed)
            consumed.update(range(i + 1, option_at))
            op = {"op": "click", "el": el, "i": i, "const": True}
        elif option_at is not None:
            # a choice in a combobox (click or type, then pick an option): one form control, decided for every record
            consumed.update(range(i + 1, option_at + 1))
            last = option_at
            if computed_at is None:
                computed_at = len(ops)
            op = {"op": "combo", "value": {"computed": True}, "el": el, "option": option_text, "i": i}
        elif kind in ("input", "select"):
            raw = _norm(value)
            templated = templatize(raw, fields) if raw else ""
            if raw and has_blank(templated):
                spec: dict[str, Any] = {"template": templated}
            elif kind == "input" and raw and len(raw) >= 2 and computed_at is None and any(_occurs(t, raw) for _u, t in before):
                name = next((n for n, v in slot_values.items() if v == raw), None)
                if name is None:
                    name = f"s{len(slot_values) + 1}"
                    slot_values[name] = raw
                    source = next(u for u, t in before if _occurs(t, raw))
                    slots[name] = {"example": raw, "seen_on": url_shape(source), "hint": _norm(e.get("goal"))[:200]}
                spec = {"template": "{" + name + "}"}
            elif kind == "input" and raw and len(raw) >= 2 and computed_at is None and i not in decided and _occurs(instructions or "", raw):
                spec = {"template": raw}  # written in the task's instructions (a username, a fixed filter): the same for every record
            else:
                spec = {"computed": True}
                if computed_at is None:
                    computed_at = len(ops)
            op: dict[str, Any] = {"op": "fill" if kind == "input" else "select", "value": spec, "el": el, "i": i}
            if "template" in spec and not has_blank(spec["template"]):
                op["const"] = True
        elif kind == "click" and computed_at is None and (el or {}).get("node") in ("details", "summary"):
            continue  # opening a collapsed section: every page read opens them all (plan_exec._quick_read)
        elif kind == "click" and computed_at is None and url == landing and not choice_type(el) and not element_locators(el or {}, fields, slot_values, hint=_norm(e.get("goal"))):
            continue  # a click on something that cannot be found again and did not change the page: not needed to reach anything
        elif kind == "click" and choice_type(el):
            if computed_at is None:
                computed_at = len(ops)
            op = {"op": "choose", "value": {"computed": True}, "el": el, "i": i}
        elif kind in ("click", "navigate") and computed_at is None and _is_pager(e, landing):
            after_pager = True
            continue  # not part of the route: the record is found by paging at run time
        elif kind == "click":
            if computed_at is None and _is_pager(e, landing):
                after_pager = True
                continue  # not part of the route: the record is found by paging at run time
            text = _norm((el or {}).get("text"))
            if computed_at is None and text and len(text) >= 3 and _IDENTITY_LIKE.search(text) and not has_blank(templatize(text, fields)):
                # A link whose text is an id (an order number): it belongs to this record, so its text is read off the page
                name = next((n for n, v in slot_values.items() if v == text), None)
                if name is None:
                    name = f"s{len(slot_values) + 1}"
                    slot_values[name] = text
                    slots[name] = {"example": text, "seen_on": url_shape(url), "hint": _norm(e.get("goal"))[:200]}
            op = {"op": "click", "el": el, "i": i}
            if computed_at is None and url == landing and (DISMISS.match(text) or _RETRY_TEXT.match(text)):
                # closing a cookie banner or a pop-up, or a Retry on an error box: only there sometimes, so skipped when absent
                op["optional"] = True
            if after_pager:
                op["after_pager"] = True
            after_pager = False
        elif kind == "keys":
            op = {"op": "keys", "keys": value, "el": None, "i": i}
        elif kind == "navigate":
            op = {"op": "goto", "target": templatize(str(value or ""), fields), "el": None, "i": i}
        else:
            op = {"op": "back", "el": None, "i": i}
        op["url"] = url
        op["next_url"] = entries[last + 1].get("url") if last + 1 < len(entries) else None
        ops.append(op)
        if url and e.get("page_text"):
            seen_pages.append((url, e["page_text"]))

    if computed_at is None:
        gather, commit_ops = ops, []
    else:
        gather, commit_ops = ops[:computed_at], ops[computed_at:]
    submit_ops: list[dict[str, Any]] = []
    # A password typed on the way must be part of the route (a field of the record, or written in the instructions): the plan
    # signs in again with it when a session runs out
    password_i = next((k for k, e in enumerate(entries) if e["kind"] == "input" and str(((e.get("el") or {}).get("attrs") or {}).get("type") or "").lower() == "password"), None)
    password_at = entries[password_i] if password_i is not None else None
    # a constant typed (or picked) on a page the route never leaves before the form (or before the end) is on the form:
    # decided instead. The sign-in's own fields stay constants even when signing in does not change the address
    on_form: set[int] = set()
    for k, o in enumerate(gather):
        if not o.get("const"):
            continue
        if password_at is not None and o.get("url") == password_at.get("url") and o["i"] <= password_i:
            continue
        if not any(p.get("url") == o.get("url") and p.get("next_url") and p.get("next_url") != o.get("url") for p in gather[k:]):
            on_form.add(o["i"])
            continue
        # a value typed (or picked) on a page whose next button saves is put into a form, even when saving then leaves the
        # page: one of the decided values ("set Status to Approved if ..."), never the same for every record
        for p in gather[k + 1:]:
            if p.get("url") != o.get("url"):
                break
            if _saving_click(p):
                on_form.add(o["i"])
                break
            if p.get("next_url") and p.get("next_url") != o.get("url"):
                break
    if on_form:
        return None, "", on_form
    if password_at is not None and not any(o["op"] == "fill" and o["el"] is password_at.get("el") for o in gather):
        return None, "the AI signed in with a password that is not written in the task's instructions, so the plan could not sign in again", set()
    signin_url = password_at.get("url") if password_at is not None else None
    if not commit_ops:
        # No form: the button that saves (Approve on the record's page) is the plan's saving step, pressed once at the end and
        # never in a dry run; the steps after it (back to the list) are not needed. When the instructions choose between
        # outcomes, one record's button cannot stand for every record's, so no plan is made
        # the last such button is the saving step; an earlier one (Start review, then Approve) stays a route step marked as saving
        first = max((k for k, o in enumerate(gather) if _saving_click(o) and o.get("url") != signin_url), default=None)
        if first is not None:
            if _CHOOSES.search(instructions or ""):
                return None, f"the AI decided by pressing {_norm((gather[first]['el'] or {}).get('text'))!r}, a button the instructions choose between, with no form to fill", set()
            end = first
            while end + 1 < len(gather) and gather[end + 1]["op"] == "click" and gather[end].get("next_url") in (None, gather[end].get("url")):
                end += 1  # a confirmation on the same page ("Yes, approve")
            submit_ops = gather[first: end + 1]
            gather = gather[:first]
    gather = _cut_detours(gather, entries[0].get("url") or "")

    # What the commit phase looks like: form controls (typed or chosen), widgets (runs of clicks that fill a field, like a
    # calendar), and at the end the clicks that save. A run of clicks after the last control counts as a widget until the
    # first button that saves.
    controls: list[dict[str, Any]] = []
    advance_ops: list[dict[str, Any]] = []  # the buttons between the steps of a form with several steps (a wizard's Next)
    if commit_ops:
        last_control = max((k for k, o in enumerate(commit_ops) if o["op"] in ("fill", "select", "choose", "combo")), default=-1)
        trailing = commit_ops[last_control + 1:]
        split = next((k for k, o in enumerate(trailing) if o["op"] == "click" and _submit_like(o)), 0)
        sequence, submit_ops = commit_ops[: last_control + 1] + trailing[:split], trailing[split:]
        run: list[dict[str, Any]] = []

        def flush_widget() -> None:
            if not run:
                return
            first = run[0]["el"] or {}
            label = _norm(first.get("text")) or (first.get("attrs") or {}).get("aria-label") or (first.get("attrs") or {}).get("title") or "a widget"
            goals = " / ".join(g for g in (_norm(entries[o["i"]].get("goal")) for o in run) if g)
            controls.append({
                "key": f"widget{sum(1 for c in controls if c['type'] == 'widget') + 1}", "node": "widget", "type": "widget", "label": label, "hint": goals[:300],
                "value": {"computed": True}, "stage": position[0],
                # how to open it again (the first click of the run), for pickers the plan can drive itself
                "opener": element_locators(first, fields, slot_values) if first.get("node") in ("button", "a", "input") else [],
            })
            run.clear()

        position = [0]  # the step of the form the agent is on: Next moves it on, Back moves it back
        for o in sequence:
            if o["op"] == "click" and not run and _BACK_TEXT.match(_norm((o.get("el") or {}).get("text"))) and o["el"].get("node") in ("button", "a", "input"):
                position[0] = max(0, position[0] - 1)  # the agent went back a step to change something: not a field of the form
                continue
            if o["op"] in ("fill", "select", "choose", "combo"):
                flush_widget()
                el = o["el"]
                attrs = el.get("attrs") or {}
                key = trace_key(el)
                if not key and o["op"] == "choose" and _aria_choice(el):
                    if _role(el) in ("checkbox", "switch"):
                        return None, f"the AI clicked a {_role(el)} with no name of its own, so it cannot be found again", set()
                    return None, f"the AI clicked a choice ({_role(el)} {_norm(el.get('text'))!r}) that is in no named group (radiogroup or group), so it cannot be found again", set()
                if not key:
                    return None, "a form field has neither an id nor a name to find it by", set()
                if o["op"] in ("choose", "combo") and any(c["key"] == key for c in controls):
                    continue  # another box of the same group (or the same one clicked again)
                stage = position[0]
                if o["op"] == "combo":
                    controls.append({"key": key, "node": "select", "type": "combobox", "label": _tidy(el.get("text")), "value": o["value"], "stage": stage})
                elif o["op"] == "fill" and _editable(el):
                    controls.append({"key": key, "node": "textarea", "type": "richtext", "label": _tidy(el.get("text")), "value": o["value"], "stage": stage})
                elif o["op"] == "choose" and _aria_choice(el):
                    controls.append({"key": key, "node": "input", "type": choice_type(el), "label": key[len("aria:"):], "value": o["value"], "stage": stage})
                else:
                    kind = choice_type(el) if o["op"] == "choose" else (attrs.get("type") or "")
                    controls.append({"key": key, "node": el.get("node"), "type": kind, "label": _norm(el.get("text")), "value": o["value"], "stage": stage})
            elif o["op"] == "click" and _advance_like(o) and not run:
                if position[0] == len(advance_ops):
                    advance_ops.append(o)  # the form moves on to a step not seen before
                position[0] += 1
            elif o["op"] == "click":
                run.append(o)
            else:
                return None, "the AI pressed keys or changed page in the middle of the form", set()
        flush_widget()
        if not submit_ops:
            return None, "the AI never clicked a button to save after filling the form", set()
        if not any(c["type"] != "widget" for c in controls):
            return None, "the form has no field that can be filled in directly to find it by", set()

    own_shapes: dict[str, str] = {}  # addresses that are the record's own pages -> their page type with the record's part as "*"

    def finish(o: dict[str, Any], nxt: dict[str, Any] | None) -> dict[str, Any]:
        out: dict[str, Any] = {"op": o["op"], "goal": ""}
        if o["op"] in ("click", "fill", "select"):
            out["by"] = element_locators(o["el"], fields, slot_values, hint=_norm(entries[o["i"]].get("goal")))
            if not out["by"]:
                raise ValueError("an element has no stable way to find it again")
        if o["op"] == "fill" or o["op"] == "select":
            out["value"] = o["value"].get("template", "")
        if o["op"] == "keys":
            out["keys"] = o["keys"]
        if o["op"] == "goto":
            out["url"] = o["target"]
        if o["op"] == "revisit":
            out["to"] = o["to"]  # the step after which the plan was on that page
        blanks = _blanks_in(out.get("by", []))
        if o.get("next_url") and nxt is not None and o["op"] in ("click", "keys", "goto", "back", "revisit"):
            if blanks:
                own_shapes[o["next_url"]] = url_shape(o["next_url"], own_page=True)
            # a step on the record's own page that stays there (a tab) expects that page type too, not the first record's address
            out["expect"] = own_shapes.get(o["next_url"]) or url_shape(o["next_url"])
        if o["op"] == "click" and o.get("url") and o.get("url") == o.get("next_url"):
            out["stays"] = True  # it shows something on the same page (a tab, a section) instead of opening another one
        entry_goal = _norm(entries[o["i"]].get("goal"))
        out["goal"] = entry_goal[:160]
        out["bound"] = sorted(b for b in blanks if b in fields)
        if out["bound"]:
            out["own"] = True  # found by the record's own value: it lands on the record's own page (read back after saving)
        if o.get("optional"):
            out["optional"] = True
        if _saving_click(o) and o.get("url") != signin_url:
            out["saves"] = True  # it may change something on the site: pressed once, never in a dry run, never again on a retry
        if o.get("after_pager") and blanks:
            out["paged"] = True  # reached by paging through a list in the trace: found by paging at run time
        out["uses"] = sorted(b for b in blanks | set(re.findall(r"\{(\w+)\}", str(out.get("value", "")))) if b in slots)
        return out

    all_ops = gather + (commit_ops or submit_ops)
    try:
        gather_out = []
        for k, o in enumerate(gather):
            nxt = all_ops[k + 1] if k + 1 < len(all_ops) else None
            gather_out.append(finish(o, nxt))
            if o["op"] == "revisit" and gather_out[o["to"]].get("expect"):
                gather_out[-1]["expect"] = gather_out[o["to"]]["expect"]  # the same page as then
        submit_out = [finish(o, None) for o in submit_ops]
        advance_out = [finish(o, None) for o in advance_ops]
    except ValueError as exc:
        return None, str(exc), set()

    # A sign-in on the way: the steps on the page where the agent typed a password, up to the click that signed in (the first
    # click after the password, whether or not it left the page), are kept so the plan can sign in again when a session runs
    # out. A step that uses the record (its search) is never part of it
    login: dict[str, Any] | None = None
    if password_at is not None:
        login_url = password_at.get("url") or ""
        first = next(k for k, o in enumerate(gather) if o.get("url") == login_url)
        steps: list[dict[str, Any]] = []
        for k in range(first, len(gather)):
            o = gather[k]
            if o.get("url") != login_url or gather_out[k]["bound"] or gather_out[k]["uses"] or has_blank(str(gather_out[k].get("value", ""))):
                break
            if not gather_out[k].get("optional"):
                steps.append(dict(gather_out[k]))
            if (o.get("next_url") and o.get("next_url") != login_url) or (o["op"] in ("click", "keys") and o["i"] > password_i):
                break  # the click (or Enter) that signed in
        login = {"page": url_shape(login_url), "steps": steps}

    key_fields = sorted({b for o in gather_out + submit_out for b in o["bound"]})
    uses_field = key_fields or any("{" in str(o.get("value", "")) for o in gather_out)
    if not uses_field:
        return None, "nothing in the AI's route depends on the record, so there is nothing to learn from it", set()
    if not controls and not submit_out and not gather_out:
        return None, "the plan would do nothing", set()

    plan = {
        "kind": "plan",
        "version": PLAN_VERSION,
        "start": templatize(entries[0].get("url") or "", fields),
        "slots": slots,
        "gather": gather_out,
        "commit": {"controls": controls, "submit": submit_out, "advance": advance_out},
        "key_fields": key_fields,
        "trust": {"ok": 0, "bad": 0, "signatures": []},
    }
    if login is not None:
        plan["login"] = login
    return plan, "ok", set()


# Adds to a plan the form fields another record's live route filled that the plan does not know (another branch of the same
# form: the deduction a "with deduction" outcome needs, when the plan was learned from a plain approval). They are only
# descriptions: the model then decides them in its first question instead of a second one on their step, and a value is only
# ever entered into a field that is really on the form. Takes the plan, that record's fields, its trace and the task's
# instructions; returns the keys added.
def learn_fields(plan: dict[str, Any], fields: dict[str, Any], trace: list[dict[str, Any]], instructions: str = "") -> list[str]:
    other, _why = compile_plan(fields, trace, instructions)
    if other is None or len(other["commit"].get("advance", [])) != len(plan["commit"].get("advance", [])):
        return []
    known = {c["key"] for c in plan["commit"]["controls"]}
    added = [dict(c, value={"computed": True}) for c in other["commit"]["controls"] if c["key"] not in known and c["type"] != "widget"]
    plan["commit"]["controls"].extend(added)
    return [c["key"] for c in added]


# True while a plan has not yet agreed with the live agent on enough records.
def needs_proof(recipe: dict[str, Any]) -> bool:
    if recipe.get("kind") != "plan":
        return False
    trust = recipe.get("trust") or {}
    ok = int(trust.get("ok", 0))
    if ok < PROOF_NEEDED:
        return True
    # Two agreeing records that made the same choice prove less than two that made different ones: try one more
    return len(set(trust.get("signatures", []))) < 2 and ok < PROOF_MAX


# --------------------------------------------------------------------- comparing a plan with the live agent

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _same_value(kind: str, planned: str | None, actual: str | None) -> bool:
    a, b = _norm(planned), _norm(actual)
    if kind == "check":  # one checkbox: ticked or not (the live agent's trace holds its label when it ticked it)
        return (a.lower() in ("yes", "true", "on", "1") or (bool(a) and a.lower() not in ("no", "false", "off", "0"))) == (
            bool(b) and b.lower() not in ("no", "false", "off", "0"))
    if kind == "checks":  # a group of checkboxes: the same labels ticked, in any order
        return {x.strip().lower() for x in a.split(",") if x.strip()} == {x.strip().lower() for x in b.split(",") if x.strip()}
    if not a or not b:
        return a == b
    if kind in ("text", "note"):
        na, nb = sorted(_NUMBER.findall(a.replace(",", ""))), sorted(_NUMBER.findall(b.replace(",", "")))
        return na == nb if (na or nb) else a.lower() == b.lower()
    return a.lower() == b.lower()


# What the live agent entered into the form, from its trace: {control key: value}. Only controls the plan knows about
# (their keys, as trace_key gives them) are looked at. A radio button (native or ARIA) gives its label; checkboxes that share a
# name or a group give the labels left ticked ("Meals, Taxi"), a single checkbox its label when ticked; a combobox the text of
# the option picked in it. Takes the trace and the keys of the form's controls.
def committed_values(trace: list[dict[str, Any]], keys: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    ticked: dict[str, list[str]] = {}
    combo: str | None = None  # the combobox whose list the agent opened last
    for e in trace:
        el = e.get("el")
        if not el:
            continue
        if e.get("kind") == "click" and _role(el) == "option" and combo is not None:
            out[combo] = _norm(el.get("text"))  # the option picked in the combobox
            combo = None
            continue
        key = trace_key(el)
        combo = key if _is_combo(el) and key in keys else None
        if key not in keys:
            continue
        ctype = choice_type(el)
        if e.get("kind") == "click" and ctype == "radio":
            out[key] = _norm(el.get("text"))
        elif e.get("kind") == "click" and ctype == "checkbox":
            label = _norm(el.get("text"))
            boxes = ticked.setdefault(key, [])
            boxes.remove(label) if label in boxes else boxes.append(label)  # a second click unticks it
        elif e.get("kind") in ("input", "select") and e.get("value") not in (None, ""):
            out[key] = _norm(e["value"])
    for key, boxes in ticked.items():
        out[key] = ", ".join(sorted(boxes))
    return out


# Compares the values a dry run planned with what the live agent entered. Takes both dicts and each control's kind
# ("select", "date", "text", ...). Returns (same, a short description of the first difference).
def values_agree(planned: dict[str, str], actual: dict[str, str], kinds: dict[str, str]) -> tuple[bool, str]:
    for key in sorted(set(planned) | set(actual)):
        if kinds.get(key) == "widget":
            continue  # the live agent's trace holds the clicks that set a custom control, not the value, so it cannot be compared
        if kinds.get(key) == "note" and not (_norm(planned.get(key)) and _norm(actual.get(key))):
            continue  # an optional message one side left empty: not part of the decision
        if not _same_value(kinds.get(key, "other"), planned.get(key), actual.get(key)):
            return False, f"{key}: the plan would enter {planned.get(key)!r} but the live agent entered {actual.get(key)!r}"
    return True, ""


# --------------------------------------------------------------------- is the old click replay safe?

# Decides whether the clicks the live agent made on its first record can be replayed for other records.
# They cannot when a typed or chosen value was not one of the record's fields (it was read or worked out), or when a
# click's locator names something that belongs to the first record (a link to its page, a button labelled with its id).
# Takes the record's fields, the trace and how many typed values the step recorder could not tie to a field.
# Returns (safe, reason).
def replay_verdict(fields: dict[str, Any], trace: list[dict[str, Any]], unmatched_inputs: int) -> tuple[bool, str]:
    if unmatched_inputs:
        return False, f"{unmatched_inputs} value(s) the AI typed or chose are not copies of the record's fields (they were read or worked out)"
    for e in trace:
        if e.get("kind") != "click" or not e.get("el"):
            continue
        attrs = e["el"].get("attrs") or {}
        if (attrs.get("type") or "").lower() == "checkbox":
            continue
        for c in element_locators(e["el"], fields, {}):
            if has_blank(spec_text(c)):
                return False, f"a click is tied to the first record ({spec_text(c)!r}), so it would repeat that record's click"
        for attr in ("href", "id", "name", "aria-label", "title"):
            if _IDENTITY_LIKE.search(str(attrs.get(attr) or "")):
                return False, f"a click points at one particular item ({attr}={attrs.get(attr)!r})"
    return True, "ok"
