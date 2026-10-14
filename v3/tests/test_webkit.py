"""Checks for the real-world page helpers (recipe/webkit.py):

    python tests/test_webkit.py

Part 1 needs nothing (no browser): the login test, the dismiss words, picking a combobox choice and joining control lists.
Part 2 opens pages written here (page.set_content) in a headless browser: a decision form with ARIA radio groups and
checkboxes, a searchable combobox whose choices appear in a "portal" at the end of the page, and a rich-text note; the same
kind of form inside an <iframe>; lists that grow as you scroll or with a "Load more" or "Next" button; cookie banners and
pop-ups; an error box with a Retry button; and login pages. It uses no test site, no server and no model."""

import asyncio
import html
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recipe.plan_exec as px
from recipe import webkit
from recipe.webkit import DISMISS, is_login_page, merge_controls, pick_option

checks = 0


def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# ------------------------------------------------------------------ part 1: no browser needed

check("a password box and 'Sign in' make a login page", is_login_page("Welcome back\nSign in to your account\nUsername\nPassword", True))
check("'Log in', 'LOGIN' and 'Sign-in' count as sign-in words", all(is_login_page(t, True) for t in ("Log in", "LOGIN", "Sign-in with your email")))
check("no password box: not a login page, whatever it says", not is_login_page("Sign in", False))
check("a password box without sign-in words (changing a password) is not a login page", not is_login_page("Change your password\nNew password\nSave", True))
check("'Sign in' inside a longer word does not count", not is_login_page("Designing interfaces\nPassword strength", True))

check("the dismiss words match closing buttons", all(DISMISS.match(t) for t in ("Accept all", "Accept", "Got it", "OK", "Okay", "No thanks", "No, thanks", "Not now", "Close", "×", "✕", "I agree")))
check("the dismiss words never match a button that decides or pays", not any(DISMISS.match(t) for t in ("Submit", "Submit decision", "Accept and pay", "Confirm", "Delete", "Save", "Continue to payment")))

REASONS = ["No cover on loss date", "Peril not covered", "Duplicate claim"]
check("a choice is picked by its exact text, ignoring case and spacing", pick_option(REASONS, "peril  NOT covered") == 1)
check("a value that is not a choice never picks the only choice that contains it", pick_option(REASONS, "Duplicate") is None
      and pick_option(["Partially approved", "Rejected", "On hold"], "Approved") is None)
check("a value that several choices contain picks none", pick_option(REASONS, "cover") is None)
check("a value no choice holds picks none", pick_option(REASONS, "Flood damage") is None and pick_option(REASONS, "  ") is None)

native = [{"key": "payout", "tag": "input", "type": "number"}, {"key": "rs-input-k3j9x2", "tag": "input", "type": ""}, {"key": "q", "tag": "input", "type": ""}]
custom = [{"key": "combo:Decline reason", "native_key": "rs-input-k3j9x2", "type": "combobox"}, {"key": "q", "type": "combobox"}]
check("joining control lists drops the native input a custom control stands for", [c["key"] for c in merge_controls(native, custom)] == ["payout", "combo:Decline reason", "q"])


# ------------------------------------------------------------------ part 2: pages in a headless browser

STYLE = """<style>
  body { font-family: sans-serif; margin: 16px; }
  [role=radio], [role=checkbox], [role=switch] { display: inline-block; padding: 4px 8px; border: 1px solid #888; margin: 2px; cursor: pointer; min-width: 20px; min-height: 16px; }
  [aria-checked=true] { background: #cde; }
  .sel, .combo { border: 1px solid #888; width: 320px; padding: 4px; display: flex; gap: 6px; }
  .sel input, .combo input { border: 0; outline: 0; width: 120px; }
  .menu { position: absolute; background: #fff; border: 1px solid #888; width: 320px; z-index: 10; }
  [role=option] { padding: 4px; cursor: pointer; }
  .note { border: 1px solid #888; min-height: 40px; width: 320px; }
</style>"""

# A small script that makes ARIA radios and checkboxes work, and turns a box into a searchable combobox whose choices are
# drawn a moment after typing, in a list added at the end of the body (a "portal"), as React-select does.
WIDGETS_JS = """<script>
  window.clicks = {submit: 0};
  document.addEventListener('click', (e) => {
    const r = e.target.closest('[role=radio]');
    if (r) { for (const o of r.closest('[role=radiogroup]').querySelectorAll('[role=radio]')) o.setAttribute('aria-checked', String(o === r)); }
    const b = e.target.closest('[role=checkbox], [role=switch]');
    if (b) b.setAttribute('aria-checked', String(b.getAttribute('aria-checked') !== 'true'));
  });
  function makeCombo(input, shown, options, listId) {
    let menu = null;
    const close = () => { if (menu) { menu.remove(); menu = null; } input.setAttribute('aria-expanded', 'false'); };
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      setTimeout(() => {
        close();
        if (!q) return;
        menu = document.createElement('div');
        menu.className = 'menu'; menu.id = listId; menu.setAttribute('role', 'listbox');
        const r = input.getBoundingClientRect();
        menu.style.left = r.left + 'px'; menu.style.top = (r.bottom + scrollY + 6) + 'px';
        for (const o of options.filter((o) => o.toLowerCase().includes(q))) {
          const d = document.createElement('div');
          d.setAttribute('role', 'option'); d.textContent = o;
          d.addEventListener('mousedown', (e) => { e.preventDefault(); shown.textContent = o; input.value = ''; close(); });
          menu.appendChild(d);
        }
        document.body.appendChild(menu);
        input.setAttribute('aria-expanded', 'true');
      }, 120);
    });
  }
</script>"""

DECIDE_PAGE = f"""<!doctype html><html><head><title>Claim</title>{STYLE}</head><body>
{WIDGETS_JS}
<form id="search" role="search"><input role="combobox" aria-autocomplete="list" name="q" placeholder="Search claims"></form>
<h1>Decision</h1>
<form id="decide">
  <span id="outcome-l">Outcome</span>
  <div role="radiogroup" aria-labelledby="outcome-l" id="rg-k3j9x2">
    <div role="radio" aria-checked="false" tabindex="0">Approve</div>
    <div role="radio" aria-checked="false" tabindex="-1">Approve partially</div>
    <div role="radio" aria-checked="false" tabindex="-1">Decline</div>
    <div role="radio" aria-checked="false" tabindex="-1">Request information</div>
  </div>
  <fieldset><legend>Priority</legend>
    <div role="radiogroup"><span role="radio" aria-checked="true">Normal</span><span role="radio" aria-checked="false">Urgent</span></div>
  </fieldset>
  <div role="radiogroup" aria-label="Hidden choice" style="display:none"><div role="radio" aria-checked="false">Secret</div></div>
  <p><label>Payout <input type="number" name="payout"></label>
     <select name="currency"><option>INR</option><option>USD</option></select></p>
  <div role="checkbox" aria-checked="false" tabindex="0">Fraud suspected</div>
  <div role="switch" aria-checked="true" aria-label="Notify customer" tabindex="0"></div>
  <div class="label">Documents received</div>
  <div role="group" aria-label="Documents received">
    <div role="checkbox" aria-checked="false">Photos</div>
    <div role="checkbox" aria-checked="false">Police report</div>
    <div role="checkbox" aria-checked="false">Repair invoice</div>
  </div>
  <div class="field"><label>Decline reason</label>
    <div class="sel" id="sel-x8k2p9"><div class="sel__value"><span class="sel__placeholder">Select...</span></div>
      <input role="combobox" aria-autocomplete="list" aria-expanded="false" id="rs-input-k3j9x2" autocomplete="off"><span class="sel__arrow">▾</span></div>
  </div>
  <div class="field"><div class="label">Internal note</div><div class="note" contenteditable="true"></div></div>
  <button type="button" id="submit" onclick="window.clicks.submit++">Submit decision</button>
</form>
<script>
  makeCombo(document.getElementById('rs-input-k3j9x2'), document.querySelector('.sel__value'),
            ['No cover on loss date', 'Peril not covered', 'Duplicate claim'], 'reason-menu');
</script>
</body></html>"""

# The decision form inside a same-origin iframe (written with srcdoc); its ids are made up afresh on every load
FRAME_FORM = f"""<!doctype html><html><head>{STYLE}</head><body>
{WIDGETS_JS}
<h2>Decision</h2>
<div id="outcome-l">Outcome</div>
<div role="radiogroup" aria-labelledby="outcome-l">
  <div role="radio" aria-checked="false">Approve</div><div role="radio" aria-checked="false">Decline</div><div role="radio" aria-checked="false">Request information</div>
</div>
<p><label>Payout (₹) <input type="number" name="payout"></label></p>
<label id="doc-l">Document needed</label>
<div role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-labelledby="doc-l" aria-controls="doc-list" class="combo">
  <span class="shown"></span><input type="text" aria-autocomplete="list" autocomplete="off">
</div>
<p>Internal note</p>
<div class="note" contenteditable="true" id="note"></div>
<button type="button" id="submit" onclick="window.submitted = true">Submit decision</button>
<script>
  const rid = () => 'f-' + Math.random().toString(36).slice(2, 5) + Math.floor(100 + Math.random() * 900);
  const box = document.querySelector('[role=combobox]');
  box.id = rid(); document.querySelector('[role=radiogroup]').id = rid();
  makeCombo(box.querySelector('input'), box.querySelector('.shown'), ['Police report', 'Photos', 'Repair invoice'], 'doc-list');
</script>
</body></html>"""

FRAME_PAGE = f"""<!doctype html><html><body>
<h1>Claim CLM-26-04123</h1><p>Peril: Theft</p><p>Amount claimed: ₹48,000</p>
<iframe id="decision" style="width:700px;height:520px;border:0" srcdoc="{html.escape(FRAME_FORM, quote=True)}"></iframe>
<iframe style="display:none" srcdoc="<p>tracking pixel text</p>"></iframe>
</body></html>"""

INFINITE_PAGE = """<!doctype html><html><body>
<h1>Claims</h1><ul id="feed"></ul><p id="status"></p>
<script>
  let n = 0, busy = false;
  const feed = document.getElementById('feed');
  function add() { for (let i = 0; i < 20; i++) { const li = document.createElement('li'); li.style.height = '40px'; li.textContent = 'Claim CLM-' + (1000 + ++n); feed.appendChild(li); } }
  add();
  addEventListener('scroll', () => {
    if (busy || n >= 60) return;
    if (scrollY + innerHeight >= document.documentElement.scrollHeight - 300) {
      busy = true; document.getElementById('status').textContent = 'Loading…';
      setTimeout(() => { add(); busy = false; document.getElementById('status').textContent = ''; }, 300);
    }
  });
</script></body></html>"""

INNER_SCROLL_PAGE = """<!doctype html><html><body>
<h1>Claims</h1>
<div id="box" style="height:300px; overflow:auto; border:1px solid #888">
  <table><thead><tr><th>Claim</th><th>Amount</th></tr></thead><tbody id="rows"></tbody></table>
</div>
<script>
  let n = 0, busy = false;
  const box = document.getElementById('box'), rows = document.getElementById('rows');
  function add() { for (let i = 0; i < 20; i++) { const tr = document.createElement('tr'); tr.style.height = '30px'; tr.innerHTML = '<td>CLM-' + (2000 + ++n) + '</td><td>₹' + (n * 100) + '</td>'; rows.appendChild(tr); } }
  add();
  box.addEventListener('scroll', () => {
    if (busy || n >= 60) return;
    if (box.scrollTop + box.clientHeight >= box.scrollHeight - 100) { busy = true; setTimeout(() => { add(); busy = false; }, 250); }
  });
</script></body></html>"""

MORE_BUTTON_PAGE = """<!doctype html><html><body>
<h1>Claims</h1><ul id="list"></ul><button id="more">Load more</button>
<script>
  let n = 0;
  const list = document.getElementById('list'), more = document.getElementById('more');
  function add() { for (let i = 0; i < 20; i++) { const li = document.createElement('li'); li.textContent = 'CLM-' + (3000 + ++n); list.appendChild(li); } if (n >= 40) more.remove(); }
  add();
  more.addEventListener('click', () => setTimeout(add, 300));
</script></body></html>"""

PAGER_PAGE = """<!doctype html><html><body>
<table><thead><tr><th>Claim</th></tr></thead><tbody id="rows"></tbody></table>
<nav><a href="#" data-p="1">1</a> <a href="#" data-p="2">2</a> <a href="#" data-p="3">3</a> <a href="#" id="next">Next ›</a></nav>
<p id="pg"></p>
<script>
  let page = 1;
  function show() { document.getElementById('rows').innerHTML = [...Array(10)].map((_, i) => '<tr><td>CLM-' + (4000 + (page - 1) * 10 + i) + '</td></tr>').join(''); document.getElementById('pg').textContent = 'Page ' + page; }
  show();
  for (const a of document.querySelectorAll('nav a')) a.addEventListener('click', (e) => { e.preventDefault(); page = a.id === 'next' ? page + 1 : Number(a.dataset.p); show(); });
</script></body></html>"""

WIZARD_PAGE = """<!doctype html><html><body>
<form><label>Name <input name="name"></label> <button type="button" id="nx" onclick="window.nexts = (window.nexts || 0) + 1">Next</button></form>
</body></html>"""

BANNERS_PAGE = """<!doctype html><html><body>
<script>window.clicks = {page_close: 0, cookies: ''};</script>
<h1>Claims desk</h1><p>Welcome back.</p>
<button id="page-close" onclick="window.clicks.page_close++">Close</button>
<div id="cookie-bar" class="cookie-banner" style="position:fixed; left:0; right:0; bottom:0; height:70px; background:#eee; z-index:50">
  We use cookies to run this site and, if you agree, to measure how it is used.
  <button onclick="window.clicks.cookies = 'all'; this.parentElement.remove()">Accept all</button>
  <button onclick="window.clicks.cookies = 'necessary'; this.parentElement.remove()">Reject non-essential</button>
</div>
<div id="backdrop" style="position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:100">
  <div role="dialog" aria-modal="true" aria-labelledby="wn" style="background:#fff; width:420px; margin:120px auto; padding:16px">
    <h2 id="wn">What's new</h2><p>Claims now load as you scroll.</p>
    <button onclick="document.getElementById('backdrop').remove()">Got it</button>
  </div>
</div>
</body></html>"""

ACCEPT_ONLY_PAGE = """<!doctype html><html><body>
<h1>Claims desk</h1>
<div id="cookie-notice" style="border:1px solid #888; padding:8px">This site uses cookies.
  <a href="#" onclick="return false">Cookie policy</a>
  <button onclick="window.cookies = 'all'; this.parentElement.remove()">Accept</button>
  <button onclick="window.cookies = 'settings'">Manage preferences</button>
</div>
<p>Content.</p>
</body></html>"""

APP_PROMO_PAGE = """<!doctype html><html><body>
<h1>Claims desk</h1><p>Content.</p>
<div id="promo" style="position:fixed; top:20%; left:20%; width:60%; height:60%; background:#fff; border:1px solid #888">
  <p>Try our new mobile app!</p><button onclick="document.getElementById('promo').remove()">×</button>
</div>
</body></html>"""

DECISION_MODAL_PAGE = """<!doctype html><html><body>
<script>window.clicks = {submit: 0, close: 0, fill_close: 0};</script>
<h1>Claim CLM-26-04123</h1>
<div role="dialog" aria-modal="true" style="position:fixed; top:25%; left:25%; width:50%; background:#fff; border:1px solid #888; z-index:10">
  <h2>Submit decision?</h2><p>Approve CLM-26-04123 with a payout of ₹43,000.</p>
  <button onclick="window.clicks.submit++">Submit</button><button onclick="window.clicks.close++">Close</button>
</div>
</body></html>"""

FORM_MODAL_PAGE = """<!doctype html><html><body>
<script>window.clicks = {close: 0};</script>
<h1>Claim CLM-26-04123</h1>
<div role="dialog" aria-modal="true" style="position:fixed; top:25%; left:25%; width:50%; background:#fff; border:1px solid #888">
  <h2>Add a note</h2><label>Note <input name="note"></label>
  <button onclick="window.clicks.close++">Close</button>
</div>
</body></html>"""

ERROR_PAGE = """<!doctype html><html><body>
<h1>Claims</h1>
<div id="app"><div class="error-box" role="alert"><p>Something went wrong while loading the claims.</p><button id="retry">Retry</button></div></div>
<script>
  window.tries = 0;
  document.getElementById('retry').addEventListener('click', () => {
    window.tries++;
    document.getElementById('app').innerHTML = '<table><thead><tr><th>Claim</th></tr></thead><tbody><tr><td>CLM-26-04123</td></tr></tbody></table>';
  });
</script></body></html>"""

TOOLBAR_PAGE = """<!doctype html><html><body>
<script>window.reloads = 0;</script>
<div class="toolbar"><button onclick="window.reloads++">Reload</button> <span>Showing all 240 claims, newest first. """ + "Each claim lists its policy, peril and amount. " * 15 + """</span></div>
<p>Error rates by peril are on the reports page.</p>
</body></html>"""

LOGIN_PAGE = """<!doctype html><html><head><title>Claims desk</title></head><body>
<form><h1>Sign in</h1><label>Username <input name="username"></label><label>Password <input type="password" name="password"></label>
<button>Log in</button></form>
</body></html>"""

HIDDEN_PASSWORD_PAGE = """<!doctype html><html><body>
<h1>Claims</h1><a href="#">Sign in</a> as someone else
<form style="display:none"><input type="password" name="password"></form>
</body></html>"""

# A filter bar and a form that both have an ARIA radio group printed "Status" just before it
NAMESAKE_PAGE = """<!doctype html><html><body>
<script>document.addEventListener('click', (e) => { const r = e.target.closest('[role=radio]'); if (r) for (const o of r.closest('[role=radiogroup]').querySelectorAll('[role=radio]')) o.setAttribute('aria-checked', String(o === r)); });</script>
<div class="filters"><span>Status</span>
  <div role="radiogroup" id="filter"><span role="radio" aria-checked="true">Open</span> <span role="radio" aria-checked="false">Closed</span></div></div>
<form id="update"><h3>Update</h3><span>Status</span>
  <div role="radiogroup" id="form-status"><span role="radio" aria-checked="true">Open</span> <span role="radio" aria-checked="false">Closed</span></div>
  <button type="button" id="save">Save</button></form>
</body></html>"""

# Two iframes holding the same radio group: without knowing where it was read, a key names neither for sure
TWIN_FRAME = """<div role="radiogroup" aria-label="Outcome"><div role="radio" aria-checked="false">Approve</div></div>"""
TWIN_FRAMES_PAGE = f"""<!doctype html><html><body>
<iframe style="width:400px;height:100px" srcdoc="{html.escape(TWIN_FRAME, quote=True)}"></iframe>
<iframe style="width:400px;height:100px" srcdoc="{html.escape(TWIN_FRAME, quote=True)}"></iframe>
</body></html>"""

# A switch whose own text is its state (Off / On) and a checkbox that shows a tick when checked, each after its label
STATE_TEXT_PAGE = """<!doctype html><html><body>
<script>document.addEventListener('click', (e) => {
  const s = e.target.closest('[role=switch]');
  if (s) { const on = s.getAttribute('aria-checked') !== 'true'; s.setAttribute('aria-checked', String(on)); s.textContent = on ? 'On' : 'Off'; }
  const b = e.target.closest('[role=checkbox]');
  if (b) { const on = b.getAttribute('aria-checked') !== 'true'; b.setAttribute('aria-checked', String(on)); b.textContent = on ? '✓' : ''; }
});</script>
<div><p>Email alerts</p><div role="switch" aria-checked="false" tabindex="0" style="display:inline-block;padding:4px;border:1px solid #888">Off</div></div>
<div><span>Fraud suspected</span> <div role="checkbox" aria-checked="false" tabindex="0" style="display:inline-block;width:20px;height:20px;border:1px solid #888"></div></div>
</body></html>"""

# A searchable combobox whose choices appear in a portal; `shown` is the element that shows the chosen value
PORTAL_COMBO_JS = """<script>
function portalCombo(input, shown, options, top) {
  let menu = null;
  const close = () => { if (menu) { menu.remove(); menu = null; } };
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    setTimeout(() => {
      close(); if (!q) return;
      menu = document.createElement('div'); menu.setAttribute('role', 'listbox');
      menu.style.cssText = 'position:absolute;left:10px;background:#fff;border:1px solid #888;width:300px;z-index:5;top:' + top + 'px';
      for (const o of options.filter((o) => o.toLowerCase().includes(q))) {
        const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = o; d.style.padding = '4px';
        d.addEventListener('mousedown', (e) => { e.preventDefault(); shown.textContent = o; input.value = ''; close(); });
        menu.appendChild(d);
      }
      document.body.appendChild(menu);
    }, 100);
  });
}
</script>"""

# A React-select box as built for production: hashed class names (no "placeholder" or "value" in them) and a random id
HASHED_SELECT_PAGE = f"""<!doctype html><html><body>{PORTAL_COMBO_JS}
<div class="field"><label>Decline reason</label>
  <div class="css-13cymwt" id="ctl"><div class="css-hlgwow"><div class="css-1jqq78o" id="ph">Select...</div>
    <div class="css-19bb58m"><input role="combobox" aria-autocomplete="list" id="rs-k3j9x2" style="width:100px"></div></div>
    <div class="css-1wy0on6">▾</div></div>
</div>
<script>portalCombo(document.getElementById('rs-k3j9x2'), document.getElementById('ph'), ['No cover on loss date', 'Peril not covered', 'Duplicate claim'], 200);</script>
</body></html>"""

# select2-style markup: a hidden native select, and a box whose aria-labelledby points at the span that shows the value
SELECT2_PAGE = """<!doctype html><html><body>
<label for="reason">Decline reason</label>
<select id="reason" name="reason" aria-hidden="true" tabindex="-1" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">
  <option></option><option>No cover on loss date</option><option>Peril not covered</option></select>
<span class="select2" style="display:inline-block;width:300px;border:1px solid #888"><span class="selection">
  <span class="select2-selection" role="combobox" aria-haspopup="true" aria-expanded="false" tabindex="0" aria-labelledby="select2-reason-container">
    <span class="select2-selection__rendered" id="select2-reason-container" role="textbox" aria-readonly="true"><span class="select2-selection__placeholder">Select a reason</span></span>
    <span class="select2-selection__arrow" role="presentation"><b role="presentation"></b></span></span></span></span>
<script>
  document.querySelector('.select2-selection').addEventListener('mousedown', (e) => {
    e.preventDefault();
    if (document.getElementById('dd')) return;
    const dd = document.createElement('span'); dd.id = 'dd'; dd.style.cssText = 'position:absolute;top:60px;left:10px;background:#fff;border:1px solid #888';
    dd.innerHTML = '<ul role="listbox"></ul>';
    for (const o of ['No cover on loss date', 'Peril not covered']) {
      const li = document.createElement('li'); li.setAttribute('role', 'option'); li.textContent = o;
      li.addEventListener('mouseup', () => { document.getElementById('select2-reason-container').textContent = o; document.getElementById('reason').value = o; dd.remove(); });
      dd.firstChild.appendChild(li);
    }
    document.body.appendChild(dd);
  });
</script></body></html>"""

# An ARIA listbox that is always shown (another control) next to a combobox whose portal list does not name itself
LISTBOX_AND_COMBO_PAGE = f"""<!doctype html><html><body>{PORTAL_COMBO_JS}
<script>window.prio = '';</script>
<p>Priority</p>
<div role="listbox" aria-label="Priority" style="border:1px solid #888;width:200px">
  <div role="option" onclick="window.prio = 'Low'">Low</div><div role="option" onclick="window.prio = 'High'">High</div>
</div>
<div class="field"><label>Severity</label>
  <div class="ctl" style="border:1px solid #888;width:300px"><span class="single-value" id="shown"></span><input role="combobox" aria-autocomplete="list" name="severity"></div></div>
<script>portalCombo(document.querySelector('[name=severity]'), document.getElementById('shown'), ['Low', 'High', 'Critical'], 220);</script>
</body></html>"""

# Dialogs that ask for a choice (radio buttons, a checkbox) and offer "Continue": confirming them decides something
RADIO_MODAL_PAGE = """<!doctype html><html><body>
<script>window.cont = 0;</script><h1>Claim CLM-26-04123</h1>
<div role="dialog" aria-modal="true" style="position:fixed;top:20%;left:25%;width:50%;background:#fff;border:1px solid #888">
  <h2>Choose an outcome</h2>
  <label><input type="radio" name="o" value="a" checked> Approve</label> <label><input type="radio" name="o" value="d"> Decline</label>
  <button onclick="window.cont++">Continue</button><button>Cancel</button>
</div></body></html>"""

CHECK_MODAL_PAGE = """<!doctype html><html><body>
<script>window.cont = 0;</script><h1>Claims</h1>
<div role="dialog" aria-modal="true" style="position:fixed;top:20%;left:25%;width:50%;background:#fff;border:1px solid #888">
  <h2>Bulk update</h2><p>Apply to all 40 selected rows.</p>
  <label><input type="checkbox" name="notify"> Notify customers</label>
  <button onclick="window.cont++">Continue</button>
</div></body></html>"""

NOT_AGAIN_PAGE = """<!doctype html><html><body>
<h1>Claims</h1>
<div role="dialog" aria-modal="true" id="tips" style="position:fixed;top:20%;left:25%;width:50%;background:#fff;border:1px solid #888">
  <h2>What's new</h2><p>Claims now load as you scroll.</p>
  <label><input type="checkbox"> Don't show this again</label>
  <button onclick="document.getElementById('tips').remove()">Got it</button>
</div></body></html>"""

# A pop-up whose button does not close it: pressed once, not again and again
STUCK_MODAL_PAGE = """<!doctype html><html><body>
<script>window.presses = 0;</script><h1>Claims</h1>
<div role="dialog" aria-modal="true" style="position:fixed;top:20%;left:25%;width:50%;background:#fff;border:1px solid #888">
  <h2>What's new</h2><p>Claims now load as you scroll.</p><button onclick="window.presses++">Got it</button>
</div></body></html>"""

# An app whose whole page sits in a fixed frame (with its own Close for a detail view), and a list page with a fixed bar
# along the bottom; then a bar along the bottom that is a notice with a Dismiss button
APP_SHELL_PAGE = """<!doctype html><html><body>
<script>window.closed = 0;</script>
<div id="app" style="position:fixed;inset:0;overflow:auto;background:#fff">
  <h1>Claim CLM-26-04123</h1><p>Amount: ₹2,480</p><p>Status: Open</p>
  <button onclick="window.closed++">Close</button> <a href="#list">Back to list</a>
</div></body></html>"""

BOTTOM_BAR_PAGE = """<!doctype html><html><body>
<script>window.cont = 0;</script>
<ul>""" + "".join(f"<li>CLM-{5000 + i}</li>" for i in range(10)) + """</ul>
<div style="position:fixed;left:0;right:0;bottom:0;height:56px;background:#eee">3 rows selected <button onclick="window.cont++">Continue</button></div>
</body></html>"""

BOTTOM_NOTICE_PAGE = """<!doctype html><html><body>
<h1>Claims</h1><p>Content.</p>
<div id="notice" style="position:fixed;left:0;right:0;bottom:0;height:50px;background:#eee">Claims now load as you scroll.
  <button onclick="document.getElementById('notice').remove()">Dismiss</button></div>
</body></html>"""

# A feed of cards (not list items) that grows as you scroll, on a page that always shows a progress bar
PROGRESS_FEED_PAGE = """<!doctype html><html><body>
<div role="progressbar" aria-valuenow="40" aria-valuemin="0" aria-valuemax="100" style="width:200px;height:8px;background:#ccc">Storage 40%</div>
<div id="feed"></div>
<script>
  let n = 0, busy = false;
  const feed = document.getElementById('feed');
  function add() { for (let i = 0; i < 20; i++) { const d = document.createElement('div'); d.className = 'card'; d.style.height = '60px'; d.textContent = 'Claim CLM-' + (6000 + ++n); feed.appendChild(d); } }
  add();
  addEventListener('scroll', () => { if (busy || n >= 60) return; if (scrollY + innerHeight >= document.documentElement.scrollHeight - 200) { busy = true; setTimeout(() => { add(); busy = false; }, 300); } });
</script></body></html>"""

# A "Show more details" text expander above a list that has its own "Load more" button
EXPANDER_PAGE = """<!doctype html><html><body>
<p>Open claims. <button id="expander" onclick="document.getElementById('more-text').style.display = 'block'; this.textContent = 'Show less'">Show more details</button></p>
<p id="more-text" style="display:none">""" + "Claims are listed newest first. " * 60 + """</p>
<ul id="list"></ul><button id="more">Load more</button>
<script>
  let n = 0;
  const list = document.getElementById('list');
  function add() { for (let i = 0; i < 20; i++) { const li = document.createElement('li'); li.textContent = 'CLM-' + (7000 + ++n); list.appendChild(li); } }
  add();
  document.getElementById('more').addEventListener('click', () => setTimeout(add, 200));
</script></body></html>"""

# An amount that happens to be 504 next to a Reload button; then server errors shown by their number
AMOUNT_RELOAD_PAGE = """<!doctype html><html><body><script>window.reloads = 0;</script>
<h1>Claim 7</h1><div class="summary">Amount claimed: 504 <button onclick="window.reloads++">Reload</button></div>
</body></html>"""

CODE_ONLY_PAGE = """<!doctype html><html><body><script>window.tries = 0;</script>
<div class="err"><h2>503</h2><button onclick="window.tries++">Retry</button></div>
</body></html>"""

BAD_GATEWAY_PAGE = """<!doctype html><html><body><script>window.tries = 0;</script>
<div class="err"><p>502 Bad Gateway</p><button onclick="window.tries++">Try again</button></div>
</body></html>"""


async def open_page(context, content):
    page = await context.new_page()
    await page.set_content(content)
    return page


async def by_key(page, key):
    found = [c for c in await webkit.custom_controls(page) if c["key"] == key]
    return found[0] if found else None


async def part2():
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})

        # ---------------- custom controls in a form
        page = await open_page(context, DECIDE_PAGE)
        scraped = await page.locator("#submit").evaluate(webkit.SCRAPE_CUSTOM)
        keys = [c["key"] for c in scraped]
        check("the form's custom controls are found, in page order, keyed by their labels",
              keys == ["aria:Outcome", "aria:Priority", "aria:Fraud suspected", "aria:Notify customer", "aria:Documents received", "combo:Decline reason", "edit:Internal note"])
        got = {c["key"]: c for c in scraped}
        check("every control has the native scraper's fields", all({"key", "tag", "type", "label", "visible", "options"} <= set(c) for c in scraped))
        check("an ARIA radio group is one radio control whose options are its choices' texts",
              got["aria:Outcome"]["type"] == "radio" and got["aria:Outcome"]["options"] == ["Approve", "Approve partially", "Decline", "Request information"] and got["aria:Outcome"]["value"] == "")
        check("a radio group without its own label is named by the legend before it, and shows its checked choice",
              got["aria:Priority"]["label"] == "Priority" and got["aria:Priority"]["value"] == "Normal")
        check("one ARIA checkbox is a yes/no box named by its text; a switch by its aria-label",
              got["aria:Fraud suspected"]["type"] == "checkbox" and got["aria:Fraud suspected"]["options"] == [] and got["aria:Fraud suspected"]["value"] == "no"
              and got["aria:Notify customer"]["value"] == "yes")
        check("ARIA checkboxes sharing a group are one control whose options are their texts",
              got["aria:Documents received"]["type"] == "checkbox" and got["aria:Documents received"]["options"] == ["Photos", "Police report", "Repair invoice"])
        combo = got["combo:Decline reason"]
        check("a searchable combobox is named by its label (its generated id is not used), with no options until opened and no placeholder as its value",
              combo["type"] == "combobox" and combo["options"] == [] and combo["label"] == "Decline reason" and combo["value"] == "" and combo["native_key"] == "rs-input-k3j9x2")
        check("a contenteditable note is a rich-text textarea", got["edit:Internal note"]["tag"] == "textarea" and got["edit:Internal note"]["type"] == "richtext")
        check("hidden controls and native inputs are left out", not any(k in keys for k in ("aria:Hidden choice", "payout", "currency")) and "q" not in keys)
        check("the kinds the plan compares values by fit each control",
              [px._kind_of(got[k]) for k in ("aria:Outcome", "aria:Fraud suspected", "aria:Documents received", "edit:Internal note")] == ["select", "check", "checks", "note"])
        everywhere = [c["key"] for c in await webkit.custom_controls(page)]
        check("over the whole page, the search box of another form is found too (keyed by its name)", everywhere[0] == "q" and set(keys) <= set(everywhere))
        joined = merge_controls(await px._scrape_at(page.locator("#submit")), scraped)
        check("joined with the native scraper's controls, the combobox's own input is not listed twice",
              [c["key"] for c in joined][:2] == ["payout", "currency"] and "rs-input-k3j9x2" not in [c["key"] for c in joined] and len(joined) == 9)

        await webkit.set_custom(page, got["aria:Outcome"], "approve PARTIALLY")
        states = await page.eval_on_selector_all("[aria-labelledby=outcome-l] [role=radio]", "els => els.map((e) => e.getAttribute('aria-checked'))")
        check("an ARIA radio is chosen by its text, ignoring case, and only it is checked", states == ["false", "true", "false", "false"])
        await webkit.set_custom(page, got["aria:Outcome"], "Decline")
        check("choosing another option moves the check", (await by_key(page, "aria:Outcome"))["value"] == "Decline")
        try:
            await webkit.set_custom(page, got["aria:Outcome"], "Escalate")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a value that is not one of the choices is refused with a plain message", "is not one of the choices" in refused and "Approve partially" in refused)

        await webkit.set_custom(page, got["aria:Fraud suspected"], "yes")
        await webkit.set_custom(page, got["aria:Notify customer"], "no")
        check("ARIA boxes are ticked and unticked to match yes/no",
              (await by_key(page, "aria:Fraud suspected"))["value"] == "yes" and (await by_key(page, "aria:Notify customer"))["value"] == "no")
        await webkit.set_custom(page, got["aria:Fraud suspected"], "yes")
        check("a box already in the wanted state is left alone", (await by_key(page, "aria:Fraud suspected"))["value"] == "yes")
        await webkit.set_custom(page, got["aria:Documents received"], "Police report, Photos")
        check("a group of ARIA boxes ticks exactly the labels named", (await by_key(page, "aria:Documents received"))["value"] == "Photos, Police report")
        await webkit.set_custom(page, got["aria:Documents received"], "Repair invoice")
        check("  and unticks the ones no longer named", (await by_key(page, "aria:Documents received"))["value"] == "Repair invoice")

        start = time.monotonic()
        await webkit.set_custom(page, combo, "Peril not covered")
        shown = await page.inner_text(".sel__value")
        check("the combobox is typed into, its portal choice clicked, and it shows the value", shown == "Peril not covered" and (await by_key(page, "combo:Decline reason"))["value"] == "Peril not covered")
        check("  its list of choices is closed after choosing", await page.locator("#reason-menu").count() == 0 and time.monotonic() - start < 8)
        try:
            await webkit.set_custom(page, combo, "duplicate")
            partial = ""
        except ValueError as exc:
            partial = str(exc)
        check("a value that only one choice contains is refused, not taken as that choice", "matches 'duplicate'" in partial
              and await page.inner_text(".sel__value") == "Peril not covered")
        await webkit.set_custom(page, combo, "duplicate CLAIM")
        check("a value that is a choice (case aside) picks it", await page.inner_text(".sel__value") == "Duplicate claim")
        try:
            await webkit.set_custom(page, combo, "Flood damage")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a value no choice matches is refused, and the box keeps its earlier value", "matches 'Flood damage'" in refused and await page.inner_text(".sel__value") == "Duplicate claim")
        check("  what was typed for it is taken out again, and the control reads as the value the box shows (not the typed text)",
              await page.input_value("#rs-input-k3j9x2") == "" and (await by_key(page, "combo:Decline reason"))["value"] == "Duplicate claim")

        await webkit.set_custom(page, got["edit:Internal note"], "Policy lapsed on 30 Sept 2026.")
        check("a rich-text note is filled", (await page.inner_text(".note")).strip() == "Policy lapsed on 30 Sept 2026.")
        await webkit.set_custom(page, got["edit:Internal note"], "Replaced.")
        check("  filling it again replaces what it held", (await page.inner_text(".note")).strip() == "Replaced.")
        check("setting custom controls never pressed the form's Submit button", await page.evaluate("window.clicks.submit") == 0)

        try:
            await webkit.set_custom(page, {"key": "payout", "tag": "input", "type": "number", "label": "Payout"}, "4000")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a native control is refused (the plan's own setter fills those)", "is not a custom control" in refused)
        await page.evaluate("() => { const d = document.createElement('div'); d.id = 'cover'; d.style.cssText = 'position:fixed;inset:0;z-index:99'; document.body.appendChild(d); }")
        webkit._OP_TIMEOUT_MS, saved = 500, webkit._OP_TIMEOUT_MS
        try:
            await webkit.set_custom(page, got["aria:Fraud suspected"], "no")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        finally:
            webkit._OP_TIMEOUT_MS = saved
        check("a box that cannot be clicked (covered) gives a ValueError with a plain message, not a browser error",
              refused.startswith("could not set 'Fraud suspected' to 'no'") and (await by_key(page, "aria:Fraud suspected"))["value"] == "yes")
        await page.close()

        # ---------------- a form inside an iframe
        page = await open_page(context, FRAME_PAGE)
        await page.wait_for_function("document.querySelector('#decision').contentDocument && document.querySelector('#decision').contentDocument.querySelector('[role=radiogroup]')")
        fs = webkit.frames(page)
        check("the page's frames are listed main first, then its iframes", len(fs) == 3 and fs[0] is page.main_frame and all(f.parent_frame is page.main_frame for f in fs[1:]))
        text = await webkit.read_frames_text(page)
        check("the shown iframe's text is read after a [frame] line; a hidden iframe is not", text.startswith("\n\n[frame]\n") and "Document needed" in text and "Outcome" in text and "tracking pixel" not in text)
        check("the frames' text is capped", len(await webkit.read_frames_text(page, limit=40)) <= 40)
        inside = {c["key"]: c for c in await webkit.custom_controls(page)}
        check("the custom controls inside the iframe are found, and its random ids are not used as keys",
              set(inside) == {"aria:Outcome", "combo:Document needed", "note"} and inside["combo:Document needed"]["type"] == "combobox")
        frame = fs[1] if await fs[1].query_selector("[role=radiogroup]") else fs[2]
        await webkit.set_custom(page, inside["aria:Outcome"], "Request information")
        check("an ARIA radio inside the iframe is chosen", await frame.eval_on_selector("[role=radio][aria-checked=true]", "e => e.textContent") == "Request information")
        await webkit.set_custom(page, inside["combo:Document needed"], "police report")
        check("a combobox inside the iframe (a div with an input) is chosen through its portal list", (await frame.inner_text(".shown")) == "Police report")
        await webkit.set_custom(page, inside["note"], "Ask for the FIR copy.")
        check("a rich-text note inside the iframe is filled", (await frame.inner_text("#note")).strip() == "Ask for the FIR copy.")
        check("nothing in the iframe was submitted", await frame.evaluate("window.submitted === undefined"))
        try:
            await webkit.set_custom(page, {"key": "aria:Missing", "type": "radio", "label": "Missing"}, "Yes")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a control that is in no frame is refused with a plain message", "not on the page" in refused)
        await page.close()

        # ---------------- the same label twice, labels that change with the value, and lists that are not the combobox's
        page = await open_page(context, NAMESAKE_PAGE)
        in_form = await webkit.scrape_custom(page.locator("#save"))
        check("a form's control is keyed within its form and remembers where it was read",
              [(c["key"], c["value"]) for c in in_form] == [("aria:Status", "Open")] and in_form[0]["scope"]["root"] == "form")
        status = "g => [...g.querySelectorAll('[role=radio]')].find((r) => r.getAttribute('aria-checked') === 'true').textContent"
        await webkit.set_custom(page, in_form[0], "Closed")
        check("  setting it changes the form's own control, not the one of the same name outside the form",
              await page.eval_on_selector("#form-status", status) == "Closed" and await page.eval_on_selector("#filter", status) == "Open")
        whole = {c["key"]: c for c in await webkit.custom_controls(page)}
        await webkit.set_custom(page, whole["aria:Status #2"], "Open")
        check("  read over the whole page, the second 'Status' is numbered, and its key reaches that same form control",
              set(whole) == {"aria:Status", "aria:Status #2"} and await page.eval_on_selector("#form-status", status) == "Open"
              and await page.eval_on_selector("#filter", status) == "Open")
        await page.close()

        page = await open_page(context, TWIN_FRAMES_PAGE)
        await page.wait_for_function("[...document.querySelectorAll('iframe')].every((f) => f.contentDocument && f.contentDocument.querySelector('[role=radiogroup]'))")
        try:
            await webkit.set_custom(page, {"key": "aria:Outcome", "type": "radio", "label": "Outcome"}, "Approve")
            refused = ""
        except ValueError as exc:
            refused = str(exc)
        check("a key found in two frames, with nothing telling which is meant, is refused (and neither is set)",
              "more than one frame" in refused and all([await f.evaluate("!document.querySelector('[aria-checked=true]')") for f in webkit.frames(page)[1:]]))
        await page.close()

        page = await open_page(context, STATE_TEXT_PAGE)
        boxes = await webkit.custom_controls(page)
        check("a switch showing On/Off and a box showing a tick are named by the text before them, not by their state",
              [c["key"] for c in boxes] == ["aria:Email alerts", "aria:Fraud suspected"])
        for c in boxes:
            await webkit.set_custom(page, c, "yes")
        check("  they are ticked, and their keys stay the same once their text changes",
              [(c["key"], c["value"]) for c in await webkit.custom_controls(page)] == [("aria:Email alerts", "yes"), ("aria:Fraud suspected", "yes")])
        await page.close()

        page = await open_page(context, HASHED_SELECT_PAGE)
        combo = (await webkit.custom_controls(page))[0]
        check("a combobox whose placeholder sits in a plainly named box is named by its field's label, not by 'Select...'",
              combo["key"] == "combo:Decline reason" and combo["label"] == "Decline reason" and combo["value"] == "")
        await webkit.set_custom(page, combo, "Peril not covered")
        after = (await webkit.custom_controls(page))[0]
        check("  it is set, and its key stays the same once it shows the choice", (after["key"], after["value"]) == ("combo:Decline reason", "Peril not covered"))
        await page.close()

        page = await open_page(context, SELECT2_PAGE)
        combo = (await webkit.custom_controls(page))[0]
        check("a select2-style box, labelled by its own value's span, is named by the field's label and its placeholder is no value",
              combo["key"] == "combo:Decline reason" and combo["value"] == "")
        await webkit.set_custom(page, combo, "Peril not covered")
        after = (await webkit.custom_controls(page))[0]
        check("  it is set through the list it opens, and keeps its key",
              await page.eval_on_selector("#reason", "e => e.value") == "Peril not covered" and (after["key"], after["value"]) == ("combo:Decline reason", "Peril not covered"))
        await page.close()

        page = await open_page(context, LISTBOX_AND_COMBO_PAGE)
        combo = [c for c in await webkit.custom_controls(page) if c["type"] == "combobox"][0]
        await webkit.set_custom(page, combo, "High")
        check("a combobox's choice is taken from the list it opened, never from another list always shown on the page",
              await page.inner_text("#shown") == "High" and await page.evaluate("window.prio") == "")
        await page.close()

        # ---------------- lists that show more
        page = await open_page(context, INFINITE_PAGE)
        grew = await webkit.load_more(page)
        count = await page.locator("li").count()
        check("an infinite list grows when scrolled to the bottom", grew and count == 40)
        grew2 = await webkit.load_more(page)
        check("  and again", grew2 and await page.locator("li").count() == 60)
        check("  a list with nothing more to show says so", not await webkit.load_more(page, wait=1.0))
        await page.close()

        page = await open_page(context, INNER_SCROLL_PAGE)
        grew = await webkit.load_more(page)
        check("a list in a scrolling box grows when that box is scrolled to its bottom", grew and await page.locator("tbody tr").count() == 40)
        await page.close()

        page = await open_page(context, MORE_BUTTON_PAGE)
        grew = await webkit.load_more(page)
        check("a 'Load more' button is pressed and the list grows", grew and await page.locator("li").count() == 40)
        check("  once the button is gone and nothing grows, it says so", not await webkit.load_more(page, wait=1.0))
        await page.close()

        page = await open_page(context, PAGER_PAGE)
        grew = await webkit.load_more(page)
        check("a pager's Next is pressed (not a page number) and the new rows count as more", grew and await page.inner_text("#pg") == "Page 2")
        await page.close()

        page = await open_page(context, WIZARD_PAGE)
        grew = await webkit.load_more(page, wait=1.0)
        check("a form's Next button (a wizard step) is never pressed to show more", not grew and await page.evaluate("window.nexts === undefined"))
        await page.close()

        page = await open_page(context, PROGRESS_FEED_PAGE)
        grew = await webkit.load_more(page)
        check("a feed of cards grows even though a progress bar was on the page all along (only new loading signs count)",
              grew and await page.locator(".card").count() == 40)
        await page.close()

        page = await open_page(context, EXPANDER_PAGE)
        grew = await webkit.load_more(page)
        check("a 'Show more details' text expander is passed over for the list's own 'Load more'",
              grew and await page.locator("li").count() == 40 and await page.inner_text("#expander") == "Show more details")
        await page.close()

        # ---------------- interruptions
        page = await open_page(context, BANNERS_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("the 'What's new' pop-up on top is closed first, then the cookie banner under it", closed == ["Got it", "Reject non-essential"])
        check("  on a cookie banner, only the necessary cookies are accepted", await page.evaluate("window.clicks.cookies") == "necessary")
        check("  both are gone", await page.locator("#backdrop").count() == 0 and await page.locator("#cookie-bar").count() == 0)
        check("  a 'Close' button in the page itself (not in an overlay) is never pressed", await page.evaluate("window.clicks.page_close") == 0)
        check("  a page with nothing in front of it gets no clicks", await webkit.dismiss_interruptions(page) == [] and await page.evaluate("window.clicks.page_close") == 0)
        await page.close()

        page = await open_page(context, ACCEPT_ONLY_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a cookie notice printed in the page, offering only 'Accept', is accepted (not its policy link or its settings)", closed == ["Accept"] and await page.evaluate("window.cookies") == "all")
        await page.close()

        page = await open_page(context, APP_PROMO_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a fixed pop-up covering much of the window, with no role, is closed with its × button", closed == ["×"] and await page.locator("#promo").count() == 0)
        await page.close()

        page = await open_page(context, DECISION_MODAL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a modal that submits a decision is left alone: neither its Submit nor its Close is pressed",
              closed == [] and await page.evaluate("window.clicks.submit + window.clicks.close") == 0)
        await page.close()

        page = await open_page(context, FORM_MODAL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a modal asking for something to be filled in is left alone", closed == [] and await page.evaluate("window.clicks.close") == 0)
        await page.close()

        page = await open_page(context, RADIO_MODAL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a dialog asking for a choice with radio buttons is left alone (its Continue would confirm the choice)", closed == [] and await page.evaluate("window.cont") == 0)
        await page.close()
        page = await open_page(context, CHECK_MODAL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("  and so is one with a checkbox to decide on", closed == [] and await page.evaluate("window.cont") == 0)
        await page.close()
        page = await open_page(context, NOT_AGAIN_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a 'What's new' pop-up whose only box is 'Don't show this again' is still closed", closed == ["Got it"] and await page.locator("#tips").count() == 0)
        await page.close()
        page = await open_page(context, STUCK_MODAL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a pop-up that stays after its button is pressed is pressed once, not again and again", closed == ["Got it"] and await page.evaluate("window.presses") == 1)
        await page.close()

        page = await open_page(context, """<h1>Claim C-1</h1><p>Status: Decline</p>
          <div role=dialog aria-modal=true style="position:fixed;inset:0;background:#fff">
            <p>Decline claim C-1 and email the customer now?</p>
            <button onclick="window.cancel=(window.cancel||0)+1">Cancel</button><button onclick="window.ok=(window.ok||0)+1">OK</button></div>""")
        closed = await webkit.dismiss_interruptions(page)
        check("a confirmation (Cancel next to OK) is never answered: the page asks before it acts, and its OK may save",
              closed == [] and await page.evaluate("(window.ok||0) + (window.cancel||0)") == 0)
        check("  it is seen as a dialog that holds nothing to fill in", await webkit.dialog_shown(page))
        await page.close()
        page = await open_page(context, FORM_MODAL_PAGE)
        check("  a dialog that holds a form to fill in is not", not await webkit.dialog_shown(page))
        await page.close()

        page = await open_page(context, APP_SHELL_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a fixed frame that holds the page itself (its main heading) is not an overlay: its Close is never pressed", closed == [] and await page.evaluate("window.closed") == 0)
        await page.close()
        page = await open_page(context, BOTTOM_BAR_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("a bar along the bottom of a list page: its Continue is never pressed", closed == [] and await page.evaluate("window.cont") == 0)
        await page.close()
        page = await open_page(context, BOTTOM_NOTICE_PAGE)
        closed = await webkit.dismiss_interruptions(page)
        check("  but a notice along the bottom is closed with its Dismiss button", closed == ["Dismiss"] and await page.locator("#notice").count() == 0)
        await page.close()

        # ---------------- error boxes
        page = await open_page(context, ERROR_PAGE)
        retried = await webkit.retry_if_error(page)
        check("an error box's Retry button is pressed once and the content loads", retried and await page.evaluate("window.tries") == 1 and "CLM-26-04123" in await page.inner_text("body"))
        check("  with the error gone, nothing more is pressed", not await webkit.retry_if_error(page) and await page.evaluate("window.tries") == 1)
        await page.close()

        page = await open_page(context, TOOLBAR_PAGE)
        check("a Reload button that is not next to an error message is not pressed", not await webkit.retry_if_error(page) and await page.evaluate("window.reloads") == 0)
        await page.close()
        page = await open_page(context, AMOUNT_RELOAD_PAGE)
        check("  nor one next to an amount that happens to be 504", not await webkit.retry_if_error(page) and await page.evaluate("window.reloads") == 0)
        await page.close()
        pressed = []
        for content in (CODE_ONLY_PAGE, BAD_GATEWAY_PAGE):
            page = await open_page(context, content)
            pressed.append(await webkit.retry_if_error(page) and await page.evaluate("window.tries") == 1)
            await page.close()
        check("a server error shown by its number alone ('503') or beside words that say so ('502 Bad Gateway') is retried", pressed == [True, True])

        # ---------------- logins
        page = await open_page(context, LOGIN_PAGE)
        check("a login form is recognised", await webkit.login_form_shown(page))
        await page.close()
        page = await open_page(context, HIDDEN_PASSWORD_PAGE)
        check("a hidden password box with a 'Sign in' link is not a login page", not await webkit.login_form_shown(page))
        await page.close()
        page = await open_page(context, f"<iframe srcdoc=\"{html.escape(LOGIN_PAGE, quote=True)}\" style='width:600px;height:300px'></iframe>")
        await page.wait_for_function("document.querySelector('iframe').contentDocument && document.querySelector('iframe').contentDocument.querySelector('input[type=password]')")
        check("a login form inside an iframe is recognised", await webkit.login_form_shown(page))
        await page.close()

        await browser.close()


asyncio.run(part2())
print(f"\n{checks} checks passed")
