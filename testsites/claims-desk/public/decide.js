// Claims Desk: the decision form, drawn inside the claim page's same-origin iframe (/claims/<key>/decide). It is built from ARIA
// widgets rather than native controls, the way many web apps are:
//  - the outcome is a radio group of <div role=radio>s
//  - the decline reason and the document needed are searchable comboboxes: the options are drawn in a portal at the end of the
//    body a moment after you type (or click), and typing alone chooses nothing; an option has to be picked
//  - the internal note is a contenteditable box
// Every control gets a new random id each time it is drawn; native inputs keep a stable name. Saving is a fetch; the claim page
// around the frame is told by postMessage, shows a toast and redraws its decision line.
(function () {
  const root = document.getElementById("decide");
  const portal = document.getElementById("portal");
  const key = root.dataset.key;
  const claimNo = root.dataset.claim;
  const OUTCOMES = [["approve", "Approve"], ["partial", "Approve partially"], ["decline", "Decline"], ["request_info", "Request information"]];
  const COMBOS = {
    reason: { label: "Decline reason", options: JSON.parse(root.dataset.reasons || "[]") },
    document: { label: "Document needed", options: JSON.parse(root.dataset.documents || "[]") },
  };
  const PAYS = new Set(["approve", "partial"]);
  const st = { outcome: "", payout: "", reason: "", document: "", saving: false };
  let current = null; // the claim's saved decision, as the API returns it
  let savedLine = ""; // "Decision saved ..." after a save from this frame
  let combo = null; // the open combobox: {name, input, items, active, timer}

  // Takes any value; returns it as text that is safe inside HTML.
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Takes whole rupees; returns them in Indian grouping ("1,25,000").
  const money = (n) => Number(n).toLocaleString("en-IN");
  // Controls get a fresh random id every time they are drawn, as many UI libraries do
  const rid = () => "c" + Math.random().toString(36).slice(2, 9);

  // Sends the whole window (not just this frame) to sign in, coming back to the claim page.
  function toLogin() {
    const top = window.top.location;
    window.top.location.href = "/login?expired=1&next=" + encodeURIComponent(top.pathname + top.search);
  }

  // Takes an /api address and fetch options; returns the parsed answer, or throws with the site's message (signs in again on 401).
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (r.status === 401) { toLogin(); throw new Error("Your session has expired."); }
    let data = {};
    try { data = await r.json(); } catch { data = {}; }
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  // Tells the claim page how tall the form is, counting an open option list, so the frame can grow to show all of it.
  function postHeight() {
    const box = portal.firstElementChild;
    const bottom = Math.max(root.getBoundingClientRect().bottom, box ? box.getBoundingClientRect().bottom : 0);
    if (window.parent !== window) window.parent.postMessage({ type: "decide-height", height: Math.ceil(bottom + window.scrollY) + 16 }, location.origin);
  }

  // Takes a decision as the API returns it; returns it as one line of text.
  function decisionLine(d) {
    const bits = [d.outcome];
    if (d.payout != null) bits.push(`payout ₹${money(d.payout)}`);
    if (d.reason) bits.push(d.reason);
    if (d.document) bits.push(`asked for: ${d.document}`);
    return bits.join(" · ");
  }

  // Takes a message (or ""); shows it above the submit button, or hides the box.
  function showProblem(text) {
    const box = root.querySelector(".problem");
    if (!box) return;
    box.textContent = text;
    box.hidden = !text;
    postHeight();
  }

  // ------------------------------------------------------------------ the searchable combobox

  // Takes a combobox name; returns the field's markup (a label, and a div holding the input that has role=combobox).
  function comboField(name) {
    const id = rid();
    return `<div class="field"><label id="${rid()}" for="${id}">${COMBOS[name].label}</label>
      <div class="combo"><input type="text" id="${id}" name="${name}" role="combobox" aria-autocomplete="list" aria-haspopup="listbox"
        aria-expanded="false" autocomplete="off" spellcheck="false" placeholder="Type to search…" value="${esc(st[name])}">
        <span class="chev" aria-hidden="true">▾</span></div></div>`;
  }

  // Closes the open option list, if any.
  function closeCombo() {
    if (!combo) return;
    clearTimeout(combo.timer);
    for (const a of ["aria-activedescendant", "aria-controls"]) combo.input.removeAttribute(a);
    combo.input.setAttribute("aria-expanded", "false");
    combo = null;
    portal.innerHTML = "";
    postHeight();
  }

  // Puts the open option list right under its input (the portal is outside the form, so it has to be placed by hand).
  function placeList() {
    const box = portal.firstElementChild;
    if (!combo || !box) return;
    const r = combo.input.getBoundingClientRect();
    Object.assign(box.style, { left: `${r.left + window.scrollX}px`, top: `${r.bottom + window.scrollY + 2}px`, width: `${r.width}px` });
  }

  // Draws the open combobox's option list in the portal ("Searching…" while the search runs).
  function drawList() {
    const { input, items, active, searching } = combo;
    const listId = rid();
    const rows = searching ? `<div class="empty">Searching…</div>`
      : items.length ? items.map((t, i) => `<div class="option" role="option" id="${rid()}" data-i="${i}" aria-selected="${i === active}">${esc(t)}</div>`).join("")
      : `<div class="empty">No options</div>`;
    portal.innerHTML = `<div class="listbox" role="listbox" id="${listId}">${rows}</div>`;
    placeList();
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", listId);
    const on = portal.querySelector(`[data-i="${active}"]`);
    if (on) input.setAttribute("aria-activedescendant", on.id); else input.removeAttribute("aria-activedescendant");
    for (const el of portal.querySelectorAll("[role=option]")) {
      el.addEventListener("mousedown", (e) => e.preventDefault()); // keep the focus in the input
      el.addEventListener("click", () => choose(combo.items[Number(el.dataset.i)]));
    }
    postHeight();
  }

  // Takes a combobox input and its name; opens its option list and fills it, after a short search delay, with the options that
  // contain what is typed (all of them when the box shows the chosen value or nothing).
  function openCombo(input, name) {
    if (!combo || combo.input !== input) {
      closeCombo();
      combo = { name, input, items: [], active: 0, searching: true, timer: null };
    }
    clearTimeout(combo.timer);
    combo.searching = true;
    drawList();
    const typed = input.value === st[name] ? "" : input.value.trim().toLowerCase();
    combo.timer = setTimeout(() => {
      if (!combo || combo.input !== input) return;
      combo.items = COMBOS[name].options.filter((o) => o.toLowerCase().includes(typed));
      combo.active = 0;
      combo.searching = false;
      drawList();
    }, 150 + Math.floor(Math.random() * 250));
  }

  // Takes an option's text; makes it the open combobox's value and closes the list.
  function choose(text) {
    if (!combo || text === undefined) return;
    const { input, name } = combo;
    st[name] = text;
    input.value = text;
    closeCombo();
    showProblem("");
  }

  // Takes a combobox input and its name; wires up opening, keys, choosing and losing focus.
  function wireCombo(input, name) {
    input.addEventListener("click", () => openCombo(input, name));
    input.addEventListener("input", () => openCombo(input, name));
    input.addEventListener("keydown", (e) => {
      const open = combo && combo.input === input;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!open) return openCombo(input, name);
        if (combo.searching || !combo.items.length) return;
        const step = e.key === "ArrowDown" ? 1 : -1;
        combo.active = (combo.active + step + combo.items.length) % combo.items.length;
        drawList();
      } else if (e.key === "Enter") {
        e.preventDefault(); // never submits the form from inside the combobox
        if (open && !combo.searching && combo.items.length) choose(combo.items[combo.active]);
      } else if (e.key === "Escape" && open) {
        closeCombo();
        input.value = st[name];
      }
    });
    // Leaving the box puts back the chosen value: text typed without picking an option is thrown away
    input.addEventListener("blur", () => setTimeout(() => {
      if (document.activeElement === input) return;
      if (combo && combo.input === input) closeCombo();
      input.value = st[name];
    }, 150));
  }

  // ------------------------------------------------------------------ the form

  // Draws the fields that depend on the outcome (a payout, a decline reason or a document), with new ids each time.
  function drawDetails() {
    closeCombo();
    const box = root.querySelector(".details");
    if (PAYS.has(st.outcome)) {
      const id = rid();
      box.innerHTML = `<div class="field"><label for="${id}">Payout (₹)</label>
        <input type="number" id="${id}" name="payout" min="0" step="1" inputmode="numeric" value="${esc(st.payout)}">
        <p class="hint">Whole rupees.</p></div>`;
      box.querySelector("input").addEventListener("input", (e) => { st.payout = e.target.value; });
    } else if (st.outcome === "decline" || st.outcome === "request_info") {
      const name = st.outcome === "decline" ? "reason" : "document";
      box.innerHTML = comboField(name);
      wireCombo(box.querySelector("input[role=combobox]"), name);
    } else box.innerHTML = "";
    postHeight();
  }

  // Takes an outcome value; checks that radio (and unchecks the others) and draws the fields it needs.
  function pickOutcome(value) {
    for (const el of root.querySelectorAll("[role=radio]")) {
      const on = el.dataset.v === value;
      el.setAttribute("aria-checked", String(on));
      el.tabIndex = on ? 0 : -1;
    }
    if (st.outcome === value) return;
    st.outcome = value;
    showProblem("");
    drawDetails();
  }

  // Returns what is wrong with the form as filled in, or "" (the same checks the server makes).
  function formProblem() {
    if (!st.outcome) return "Choose an outcome.";
    if (PAYS.has(st.outcome) && !/^\d+$/.test(String(st.payout).trim())) return "Enter the payout in whole rupees.";
    if (st.outcome === "decline" && !COMBOS.reason.options.includes(st.reason)) return "Choose a decline reason from the list.";
    if (st.outcome === "request_info" && !COMBOS.document.options.includes(st.document)) return "Choose the document needed from the list.";
    return "";
  }

  // Takes the submit event; checks the form, saves the decision and tells the claim page.
  async function submit(e) {
    e.preventDefault();
    if (st.saving) return;
    closeCombo();
    const problem = formProblem();
    if (problem) return showProblem(problem);
    const payload = { outcome: st.outcome, note: root.querySelector(".note").innerText.trim() };
    if (PAYS.has(st.outcome)) payload.payout = Number(st.payout);
    if (st.outcome === "decline") payload.reason = st.reason;
    if (st.outcome === "request_info") payload.document = st.document;
    const button = root.querySelector("button[type=submit]");
    st.saving = true;
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      await api(`/api/claims/${key}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const fresh = await api("/api/claims/" + key);
      current = fresh.decision;
      savedLine = `Decision saved for ${claimNo}: ${decisionLine(current)}.`;
      Object.assign(st, { outcome: "", payout: "", reason: "", document: "", saving: false });
      draw();
      if (window.parent !== window) window.parent.postMessage({ type: "decision-saved", claim: claimNo, status: fresh.status }, location.origin);
    } catch (err) {
      st.saving = false;
      button.disabled = false;
      button.textContent = "Submit decision";
      showProblem(err.message);
    }
  }

  // Draws the whole form (new ids for every control) with the claim's saved decision above it.
  function draw() {
    const outLabel = rid();
    const noteLabel = rid();
    const radios = OUTCOMES.map(([v, t], i) => `<div role="radio" id="${rid()}" class="radio" data-v="${v}" aria-checked="${st.outcome === v}"
      tabindex="${st.outcome === v || (!st.outcome && i === 0) ? 0 : -1}"><span class="dot" aria-hidden="true"></span>${t}</div>`).join("");
    root.innerHTML = `${savedLine ? `<div class="saved" role="status">${esc(savedLine)}</div>` : ""}
      ${current ? `<p class="current">Current decision: <b>${esc(decisionLine(current))}</b></p>` : ""}
      <form class="decision" novalidate>
        <div class="field"><span class="lbl" id="${outLabel}">Outcome</span>
          <div role="radiogroup" aria-labelledby="${outLabel}" class="radios">${radios}</div></div>
        <div class="details"></div>
        <div class="field"><span class="lbl" id="${noteLabel}">Internal note (optional)</span>
          <div class="note" id="${rid()}" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="${noteLabel}"></div></div>
        <div class="problem" role="alert" hidden></div>
        <div class="row-buttons"><button type="submit" class="primary">Submit decision</button></div>
      </form>`;
    const items = [...root.querySelectorAll("[role=radio]")];
    items.forEach((el, i) => {
      el.addEventListener("click", () => pickOutcome(el.dataset.v));
      el.addEventListener("keydown", (e) => {
        if (e.key === " " || e.key === "Enter") { e.preventDefault(); pickOutcome(el.dataset.v); }
        const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (step) {
          e.preventDefault();
          const next = items[(i + step + items.length) % items.length];
          next.focus();
          pickOutcome(next.dataset.v);
        }
      });
    });
    root.querySelector("form").addEventListener("submit", submit);
    drawDetails();
  }

  // Loads the claim (slow, like every API call here), then draws the form.
  async function start() {
    try {
      current = (await api("/api/claims/" + key)).decision;
    } catch (err) {
      root.innerHTML = `<div class="problem" role="alert">${esc(err.message)}</div>`;
      postHeight();
      return;
    }
    draw();
  }

  window.addEventListener("resize", placeList);
  start();
})();
