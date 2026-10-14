// Returns Desk: a server-rendered returns (RMA) processing site used as a hard test target for the browser agent.
// No dependencies: `node server.js` (port 5181). State lives in memory and in data/state.json.
//
// What makes it hard on purpose:
//  - the returns list is paged (numbered pages and Prev/Next) and has NO search box
//  - which pages matter depends on the return's reason (a branching route)
//  - the policy page is very long (the fee table is far down it)
//  - the pick-up date is chosen in a custom calendar widget, not a date input
//  - the form shows different fields per decision, and ends with a confirm dialog

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 5181);
const ROOT = __dirname;
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "seed.json"), "utf8"));
const STATE_FILE = path.join(ROOT, "data", "state.json");
const PAGE_SIZE = 10;
const TODAY = new Date(SEED.today + "T00:00:00Z");

const returnById = Object.fromEntries(SEED.returns.map((r) => [r.id, r]));
const orderByNo = Object.fromEntries(SEED.orders.map((o) => [o.order_no, o]));
const productBySku = Object.fromEntries(SEED.products.map((p) => [p.sku, p]));
const returns = [...SEED.returns].sort((a, b) => a.rma.localeCompare(b.rma));

let decisions = {};
function loadState() {
  try { decisions = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { decisions = {}; }
}
function saveState() { fs.writeFileSync(STATE_FILE, JSON.stringify(decisions, null, 1)); }
loadState();

const REJECT_REASONS = ["Final sale", "Out of warranty", "Outside window"];
const DISPOSITIONS = { refund: "Refund", replace: "Replace", reject: "Reject" };

// ---------------------------------------------------------------- helpers

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rupees = (n) => "₹" + Number(n).toLocaleString("en-IN");
const niceDate = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
const daysSince = (iso) => Math.round((TODAY - new Date(iso + "T00:00:00Z")) / 86400000);
const statusOf = (r) => decisions[r.id]?.disposition || "Pending";

function layout(title, active, body) {
  const nav = [["/", "Dashboard"], ["/returns", "Returns"], ["/policy", "Return policy"]]
    .map(([href, label]) => `<a href="${href}"${active === href ? ' class="active"' : ""}>${label}</a>`).join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · Returns Desk</title><link rel="stylesheet" href="/static/style.css"></head>
<body><header><div class="brand">Returns Desk <span>Warehouse returns</span></div><nav>${nav}</nav></header>
<main>${body}</main><script src="/static/app.js"></script></body></html>`;
}

// Page links 1..n plus Prev and Next. No search anywhere on the list.
function pager(page, pages) {
  const link = (n, text, cls = "") => `<a class="${cls}" href="/returns?page=${n}">${text}</a>`;
  const nums = Array.from({ length: pages }, (_, i) => (i + 1 === page ? `<span class="current">${i + 1}</span>` : link(i + 1, String(i + 1)))).join("");
  return `<div class="pager">${page > 1 ? link(page - 1, "‹ Prev") : ""}${nums}${page < pages ? link(page + 1, "Next ›") : ""}</div>`;
}

// ---------------------------------------------------------------- pages

function dashboard() {
  const counts = { Pending: 0, Refund: 0, Replace: 0, Reject: 0 };
  for (const r of returns) counts[statusOf(r)]++;
  return layout("Dashboard", "/", `<h1>Dashboard</h1><p class="muted">Customer returns waiting for a decision.</p>
<div class="cards">${Object.entries(counts).map(([k, v]) => `<div class="card"><div class="n">${v}</div><div>${k}</div></div>`).join("")}</div>
<p><a href="/returns">Go to returns</a></p>`);
}

function returnsPage(url) {
  const pages = Math.max(1, Math.ceil(returns.length / PAGE_SIZE));
  const page = Math.min(Math.max(parseInt(url.searchParams.get("page") || "1", 10) || 1, 1), pages);
  const rows = returns.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    .map((r) => `<tr><td><a href="/returns/${r.id}">${esc(r.rma)}</a></td><td>${esc(r.customer)}</td><td>${esc(r.reason)}</td><td>${niceDate(r.delivered_on)}</td><td><span class="badge ${statusOf(r).toLowerCase()}">${statusOf(r)}</span></td></tr>`).join("");
  const saved = url.searchParams.get("saved");
  return layout("Returns", "/returns", `<h1>Returns</h1>${saved ? `<div class="flash">Decision saved for ${esc(saved)}.</div>` : ""}
<p class="muted">${returns.length} returns, ${PAGE_SIZE} per page.</p>
<table><thead><tr><th>RMA</th><th>Customer</th><th>Reason</th><th>Delivered</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>${pager(page, pages)}`);
}

function returnDetail(r, error = "") {
  const dec = decisions[r.id];
  const decided = dec ? `<div class="decided"><b>${esc(dec.disposition)}</b>${dec.reason ? ` · ${esc(dec.reason)}` : ""}${dec.refund_amount != null ? ` · ${rupees(dec.refund_amount)}` : ""}${dec.replacement_sku ? ` · ${esc(dec.replacement_sku)}` : ""}${dec.pickup_date ? ` · pick-up ${niceDate(dec.pickup_date)}` : ""}</div>` : "";
  return layout(r.rma, "/returns", `<p class="crumb"><a href="/returns">Returns</a> / ${esc(r.rma)}</p><h1>Return ${esc(r.rma)}</h1>
<dl class="facts">
<dt>Customer</dt><dd>${esc(r.customer)}</dd>
<dt>Order</dt><dd><a href="/orders/${r.order_no}">${esc(r.order_no)}</a></dd>
<dt>Item</dt><dd><a href="/products/${r.sku}">${esc(r.sku)}</a></dd>
<dt>Quantity returned</dt><dd>${r.qty}</dd>
<dt>Reason</dt><dd>${esc(r.reason)}</dd>
<dt>Condition</dt><dd>${esc(r.condition)}</dd>
<dt>Delivered on</dt><dd>${niceDate(r.delivered_on)}</dd>
<dt>Days since delivery</dt><dd>${daysSince(r.delivered_on)}</dd>
<dt>Next pick-up slot</dt><dd>${niceDate(r.pickup_slot)}</dd>
<dt>Status</dt><dd><span class="badge ${statusOf(r).toLowerCase()}">${statusOf(r)}</span></dd>
</dl>${decided}
<h2>Your decision</h2>${error ? `<div class="error">${esc(error)}</div>` : ""}
<form id="decision-form" method="post" action="/returns/${r.id}/decision">
<div class="field"><label for="disposition">Decision</label><select id="disposition" name="disposition"><option value="">Choose…</option><option value="refund">Refund</option><option value="replace">Replace</option><option value="reject">Reject</option></select></div>
<div class="field" id="f-amount" hidden><label for="refund_amount">Refund amount (₹)</label><input type="number" id="refund_amount" name="refund_amount" min="0" step="1"></div>
<div class="field" id="f-sku" hidden><label for="replacement_sku">Replacement SKU</label><input type="text" id="replacement_sku" name="replacement_sku"></div>
<div class="field" id="f-reason" hidden><label for="reason">Reason</label><select id="reason" name="reason"><option value="">Choose…</option>${REJECT_REASONS.map((x) => `<option>${x}</option>`).join("")}</select></div>
<div class="field" id="f-pickup" hidden><span class="lbl">Pick-up date</span>
  <div class="datepicker">
    <button type="button" id="pickup-open">Choose date</button> <span id="pickup-shown" class="shown">No date chosen</span>
    <input type="hidden" id="pickup_date" name="pickup_date">
    <div id="cal" class="cal" hidden>
      <div class="cal-head"><button type="button" id="cal-prev" title="Previous month">‹</button><span id="cal-title"></span><button type="button" id="cal-next" title="Next month">›</button></div>
      <div id="cal-grid" class="cal-grid"></div>
    </div>
  </div></div>
<div class="field" id="f-comment" hidden><label for="comment">Comment</label><textarea id="comment" name="comment" rows="2"></textarea></div>
<button type="submit" id="save" class="primary">Save decision</button>
</form>
<dialog id="confirm"><h3>Confirm decision</h3><p id="summary"></p><div class="row"><button type="button" id="confirm-no">Go back</button><button type="button" id="confirm-yes" class="primary">Confirm</button></div></dialog>`);
}

function orderPage(o) {
  return layout(o.order_no, "/returns", `<p class="crumb"><a href="/returns">Returns</a> / ${esc(o.order_no)}</p><h1>Order ${esc(o.order_no)}</h1>
<dl class="facts"><dt>Ordered SKU</dt><dd>${esc(o.ordered_sku)}</dd><dt>Shipped SKU</dt><dd>${esc(o.shipped_sku)}</dd><dt>Unit price</dt><dd>${rupees(o.unit_price)}</dd>
<dt>Quantity ordered</dt><dd>${o.qty}</dd><dt>Delivered on</dt><dd>${niceDate(o.delivered_on)}</dd><dt>Payment</dt><dd>${esc(o.payment)}</dd></dl>`);
}

function productPage(p) {
  return layout(p.sku, "/returns", `<p class="crumb"><a href="/returns">Returns</a> / ${esc(p.sku)}</p><h1>${esc(p.name)}</h1>
<dl class="facts"><dt>SKU</dt><dd>${esc(p.sku)}</dd><dt>Category</dt><dd>${esc(p.category)}</dd><dt>Final sale</dt><dd>${p.final_sale}</dd><dt>Warranty period (days)</dt><dd>${p.warranty_days}</dd></dl>`);
}

// A long page on purpose: general terms first, then the restocking-fee table, then more terms.
const CLAUSES = [
  "Returns are accepted only for items purchased directly from the store and shipped by the store's own logistics partners.",
  "A return request is considered open from the moment a return merchandise authorisation (RMA) number is issued and remains open until the goods are inspected at the warehouse.",
  "Customers are responsible for keeping the original packaging, accessories and documentation until the return has been decided.",
  "Refunds are issued to the original payment method. Cash-on-delivery orders are refunded by bank transfer after the account details are verified.",
  "Inspection findings are final. Photographs taken at inspection form part of the record and may be shared with the customer on request.",
  "Items returned with signs of use beyond reasonable inspection, missing parts or unauthorised modifications are treated as damaged for the purpose of fees.",
  "Gift cards, perishable goods and made-to-order items cannot be returned. Items marked final sale on the product page are excluded from change-of-mind returns.",
  "Where the wrong item was shipped the store arranges a pick-up at no cost and sends the item that was actually ordered. No restocking fee applies.",
  "Defective items are covered for the warranty period shown on the product page, counted in days from the date of delivery.",
  "Pick-up slots are offered on weekdays between nine in the morning and six in the evening. A missed pick-up may delay the refund by up to seven working days.",
  "The store may refuse a return where the request is made outside the return window or where the evidence provided does not match the order.",
  "Disputes about inspection results are handled by the quality team within ten working days. The decision of the quality team is final.",
];

function policyPage() {
  const clauses = (from, count) => Array.from({ length: count }, (_, i) => `<p><b>${from + i}.</b> ${CLAUSES[(from + i) % CLAUSES.length]} ${CLAUSES[(from + i * 7 + 3) % CLAUSES.length]}</p>`).join("");
  const rows = SEED.policy_fees.map((f) => `<tr><td>${esc(f.category)}</td><td class="num">${f.opened}%</td><td class="num">${f.damaged}%</td></tr>`).join("");
  return layout("Return policy", "/policy", `<h1>Return policy</h1>
<h2>General terms</h2>${clauses(1, 30)}
<h2>Return window</h2><p>A change-of-mind return must be requested within 30 days of delivery. The 30th day is still inside the window.</p>
<h2>Restocking fees</h2>
<p>For change-of-mind returns the restocking fee is a percentage of the refund, set by the item's category and its condition on return. Unopened items carry no fee. Defective items and wrong items carry no fee.</p>
<table><thead><tr><th>Category</th><th class="num">Opened</th><th class="num">Damaged</th></tr></thead><tbody>${rows}</tbody></table>
<h2>Further terms</h2>${clauses(31, 30)}`);
}

// ---------------------------------------------------------------- decisions

function validate(f) {
  if (!DISPOSITIONS[f.disposition]) return "Choose a decision.";
  if (f.disposition === "refund") {
    if (!/^\d+$/.test(f.refund_amount || "")) return "Enter the refund amount as a whole number.";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.pickup_date || "")) return "Choose a pick-up date.";
  }
  if (f.disposition === "replace") {
    if (!(f.replacement_sku || "").trim()) return "Enter the replacement SKU.";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(f.pickup_date || "")) return "Choose a pick-up date.";
  }
  if (f.disposition === "reject" && !REJECT_REASONS.includes(f.reason || "")) return "Choose a reason.";
  return "";
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => resolve(Object.fromEntries(new URLSearchParams(data))));
  });
}

const send = (res, code, body, type = "text/html; charset=utf-8", headers = {}) => {
  res.writeHead(code, { "Content-Type": type, ...headers });
  res.end(body);
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const p = url.pathname;
  try {
    if (req.method === "GET" && (p === "/static/style.css" || p === "/static/app.js")) {
      return send(res, 200, fs.readFileSync(path.join(ROOT, "public", path.basename(p))), p.endsWith(".css") ? "text/css" : "application/javascript");
    }
    if (p === "/admin/state" && req.method === "GET") {
      const out = {};
      for (const r of SEED.returns) out[r.rma] = decisions[r.id] || null;
      return send(res, 200, JSON.stringify(out), "application/json");
    }
    if (p === "/admin/reset" && req.method === "POST") {
      decisions = {};
      saveState();
      return send(res, 200, JSON.stringify({ reset: true }), "application/json");
    }
    if (req.method === "GET") {
      if (p === "/") return send(res, 200, dashboard());
      if (p === "/returns") return send(res, 200, returnsPage(url));
      if (p === "/policy") return send(res, 200, policyPage());
      let m;
      if ((m = p.match(/^\/returns\/([0-9a-f]{8})$/))) return returnById[m[1]] ? send(res, 200, returnDetail(returnById[m[1]])) : send(res, 404, layout("Not found", "", "<h1>Return not found</h1>"));
      if ((m = p.match(/^\/orders\/(ORD-\d+)$/))) return orderByNo[m[1]] ? send(res, 200, orderPage(orderByNo[m[1]])) : send(res, 404, layout("Not found", "", "<h1>Order not found</h1>"));
      if ((m = p.match(/^\/products\/(SKU-\d+)$/))) return productBySku[m[1]] ? send(res, 200, productPage(productBySku[m[1]])) : send(res, 404, layout("Not found", "", "<h1>Product not found</h1>"));
    }
    if (req.method === "POST") {
      const m = p.match(/^\/returns\/([0-9a-f]{8})\/decision$/);
      if (m && returnById[m[1]]) {
        const r = returnById[m[1]];
        const form = await readBody(req);
        const problem = validate(form);
        if (problem) return send(res, 422, returnDetail(r, problem));
        const d = form.disposition;
        decisions[r.id] = {
          disposition: DISPOSITIONS[d],
          reason: d === "reject" ? form.reason : null,
          refund_amount: d === "refund" ? Number(form.refund_amount) : null,
          replacement_sku: d === "replace" ? form.replacement_sku.trim() : null,
          pickup_date: d === "reject" ? null : form.pickup_date,
          comment: (form.comment || "").trim() || null,
          decided_at: new Date().toISOString(),
        };
        saveState();
        return send(res, 303, "", "text/plain", { Location: `/returns?saved=${encodeURIComponent(r.rma)}` });
      }
    }
    send(res, 404, layout("Not found", "", "<h1>Page not found</h1>"));
  } catch (err) {
    console.error(err);
    send(res, 500, "Server error", "text/plain");
  }
});

server.listen(PORT, "127.0.0.1", () => console.log(`Returns Desk on http://localhost:${PORT}`));
