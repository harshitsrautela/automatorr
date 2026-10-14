"""Records everything the live AI does on one record (clicks, typing, navigation) so a recipe can be learned from it."""

import logging
from typing import Any

from browser_use.agent.views import AgentHistory

logger = logging.getLogger(__name__)

# The agent actions that change what is on the page and can be repeated by a script
_KINDS = {
    "navigate": "navigate",
    "click": "click",
    "input": "input",
    "input_text": "input",
    "send_keys": "keys",
    "select_dropdown": "select",
    "go_back": "back",
}


# Clicked elements that are one choice of a group (an ARIA radio button, a checkbox, a menu item), and the roles of the
# containers that name such a group
_CHOICE_ROLES = {"radio", "checkbox", "switch", "option", "menuitemradio", "menuitemcheckbox"}
_GROUP_ROLES = {"radiogroup", "group", "listbox", "menu"}


# The role of a page element: its own role attribute, else the one the accessibility tree gives it. Takes a browser-use DOM
# node; returns the role in lower case ("" when it has none).
def _role_of(node: Any) -> str:
    attrs = getattr(node, "attributes", None) or {}
    role = (attrs.get("role") or "").strip().lower()
    if not role:
        ax = getattr(node, "ax_node", None)
        role = str(getattr(ax, "role", None) or "").strip().lower()
    return role


# The name of the group a choice belongs to: the accessible name (or aria-label) of the nearest container with the role
# radiogroup, group, listbox or menu. Takes a browser-use DOM node; returns the name, or None when there is no named group.
def _group_name(node: Any) -> str | None:
    group = _group_node(node)
    if group is None:
        return None
    ax = getattr(group, "ax_node", None)
    name = (getattr(ax, "name", None) or (getattr(group, "attributes", None) or {}).get("aria-label") or "").strip()
    return " ".join(name.split()) or None


# The nearest container of a choice with the role radiogroup, group, listbox or menu. Takes a browser-use DOM node; returns
# that container's node, or None.
def _group_node(node: Any) -> Any:
    parent = getattr(node, "parent_node", None)
    for _depth in range(30):
        if parent is None:
            return None
        if _role_of(parent) in _GROUP_ROLES:
            return parent
        parent = getattr(parent, "parent_node", None)
    return None


# Boxes (ARIA checkboxes and switches) and the other things a person fills in, as the run-time form reader tells them apart
_BOX_ROLES = {"checkbox", "switch"}
_CONTROL_TAGS = {"input", "select", "textarea"}
_CONTROL_ROLES = {"radio", "combobox", "textbox", "slider", "spinbutton", "listbox"}


# True when a choice's group holds two or more ARIA boxes and no other control: the run-time form reader
# (recipe/webkit.py) then reads those boxes as one control named after the group; a box in any other group is read on its
# own, by its own name. Takes the clicked box's browser-use DOM node.
def _group_of_boxes(node: Any) -> bool:
    group = _group_node(node)
    if group is None:
        return False
    boxes, others = 0, 0
    stack = list(getattr(group, "children_nodes", None) or [])
    while stack:
        n = stack.pop()
        tag = str(getattr(n, "node_name", "") or "").lower()
        role = _role_of(n)
        if role in _BOX_ROLES and tag != "input":
            boxes += 1
            continue  # what is inside a box is part of it
        attrs = getattr(n, "attributes", None) or {}
        if role in _CONTROL_ROLES or "contenteditable" in attrs or (tag in _CONTROL_TAGS and str(attrs.get("type") or "").lower() != "hidden"):
            others += 1
        stack.extend(getattr(n, "children_nodes", None) or [])
    return boxes >= 2 and others == 0


# How much of a page's text is kept in a trace (enough to see where a value was read, small enough to store)
_PAGE_TEXT_CHARS = 8000


# Collects the repeatable actions of one agent run through browser-use's per-step callback.
# The first entry for each address also keeps the text of that page, so a later step's typed value can be traced back
# to where the agent read it (a plan needs to know whether a value came from the record, from a page, or was worked out).
class TraceRecorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._texted: set[str] = set()

    # Handles one agent step: stores each repeatable action with the element it touched.
    # Takes the browser state, the agent output and the step number; returns nothing.
    def callback(self, browser_state_summary, agent_output, step_num: int) -> None:
        actions = getattr(agent_output, "action", None) or []
        goal = (getattr(agent_output, "next_goal", None) or "").strip()
        elements: list[Any] = []
        selector_map = getattr(getattr(browser_state_summary, "dom_state", None), "selector_map", None)
        if selector_map is not None:
            try:
                elements = AgentHistory.get_interacted_element(agent_output, selector_map)
            except Exception:
                logger.exception("Could not read interacted elements for step %s", step_num)
        page_text = None
        url_now = getattr(browser_state_summary, "url", None)
        if url_now and url_now not in self._texted:
            try:
                page_text = browser_state_summary.dom_state.llm_representation()[:_PAGE_TEXT_CHARS]
            except Exception:
                page_text = None
        for offset, action in enumerate(actions):
            node = None  # the page element itself (with its parents), to read what the stored copy does not keep
            try:
                index = action.get_index()
                if index is not None and selector_map is not None:
                    node = selector_map.get(index)
            except Exception:
                node = None
            try:
                data = action.model_dump(exclude_unset=True)
            except Exception:
                continue
            if not data:
                continue
            name = next(iter(data))
            kind = _KINDS.get(name)
            if kind is None:
                continue  # reading, scrolling, waiting, finishing: nothing to repeat
            params = data[name] if isinstance(data[name], dict) else {}
            element = elements[offset] if offset < len(elements) else None
            entry: dict[str, Any] = {
                "step": step_num,
                "goal": goal,
                "kind": kind,
                "url": getattr(browser_state_summary, "url", None),
                "el": None,
            }
            if kind == "navigate":
                entry["value"] = params.get("url")
            elif kind == "input":
                entry["value"] = params.get("text")
            elif kind == "keys":
                entry["value"] = params.get("keys")
            elif kind == "select":
                entry["value"] = params.get("text")
            if element is not None:
                entry["el"] = {
                    "node": (element.node_name or "").lower(),
                    "attrs": dict(element.attributes or {}),
                    "text": element.ax_name or "",
                    "xpath": element.x_path,
                }
                if not element.ax_name:
                    # an element with no name of its own (a clickable table row): keep the text inside it, so it can be found again
                    # as "the row that contains this record's value"
                    for source in (node, element):
                        try:
                            entry["el"]["inner"] = " ".join(source.get_all_children_text().split())[:300]
                            break
                        except Exception:
                            continue
                if node is not None and _role_of(node) in _CHOICE_ROLES:
                    # one choice of a group (an ARIA radio button with no name or id): the group's name finds it again
                    group = _group_name(node)
                    if group:
                        entry["el"]["group"] = group
                        if _role_of(node) in _BOX_ROLES and _group_of_boxes(node):
                            entry["el"]["group_of_boxes"] = True  # its group is one control of several boxes
            if kind in ("click", "input", "select") and entry["el"] is None:
                entry["note"] = "no element found for this action"
            if page_text is not None and url_now not in self._texted:
                entry["page_text"] = page_text
                self._texted.add(url_now)
            self.entries.append(entry)
