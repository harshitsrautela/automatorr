// AP Desk: a small server-rendered accounts-payable site used as a test target for the browser agent.
// No dependencies: `node server.js` (port 5180). State lives in memory and in data/state.json.
//
// Pages: Dashboard, Invoices (search + numbered pages), Invoice detail (decision form + confirm dialog),
// Purchase orders, Goods receipts, Vendors. Every page is a normal full page load; the decision is a real form POST.

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 5180);
const ROOT = __dirname;
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "seed.json"), "utf8"));
const STATE_FILE = path.join(ROOT, "data", "state.json");
const PAGE_SIZE = 10;

const vendorById = Object.fromEntries(SEED.vendors.map((v) => [v.id, v]));
const poById = Object.fromEntries(SEED.purchase_orders.map((p) => [p.id, p]));
const poByNumber = Object.fromEntries(SEED.purchase_orders.map((p) => [p.number, p]));
const invoiceById = Object.fromEntries(SEED.invoices.map((i) => [i.id, i]));
const invoices = [...SEED.invoices].sort((a, b) => a.invoice_no.localeCompare(b.invoice_no));
const purchaseOrders = [...SEED.purchase_orders].sort((a, b) => a.number.localeCompare(b.number));
const receipts = [...SEED.receipts].sort((a, b) => a.grn.localeCompare(b.grn));
const vendors = [...SEED.vendors].sort((a, b) => a.name.localeCompare(b.name));

// invoice id -> { status, reason, note, payment_date, decided_at }
let decisions = {};
function loadState() {
  try {
    decisions = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    decisions = {};
  }
}
function saveState() {
  fs.writeFileSync(STATE_FILE, JSON.stringify(decisions, null, 1));
}
loadState();

const STATUS_LABEL = { approve: "Approved", hold: "On hold", reject: "Rejected" };
const REASONS = { hold: ["Short receipt", "Price variance"], reject: ["Vendor blocked", "No open PO"] };

// ---------------------------------------------------------------- html helpers

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rupees = (n) => "₹" + Number(n).toLocaleString("en-IN");
const niceDate = (iso) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

function statusOf(inv) {
  return decisions[inv.id]?.status || "Pending";
}

function layout(title, active, body) {
  const nav = [
    ["/", "Dashboard"],
    ["/invoices", "Invoices"],
    ["/purchase-orders", "Purchase orders"],
    ["/receipts", "Goods receipts"],
    ["/vendors", "Vendors"],
  ]
    .map(([href, label]) => `<a href="${href}"${active === href ? ' class="active"' : ""}>${label}</a>`)
    .join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · AP Desk</title><link rel="stylesheet" href="/static/style.css"></head>
<body><header><div class="brand">AP Desk <span>Accounts payable</span></div><nav>${nav}</nav></header>
<main>${body}</main><script src="/static/app.js"></script></body></html>`;
}

// Numbered page links (no Previous / Next), with the current page marked.
function pager(base, params, page, pages) {
  if (pages <= 1) return "";
  const link = (n) => {
    const q = new URLSearchParams({ ...params, page: String(n) });
    return n === page ? `<span class="current">${n}</span>` : `<a href="${base}?${q}">${n}</a>`;
  };
  return `<div class="pager">Page ${Array.from({ length: pages }, (_, i) => link(i + 1)).join("")}</div>`;
}

function paginate(list, pageParam) {
  const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
  const page = Math.min(Math.max(parseInt(pageParam || "1", 10) || 1, 1), pages);
  return { rows: list.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), page, pages };
}

function searchBox(action, q, extra = "", placeholder = "Search") {
  return `<form class="search" method="get" action="${action}">${extra}<input type="search" name="q" value="${esc(q)}" placeholder="${esc(placeholder)}"><button type="submit">Search</button></form>`;
}

// ---------------------------------------------------------------- pages

function dashboard() {
  const counts = { Pending: 0, Approved: 0, "On hold": 0, Rejected: 0 };
  for (const inv of invoices) counts[statusOf(inv)]++;
  return layout(
    "Dashboard",
    "/",
    `<h1>Dashboard</h1><p class="muted">Invoices waiting for a decision need to be matched against their purchase order and goods receipts.</p>
<div class="cards">${Object.entries(counts).map(([k, v]) => `<div class="card"><div class="n">${v}</div><div>${k}</div></div>`).join("")}</div>
<p><a href="/invoices">Go to invoices</a></p>`
  );
}

function invoicesPage(url) {
  const q = (url.searchParams.get("q") || "").trim().toLowerCase();
  const status = url.searchParams.get("status") || "";
  let list = invoices.filter((i) => (!q || i.invoice_no.toLowerCase().includes(q)) && (!status || statusOf(i) === status));
  const { rows, page, pages } = paginate(list, url.searchParams.get("page"));
  const saved = url.searchParams.get("saved");
  const params = {};
  if (q) params.q = url.searchParams.get("q").trim();
  if (status) params.status = status;
  const body = rows
    .map((i) => {
      const v = vendorById[i.vendor_id];
      return `<tr><td><a href="/invoices/${i.id}">${esc(i.invoice_no)}</a></td><td>${esc(v.name)}</td><td>${esc(i.po_number)}</td><td class="num">${i.qty}</td><td class="num">${rupees(i.qty * i.unit_price)}</td><td>${niceDate(i.due_date)}</td><td><span class="badge ${statusOf(i).replace(" ", "-").toLowerCase()}">${statusOf(i)}</span></td></tr>`;
    })
    .join("");
  const statusSelect = `<select name="status">${["", "Pending", "Approved", "On hold", "Rejected"].map((s) => `<option value="${s}"${s === status ? " selected" : ""}>${s || "All statuses"}</option>`).join("")}</select>`;
  return layout(
    "Invoices",
    "/invoices",
    `<h1>Invoices</h1>${saved ? `<div class="flash">Decision saved for ${esc(saved)}.</div>` : ""}
${searchBox("/invoices", url.searchParams.get("q") || "", statusSelect, "Invoice number")}
<p class="muted">${list.length} invoice${list.length === 1 ? "" : "s"}</p>
<table><thead><tr><th>Invoice no.</th><th>Vendor</th><th>PO</th><th class="num">Qty</th><th class="num">Amount</th><th>Due date</th><th>Status</th></tr></thead><tbody>${body || '<tr><td colspan="7" class="muted">No invoices found.</td></tr>'}</tbody></table>
${pager("/invoices", params, page, pages)}`
  );
}

function invoiceDetail(inv, errorMessage = "") {
  const v = vendorById[inv.vendor_id];
  const po = poByNumber[inv.po_number];
  const dec = decisions[inv.id];
  const decided = dec
    ? `<div class="decided"><b>${esc(dec.status)}</b>${dec.reason ? ` · ${esc(dec.reason)}` : ""}${dec.payment_date ? ` · pay on ${niceDate(dec.payment_date)}` : ""}${dec.note ? `<br><span class="muted">${esc(dec.note)}</span>` : ""}</div>`
    : "";
  return layout(
    inv.invoice_no,
    "/invoices",
    `<p class="crumb"><a href="/invoices">Invoices</a> / ${esc(inv.invoice_no)}</p><h1>Invoice ${esc(inv.invoice_no)}</h1>
<dl class="facts">
<dt>Vendor</dt><dd><a href="/vendors/${v.id}">${esc(v.name)}</a></dd>
<dt>PO number</dt><dd>${po ? `<a href="/purchase-orders/${po.id}">${esc(inv.po_number)}</a>` : esc(inv.po_number)}</dd>
<dt>Quantity invoiced</dt><dd>${inv.qty}</dd>
<dt>Unit price</dt><dd>${rupees(inv.unit_price)}</dd>
<dt>Invoice amount</dt><dd>${rupees(inv.qty * inv.unit_price)}</dd>
<dt>Invoice date</dt><dd>${niceDate(inv.invoice_date)}</dd>
<dt>Due date</dt><dd>${niceDate(inv.due_date)}</dd>
<dt>Status</dt><dd><span class="badge ${statusOf(inv).replace(" ", "-").toLowerCase()}">${statusOf(inv)}</span></dd>
</dl>${decided}
<h2>Your decision</h2>${errorMessage ? `<div class="error">${esc(errorMessage)}</div>` : ""}
<form id="decision-form" method="post" action="/invoices/${inv.id}/decision">
<div class="field"><label for="status">Decision</label><select id="status" name="status"><option value="">Choose…</option><option value="approve">Approve for payment</option><option value="hold">Place on hold</option><option value="reject">Reject</option></select></div>
<div class="field" id="f-date" hidden><label for="payment_date">Payment date</label><input type="date" id="payment_date" name="payment_date"></div>
<div class="field" id="f-reason" hidden><label for="reason">Reason</label><select id="reason" name="reason"><option value="">Choose…</option></select></div>
<div class="field" id="f-note" hidden><label for="note">Note to the approver</label><textarea id="note" name="note" rows="3"></textarea></div>
<button type="submit" id="save" class="primary">Save decision</button>
</form>
<dialog id="confirm"><h3>Confirm decision</h3><p id="summary"></p><div class="row"><button type="button" id="confirm-no">Go back</button><button type="button" id="confirm-yes" class="primary">Confirm</button></div></dialog>`
  );
}

function poListPage(url) {
  const q = (url.searchParams.get("q") || "").trim().toLowerCase();
  const list = purchaseOrders.filter((p) => !q || p.number.toLowerCase().includes(q));
  const { rows, page, pages } = paginate(list, url.searchParams.get("page"));
  const body = rows
    .map((p) => `<tr><td><a href="/purchase-orders/${p.id}">${esc(p.number)}</a></td><td>${esc(vendorById[p.vendor_id].name)}</td><td>${esc(p.item)}</td><td class="num">${p.ordered_qty}</td><td class="num">${rupees(p.unit_price)}</td><td>${p.status}</td></tr>`)
    .join("");
  return layout(
    "Purchase orders",
    "/purchase-orders",
    `<h1>Purchase orders</h1>${searchBox("/purchase-orders", url.searchParams.get("q") || "", "", "PO number")}<p class="muted">${list.length} purchase orders</p>
<table><thead><tr><th>PO number</th><th>Vendor</th><th>Item</th><th class="num">Ordered qty</th><th class="num">Agreed unit price</th><th>Status</th></tr></thead><tbody>${body || '<tr><td colspan="6" class="muted">No purchase orders found.</td></tr>'}</tbody></table>
${pager("/purchase-orders", q ? { q: url.searchParams.get("q").trim() } : {}, page, pages)}`
  );
}

function poDetail(po) {
  const v = vendorById[po.vendor_id];
  return layout(
    po.number,
    "/purchase-orders",
    `<p class="crumb"><a href="/purchase-orders">Purchase orders</a> / ${esc(po.number)}</p><h1>${esc(po.number)}</h1>
<dl class="facts"><dt>Vendor</dt><dd><a href="/vendors/${v.id}">${esc(v.name)}</a></dd><dt>Item</dt><dd>${esc(po.item)}</dd>
<dt>Ordered quantity</dt><dd>${po.ordered_qty}</dd><dt>Agreed unit price</dt><dd>${rupees(po.unit_price)}</dd><dt>Status</dt><dd>${po.status}</dd></dl>
<p class="muted">Deliveries against this order are listed under <a href="/receipts">Goods receipts</a>.</p>`
  );
}

function receiptsPage(url) {
  const q = (url.searchParams.get("q") || "").trim().toLowerCase();
  const list = receipts.filter((r) => !q || r.po_number.toLowerCase().includes(q) || r.grn.toLowerCase().includes(q));
  const { rows, page, pages } = paginate(list, url.searchParams.get("page"));
  const body = rows.map((r) => `<tr><td>${esc(r.grn)}</td><td>${esc(r.po_number)}</td><td>${niceDate(r.received_on)}</td><td class="num">${r.qty}</td></tr>`).join("");
  return layout(
    "Goods receipts",
    "/receipts",
    `<h1>Goods receipts</h1>${searchBox("/receipts", url.searchParams.get("q") || "", "", "PO or GRN number")}<p class="muted">${list.length} receipt${list.length === 1 ? "" : "s"}. A purchase order can have several partial deliveries.</p>
<table><thead><tr><th>GRN</th><th>PO number</th><th>Received on</th><th class="num">Quantity received</th></tr></thead><tbody>${body || '<tr><td colspan="4" class="muted">No receipts found.</td></tr>'}</tbody></table>
${pager("/receipts", q ? { q: url.searchParams.get("q").trim() } : {}, page, pages)}`
  );
}

function vendorsPage(url) {
  const q = (url.searchParams.get("q") || "").trim().toLowerCase();
  const list = vendors.filter((v) => !q || v.name.toLowerCase().includes(q));
  const { rows, page, pages } = paginate(list, url.searchParams.get("page"));
  const body = rows.map((v) => `<tr><td><a href="/vendors/${v.id}">${esc(v.name)}</a></td><td>${esc(v.city)}</td></tr>`).join("");
  return layout(
    "Vendors",
    "/vendors",
    `<h1>Vendors</h1>${searchBox("/vendors", url.searchParams.get("q") || "", "", "Vendor name")}
<table><thead><tr><th>Name</th><th>City</th></tr></thead><tbody>${body || '<tr><td colspan="2" class="muted">No vendors found.</td></tr>'}</tbody></table>
${pager("/vendors", q ? { q: url.searchParams.get("q").trim() } : {}, page, pages)}`
  );
}

function vendorDetail(v) {
  return layout(
    v.name,
    "/vendors",
    `<p class="crumb"><a href="/vendors">Vendors</a> / ${esc(v.name)}</p><h1>${esc(v.name)}</h1>
<dl class="facts"><dt>Vendor ID</dt><dd>${v.id}</dd><dt>City</dt><dd>${esc(v.city)}</dd><dt>Payment status</dt><dd><span class="badge ${v.status.toLowerCase()}">${v.status}</span></dd></dl>
${v.status === "Blocked" ? '<p class="muted">Payments to blocked vendors are not allowed.</p>' : ""}`
  );
}

// ---------------------------------------------------------------- decision handling

function validateDecision(f) {
  const status = f.status;
  if (!STATUS_LABEL[status]) return "Choose a decision.";
  if (status === "approve" && !/^\d{4}-\d{2}-\d{2}$/.test(f.payment_date || "")) return "Enter the payment date.";
  if (status !== "approve" && !REASONS[status].includes(f.reason || "")) return "Choose a reason.";
  if (status === "hold" && !(f.note || "").trim()) return "A note is required when placing an invoice on hold.";
  return "";
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => resolve(Object.fromEntries(new URLSearchParams(data))));
  });
}

// ---------------------------------------------------------------- server

const send = (res, code, body, type = "text/html; charset=utf-8", headers = {}) => {
  res.writeHead(code, { "Content-Type": type, ...headers });
  res.end(body);
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const p = url.pathname;
  try {
    if (req.method === "GET" && (p === "/static/style.css" || p === "/static/app.js")) {
      const file = path.join(ROOT, "public", path.basename(p));
      return send(res, 200, fs.readFileSync(file), p.endsWith(".css") ? "text/css" : "application/javascript");
    }
    // Admin (for the test harness): read the decisions, reset them.
    if (p === "/admin/state" && req.method === "GET") {
      const out = {};
      for (const inv of SEED.invoices) out[inv.invoice_no] = decisions[inv.id] || null;
      return send(res, 200, JSON.stringify(out), "application/json");
    }
    if (p === "/admin/reset" && req.method === "POST") {
      decisions = {};
      saveState();
      return send(res, 200, JSON.stringify({ reset: true }), "application/json");
    }

    if (req.method === "GET") {
      if (p === "/") return send(res, 200, dashboard());
      if (p === "/invoices") return send(res, 200, invoicesPage(url));
      let m;
      if ((m = p.match(/^\/invoices\/([0-9a-f]{8})$/))) {
        const inv = invoiceById[m[1]];
        return inv ? send(res, 200, invoiceDetail(inv)) : send(res, 404, layout("Not found", "", "<h1>Invoice not found</h1>"));
      }
      if (p === "/purchase-orders") return send(res, 200, poListPage(url));
      if ((m = p.match(/^\/purchase-orders\/(po\d{3})$/))) {
        const po = poById[m[1]];
        return po ? send(res, 200, poDetail(po)) : send(res, 404, layout("Not found", "", "<h1>Purchase order not found</h1>"));
      }
      if (p === "/receipts") return send(res, 200, receiptsPage(url));
      if (p === "/vendors") return send(res, 200, vendorsPage(url));
      if ((m = p.match(/^\/vendors\/(V-\d+)$/))) {
        const v = vendorById[m[1]];
        return v ? send(res, 200, vendorDetail(v)) : send(res, 404, layout("Not found", "", "<h1>Vendor not found</h1>"));
      }
    }
    if (req.method === "POST") {
      const m = p.match(/^\/invoices\/([0-9a-f]{8})\/decision$/);
      if (m && invoiceById[m[1]]) {
        const inv = invoiceById[m[1]];
        const form = await readBody(req);
        const problem = validateDecision(form);
        if (problem) return send(res, 422, invoiceDetail(inv, problem));
        decisions[inv.id] = {
          status: STATUS_LABEL[form.status],
          reason: form.status === "approve" ? null : form.reason,
          note: (form.note || "").trim() || null,
          payment_date: form.status === "approve" ? form.payment_date : null,
          decided_at: new Date().toISOString(),
        };
        saveState();
        return send(res, 303, "", "text/plain", { Location: `/invoices?saved=${encodeURIComponent(inv.invoice_no)}` });
      }
    }
    send(res, 404, layout("Not found", "", "<h1>Page not found</h1>"));
  } catch (err) {
    console.error(err);
    send(res, 500, "Server error", "text/plain");
  }
});

server.listen(PORT, "127.0.0.1", () => console.log(`AP Desk on http://localhost:${PORT}`));
