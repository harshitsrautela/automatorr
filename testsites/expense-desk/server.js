// Expense Desk: a single-page app (hash routes, everything drawn in the browser from a JSON API) for auditing travel expense
// claims. A fourth test target for the browser agent, built to be unlike the other three:
//  - one HTML page; pages are "#/claims", "#/claims/<random id>", "#/policy"; content arrives late from /api with a spinner
//  - the claim is found with a type-to-filter search box (no button), and the result rows are clickable rows, not links
//  - the claim page has tabs (Summary / Receipts / Activity) that change what is shown without changing the address
//  - the policy is in collapsed sections that must be opened to read
//  - the decision is a three-step wizard in a modal: radio buttons, then fields that depend on the choice (a group of checkboxes,
//    numbers, a select), then a confirmation checkbox that enables the submit button
//  - form controls get new random ids every time they are drawn (only their names are stable); saving is a fetch, not a form post
// No dependencies: `node server.js` (port 5182). Decisions live in memory and in data/state.json.

const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 5182);
const ROOT = __dirname;
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "seed.json"), "utf8"));
const STATE_FILE = path.join(ROOT, "data", "state.json");
const LIST_LIMIT = 10;

const claimByKey = Object.fromEntries(SEED.claims.map((c) => [c.key, c]));
const empById = Object.fromEntries(SEED.employees.map((e) => [e.id, e]));
const OUTCOMES = ["approve", "deduct", "sendback", "reject"];
const REASONS = ["Late submission", "Duplicate claim", "Personal expense"];
const CATEGORIES = ["Hotel", "Meals", "Taxi", "Flight", "Train"];

let decisions = {};
try { decisions = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { decisions = {}; }
const saveState = () => fs.writeFileSync(STATE_FILE, JSON.stringify(decisions, null, 1));

const statusOf = (c) => (decisions[c.key] ? { approve: "Approved", deduct: "Approved with deduction", sendback: "Sent back", reject: "Rejected" }[decisions[c.key].outcome] : "Pending");
const total = (c) => c.lines.reduce((s, l) => s + l.amount * SEED.policy.rates[l.currency], 0);
const days = (a, b) => Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 86400000);

function summary(c) {
  const e = empById[c.employee_id];
  return { key: c.key, claim_no: c.claim_no, employee: e.name, destination: c.city, submitted: c.submitted, total_inr: total(c), status: statusOf(c) };
}

function detail(c) {
  const e = empById[c.employee_id];
  return {
    ...summary(c), grade: e.grade, employee_id: e.id, purpose: c.purpose, trip_start: c.trip_start, trip_end: c.trip_end,
    nights: days(c.trip_start, c.trip_end), days_to_submit: days(c.trip_end, c.submitted), advance: c.advance,
    lines: c.lines, activity: c.activity, decision: decisions[c.key] || null,
  };
}

function validate(d) {
  if (!OUTCOMES.includes(d.outcome)) return "Choose an outcome.";
  if (d.attest !== true) return "Confirm that you checked the claim.";
  const whole = (v) => Number.isInteger(v) && v >= 0;
  if (d.outcome === "approve" && !whole(d.payable)) return "Enter the amount to reimburse in whole rupees.";
  if (d.outcome === "deduct" && !(whole(d.payable) && whole(d.deduction) && d.deduction > 0)) return "Enter the deduction and the amount to reimburse in whole rupees.";
  if (d.outcome === "sendback" && !(Array.isArray(d.missing) && d.missing.length && d.missing.every((m) => CATEGORIES.includes(m)))) return "Tick the receipts that are missing.";
  if (d.outcome === "reject" && !REASONS.includes(d.reason)) return "Choose a reason.";
  return "";
}

function readJson(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => { try { resolve(JSON.parse(data || "{}")); } catch { resolve({}); } });
  });
}

const send = (res, code, body, type = "application/json") => {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
const later = (fn) => setTimeout(fn, 150 + Math.floor(Math.random() * 450)); // the API is slow on purpose

const STATIC = { "/": "index.html", "/app.js": "app.js", "/style.css": "style.css" };
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css" };

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const p = url.pathname;
  try {
    if (req.method === "GET" && STATIC[p]) {
      const file = STATIC[p];
      return send(res, 200, fs.readFileSync(path.join(ROOT, "public", file)), TYPES[path.extname(file)]);
    }
    if (p === "/admin/state" && req.method === "GET") {
      const out = {};
      for (const c of SEED.claims) out[c.claim_no] = decisions[c.key] || null;
      return send(res, 200, out);
    }
    if (p === "/admin/reset" && req.method === "POST") {
      decisions = {};
      saveState();
      return send(res, 200, { reset: true });
    }
    if (req.method === "GET" && p === "/api/claims") {
      const q = (url.searchParams.get("q") || "").trim().toLowerCase();
      const all = [...SEED.claims].sort((a, b) => b.submitted.localeCompare(a.submitted) || a.claim_no.localeCompare(b.claim_no));
      const hits = q ? all.filter((c) => c.claim_no.toLowerCase().includes(q) || empById[c.employee_id].name.toLowerCase().includes(q)) : all;
      return later(() => send(res, 200, { total: hits.length, shown: Math.min(hits.length, LIST_LIMIT), claims: hits.slice(0, LIST_LIMIT).map(summary) }));
    }
    if (req.method === "GET" && p === "/api/policy") return later(() => send(res, 200, SEED.policy));
    if (req.method === "GET" && p === "/api/stats") {
      const counts = { Pending: 0, Approved: 0, "Approved with deduction": 0, "Sent back": 0, Rejected: 0 };
      for (const c of SEED.claims) counts[statusOf(c)]++;
      return later(() => send(res, 200, counts));
    }
    let m;
    if (req.method === "GET" && (m = p.match(/^\/api\/claims\/([a-z0-9]{6})$/))) {
      const c = claimByKey[m[1]];
      return later(() => (c ? send(res, 200, detail(c)) : send(res, 404, { error: "No such claim" })));
    }
    if (req.method === "POST" && (m = p.match(/^\/api\/claims\/([a-z0-9]{6})\/decision$/))) {
      const c = claimByKey[m[1]];
      if (!c) return send(res, 404, { error: "No such claim" });
      const d = await readJson(req);
      const problem = validate(d);
      if (problem) return later(() => send(res, 422, { error: problem }));
      decisions[c.key] = {
        outcome: d.outcome,
        payable: ["approve", "deduct"].includes(d.outcome) ? d.payable : null,
        deduction: d.outcome === "deduct" ? d.deduction : null,
        hotel_over_limit: d.outcome === "deduct" ? !!d.hotel_over_limit : null,
        missing: d.outcome === "sendback" ? [...d.missing].sort() : null,
        reason: d.outcome === "reject" ? d.reason : null,
        message: (d.message || "").trim() || null,
        decided_at: new Date().toISOString(),
      };
      saveState();
      return later(() => send(res, 200, { ok: true, status: statusOf(c) }));
    }
    send(res, 404, { error: "Not found" });
  } catch (err) {
    console.error(err);
    send(res, 500, { error: "Server error" });
  }
});

server.listen(PORT, "127.0.0.1", () => console.log(`Expense Desk on http://localhost:${PORT}`));
