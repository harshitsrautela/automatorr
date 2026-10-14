// Expense Desk: the whole app. A tiny hash router draws each page from the JSON API.
(function () {
  const view = document.getElementById("view");
  const modalRoot = document.getElementById("modal-root");
  const toast = document.getElementById("toast");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => Number(n).toLocaleString("en-IN");
  const nice = (iso) => { const [y, m, d] = iso.split("-"); return `${d} ${MONTHS[Number(m) - 1]} ${y}`; };
  // Controls get a fresh random id each time they are drawn, the way many UI libraries do it; only their names are stable
  const rid = () => "f" + Math.floor(10000 + Math.random() * 90000);
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || r.statusText);
    return body;
  };
  let renderToken = 0;
  let toastTimer = null;

  function route() {
    const hash = location.hash.replace(/^#/, "") || "/";
    for (const a of document.querySelectorAll("nav a")) a.classList.toggle("active", hash === a.dataset.nav || (a.dataset.nav !== "/" && hash.startsWith(a.dataset.nav)));
    const token = ++renderToken;
    modalRoot.innerHTML = "";
    view.innerHTML = `<p class="muted">Loading…</p>`;
    let m;
    if (hash === "/") return dashboard(token);
    if (hash === "/claims") return claims(token);
    if (hash === "/policy") return policy(token);
    if ((m = hash.match(/^\/claims\/([a-z0-9]{6})$/))) return claim(token, m[1]);
    view.innerHTML = `<h1>Page not found</h1><p><a href="#/claims">Go to claims</a></p>`;
  }
  window.addEventListener("hashchange", route);

  async function dashboard(token) {
    const s = await api("/api/stats");
    if (token !== renderToken) return;
    view.innerHTML = `<h1>Dashboard</h1><p class="muted">Travel expense claims waiting for an audit decision.</p>
      <div class="cards">${Object.entries(s).map(([k, v]) => `<div class="card"><div class="n">${v}</div><div>${esc(k)}</div></div>`).join("")}</div>
      <p><a href="#/claims">Go to claims</a></p>`;
  }

  // ------------------------------------------------------------------ the claims list: type to filter, rows are clickable
  async function claims(token) {
    view.innerHTML = `<h1>Claims</h1>
      <input type="search" class="search" id="${rid()}" name="q" placeholder="Search by claim number or employee" autocomplete="off">
      <div id="results"><p class="muted">Loading…</p></div>`;
    const box = view.querySelector("input[name=q]");
    const results = view.querySelector("#results");
    let timer = null;
    let asked = 0;
    async function load() {
      const mine = ++asked;
      results.innerHTML = `<p class="muted">Loading…</p>`;
      const data = await api("/api/claims?q=" + encodeURIComponent(box.value));
      if (mine !== asked || token !== renderToken) return;
      if (!data.claims.length) { results.innerHTML = `<p class="muted">No claims match “${esc(box.value)}”.</p>`; return; }
      results.innerHTML = `<table><thead><tr><th>Claim</th><th>Employee</th><th>Destination</th><th>Submitted</th><th class="num">Total (₹)</th><th>Status</th></tr></thead><tbody>
        ${data.claims.map((c) => `<tr class="row" tabindex="0" data-r="${Math.random().toString(36).slice(2, 8)}" data-key="${c.key}"><td>${esc(c.claim_no)}</td><td>${esc(c.employee)}</td><td>${esc(c.destination)}</td><td>${nice(c.submitted)}</td><td class="num">${money(c.total_inr)}</td><td><span class="badge">${esc(c.status)}</span></td></tr>`).join("")}
        </tbody></table><p class="muted">Showing ${data.shown} of ${data.total} claim${data.total === 1 ? "" : "s"}.${data.shown < data.total ? " Type in the search box to find others." : ""}</p>`;
      for (const tr of results.querySelectorAll("tr.row")) {
        const open = () => { location.hash = "#/claims/" + tr.dataset.key; };
        tr.addEventListener("click", open);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      }
    }
    box.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 350); });
    load();
  }

  // ------------------------------------------------------------------ one claim: tabs that do not change the address
  async function claim(token, key) {
    let c;
    try { c = await api("/api/claims/" + key); } catch { if (token === renderToken) view.innerHTML = `<h1>Claim not found</h1>`; return; }
    if (token !== renderToken) return;
    const decided = c.decision ? `<div class="decided"><b>${esc(c.status)}</b>${c.decision.payable != null ? ` · reimburse ₹${money(c.decision.payable)}` : ""}${c.decision.deduction ? ` · deduction ₹${money(c.decision.deduction)}` : ""}${c.decision.missing ? ` · missing: ${esc(c.decision.missing.join(", "))}` : ""}${c.decision.reason ? ` · ${esc(c.decision.reason)}` : ""}</div>` : "";
    view.innerHTML = `<p class="crumb"><a href="#/claims">Claims</a> / ${esc(c.claim_no)}</p>
      <div class="head"><h1>Claim ${esc(c.claim_no)}</h1><span class="badge">${esc(c.status)}</span><span class="spacer"></span>
      <button type="button" class="primary" id="review">Review claim</button></div>${decided}
      <div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-tab="summary">Summary</button><button role="tab" aria-selected="false" data-tab="receipts">Receipts</button><button role="tab" aria-selected="false" data-tab="activity">Activity</button></div>
      <div id="panel" role="tabpanel"></div>`;
    const panel = view.querySelector("#panel");
    const tabs = {
      summary: () => `<dl class="facts">
        <dt>Employee</dt><dd>${esc(c.employee)}</dd><dt>Grade</dt><dd>${esc(c.grade)}</dd><dt>Purpose</dt><dd>${esc(c.purpose)}</dd>
        <dt>Destination</dt><dd>${esc(c.destination)}</dd><dt>Trip</dt><dd>${nice(c.trip_start)} – ${nice(c.trip_end)}</dd><dt>Nights</dt><dd>${c.nights}</dd>
        <dt>Submitted</dt><dd>${nice(c.submitted)}</dd><dt>Days from trip end to submission</dt><dd>${c.days_to_submit}</dd>
        <dt>Advance paid</dt><dd>₹${money(c.advance)}</dd><dt>Total claimed (in rupees)</dt><dd>₹${money(c.total_inr)}</dd></dl>`,
      receipts: () => `<table><thead><tr><th>Date</th><th>Category</th><th>Description</th><th class="num">Amount</th><th>Currency</th><th>Receipt</th></tr></thead><tbody>
        ${c.lines.map((l) => `<tr><td>${nice(l.date)}</td><td>${esc(l.category)}</td><td>${esc(l.description)}</td><td class="num">${money(l.amount)}</td><td>${esc(l.currency)}</td><td>${esc(l.receipt)}</td></tr>`).join("")}</tbody></table>
        <p class="muted">Amounts are in the currency of the receipt.</p>`,
      activity: () => `<ul>${c.activity.map((a) => `<li>${nice(a.at)}: ${esc(a.text)}</li>`).join("")}</ul>`,
    };
    function show(name) {
      for (const b of view.querySelectorAll("[role=tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
      panel.innerHTML = tabs[name]();
    }
    for (const b of view.querySelectorAll("[role=tab]")) b.addEventListener("click", () => show(b.dataset.tab));
    show("summary");
    view.querySelector("#review").addEventListener("click", () => wizard(c));
  }

  // ------------------------------------------------------------------ the decision: a three-step wizard in a modal
  function wizard(c) {
    const st = { step: 1, outcome: "", payable: "", deduction: "", hotel_over_limit: false, missing: [], reason: "", message: "", attest: false, problem: "" };
    const OUT = [["approve", "Approve"], ["deduct", "Approve with deduction"], ["sendback", "Send back to employee"], ["reject", "Reject"]];
    const CATS = ["Hotel", "Meals", "Taxi", "Flight", "Train"];
    const REASONS = ["Late submission", "Duplicate claim", "Personal expense"];
    const num = (label, name) => { const id = rid(); return `<div class="field"><label for="${id}">${label}</label><input type="number" id="${id}" name="${name}" min="0" step="1" value="${esc(st[name])}"></div>`; };

    function body() {
      if (st.step === 1) {
        return `<fieldset><legend>Outcome</legend>${OUT.map(([v, t]) => { const id = rid(); return `<label class="choice" for="${id}"><input type="radio" id="${id}" name="outcome" value="${v}"${st.outcome === v ? " checked" : ""}> ${t}</label>`; }).join("")}</fieldset>`;
      }
      if (st.step === 2) {
        if (st.outcome === "approve") return num("Amount to reimburse (₹)", "payable");
        if (st.outcome === "deduct") {
          const id = rid();
          return num("Deduction (₹)", "deduction") + num("Amount to reimburse (₹)", "payable") +
            `<label class="choice" for="${id}"><input type="checkbox" id="${id}" name="hotel_over_limit"${st.hotel_over_limit ? " checked" : ""}> Hotel above policy limit</label>`;
        }
        if (st.outcome === "sendback") {
          const mid = rid();
          return `<fieldset><legend>Receipts missing for</legend>${CATS.map((cat) => { const id = rid(); return `<label class="choice" for="${id}"><input type="checkbox" id="${id}" name="missing" value="${cat}"${st.missing.includes(cat) ? " checked" : ""}> ${cat}</label>`; }).join("")}</fieldset>
            <div class="field"><label for="${mid}">Message to employee (optional)</label><textarea id="${mid}" name="message" rows="2">${esc(st.message)}</textarea></div>`;
        }
        const id = rid();
        return `<div class="field"><label for="${id}">Reason</label><select id="${id}" name="reason"><option value="">Choose…</option>${REASONS.map((r) => `<option${st.reason === r ? " selected" : ""}>${r}</option>`).join("")}</select></div>`;
      }
      const label = OUT.find(([v]) => v === st.outcome)[1];
      const bits = [label];
      if (st.payable !== "") bits.push(`reimburse ₹${money(st.payable)}`);
      if (st.outcome === "deduct") bits.push(`deduction ₹${money(st.deduction)}`);
      if (st.outcome === "sendback") bits.push("missing: " + st.missing.join(", "));
      if (st.outcome === "reject") bits.push(st.reason);
      const id = rid();
      return `<p><b>${esc(c.claim_no)}</b>: ${esc(bits.join(" · "))}</p>
        <label class="choice" for="${id}"><input type="checkbox" id="${id}" name="attest"${st.attest ? " checked" : ""}> I have checked this claim against the travel policy</label>`;
    }

    function collect() {
      const dlg = modalRoot.querySelector(".dialog");
      const val = (n) => dlg.querySelector(`[name="${n}"]`);
      if (st.step === 1) { const r = dlg.querySelector("input[name=outcome]:checked"); st.outcome = r ? r.value : ""; }
      if (st.step === 2) {
        for (const n of ["payable", "deduction", "reason", "message"]) if (val(n)) st[n] = val(n).value;
        if (val("hotel_over_limit")) st.hotel_over_limit = val("hotel_over_limit").checked;
        if (st.outcome === "sendback") st.missing = [...dlg.querySelectorAll("input[name=missing]:checked")].map((x) => x.value);
      }
      if (st.step === 3) st.attest = val("attest").checked;
    }

    function stepProblem() {
      const whole = (v) => /^\d+$/.test(String(v).trim());
      if (st.step === 1 && !st.outcome) return "Choose an outcome.";
      if (st.step === 2) {
        if (st.outcome === "approve" && !whole(st.payable)) return "Enter the amount to reimburse in whole rupees.";
        if (st.outcome === "deduct" && !(whole(st.payable) && whole(st.deduction))) return "Enter the deduction and the amount to reimburse in whole rupees.";
        if (st.outcome === "sendback" && !st.missing.length) return "Tick at least one missing receipt.";
        if (st.outcome === "reject" && !st.reason) return "Choose a reason.";
      }
      return "";
    }

    function draw() {
      const titles = ["Outcome", "Details", "Confirm"];
      modalRoot.innerHTML = `<div class="overlay"><div class="dialog" role="dialog" aria-modal="true" aria-label="Review ${esc(c.claim_no)}">
        <h3>Review ${esc(c.claim_no)}</h3><div class="steps">Step ${st.step} of 3 · ${titles[st.step - 1]}</div>
        ${st.problem ? `<div class="problem">${esc(st.problem)}</div>` : ""}${body()}
        <div class="row-buttons">${st.step === 1 ? `<button type="button" data-act="cancel">Cancel</button>` : `<button type="button" data-act="back">Back</button>`}
        ${st.step < 3 ? `<button type="button" class="primary" data-act="next">Next</button>` : `<button type="button" class="primary" data-act="submit"${st.attest ? "" : " disabled"}>Submit decision</button>`}</div>
      </div></div>`;
      const dlg = modalRoot.querySelector(".dialog");
      const attest = dlg.querySelector("input[name=attest]");
      if (attest) attest.addEventListener("change", () => { st.attest = attest.checked; dlg.querySelector("[data-act=submit]").disabled = !attest.checked; });
      dlg.querySelector("[data-act=cancel]")?.addEventListener("click", () => { modalRoot.innerHTML = ""; });
      dlg.querySelector("[data-act=back]")?.addEventListener("click", () => { collect(); st.problem = ""; st.step--; draw(); });
      dlg.querySelector("[data-act=next]")?.addEventListener("click", () => {
        collect();
        st.problem = stepProblem();
        if (!st.problem) st.step++;
        draw();
      });
      dlg.querySelector("[data-act=submit]")?.addEventListener("click", async () => {
        collect();
        const payload = { outcome: st.outcome, attest: st.attest, message: st.message };
        if (st.outcome === "approve" || st.outcome === "deduct") payload.payable = Number(st.payable);
        if (st.outcome === "deduct") { payload.deduction = Number(st.deduction); payload.hotel_over_limit = st.hotel_over_limit; }
        if (st.outcome === "sendback") payload.missing = st.missing;
        if (st.outcome === "reject") payload.reason = st.reason;
        try {
          await api(`/api/claims/${c.key}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        } catch (err) { st.problem = err.message; draw(); return; }
        modalRoot.innerHTML = "";
        toast.textContent = `Decision saved for ${c.claim_no}.`;
        toast.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { toast.hidden = true; }, 6000);
        route();
      });
    }
    draw();
  }

  // ------------------------------------------------------------------ the policy: collapsed sections
  async function policy(token) {
    const p = await api("/api/policy");
    if (token !== renderToken) return;
    const grades = ["L1", "L2", "L3", "L4"];
    const limits = Object.entries(p.limits).map(([tier, row]) => `<tr><td>${esc(tier)}</td>${grades.map((g) => `<td class="num">${money(row[g])}</td>`).join("")}</tr>`).join("");
    const tiers = Object.entries(p.tiers).map(([tier, cities]) => `<tr><td>${esc(tier)}</td><td>${cities.map(esc).join(", ")}</td></tr>`).join("");
    const rates = Object.entries(p.rates).filter(([c]) => c !== "INR").map(([c, r]) => `<tr><td>${c}</td><td class="num">${r}</td></tr>`).join("");
    view.innerHTML = `<h1>Travel policy</h1><p class="muted">Open a section to read it.</p>
      <details><summary>1. Who this policy covers</summary><p>All employees travelling on company business, in India and abroad. Contractors follow the terms of their contract. Personal travel added to a business trip is paid by the employee.</p></details>
      <details><summary>2. Submission window</summary><p>A claim must be submitted within ${p.window_days} days of the last day of the trip. The ${p.window_days}th day is still inside the window. Claims submitted later are rejected as a late submission.</p></details>
      <details><summary>3. Receipts</summary><p>Every line of a claim needs a receipt. A claim with a missing receipt is sent back to the employee, naming every category whose receipt is missing.</p></details>
      <details><summary>4. Hotel limits per night</summary><p>The most the company pays for a hotel, per night, in rupees, by the city's tier and the employee's grade. The limit for a stay is the nightly limit times the number of nights. Anything above it is deducted from the claim.</p>
        <table><thead><tr><th>City tier</th>${grades.map((g) => `<th class="num">${g}</th>`).join("")}</tr></thead><tbody>${limits}</tbody></table></details>
      <details><summary>5. City tiers</summary><table><thead><tr><th>Tier</th><th>Cities</th></tr></thead><tbody>${tiers}</tbody></table></details>
      <details><summary>6. Exchange rates</summary><p>Amounts in other currencies are converted to rupees at these rates.</p><table><thead><tr><th>Currency</th><th class="num">Rupees per unit</th></tr></thead><tbody>${rates}</tbody></table></details>
      <details><summary>7. Meals and local travel</summary><p>Meals and local travel are paid as claimed, with receipts. Alcohol is never paid.</p></details>
      <details><summary>8. Advances</summary><p>An advance paid before the trip is subtracted from the amount to reimburse.</p></details>`;
  }

  route();
})();
