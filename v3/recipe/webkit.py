"""Helpers for real-world web pages, used by the plan executor: pages split into frames, form controls that are not plain
inputs (ARIA radio groups and checkboxes, searchable comboboxes, rich-text boxes), lists that show more rows as you scroll
or press "Load more", cookie banners and "What's new" pop-ups, error boxes with a Retry button, and login pages.

Every helper looks at the page and all of its frames, finds things the way a person does (roles, labels, visible text)
rather than by ids, and never presses a button that saves, submits, deletes, pays or confirms a decision.

To act on an element found by a script in the page, the script marks it with a temporary attribute (data-webkit-mark) and
Playwright clicks the marked element, so the click is a real one (the page's own handlers run). Marks are removed after."""

import asyncio
import re
import uuid
from typing import Any, Callable, NamedTuple

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page

_OP_TIMEOUT_MS = 6000
# How much of each frame's text is read, and of all frames together
_FRAME_CHARS = 6000
_FRAMES_CHARS = 12000
# How long a list may take to show more rows after a click or a scroll
_MORE_WAIT = 3.0
# How long a control may take to appear, to show a new value, and to offer its choices after typing
_FIND_WAIT = 2.0
_SETTLE_WAIT = 2.0
_OPTIONS_WAIT = 3.0

# The buttons that close an interruption (a cookie banner, a "What's new" pop-up). Nothing here saves or decides anything.
DISMISS = re.compile(r"^(accept( all)?|agree|i agree|got it|ok(ay)?|close|dismiss|no,? thanks|not now|skip|continue|×|✕)$", re.I)
# On a cookie banner, the choice that allows only the cookies the site needs is preferred to accepting all of them
_REJECT = re.compile(
    r"^(reject|decline|deny|refuse)( all| optional| non-essential| non essential| additional)?( cookies)?$"
    r"|^(accept |allow |use )?(only )?(strictly )?(necessary|essential|required)( cookies)?( only)?$",
    re.I,
)
_LOGIN_WORDS = re.compile(r"\b(sign[\s-]?in|log[\s-]?in|login|signin|log[\s-]?on|sign[\s-]?on)\b", re.I)
_YES = {"yes", "true", "1", "on", "checked", "ticked", "tick", "y"}


# ----------------------------------------------------------------------------- shared page-side helpers

# JavaScript shared by the snippets below: visibility, the text and label of an element, whether an id looks generated,
# and `collect(root)`, which finds the custom controls in a root element. Each item it returns holds the control (in the
# shape the plan executor's form scraper uses, plus `value`, `custom` and sometimes `native_key`), its element `el`, the
# elements of its options `opts`, and for a combobox the `input` typed into, the text it `display`s and whether its list
# is `open`. It also has rootOf/scopeOf/rootIn, which name the part of the page (form, dialog or page) keys are counted in.
_LIB = r"""
const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
const tidy = (t) => clean(t).replace(/[\s:*]+$/, '');
const shown = (c) => {
  if (!c || !c.isConnected || c.closest('[hidden]')) return false;
  if (!(c.offsetWidth || c.offsetHeight || c.getClientRects().length)) return false;
  const s = getComputedStyle(c);
  return s.visibility !== 'hidden' && s.display !== 'none';
};
const nameOf = (b) => (clean(b.getAttribute('aria-label')) || clean(b.innerText) || clean(b.value) || clean(b.getAttribute('title'))).replace(/^[\s.!:…]+|[\s.!:…]+$/g, '');
const byIds = (ids) => clean(String(ids || '').split(/\s+/).map((i) => { const e = i && document.getElementById(i); return e ? (e.innerText || e.textContent) : ''; }).join(' '));
const textOf = (c) => clean(c.getAttribute('aria-label')) || byIds(c.getAttribute('aria-labelledby')) || clean(c.innerText) || clean(c.getAttribute('title')) || clean(c.value);
const CONTROL = 'input:not([type=hidden]), select, textarea, [role=radio], [role=checkbox], [role=switch], [role=combobox], [role=textbox], [role=listbox], [role=slider], [role=spinbutton], [contenteditable]';
const LABELISH = 'label, legend, h1, h2, h3, h4, h5, h6, dt, [class*=label i], [class*=title i]';
const VALUEISH = /value|selected|single|chip|token|placeholder/i;
const BOX = '[role=checkbox], [role=switch]';
const COMBO = '[role=combobox], [aria-haspopup=listbox], input[aria-autocomplete]';
const POPUP = '[role=listbox], [role=menu], [role=option], [role=tree], [role=grid]';
const DIALOGS = '[role=dialog], dialog, [aria-modal="true"]';
// Words a switch or a checkbox may show for its state: they change when it is clicked, so they never name it
const STATE = /^(on|off|yes|no|true|false|checked|unchecked|ticked|unticked|enabled|disabled|active|inactive|selected|unselected)$/i;
// An id that a framework generated (":r3:", "f-k3j9x2", "field-20931") changes from one page load to the next
const stableId = (id) => {
  if (!id || /:|\d{3,}/.test(id)) return '';
  for (const part of id.split(/[-_.]/)) {
    if (part.length >= 5 && /\d/.test(part) && /[a-z]/i.test(part) && !/^[a-z]+\d{1,2}$/i.test(part)) return '';
  }
  return id;
};
// The part of the page an element's controls are read from, and their keys counted in (a key that repeats gets " #2"):
// its form, else its dialog, else the whole page. A scope {root, index, url} names that part so it can be found again.
const rootOf = (el) => el.form || el.closest('form') || el.closest(DIALOGS) || document.body;
const scopeOf = (root) => {
  const kind = root.matches('form') ? 'form' : root.matches(DIALOGS) ? 'dialog' : 'page';
  const index = kind === 'page' ? 0 : [...document.querySelectorAll(kind === 'form' ? 'form' : DIALOGS)].indexOf(root);
  return {root: kind, index, url: location.href};
};
const rootIn = (scope) => {
  if (!scope || !scope.root || scope.root === 'page') return document.body;
  return document.querySelectorAll(scope.root === 'form' ? 'form' : DIALOGS)[scope.index] || null;
};
// The label the page's markup gives an element: aria-label, aria-labelledby, a <label> for it. Anything inside `within`
// (a widget's own box, which shows its current value) is not its label.
const ownLabel = (el, within) => {
  const inside = (e) => !!within && within.contains(e);
  const ids = String(el.getAttribute('aria-labelledby') || '').split(/\s+/).filter((i) => { const e = i && document.getElementById(i); return e && !inside(e); });
  const own = tidy(el.getAttribute('aria-label')) || tidy(byIds(ids.join(' ')));
  if (own) return own;
  const labels = [...(el.labels || [])];
  if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l && !labels.includes(l)) labels.push(l); }
  for (const l of labels) {
    if (inside(l)) continue;
    let t = clean(l.innerText);
    if (within && l.contains(within)) t = t.replace(clean(within.innerText), '');
    if (tidy(t)) return tidy(t);
  }
  return '';
};
// The text printed just before the element: a wrapping <label>, or the nearest earlier label-like or short text, looking at
// the element's earlier siblings and then its parents' (never past another form field; a part hidden from screen readers,
// such as the native select a widget stands for, is passed over). For a widget, call it on the widget's whole box, so
// nothing that shows the chosen value is taken for its label.
const nearLabel = (el, inWidget) => {
  const wrap = el.closest('label');
  if (wrap && wrap !== el) { const t = tidy(clean(wrap.innerText).replace(clean(el.innerText), '')); if (t) return t; }
  let node = el;
  for (let up = 0; up < 4 && node && node.parentElement && node !== document.body; up++) {
    for (let s = node.previousElementSibling; s; s = s.previousElementSibling) {
      if (!shown(s) || s.getAttribute('aria-hidden') === 'true') continue;
      if (inWidget && VALUEISH.test(s.getAttribute('class') || '')) continue;
      if (s.matches(CONTROL) || s.querySelector(CONTROL)) return '';
      const t = tidy(s.innerText);
      if (!t) continue;
      if (s.matches(LABELISH) || t.length <= 60) return t;
    }
    node = node.parentElement;
  }
  return '';
};
const labelOf = (el) => {
  if (el.tagName === 'FIELDSET') { const lg = el.querySelector(':scope > legend'); if (lg && tidy(lg.innerText)) return tidy(lg.innerText); }
  return ownLabel(el) || nearLabel(el, false) || tidy(el.getAttribute('placeholder')) || tidy(el.getAttribute('data-placeholder'));
};
// The words a checkbox or switch shows inside itself, when they can name it: not a state word (On, Off) and not a mark (✓)
const ownWords = (b) => { const t = tidy(b.innerText); return t && /\p{L}/u.test(t) && !STATE.test(t) ? t : ''; };
// The smallest part of the page that holds a combobox and the value it shows: its parents up to the one that also holds
// the field's label or another field.
const boxOf = (el) => {
  let node = el;
  for (let up = 0; up < 4; up++) {
    const p = node.parentElement;
    if (!p || p === document.body || p.matches('form, fieldset, [role=dialog], dialog')) break;
    if ([...p.querySelectorAll(CONTROL)].some((x) => x !== el && !el.contains(x) && !x.contains(el) && !x.closest(POPUP) && shown(x))) break;
    if ([...p.querySelectorAll('label, legend')].some(shown) || clean(p.innerText).length > 200) break;
    node = p;
  }
  return node;
};
// The shown [role=option] elements of the list a combobox names (aria-controls / aria-owns).
const linkedOptions = (el, input) => {
  const scopes = [];
  for (const n of [el, input]) {
    if (!n) continue;
    for (const attr of ['aria-controls', 'aria-owns']) {
      for (const id of String(n.getAttribute(attr) || '').split(/\s+/)) {
        const s = id && document.getElementById(id);
        if (s && !scopes.includes(s)) scopes.push(s);
      }
    }
  }
  const opts = [];
  for (const s of scopes) opts.push(...(s.matches('[role=option]') ? [s] : s.querySelectorAll('[role=option]')));
  return opts.filter(shown);
};
// The choices a combobox offers right now: the options of the list it names, else any shown on the page (a list drawn in
// a "portal" at the end of the body) except those that were shown before the box was opened ("<token>:old": another
// control's list that is always on the page).
const optionsOf = (el, input, token) => {
  const own = linkedOptions(el, input);
  if (own.length) return own;
  const old = token ? token + ':old' : '';
  return [...document.querySelectorAll('[role=option]')].filter((o) => shown(o) && !(old && o.getAttribute('data-webkit-mark') === old));
};
// Whether a combobox says its list is open (aria-expanded), or the list it names is shown.
const isOpen = (el, input) => [el, input].some((n) => n && n.getAttribute('aria-expanded') === 'true') || linkedOptions(el, input).length > 0;
const collect = (root) => {
  const items = [];
  // ARIA radio groups: one control; its value is the checked option's text
  for (const g of root.querySelectorAll('[role=radiogroup]')) {
    if (!shown(g) || g.closest('[role=listbox], [role=menu]')) continue;
    const opts = [...g.querySelectorAll('[role=radio]')].filter((r) => r.tagName !== 'INPUT' && r.closest('[role=radiogroup]') === g && shown(r));
    if (!opts.length) continue;
    const label = labelOf(g) || stableId(g.id) || 'choice';
    const on = opts.find((r) => r.getAttribute('aria-checked') === 'true');
    items.push({el: g, opts, control: {key: 'aria:' + label, tag: 'input', type: 'radio', label, visible: true, options: opts.map(textOf), value: on ? textOf(on) : '', custom: true}});
  }
  // ARIA checkboxes and switches: several in a container that holds nothing else are one group, like native ones sharing a name
  const isBox = (x) => x.matches(BOX) && x.tagName !== 'INPUT';
  const boxes = [...root.querySelectorAll(BOX)].filter((b) => isBox(b) && shown(b) && !b.closest(POPUP));
  const groupOf = (b) => {
    const c = b.parentElement && b.parentElement.closest('[role=group], fieldset');
    if (c && [...c.querySelectorAll(BOX)].filter(isBox).length >= 2 && ![...c.querySelectorAll(CONTROL)].some((x) => shown(x) && !x.closest(BOX))) return c;
    const p = b.parentElement;
    const kids = p ? [...p.children].filter(shown) : [];
    if (kids.length >= 2 && kids.every(isBox)) return p;
    return null;
  };
  const groups = new Map();
  for (const b of boxes) {
    const g = groupOf(b) || b;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(b);
  }
  for (const [g, list] of groups) {
    if (list.length >= 2 && !list.includes(g)) {
      const label = labelOf(g) || stableId(g.id) || 'choices';
      const on = list.filter((b) => b.getAttribute('aria-checked') === 'true').map(textOf).sort();
      items.push({el: g, opts: list, control: {key: 'aria:' + label, tag: 'input', type: 'checkbox', label, visible: true, options: list.map(textOf), value: on.join(', '), custom: true}});
    } else {
      // one box: named by its markup, else by the words inside it (not "On"/"Off" or a ✓, which change when it is
      // clicked), else by the text before it
      for (const b of list) {
        const label = ownLabel(b) || ownWords(b) || nearLabel(b, false) || tidy(b.getAttribute('title')) || stableId(b.id) || 'box';
        items.push({el: b, opts: [b], control: {key: 'aria:' + label, tag: 'input', type: 'checkbox', label, visible: true, options: [], value: b.getAttribute('aria-checked') === 'true' ? 'yes' : 'no', custom: true}});
      }
    }
  }
  // Comboboxes (a box to type into whose choices appear as you type, or a button that opens a list). The plan compares and
  // names its value like a select's, so its tag is "select".
  const combos = [...root.querySelectorAll(COMBO)].filter((c) => c.tagName !== 'SELECT' && !(c.parentElement && c.parentElement.closest(COMBO)));
  const typers = combos.filter((c) => c.matches('input, textarea') || c.querySelector('input:not([type=hidden]), textarea'));
  for (const c of combos) {
    const input = c.matches('input, textarea') ? c : c.querySelector('input:not([type=hidden]), textarea');
    if (!shown(c) && !(input && shown(input))) continue;
    if (!input && typers.some((o) => o !== c && boxOf(o).contains(c))) continue;  // the arrow button of a combobox counted already
    // the widget's whole box: what is inside it shows the current value or a placeholder, so it never names the control
    const box = c.matches('input, textarea') ? boxOf(c) : c;
    const label = ownLabel(c, box) || (input && input !== c ? ownLabel(input, box) : '') || nearLabel(box, true) || tidy(c.getAttribute('placeholder')) || (input ? tidy(input.getAttribute('placeholder')) : '') || 'choice';
    const key = (input && input.name) || stableId(c.id) || (input && input !== c ? stableId(input.id) : '') || 'combo:' + label;
    // what the box shows, without its label, its arrow and clear marks (▾ ×), or a placeholder such as "Select..."
    let display = clean(box.innerText);
    for (const ph of box.querySelectorAll('[class*=placeholder i]')) { const t = clean(ph.innerText); if (t) display = clean(display.replace(t, '')); }
    if (label && display.toLowerCase().startsWith(label.toLowerCase())) display = tidy(display.slice(label.length)).replace(/^[\s:]+/, '');
    display = display.replace(/[\s\p{S}]+$/u, '');
    if (/^(select|choose|search|pick|type)\b.{0,40}(\.\.\.|…)$/i.test(display) || display === clean(input && input.getAttribute('placeholder'))) display = '';
    // its value is what the box shows; text typed into it counts only for a box that shows its value as that text, with
    // its list closed (half-typed text was never chosen)
    const open = isOpen(c, input);
    const typed = input ? clean(input.value) : '';
    const control = {key, tag: 'select', type: 'combobox', label, visible: true, options: [], value: display || (open ? '' : typed), custom: true};
    if (input && (input.name || input.id)) control.native_key = input.name || input.id;
    items.push({el: c, opts: [], input, display, open, control});
  }
  // Rich-text boxes: the outermost editable element that is not part of another control
  for (const e of root.querySelectorAll('[contenteditable]')) {
    if (!e.isContentEditable || (e.parentElement && e.parentElement.isContentEditable)) continue;
    if (e.closest(COMBO + ', [role=radiogroup], ' + POPUP) || !shown(e)) continue;
    const label = labelOf(e) || 'note';
    items.push({el: e, opts: [], control: {key: stableId(e.id) || 'edit:' + label, tag: 'textarea', type: 'richtext', label, visible: true, options: [], value: clean(e.innerText), custom: true}});
  }
  items.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1);
  const used = {};
  for (const it of items) {
    const k = it.control.key;
    used[k] = (used[k] || 0) + 1;
    if (used[k] > 1) it.control.key = k + ' #' + used[k];
  }
  return items;
};
// What the setters need to know about a control found by collect().
const describe = (hit) => ({control: hit.control, states: hit.opts.map((o) => o.getAttribute('aria-checked') === 'true'), typing: !!hit.input,
                            display: hit.display || '', input_value: hit.input ? clean(hit.input.value) : '', open: !!hit.open});
"""

# Reads the custom controls of the form (or the dialog, or else the whole page) an element is in, in the shape the plan
# executor's own scraper returns: {"key", "tag", "type", "label", "visible", "options"}, plus the control's current
# `value`, `custom: true`, the part of the page its key is counted in (`scope`, so set_custom finds this control and not a
# namesake elsewhere on the page), and for a combobox typed into a native input that input's name or id (`native_key`),
# which the native scraper may also have returned. Native inputs, selects and textareas are left to that scraper. Hidden
# ones are skipped.
SCRAPE_CUSTOM = "(el) => {" + _LIB + """
  const root = rootOf(el);
  const scope = scopeOf(root);
  return collect(root).map((x) => Object.assign(x.control, {scope}));
}"""

# Finds one custom control by its key in the part of this document its scope names (the whole page without one). With a
# token it marks the control's element ("<token>:el"), each option ("<token>:opt:<i>") and the box typed into
# ("<token>:input"); for a combobox whose list is closed it also marks the options already shown elsewhere ("<token>:old"),
# which are not its choices. Returns {control, states, typing, display, input_value, open, same_url} (same_url: this
# document is the one the scope was read in), or null when the control is not here.
_FIND = "([key, token, scope]) => {" + _LIB + """
  if (!document.body) return null;
  const root = rootIn(scope);
  const hit = root && collect(root).find((x) => x.control.key === key);
  if (!hit) return null;
  const mark = (e, what) => { if (token && e) e.setAttribute('data-webkit-mark', token + ':' + what); };
  mark(hit.el, 'el');
  hit.opts.forEach((o, i) => { if (o !== hit.el) mark(o, 'opt:' + i); });  // a lone checkbox is its own option: it keeps 'el'
  mark(hit.input, 'input');
  if (token && hit.control.type === 'combobox' && !hit.open) {
    for (const o of document.querySelectorAll('[role=option]')) if (shown(o) && !o.hasAttribute('data-webkit-mark')) mark(o, 'old');
  }
  return Object.assign(describe(hit), {same_url: !scope || !scope.url || scope.url === location.href});
}"""

# Reads a control again after acting on it: the one whose element carries the mark "<token>:el" (its key may have changed),
# else the one with this key in the part of the page its scope names. Marks nothing. Returns what _FIND does, or null.
_READ = "([key, token, scope]) => {" + _LIB + """
  if (!document.body) return null;
  const el = document.querySelector('[data-webkit-mark="' + token + ':el"]');
  let hit = el ? collect(rootOf(el)).find((x) => x.el === el) : null;
  if (!hit) { const root = rootIn(scope); hit = root && collect(root).find((x) => x.control.key === key); }
  return hit ? describe(hit) : null;
}"""

# The texts of the choices a marked combobox offers now; marks each one "<token>:choice:<i>".
_OPTIONS = "(token) => {" + _LIB + """
  const el = document.querySelector('[data-webkit-mark="' + token + ':el"]');
  const input = document.querySelector('[data-webkit-mark="' + token + ':input"]');
  for (const o of document.querySelectorAll('[data-webkit-mark^="' + token + ':choice:"]')) o.removeAttribute('data-webkit-mark');
  const opts = optionsOf(el, input, token);
  opts.forEach((o, i) => o.setAttribute('data-webkit-mark', token + ':choice:' + i));
  return opts.map(textOf);
}"""

_UNMARK = """(token) => {
  for (const e of document.querySelectorAll('[data-webkit-mark^="' + token + '"]')) e.removeAttribute('data-webkit-mark');
  for (const e of document.querySelectorAll('[data-webkit-scroller="' + token + '"]')) e.removeAttribute('data-webkit-scroller');
}"""


# ----------------------------------------------------------------------------- frames

# The page's frames: the main one first, then every child frame that is still attached (iframes, nested ones too).
# Takes the page; returns the frames.
def frames(page: Page) -> list[Frame]:
    main = page.main_frame
    return [main, *[f for f in page.frames if f is not main and not f.is_detached()]]


# Reads the text of every child frame that is shown (an iframe holding a form or a report), each after a "[frame]" line,
# so it can be added to what was read from the main page. Takes the page and the most characters to return; returns the text.
async def read_frames_text(page: Page, limit: int = _FRAMES_CHARS) -> str:
    out = ""
    for frame in frames(page)[1:]:
        if len(out) >= limit:
            break
        try:
            element = await frame.frame_element()
            if not await element.is_visible():
                continue
            text = (await frame.inner_text("body", timeout=2000)).strip()
        except Exception:  # noqa: BLE001 - a frame that is loading, gone or has no body
            continue
        if text:
            out += ("\n\n[frame]\n" + text[:_FRAME_CHARS])[: limit - len(out)]
    return out


# Removes the marks a helper left in one frame. Takes the frame and the token the marks start with.
async def _unmark(frame: Frame, token: str) -> None:
    try:
        await frame.evaluate(_UNMARK, token)
    except Exception:  # noqa: BLE001 - the frame went away: so did the marks
        pass


# ----------------------------------------------------------------------------- custom controls

# The custom controls of the form (or dialog, or page) an element belongs to. Takes the element; returns the controls.
async def scrape_custom(anchor: Locator) -> list[dict[str, Any]]:
    return await anchor.evaluate(SCRAPE_CUSTOM, timeout=2500)


# The custom controls of the whole page, frames included (the first control with a key wins). Takes the page; returns them.
async def custom_controls(page: Page) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    keys: set[str] = set()
    for frame in frames(page):
        try:
            found = await frame.locator("body").evaluate(SCRAPE_CUSTOM, timeout=2500)
        except Exception:  # noqa: BLE001
            continue
        for control in found:
            if control["key"] not in keys:
                keys.add(control["key"])
                out.append(control)
    return out


# Joins the native scraper's controls with the custom ones, dropping a native control that a custom one stands for (the
# input inside a combobox). Takes both lists; returns one list.
def merge_controls(native: list[dict[str, Any]], custom: list[dict[str, Any]]) -> list[dict[str, Any]]:
    taken = {c["key"] for c in custom} | {c["native_key"] for c in custom if c.get("native_key")}
    return [c for c in native if c["key"] not in taken] + list(custom)


# Text made comparable: spacing collapsed, ends trimmed, lower case. Takes any value; returns the text.
def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


# The labels a value for a group of checkboxes names: "Email, Phone" -> ["Email", "Phone"].
def _labels(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;]", value or "") if p.strip() and p.strip().lower() not in ("none", "nothing", "-", "n/a")]


# Which of a combobox's choices a value means: the one whose text is the value (ignoring case and spacing), and nothing else:
# a decided value that is not a choice ("Approved" among "Partially approved", "Rejected") must never pick a choice that
# merely contains it. Takes the choices' texts and the value; returns its index, or None when no choice is the value.
def pick_option(options: list[str], value: str) -> int | None:
    want = _norm(value)
    if not want:
        return None
    exact = [i for i, o in enumerate(options) if _norm(o) == want]
    return exact[0] if exact else None  # a value that is not an option is never taken as another option that contains it


# Finds a custom control by its key, in the part of the page its scope names (the form or dialog it was read in), in
# whichever frame has it, waiting a little for it to appear, and marks it. A frame whose address is the one the control was
# read at is preferred. Takes the page and the control; returns (the frame, the mark's token, what _FIND returned). Raises
# ValueError when it is not there, or when it is in several frames and nothing tells which one is meant.
async def _locate(page: Page, control: dict[str, Any], wait: float = _FIND_WAIT) -> tuple[Frame, str, dict[str, Any]]:
    key, scope = control["key"], control.get("scope")
    token = "w" + uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    while True:
        hits: list[tuple[Frame, dict[str, Any]]] = []
        for frame in frames(page):
            try:
                found = await frame.evaluate(_FIND, [key, token, scope])
            except Exception:  # noqa: BLE001 - a frame that is navigating or gone
                continue
            if found:
                hits.append((frame, found))
        if hits:
            best = [h for h in hits if h[1].get("same_url")] or hits
            for frame, _found in hits:
                if len(best) > 1 or frame is not best[0][0]:
                    await _unmark(frame, token)
            if len(best) > 1:
                raise ValueError(f"the control {key!r} is in more than one frame of the page, so which one is meant is not clear")
            return best[0][0], token, best[0][1]
        if loop.time() >= deadline:
            raise ValueError(f"the control {key!r} is not on the page or in any of its frames")
        await asyncio.sleep(0.2)


# Waits until a control passes a test, reading it afresh each time: the element _locate marked (so a control whose label
# changes with its state is still found), else the control with its key. Takes the frame, the mark's token, the control,
# the test (given what _FIND returns) and how long to wait; returns True when it passed in time.
async def _settle(frame: Frame, token: str, control: dict[str, Any], test: Callable[[dict[str, Any]], bool],
                  seconds: float = _SETTLE_WAIT) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        try:
            found = await frame.evaluate(_READ, [control["key"], token, control.get("scope")])
        except Exception:  # noqa: BLE001
            found = None
        if found and test(found):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.1)


# The element a helper marked in a frame. Takes the frame, the mark's token and what was marked ("el", "opt:2", ...);
# returns its locator.
def _marked(frame: Frame, token: str, what: str) -> Locator:
    return frame.locator(f'[data-webkit-mark="{token}:{what}"]')


# Chooses an option of an ARIA radio group by its text and checks the group then shows it as chosen.
# Takes the page, the control and the value; raises ValueError when it cannot.
async def _set_radio(page: Page, control: dict[str, Any], value: str) -> None:
    want = _norm(value)
    for attempt in range(3):
        frame, token, found = await _locate(page, control)
        try:
            current = found["control"]
            if _norm(current["value"]) == want:
                return
            options = [_norm(o) for o in current["options"]]
            if want not in options:
                raise ValueError(f"{value!r} is not one of the choices of {current['label']!r} ({', '.join(current['options'])})")
            option = _marked(frame, token, f"opt:{options.index(want)}")
            if attempt < 2:
                await option.click(timeout=_OP_TIMEOUT_MS)
            else:
                await option.dispatch_event("click")
            if await _settle(frame, token, control, lambda f: _norm(f["control"]["value"]) == want):
                return
        finally:
            await _unmark(frame, token)
    raise ValueError(f"{control.get('label') or control['key']!r} did not take the choice {value!r}")


# Ticks or unticks one ARIA checkbox (or switch) until it shows the wanted state. Takes the page, the control and the
# value (yes/no, true/false, ...); raises ValueError when it cannot.
async def _set_box(page: Page, control: dict[str, Any], value: str) -> None:
    want = "yes" if _norm(value) in _YES else "no"
    for _attempt in range(3):
        frame, token, found = await _locate(page, control)
        try:
            if found["control"]["value"] == want:
                return
            await _marked(frame, token, "el").click(timeout=_OP_TIMEOUT_MS)
            if await _settle(frame, token, control, lambda f: f["control"]["value"] == want):
                return
        finally:
            await _unmark(frame, token)
    raise ValueError(f"{control.get('label') or control['key']!r} would not change to {want!r}")


# Ticks exactly the boxes of a group of ARIA checkboxes that a value names ("Email, Phone"), one click at a time.
# Takes the page, the control and the value; raises ValueError when it cannot.
async def _set_box_group(page: Page, control: dict[str, Any], value: str) -> None:
    wanted = {_norm(v) for v in _labels(value)}
    for _attempt in range(len(control.get("options") or []) * 2 + 4):
        frame, token, found = await _locate(page, control)
        try:
            current = found["control"]
            options = [_norm(o) for o in current["options"]]
            unknown = wanted - set(options)
            if unknown:
                raise ValueError(f"{', '.join(sorted(unknown))!r} is not among the choices of {current['label']!r} ({', '.join(current['options'])})")
            wrong = [i for i, o in enumerate(options) if (o in wanted) != found["states"][i]]
            if not wrong:
                return
            before = current["value"]
            await _marked(frame, token, f"opt:{wrong[0]}").click(timeout=_OP_TIMEOUT_MS)
            await _settle(frame, token, control, lambda f, before=before: f["control"]["value"] != before, seconds=1.5)
        finally:
            await _unmark(frame, token)
    raise ValueError(f"{control.get('label') or control['key']!r} would not take {value!r}")


# Waits until a marked combobox offers choices and the list has stopped changing. Takes the frame and the mark's token;
# returns the choices' texts (empty when none appeared in time). Each choice is marked "<token>:choice:<i>".
async def _wait_options(frame: Frame, token: str, seconds: float = _OPTIONS_WAIT) -> list[str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    last: list[str] | None = None
    while True:
        await asyncio.sleep(0.15)
        try:
            texts = await frame.evaluate(_OPTIONS, token) or []
        except Exception:  # noqa: BLE001
            texts = []
        if texts and texts == last:
            return texts
        last = texts
        if loop.time() >= deadline:
            return texts


# Chooses a value in a searchable combobox the way a person does: opens it, types the value (or, failing that, its first
# word), waits for the choices (its own list, or one that appeared after it was opened: never another control's list that
# was already on the page), clicks the one that is the value (case and spacing aside; never one that only contains it) and checks the box then
# shows it. When nothing fits, puts back the text the box held and closes its list. Takes the page, the control and the
# value; raises ValueError when it cannot.
async def _set_combo(page: Page, control: dict[str, Any], value: str) -> None:
    if not value.strip():
        raise ValueError(f"there is no value to choose in {control.get('label') or control['key']!r}")
    frame, token, found = await _locate(page, control)
    try:
        # already showing the value (only the box's shown value counts: text typed into it may not have been chosen)
        if not found["open"] and _norm(found["display"]) == _norm(value):
            return
        target = _marked(frame, token, "el")
        typing = _marked(frame, token, "input") if found["typing"] else None
        words = value.strip().split()
        attempts = [value.strip(), *([words[0]] if len(words) > 1 else [])] if typing is not None else [""]
        chosen: str | None = None
        offered: list[str] = []
        for text in attempts:
            try:
                await target.click(timeout=3000)
            except Exception:  # noqa: BLE001 - covered or tiny: focusing the box is enough to type into it
                if typing is not None:
                    await typing.focus()
            if typing is not None:
                await typing.fill("", timeout=_OP_TIMEOUT_MS)
                await typing.press_sequentially(text, delay=25, timeout=_OP_TIMEOUT_MS)
            offered = await _wait_options(frame, token)
            index = pick_option(offered, value)
            if index is None:
                continue
            try:
                await _marked(frame, token, f"choice:{index}").click(timeout=2500)
            except Exception:  # noqa: BLE001 - the list was drawn again: mark it afresh and click once more
                offered = await _wait_options(frame, token, seconds=1.0)
                index = pick_option(offered, value)
                if index is None:
                    continue
                await _marked(frame, token, f"choice:{index}").click(timeout=2500)
            chosen = offered[index]
            break
        if chosen is None:
            if typing is not None:
                try:
                    # what was typed was never chosen: it must not stay in the box (a form could send it)
                    await typing.fill(found["input_value"], timeout=1000)
                    await typing.press("Escape", timeout=1000)
                except Exception:  # noqa: BLE001
                    pass
            seen = f" (it offered: {', '.join(offered[:8])})" if offered else " (no choices appeared)"
            raise ValueError(f"no choice of {control.get('label') or control['key']!r} matches {value!r}{seen}")
        want = _norm(chosen)

        # It shows the choice in its box, or holds it as the typed text with the list closed (not just what was typed)
        def shows(f: dict[str, Any]) -> bool:
            return want in _norm(f["display"]) or (_norm(f["input_value"]) == want and not f["open"])

        if not await _settle(frame, token, control, shows):
            raise ValueError(f"{control.get('label') or control['key']!r} does not show {chosen!r} after choosing it")
    finally:
        await _unmark(frame, token)


# Types a text into a rich-text box (a contenteditable element), replacing what it held, and checks it holds the text.
# Takes the page, the control and the text; raises ValueError when it cannot.
async def _set_rich(page: Page, control: dict[str, Any], value: str) -> None:
    frame, token, _found = await _locate(page, control)
    want = _norm(value)
    try:
        box = _marked(frame, token, "el")
        await box.fill(value, timeout=_OP_TIMEOUT_MS)
        if await _settle(frame, token, control, lambda f: _norm(f["control"]["value"]) == want, seconds=1.0):
            return
        # an editor that ignores a direct fill: select what it holds and type over it
        await box.click(timeout=_OP_TIMEOUT_MS)
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(value)
        if not await _settle(frame, token, control, lambda f: _norm(f["control"]["value"]) == want):
            raise ValueError(f"{control.get('label') or control['key']!r} does not hold the text that was typed")
    finally:
        await _unmark(frame, token)


# Puts a value into a custom control found by SCRAPE_CUSTOM (looked up again in the form, dialog or page it was read from,
# in whichever frame of the page it is): an ARIA radio group (the option's text), an ARIA checkbox (yes/no) or a group of
# them (labels joined by ", "), a combobox (the choice's text) or a rich-text box (the text). Takes the page, the control
# and the value; raises ValueError with a plain message when it cannot.
async def set_custom(page: Page, control: dict[str, Any], value: str) -> None:
    setters = {"radio": _set_radio, "combobox": _set_combo, "richtext": _set_rich,
               "checkbox": _set_box_group if control.get("options") else _set_box}
    setter = setters.get(control.get("type") or "")
    if setter is None:
        raise ValueError(f"{control.get('key')!r} is not a custom control (type {control.get('type')!r})")
    try:
        await setter(page, control, value)
    except PlaywrightError as exc:  # a click or a key press that timed out, or a frame that went away
        why = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise ValueError(f"could not set {control.get('label') or control['key']!r} to {value!r}: {why}") from exc


# ----------------------------------------------------------------------------- lists that show more

# Marks the buttons that may show more of a list, best first, each with "<token>:<i>" (three at most): one named like
# "Load more", "Show more", "More results" that comes after the list's last row, then other such buttons, then ones that
# open and close a part of the page (aria-expanded), then a "Next" that is not part of a form or dialog (a form's Next moves
# a wizard on). A button that shows more of a text ("Show more details", "See more info") is never one, nor a page number.
# Returns their names.
_MORE_BUTTONS = "([token, nextOk]) => {" + _LIB + r"""
  const MORE = /^(load|show|view|see)\s+(\d+\s+)?more\b.{0,30}$|^more\s+(results|items|rows|entries|records)\b.{0,20}$/i;
  const TEXT = /\bmore\s+(details?|info(rmation)?|text|about|description|options|filters?|settings|fields|actions)\b/i;
  const NEXT = /^next(\s+page)?\s*[›»>→]?$/i;
  const rows = [...document.querySelectorAll('tr, li, [role=row], [role=listitem], [role=article], article')].filter(shown);
  const last = rows[rows.length - 1];
  const found = [];
  for (const b of document.querySelectorAll('button, a, [role=button], [role=link], input[type=button]')) {
    if (!shown(b) || b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    const name = nameOf(b);
    let rank = -1;
    if (MORE.test(name) && !TEXT.test(name)) {
      const after = !last || !!(last.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
      rank = b.hasAttribute('aria-expanded') ? 2 : after ? 0 : 1;
    } else if (nextOk && NEXT.test(name) && !b.closest('form, ' + DIALOGS)) {
      rank = 3;
    }
    if (rank >= 0) found.push({rank, at: found.length, b, name});
  }
  found.sort((x, y) => x.rank - y.rank || x.at - y.at);
  return found.slice(0, 3).map((f, i) => { f.b.setAttribute('data-webkit-mark', token + ':' + i); return f.name; });
}"""

# Marks the largest part of the page that scrolls on its own (a list in a box with a scroll bar) with
# data-webkit-scroller="<token>", so it can be measured and scrolled. Changes nothing else.
_MARK_SCROLLER = """(token) => {
  let best = null, area = 0;
  for (const e of document.querySelectorAll('body *')) {
    if (e.scrollHeight <= e.clientHeight + 20 || e.clientHeight < 50) continue;
    const oy = getComputedStyle(e).overflowY;
    if (oy !== 'auto' && oy !== 'scroll' && oy !== 'overlay') continue;
    if (e.clientHeight * e.clientWidth > area) { area = e.clientHeight * e.clientWidth; best = e; }
  }
  if (best) best.setAttribute('data-webkit-scroller', token);
}"""

# Scrolls the window, and the marked scrollable part, near the bottom (phase 0) or to the very bottom (phase 1): going
# there in two moves makes sure the page sees a scroll even when it was at the bottom already.
_SCROLL = """([token, phase]) => {
  const box = document.querySelector('[data-webkit-scroller="' + token + '"]');
  const h = document.documentElement.scrollHeight;
  if (phase === 0) {
    window.scrollTo(0, Math.max(0, h - innerHeight - 600));
    if (box) box.scrollTop = Math.max(0, box.scrollHeight - box.clientHeight - 600);
  } else {
    window.scrollTo(0, h);
    if (box) box.scrollTop = box.scrollHeight;
  }
}"""

# The size of what a document shows: its height, the height of its marked scrollable part, how many rows and list items it
# has, the text of the first and last of them (which changes when a pager replaces the rows), and how many signs of loading
# it shows ("Loading…" lines, shown busy or progress elements).
_MEASURE = """(token) => {
  const items = document.querySelectorAll('tr, li, [role=row], [role=listitem], [role=article], article');
  const s = document.querySelector('[data-webkit-scroller="' + token + '"]');
  const first = items[0], last = items[items.length - 1];
  const text = document.body ? document.body.innerText : '';
  const busy = (text.match(/\\bloading(\\s+more)?\\s*(…|\\.{2,3})|please wait/gi) || []).length
    + [...document.querySelectorAll('[aria-busy=true], [role=progressbar]')].filter((e) => e.getClientRects().length > 0).length;
  return [document.documentElement.scrollHeight, s ? s.scrollHeight : 0, items.length,
          (first ? first.textContent.slice(0, 80) : '') + '|' + (last ? last.textContent.slice(0, 80) : ''), busy];
}"""


# What a page shows, summed over its frames (see _MEASURE).
class _Size(NamedTuple):
    height: int
    inner: int
    items: int
    signature: str
    busy: int


# Measures every frame of the page. Takes the page and the token of the marked scrollable parts; returns their _Size.
async def _measure(page: Page, token: str) -> _Size:
    height = inner = count = busy = 0
    signature = ""
    for frame in frames(page):
        try:
            h, s, n, sig, b = await frame.evaluate(_MEASURE, token)
        except Exception:  # noqa: BLE001 - a frame that is loading or gone
            continue
        height, inner, count, signature, busy = height + h, inner + s, count + n, signature + "\n" + sig, busy + b
    return _Size(height, inner, count, signature, busy)


# Waits for a list to show more after a button was pressed or the page scrolled. Takes the page, the token of the marked
# scrollable parts, the size before, how long to wait and whether a button was pressed; returns True when the page gained
# rows or list items, or (after a button) shows other rows, or got taller and settled with no new sign of loading (a
# "Loading…" line alone makes a page taller; a progress bar that was there before does not count).
async def _grew(page: Page, token: str, before: _Size, wait: float, pressed: bool) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    last: _Size | None = None
    while True:
        await asyncio.sleep(0.15)
        after = await _measure(page, token)
        if after.items > before.items:
            return True
        # a Next button that replaced the rows with the next ones (or opened the next page of the list)
        if pressed and after.signature != before.signature and after.items > 0:
            return True
        taller = after.height > before.height + 1 or after.inner > before.inner + 1
        loading = after.busy > before.busy
        if taller and not loading and after == last:
            return True
        if loop.time() >= deadline:
            return taller and not loading
        last = after


# Shows more of a list: presses a "Load more" / "Show more" / "More results" / "Next" button when there is one (the one
# after the list first; when a press shows nothing more, the next one, two presses at most), else, or then, scrolls the
# window and the largest scrollable part of the page to the bottom (lists that load as you scroll).
# Takes the page, how long to wait after each try and whether a "Next" may be pressed (next_ok False: only "Load more"-style
# buttons and scrolling, for a list the plan does not page through); returns True when the page gained rows or list items,
# or got taller and settled with no new sign of loading, or, after a Next button, shows other rows.
async def load_more(page: Page, wait: float = _MORE_WAIT, next_ok: bool = True) -> bool:
    token = "m" + uuid.uuid4().hex[:12]
    try:
        for frame in frames(page):
            try:
                await frame.evaluate(_MARK_SCROLLER, token)
            except Exception:  # noqa: BLE001 - a frame that is loading or gone
                pass
        before = await _measure(page, token)
        presses = 0
        for frame in frames(page):
            if presses >= 2:
                break
            try:
                names = await frame.evaluate(_MORE_BUTTONS, [token, next_ok]) or []
            except Exception:  # noqa: BLE001
                continue
            for i in range(len(names)):
                if presses >= 2:
                    break
                try:
                    button = frame.locator(f'[data-webkit-mark="{token}:{i}"]')
                    await button.scroll_into_view_if_needed(timeout=2000)
                    await button.click(timeout=_OP_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - it could not be pressed: try the next one, or scrolling
                    continue
                presses += 1
                if await _grew(page, token, before, wait, pressed=True):
                    return True
        for phase in (0, 1):
            for frame in frames(page):
                try:
                    await frame.evaluate(_SCROLL, [token, phase])
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(0.05)
        return await _grew(page, token, before, wait, pressed=False)
    finally:
        for frame in frames(page):
            await _unmark(frame, token)


# ----------------------------------------------------------------------------- interruptions

# Finds what covers the page and can be closed, and marks the button to close each with "<token>:<n>". An interruption is a
# shown dialog (role=dialog, aria-modal, <dialog>), a cookie or consent banner, or a fixed element covering 30% or more of
# the window, sitting in its middle, or anchored to its bottom edge. One is left alone when it holds something to fill in
# or a choice to make (radio buttons, checkboxes, switches; a cookie banner's toggles and a lone "don't show this again"
# box excepted), a form, a button that saves/submits/deletes/pays/confirms, a confirmation (an OK, Yes or Continue next to
# a Cancel, No or Back: the page asks before it acts), or words like "are you sure" (it is part of
# the task, not an interruption); so is a fixed part that holds the page itself (an app's frame, with the main heading or
# main area). On a cookie banner, a "reject"/"necessary only" button is preferred; otherwise a button named like DISMISS;
# on a bar along the bottom (not a cookie banner) only a button that closes it (Close, ×, Dismiss, Got it, Not now), never
# "Continue" or "OK", which act on the page. Returns [{mark, name, reachable, z, cover, sig}], the ones on top first; sig
# tells one layer and button from another across calls.
_OVERLAYS = "([token, dismissSource, rejectSource]) => {" + _LIB + r"""
  if (!document.body) return [];
  const DISMISS_RE = new RegExp(dismissSource, 'i');
  const REJECT_RE = new RegExp(rejectSource, 'i');
  const RANK = [/^(close|dismiss|×|✕|got it|no,? thanks|not now|skip)$/i, /^ok(ay)?$/i, /^(accept( all)?|agree|i agree)$/i];
  const CLOSE = /^(close|dismiss|×|✕|got it|no,? thanks|not now)$/i;
  const EDIT = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image]), select, textarea, [contenteditable]:not([contenteditable=false]), [role=combobox], [role=textbox], [role=radiogroup], [role=listbox], [role=spinbutton], [role=slider]';
  const CHOICE = 'input[type=checkbox], input[type=radio], [role=radio], [role=checkbox], [role=switch], [role=option], [role=menuitemradio], [role=menuitemcheckbox]';
  const NOT_AGAIN = /\b(don['’]?t|do not|never)\s+(show|ask|remind)\b|\bshow (this|it) again\b/i;
  const ACTION = /\b(save|submit|delete|remove|pay|purchase|buy|order|confirm|approve|decline|send|sign out|log ?out|sign in|log ?in)\b/i;
  const DANGER = /are you sure|cannot be undone|can['’]t be undone|unsaved|discard/i;
  const COOKIE = '[class*=cookie i], [id*=cookie i], [class*=consent i], [id*=consent i]';
  const DIALOG = '[role=dialog], [role=alertdialog], dialog[open], [aria-modal="true"]';
  const vw = innerWidth, vh = innerHeight;
  const fixedUp = (e) => { for (let n = e; n && n !== document.body && n !== document.documentElement; n = n.parentElement) if (getComputedStyle(n).position === 'fixed') return n; return null; };
  const cover = (e) => {
    const r = e.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)), h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    return (w * h) / (vw * vh);
  };
  const atBottom = (e) => { const r = e.getBoundingClientRect(); return r.height > 0 && r.bottom >= vh - 24 && r.bottom <= vh + 2; };
  const zOf = (e) => { let z = 0; for (let n = e; n && n !== document.documentElement; n = n.parentElement) { const v = parseInt(getComputedStyle(n).zIndex, 10); if (!isNaN(v)) z = Math.max(z, v); } return z; };
  const layers = new Set();
  for (const e of document.querySelectorAll(DIALOG)) if (shown(e)) layers.add(e);
  // a cookie or consent banner: the fixed layer or dialog it is in, else (a banner printed in the page) its outermost part
  // that is still named like one
  for (const e of document.querySelectorAll(COOKIE)) {
    if (!shown(e) || e === document.body || e === document.documentElement) continue;
    let top = fixedUp(e) || e.closest(DIALOG);
    if (!top) {
      top = e;
      for (let n = e.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) if (n.matches(COOKIE)) top = n;
      if (clean(top.innerText).length > 1500 || top.closest('form')) continue;  // a settings page's section, not a banner
    }
    layers.add(top);
  }
  for (const fx of [0.25, 0.5, 0.75]) {
    for (const y of [vh * 0.25, vh * 0.5, vh * 0.75, vh - 3]) {
      for (const hit of document.elementsFromPoint(vw * fx, y)) {
        const f = fixedUp(hit);
        if (f && shown(f) && (cover(f) >= 0.3 || atBottom(f) || (fx === 0.5 && y === vh * 0.5))) layers.add(f);
      }
    }
  }
  const out = [];
  const picked = new Set();
  const heading = [...document.querySelectorAll('h1')].find(shown);
  const pageText = clean(document.body.innerText).length;
  for (const layer of layers) {
    const text = clean(layer.innerText);
    const byRole = layer.matches(DIALOG) || !!layer.querySelector(DIALOG);
    const buttons = [...layer.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]')].filter((b) => shown(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
    const names = buttons.map(nameOf);
    // a cookie banner is named like one, or speaks of cookies and offers to accept or reject them (a pop-up that only
    // mentions cookies is judged like any other)
    const cookie = layer.matches(COOKIE) || !!layer.querySelector(COOKIE)
      || (/\bcookies?\b/i.test(text) && names.some((t) => REJECT_RE.test(t) || /^(accept|allow)\b/i.test(t)));
    if ([...layer.querySelectorAll(EDIT)].some(shown) || DANGER.test(text)) continue;
    // a confirmation: a button that goes ahead (OK, Yes, Continue, Accept) next to one that holds back (Cancel, No, Back).
    // It asks about something the page is about to do (its OK may save), so it is never answered here
    if (!cookie && names.some((t) => /^(cancel|no|back|go back|keep editing)\b/i.test(t)) && names.some((t) => /^(ok(ay)?|yes|continue|proceed|accept|confirm|go ahead)\b/i.test(t))) continue;
    if (cookie) {
      if (text.length > 6000) continue;
    } else {
      const choices = [...layer.querySelectorAll(CHOICE)].filter(shown);
      const notAgain = choices.length === 1 && NOT_AGAIN.test(clean((choices[0].closest('label') || choices[0].parentElement || choices[0]).innerText));
      if (choices.length && !notAgain) continue;  // a choice to make, which a "Continue" would confirm
      if (layer.closest('form') || layer.querySelector('form')) continue;
      if (names.some((t) => ACTION.test(t))) continue;
      if (text.length > (byRole ? 4000 : 1500) || buttons.length > 8) continue;
      if (!byRole && cover(layer) < 0.3 && names.some((t) => /^(next|back|previous)\b/i.test(t))) continue;  // a wizard's step bar
      // the page itself in a fixed frame: it holds the main area, or the main heading and most of the page's text
      if (!byRole && (layer.matches('main, [role=main]') || layer.querySelector('main, [role=main]')
                      || (heading && layer.contains(heading) && text.length * 2 > pageText))) continue;
    }
    const bar = !cookie && !byRole && cover(layer) < 0.3 && atBottom(layer);
    const order = (t) => {
      if (cookie && REJECT_RE.test(t)) return 0;
      if (!DISMISS_RE.test(t) || (bar && !CLOSE.test(t))) return 99;
      const i = RANK.findIndex((r) => r.test(t));
      return i < 0 ? RANK.length + 1 : i + 1;
    };
    let best = -1;
    buttons.forEach((b, i) => { if (!picked.has(b) && order(names[i]) < 99 && (best < 0 || order(names[i]) < order(names[best]))) best = i; });
    if (best < 0) continue;
    const b = buttons[best];
    picked.add(b);
    const r = b.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let reachable = true;
    if (cx >= 0 && cy >= 0 && cx < vw && cy < vh) { const top = document.elementFromPoint(cx, cy); reachable = !!top && (top === b || b.contains(top)); }
    const mark = token + ':' + out.length;
    b.setAttribute('data-webkit-mark', mark);
    out.push({mark, name: names[best], reachable, z: zOf(layer), cover: cover(layer), sig: names[best] + '|' + text.slice(0, 300)});
  }
  out.sort((a, b) => (Number(b.reachable) - Number(a.reachable)) || (b.z - a.z) || (b.cover - a.cover));
  return out;
}"""


# Closes what stands in front of the page: cookie banners, "What's new" and other pop-ups, in every frame, top layer first
# (closing one can uncover another). Never clicks anything outside such an overlay, never a button that saves, submits,
# deletes, pays or confirms, and never the same button of the same overlay twice (one that stays after its button was
# pressed is left as it is). Takes the page; returns the names of the buttons clicked, in order.
async def dismiss_interruptions(page: Page) -> list[str]:
    clicked: list[str] = []
    pressed: set[str] = set()
    for _round in range(4):
        progressed = False
        for frame in frames(page):
            token = "d" + uuid.uuid4().hex[:12]
            try:
                found = await frame.evaluate(_OVERLAYS, [token, DISMISS.pattern, _REJECT.pattern])
            except Exception:  # noqa: BLE001
                continue
            try:
                for item in found:
                    if item["sig"] in pressed:
                        continue
                    button = frame.locator(f'[data-webkit-mark="{item["mark"]}"]')
                    try:
                        await button.click(timeout=1500)
                    except Exception:  # noqa: BLE001 - covered by another layer: tried again once that one is closed
                        continue
                    pressed.add(item["sig"])
                    clicked.append(item["name"])
                    progressed = True
                    try:
                        await button.wait_for(state="hidden", timeout=1000)
                    except Exception:  # noqa: BLE001 - it stays, but the click was made
                        pass
                    break
            finally:
                await _unmark(frame, token)
            if progressed:
                break
        if not progressed:
            break
    return clicked


# Whether a dialog that holds nothing to fill in is shown (a question or a confirmation, as opposed to a form in a dialog).
_DIALOG_SHOWN = "() => {" + _LIB + r"""
  const EDIT = 'input:not([type=hidden]):not([type=button]):not([type=submit]), select, textarea, [contenteditable]:not([contenteditable=false]), [role=combobox], [role=textbox], [role=radiogroup], [role=radio], [role=checkbox]';
  return [...document.querySelectorAll('[role=dialog], [role=alertdialog], dialog[open], [aria-modal="true"]')]
    .some((d) => shown(d) && ![...d.querySelectorAll(EDIT)].some(shown));
}"""


# Whether the page, or a frame of it, shows a dialog that holds nothing to fill in (a question, a confirmation, a message).
# Takes the page; returns True or False.
async def dialog_shown(page: Page) -> bool:
    for frame in frames(page):
        try:
            if await frame.evaluate(_DIALOG_SHOWN):
                return True
        except Exception:  # noqa: BLE001 - a frame that is loading or gone
            continue
    return False


# ----------------------------------------------------------------------------- error boxes and logins

# Marks a shown "Retry" / "Try again" / "Reload" button that sits in a short message about an error. Returns its name, or null.
_RETRY = "(token) => {" + _LIB + r"""
  const RETRY = /^(retry|try again|reload)(\s+(now|page|loading))?$/i;
  // a server error's number counts beside words that say so ("HTTP 503", "502 Bad Gateway") or alone ("503"); a number in
  // other text ("Amount: 504") does not
  const ERROR = /\berror\b|went wrong|try again|unavailable|failed to load|could ?n[o']t load|timed out|\b(http|status|code)\s*:?\s*50[0-9]\b|\b50[0-9]\s*[-:–]?\s*(bad gateway|gateway|service|server|internal)\b/i;
  const CODE = /^(http\s*)?50[0-9]$/i;
  for (const b of document.querySelectorAll('button, a, [role=button], input[type=button]')) {
    if (!shown(b) || b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
    const name = nameOf(b).replace(/^[↻⟳\s]+/, '');
    if (!RETRY.test(name)) continue;
    const own = clean(b.innerText || b.value);
    for (let n = b.parentElement, up = 0; n && up < 6; n = n.parentElement, up++) {
      const rest = clean(clean(n.innerText).replace(own, ''));
      if (rest.length > 500) break;
      if (ERROR.test(rest) || CODE.test(rest)) { b.setAttribute('data-webkit-mark', token); return name; }
    }
  }
  return null;
}"""

# Whether a shown password box is in this document, and the text a login page would say "sign in" with (the page's text,
# its title, and the labels of input buttons, which are not part of the text).
_PASSWORD = "() => {" + _LIB + """
  const has = [...document.querySelectorAll('input[type=password]')].some(shown);
  const extra = [...document.querySelectorAll('input[type=submit], input[type=button]')].map((b) => b.value || '').join(' ');
  return [has, (document.title || '') + '\\n' + (document.body ? document.body.innerText : '') + '\\n' + extra];
}"""


# When the page (or a frame of it) shows a short error message with a Retry / Try again / Reload button, presses it once.
# Takes the page; returns True when it pressed one.
async def retry_if_error(page: Page) -> bool:
    for frame in frames(page):
        token = "r" + uuid.uuid4().hex[:12]
        try:
            name = await frame.evaluate(_RETRY, token)
        except Exception:  # noqa: BLE001
            continue
        if not name:
            continue
        try:
            await frame.locator(f'[data-webkit-mark="{token}"]').click(timeout=_OP_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
            return False
        finally:
            await _unmark(frame, token)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        return True
    return False


# Whether a page is a login page: it shows a password box and words like "sign in" or "log in".
# Takes the page's text and whether a shown password box is on it; returns True or False.
def is_login_page(text: str, has_password_input: bool) -> bool:
    return bool(has_password_input) and _LOGIN_WORDS.search(text or "") is not None


# Whether the open page, or a frame of it, is a login page. Takes the page; returns True or False.
async def login_form_shown(page: Page) -> bool:
    for frame in frames(page):
        try:
            has, text = await frame.evaluate(_PASSWORD)
        except Exception:  # noqa: BLE001
            continue
        if is_login_page(text, has):
            return True
    return False
