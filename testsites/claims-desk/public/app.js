// Claims Desk: the browser side of every page except the decision form (decide.js). It draws the cookie banner and the "What's
// new" box, and fills each page with data that arrives late from /api: the dashboard, the claims list (endless scrolling with a
// "Load more" button as a fallback, and a Retry box when a load fails), a claim (with the decision form in an iframe), a policy.
(function () {
  const body = document.body;
  const kind = body.dataset.page;
  const view = document.getElementById("view");
  const toastBox = document.getElementById("toast");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"];
  let toastTimer = null;

  // Takes any value; returns it as text that is safe inside HTML.
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Takes whole rupees; returns them in Indian grouping ("1,25,000").
  const money = (n) => Number(n).toLocaleString("en-IN");
  // Takes an ISO date; returns it as the site shows it ("05 Sept 2026").
  const nice = (iso) => { const [y, m, d] = iso.split("-"); return `${d} ${MONTHS[Number(m) - 1]} ${y}`; };

  // Sends the whole window (not just a frame) to sign in, coming back to the page it shows now.
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

  // Takes a message; shows it in the corner for a few seconds.
  function toast(text) {
    toastBox.textContent = text;
    toastBox.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastBox.hidden = true; }, 6000);
  }

  // A sign-in page drawn inside the decision frame (the session ran out as the frame loaded) sends the whole window to sign in
  if (kind === "login" && window.top !== window.self) { toLogin(); return; }

  // ------------------------------------------------------------------ interruptions

  // The cookie banner: fixed at the bottom of the window until one of its buttons is pressed (remembered for a year).
  function consentBanner() {
    if (/(^|;\s*)consent=/.test(document.cookie)) return;
    setTimeout(() => {
      const box = document.createElement("div");
      box.className = "consent";
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", "Cookie consent");
      box.innerHTML = `<p><b>Cookies on Claims Desk.</b> We use essential cookies to keep you signed in and, with your permission, analytics
        cookies to see how the desk is used.</p><div class="row-buttons"><button type="button" data-c="essential">Reject non-essential</button>
        <button type="button" class="primary" data-c="all">Accept all</button></div>`;
      for (const b of box.querySelectorAll("button")) {
        b.addEventListener("click", () => { document.cookie = `consent=${b.dataset.c}; Max-Age=31536000; Path=/; SameSite=Lax`; box.remove(); });
      }
      body.appendChild(box);
    }, 300 + Math.random() * 400);
  }

  // The "What's new" box: once per session. It is shown on the session's first page (and on later ones while it is left open)
  // until it is closed with "Got it" or the ×; either one tells the site, and the rest of the session goes without it.
  function whatsNew() {
    const root = document.createElement("div");
    root.className = "overlay";
    root.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="wn-title">
      <button type="button" class="x" aria-label="Close">×</button><h3 id="wn-title">What's new</h3>
      <ul><li>Claims now load as you scroll: there is no need to page through the list.</li>
      <li>The decision form saves without leaving the claim.</li><li>Notes can be formatted with bold and lists.</li></ul>
      <div class="row-buttons"><button type="button" class="primary" data-act="ok">Got it</button></div></div>`;
    // Closes the box and records that this session has seen it.
    const close = () => {
      root.remove();
      api("/api/whats-new", { method: "POST" }).catch(() => {});
    };
    root.querySelector(".x").addEventListener("click", close);
    root.querySelector("[data-act=ok]").addEventListener("click", close);
    body.appendChild(root);
  }

  // ------------------------------------------------------------------ pages

  // The dashboard: one card per status with how many claims have it.
  async function dashboard() {
    const s = await api("/api/stats");
    view.innerHTML = `<h1>Dashboard</h1><p class="muted">Property claims waiting for a decision.</p>
      <div class="cards">${Object.entries(s).map(([k, v]) => `<div class="card"><div class="n">${v}</div><div>${esc(k)}</div></div>`).join("")}</div>
      <p><a href="/claims">Go to claims</a></p>`;
  }

  // The claims list: 20 rows, then 20 more each time the window is scrolled near the bottom (or "Load more" is pressed).
  function claimsList() {
    view.innerHTML = `<h1>Claims</h1><p class="muted">Newest first. Scroll down to see more claims.</p>
      <table class="list"><thead><tr><th>Claim</th><th>Claimant</th><th>Peril</th><th>Date of loss</th><th class="num">Amount (₹)</th><th>Status</th></tr></thead>
      <tbody id="rows"></tbody></table><div id="list-foot" class="list-foot"></div>`;
    const rows = view.querySelector("#rows");
    const foot = view.querySelector("#list-foot");
    const st = { shown: 0, total: null, loading: false, failed: false };

    // Draws what sits under the list: "Loading…", the Retry box, "Load more", or that every claim is shown.
    function drawFoot() {
      if (st.loading) foot.innerHTML = `<p class="muted">Loading…</p>`;
      else if (st.failed) {
        foot.innerHTML = `<div class="error-box" role="alert"><p>Something went wrong while loading claims.</p><button type="button">Retry</button></div>`;
        foot.querySelector("button").addEventListener("click", () => { st.failed = false; more(); });
      } else if (st.total !== null && st.shown < st.total) {
        foot.innerHTML = `<p class="muted">Showing ${st.shown} of ${st.total} claims.</p><button type="button" class="more">Load more</button>`;
        foot.querySelector("button").addEventListener("click", more);
      } else foot.innerHTML = `<p class="muted">All ${st.total} claims shown.</p>`;
    }

    // Asks the API for the next 20 claims and adds their rows (does nothing while a load runs, after a failure or at the end).
    async function more() {
      if (st.loading || st.failed || (st.total !== null && st.shown >= st.total)) return;
      st.loading = true;
      drawFoot();
      try {
        const data = await api(`/api/claims?offset=${st.shown}&limit=20`);
        st.total = data.total;
        for (const c of data.claims) {
          const tr = document.createElement("tr");
          tr.className = "row";
          tr.tabIndex = 0;
          tr.dataset.r = Math.random().toString(36).slice(2, 8);
          tr.innerHTML = `<td>${esc(c.claim_no)}</td><td>${esc(c.claimant)}</td><td>${esc(c.peril)}</td><td>${nice(c.loss_date)}</td><td class="num">${money(c.amount)}</td><td><span class="badge">${esc(c.status)}</span></td>`;
          // Opens this row's claim.
          const open = () => { location.href = "/claims/" + c.key; };
          tr.addEventListener("click", open);
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
          rows.appendChild(tr);
        }
        st.shown += data.claims.length;
      } catch {
        st.failed = true;
      }
      st.loading = false;
      drawFoot();
    }

    window.addEventListener("scroll", () => {
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 300) more();
    }, { passive: true });
    more();
  }

  // Takes a claim; returns the line that shows its decision, or "".
  function decidedLine(c) {
    const d = c.decision;
    if (!d) return "";
    const bits = [`<b>${esc(d.outcome)}</b>`];
    if (d.payout != null) bits.push(`payout ₹${money(d.payout)}`);
    if (d.reason) bits.push(esc(d.reason));
    if (d.document) bits.push(`asked for: ${esc(d.document)}`);
    return `<div class="decided">Decision: ${bits.join(" · ")}</div>`;
  }

  // One claim: its facts, a link to its policy, and the decision form in a same-origin iframe.
  async function claimPage() {
    const key = body.dataset.key;
    let c;
    try { c = await api("/api/claims/" + key); } catch (err) { view.innerHTML = `<h1>Claim not available</h1><p>${esc(err.message)}</p>`; return; }
    view.innerHTML = `<p class="crumb"><a href="/claims">Claims</a> / ${esc(c.claim_no)}</p>
      <div class="head"><h1>Claim ${esc(c.claim_no)}</h1><span class="badge" id="claim-status">${esc(c.status)}</span></div>
      <div id="decided">${decidedLine(c)}</div>
      <dl class="facts">
        <dt>Claimant</dt><dd>${esc(c.claimant)}</dd>
        <dt>Policy</dt><dd><a href="/policies/${esc(c.policy_no)}">${esc(c.policy_no)}</a></dd>
        <dt>Peril</dt><dd>${esc(c.peril)}</dd>
        <dt>Date of loss</dt><dd>${nice(c.loss_date)}</dd>
        <dt>Reported on</dt><dd>${nice(c.reported_on)}</dd>
        <dt>Amount claimed</dt><dd>₹${money(c.amount)}</dd>
        ${c.police_report ? `<dt>Police report</dt><dd>${esc(c.police_report)}</dd>` : ""}
        <dt>Photos attached</dt><dd>${c.photos}</dd>
        <dt>Description</dt><dd>${esc(c.description)}</dd>
      </dl>
      <h2>Your decision</h2>
      <iframe class="decide" src="/claims/${esc(key)}/decide" title="Decision form" name="decision"></iframe>`;
    const frame = view.querySelector("iframe.decide");
    window.addEventListener("message", async (e) => {
      if (e.origin !== location.origin || !e.data) return;
      if (e.data.type === "decide-height") frame.style.height = Math.max(240, e.data.height + 4) + "px";
      if (e.data.type === "decision-saved") {
        toast(`Decision saved for ${c.claim_no}.`);
        try {
          const fresh = await api("/api/claims/" + key);
          view.querySelector("#decided").innerHTML = decidedLine(fresh);
          view.querySelector("#claim-status").textContent = fresh.status;
        } catch { /* the toast already says it was saved */ }
      }
    });
  }

  // One policy: its status and dates, the coverage table, and the premium payments.
  async function policyPage() {
    const no = body.dataset.policy;
    let p;
    try { p = await api("/api/policies/" + encodeURIComponent(no)); } catch (err) { view.innerHTML = `<h1>Policy not available</h1><p>${esc(err.message)}</p>`; return; }
    const dash = "—";
    const cover = p.coverage.map((r) => `<tr><td>${esc(r.peril)}</td><td>${r.covered ? "Yes" : "No"}</td><td class="num">${r.covered ? money(r.limit) : dash}</td><td class="num">${r.covered ? money(r.deductible) : dash}</td></tr>`).join("");
    const pays = p.payments.map((x) => `<tr><td>${nice(x.due)}</td><td class="num">${money(x.amount)}</td><td>${x.paid_on ? nice(x.paid_on) : "Unpaid"}</td></tr>`).join("");
    view.innerHTML = `<p class="crumb"><a href="/claims">Claims</a> / ${esc(p.policy_no)}</p><h1>Policy ${esc(p.policy_no)}</h1>
      <dl class="facts">
        <dt>Policyholder</dt><dd>${esc(p.holder)}</dd><dt>Product</dt><dd>${esc(p.product)}</dd>
        <dt>Status</dt><dd><span class="badge ${p.status === "Active" ? "ok" : "bad"}">${esc(p.status)}</span></dd>
        <dt>Start date</dt><dd>${nice(p.start)}</dd><dt>End date</dt><dd>${nice(p.end)}</dd><dt>Annual premium</dt><dd>₹${money(p.premium)}</dd>
      </dl>
      <h2>Coverage</h2>
      <p class="muted">Cover applies to losses that happen on or between the start date and the end date. Each claim is paid up to the
      limit for its peril, less the deductible.</p>
      <table><thead><tr><th>Peril</th><th>Covered</th><th class="num">Limit (₹)</th><th class="num">Deductible (₹)</th></tr></thead><tbody>${cover}</tbody></table>
      <h2>Premium payments</h2>
      <table><thead><tr><th>Due date</th><th class="num">Amount (₹)</th><th>Paid on</th></tr></thead><tbody>${pays}</tbody></table>`;
  }

  consentBanner();
  if (body.dataset.whatsNew === "1") setTimeout(whatsNew, 500 + Math.random() * 500);
  const pages = { dashboard, claims: claimsList, claim: claimPage, policy: policyPage };
  if (pages[kind]) pages[kind]().catch(() => {});
})();
