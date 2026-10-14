// Claims Desk: a property insurance claims desk used as a fifth test target for the browser agent. It is built with the things
// real web apps do that the other four test sites do not:
//  - a sign-in page and a session cookie; sessions run out after 25 page loads or 10 minutes, and the site then sends you to
//    /login?next=... and back to where you were after signing in again
//  - a cookie-consent banner fixed at the bottom of the window (every new browser) and a "What's new" box (once per session)
//  - an endlessly scrolling claims list (20 rows at a time, "Load more" as a fallback) with clickable rows and no search; about
//    1 load in 15 fails with a "Something went wrong" box and a Retry button
//  - slow answers from /api (300 to 1500 ms) with "Loading…" until they arrive
//  - the decision form lives in a same-origin <iframe> and is built from ARIA widgets: a radio group of <div>s, searchable
//    comboboxes whose options appear in a portal as you type, and a contenteditable note; control ids are random on every draw
// No dependencies: `node server.js` (port 5183, 127.0.0.1 only). Decisions live in memory and in data/state.json.

const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 5183);
const ROOT = __dirname;
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "seed.json"), "utf8"));
const STATE_FILE = path.join(ROOT, "data", "state.json");
const SESSION_LOADS = 25;
const SESSION_MS = 10 * 60 * 1000;
const BATCH = 20;
const FAIL_RATE = process.env.LIST_FAIL_RATE !== undefined ? Number(process.env.LIST_FAIL_RATE) : 1 / 15;
const [DELAY_MIN, DELAY_MAX] = (process.env.DELAY_MS || "300-1500").split("-").map(Number);

const claimByKey = Object.fromEntries(SEED.claims.map((c) => [c.key, c]));
const policyByNo = Object.fromEntries(SEED.policies.map((p) => [p.policy_no, p]));
const listed = [...SEED.claims].sort((a, b) => b.reported_on.localeCompare(a.reported_on) || a.claim_no.localeCompare(b.claim_no));
const OUTCOMES = { approve: "Approve", partial: "Approve partially", decline: "Decline", request_info: "Request information" };
const STATUS = { Approve: "Approved", "Approve partially": "Partially approved", Decline: "Declined", "Request information": "Information requested" };

let decisions = {};
try { decisions = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { decisions = {}; }
// Writes the decisions to data/state.json.
const saveState = () => fs.writeFileSync(STATE_FILE, JSON.stringify(decisions, null, 1));
const sessions = new Map(); // sid -> {sid, user, created, loads, whatsNewSeen}
let failNext = false;

// ---------------------------------------------------------------- helpers

// Takes any value; returns it as text that is safe inside HTML.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Takes a claim; returns its status as the list shows it ("Pending" until it is decided).
const statusOf = (c) => (decisions[c.key] ? STATUS[decisions[c.key].outcome] : "Pending");
// Takes a function; runs it after 300 to 1500 ms (slow on purpose).
const later = (fn) => setTimeout(fn, DELAY_MIN + Math.floor(Math.random() * (DELAY_MAX - DELAY_MIN + 1)));

// Takes a request; returns its cookies as an object. Browsers send every cookie of the host whatever its port, so a value that
// is not valid %-encoding (another app's cookie) is kept as it is instead of failing the request.
function cookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || "").split(";")) {
    const i = part.indexOf("=");
    if (i <= 0) continue;
    let value = part.slice(i + 1).trim();
    try { value = decodeURIComponent(value); } catch { /* not %-encoded: keep it raw */ }
    out[part.slice(0, i).trim()] = value;
  }
  return out;
}

// Takes a request; returns its live session, or null (no cookie, unknown, or older than 10 minutes, which also ends it).
function sessionOf(req) {
  const s = sessions.get(cookies(req).sid || "");
  if (!s) return null;
  if (Date.now() - s.created > SESSION_MS) {
    sessions.delete(s.sid);
    return null;
  }
  return s;
}

// Takes the address asked for; returns where to come back to after signing in (the decision form's frame goes back to its claim).
function backTo(url) {
  const m = url.pathname.match(/^\/claims\/([0-9a-f]{8})\/decide$/);
  return m ? `/claims/${m[1]}` : url.pathname + url.search;
}

// Takes the `next` value of the sign-in form; returns it when it is an address on this site, else "/".
function safeNext(next) {
  return typeof next === "string" && /^\/(?!\/)/.test(next) && !next.startsWith("/login") ? next : "/";
}

// Takes the response, a status code, a body (text, bytes or an object for JSON), its type and extra headers; sends it.
const send = (res, code, body, type = "text/html; charset=utf-8", headers = {}) => {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store", ...headers });
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
// Takes the response, an address and extra headers; sends the browser there (303).
const redirect = (res, to, headers = {}) => send(res, 303, "", "text/plain", { Location: to, ...headers });
// Takes the response, a status code and an object; sends the object as JSON.
const json = (res, code, body) => send(res, code, body, "application/json");

// Takes a request; returns its address parsed, or null when it cannot be read. The base is fixed because the Host header is
// not trusted: a malformed one must not stop the server.
function addressOf(req) {
  try { return new URL(req.url, "http://localhost"); } catch { return null; }
}

// Takes a request; returns its body parsed as a form or as JSON (by its content type), always as an object: JSON that is not
// an object (null, a list, a number) gives {}, so the handlers answer with a message instead of failing.
function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (c) => (data += c));
    req.on("end", () => {
      if ((req.headers["content-type"] || "").includes("application/json")) {
        let parsed = {};
        try { parsed = JSON.parse(data || "{}"); } catch { parsed = {}; }
        resolve(parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {});
      } else resolve(Object.fromEntries(new URLSearchParams(data)));
    });
  });
}

// ---------------------------------------------------------------- pages (shells: the data arrives from /api, see public/app.js)

// Takes a title, the page's kind, the main content, the session (or null) and extra body attributes; returns a whole page.
function layout(title, kind, main, s, attrs = "") {
  const nav = s
    ? `<nav>${[["/", "Dashboard", "dashboard"], ["/claims", "Claims", "claims"]].map(([href, label, k]) => `<a href="${href}"${kind === k || (k === "claims" && kind === "claim") ? ' class="active"' : ""}>${label}</a>`).join("")}</nav>
       <div class="user">${esc(s.user)} · <a href="/logout">Sign out</a></div>`
    : "";
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)} · Claims Desk</title><link rel="stylesheet" href="/static/style.css"></head>
<body data-page="${kind}"${s && !s.whatsNewSeen ? ' data-whats-new="1"' : ""}${attrs}>
<header><div class="brand">Claims Desk <span>Property claims</span></div>${nav}</header>
<main id="view">${main}</main><div id="toast" role="status" aria-live="polite" hidden></div><script src="/static/app.js"></script></body></html>`;
}

// Takes the `next` address, whether the session ran out and whether the last try was wrong; returns the sign-in page.
function loginPage(next, expired, wrong) {
  return layout("Sign in", "login", `<div class="login"><h1>Sign in to Claims Desk</h1>
${expired ? `<div class="flash warn">Your session has expired. Sign in again to continue.</div>` : ""}
${wrong ? `<div class="error" role="alert">Wrong username or password.</div>` : ""}
<form method="post" action="/login"><input type="hidden" name="next" value="${esc(next)}">
<div class="field"><label for="username">Username</label><input id="username" name="username" autocomplete="username" required></div>
<div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
<button type="submit" class="primary">Sign in</button></form></div>`, null);
}

const loading = `<p class="muted">Loading…</p>`;
// Takes the session and what was not found ("Claim", "Policy", "Page"); returns the not-found page.
const notFound = (s, what) => layout("Not found", "", `<h1>${what} not found</h1><p><a href="/claims">Go to claims</a></p>`, s);

// Takes a claim; returns the document of the decision form drawn in the claim page's iframe (the form is drawn by decide.js,
// which reads the decline reasons and the documents that can be asked for from the data attributes).
function decideDoc(c) {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Decision form</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="frame"><div id="decide" data-key="${c.key}" data-claim="${esc(c.claim_no)}" data-reasons="${esc(JSON.stringify(SEED.reasons))}"
 data-documents="${esc(JSON.stringify(SEED.documents))}">${loading}</div><div id="portal"></div>
<script src="/static/decide.js"></script></body></html>`;
}

// Takes a request, the response, its address and a function that draws the page for a session; serves the page if the session
// is live (counting the load), else sends the browser to sign in and come back. The 26th load of a session ends it.
function page(req, res, url, draw, slow = false) {
  const hadCookie = Boolean(cookies(req).sid);
  const s = sessionOf(req);
  const again = `/login?${hadCookie ? "expired=1&" : ""}next=${encodeURIComponent(backTo(url))}`;
  if (!s) return redirect(res, again, hadCookie ? { "Set-Cookie": "sid=; Path=/; Max-Age=0" } : {});
  if (s.loads >= SESSION_LOADS) {
    sessions.delete(s.sid);
    return redirect(res, `/login?expired=1&next=${encodeURIComponent(backTo(url))}`, { "Set-Cookie": "sid=; Path=/; Max-Age=0" });
  }
  s.loads++;
  const out = draw(s);
  return slow ? later(() => send(res, out.code || 200, out.html || out)) : send(res, out.code || 200, out.html || out);
}

// ---------------------------------------------------------------- the API (slow on purpose)

// Takes a claim; returns what the list shows of it.
const summary = (c) => ({ key: c.key, claim_no: c.claim_no, claimant: c.claimant, peril: c.peril, loss_date: c.loss_date, amount: c.amount, status: statusOf(c) });

// Takes a claim; returns everything the claim page shows, with its decision.
const detail = (c) => ({ ...summary(c), policy_no: c.policy_no, reported_on: c.reported_on, photos: c.photos, description: c.description,
  police_report: c.police_report ?? null, decision: decisions[c.key] || null });

// Takes a decision as posted by the form; returns what is wrong with it, or "".
function validate(d) {
  if (!OUTCOMES[d.outcome]) return "Choose an outcome.";
  // Takes a value; returns whether it is a whole number of rupees, 0 or more.
  const whole = (v) => Number.isInteger(v) && v >= 0;
  if ((d.outcome === "approve" || d.outcome === "partial") && !whole(d.payout)) return "Enter the payout in whole rupees.";
  if (d.outcome === "decline" && !SEED.reasons.includes(d.reason)) return "Choose a decline reason.";
  if (d.outcome === "request_info" && !SEED.documents.includes(d.document)) return "Choose the document needed.";
  if (d.note !== undefined && typeof d.note !== "string") return "The note must be text.";
  return "";
}

// Takes the request, the response, the address and the live session; answers one /api call.
async function api(req, res, url, s) {
  const p = url.pathname;
  let m;
  if (req.method === "POST" && p === "/api/whats-new") {
    s.whatsNewSeen = true;
    return json(res, 200, { ok: true });
  }
  if (req.method === "GET" && p === "/api/stats") {
    const counts = { Pending: 0 };
    for (const label of Object.values(STATUS)) counts[label] = 0;
    for (const c of SEED.claims) counts[statusOf(c)]++;
    return later(() => json(res, 200, counts));
  }
  if (req.method === "GET" && p === "/api/claims") {
    const offset = Math.max(0, parseInt(url.searchParams.get("offset") || "0", 10) || 0);
    const limit = Math.min(BATCH, Math.max(1, parseInt(url.searchParams.get("limit") || String(BATCH), 10) || BATCH));
    const fail = failNext || Math.random() < FAIL_RATE;
    failNext = false;
    if (fail) return later(() => json(res, 503, { error: "Something went wrong" }));
    return later(() => json(res, 200, { total: listed.length, offset, claims: listed.slice(offset, offset + limit).map(summary) }));
  }
  if (req.method === "GET" && (m = p.match(/^\/api\/claims\/([0-9a-f]{8})$/))) {
    const c = claimByKey[m[1]];
    return later(() => (c ? json(res, 200, detail(c)) : json(res, 404, { error: "No such claim" })));
  }
  if (req.method === "GET" && (m = p.match(/^\/api\/policies\/(POL-\d+)$/))) {
    const pol = policyByNo[m[1]];
    return later(() => (pol ? json(res, 200, pol) : json(res, 404, { error: "No such policy" })));
  }
  if (req.method === "POST" && (m = p.match(/^\/api\/claims\/([0-9a-f]{8})\/decision$/))) {
    const c = claimByKey[m[1]];
    if (!c) return json(res, 404, { error: "No such claim" });
    const d = await readBody(req);
    const problem = validate(d);
    if (problem) return later(() => json(res, 422, { error: problem }));
    const outcome = OUTCOMES[d.outcome];
    decisions[c.key] = {
      outcome,
      payout: d.outcome === "approve" || d.outcome === "partial" ? d.payout : null,
      reason: d.outcome === "decline" ? d.reason : null,
      document: d.outcome === "request_info" ? d.document : null,
      note: (d.note || "").trim() || null,
      decided_at: new Date().toISOString(),
    };
    saveState();
    return later(() => json(res, 200, { ok: true, status: statusOf(c) }));
  }
  return json(res, 404, { error: "Not found" });
}

// ---------------------------------------------------------------- the server

const STATIC = { "/static/style.css": ["style.css", "text/css"], "/static/app.js": ["app.js", "application/javascript"], "/static/decide.js": ["decide.js", "application/javascript"] };

// Takes a request and its response; answers it (a static file, an admin call, sign-in, the API or a page).
const server = http.createServer(async (req, res) => {
  const url = addressOf(req);
  if (!url) return send(res, 400, "Bad request", "text/plain");
  const p = url.pathname;
  try {
    if (req.method === "GET" && STATIC[p]) return send(res, 200, fs.readFileSync(path.join(ROOT, "public", STATIC[p][0])), STATIC[p][1]);
    if (p === "/favicon.ico") return send(res, 204, "", "text/plain");
    // admin for the test harness: no sign-in (the site listens on 127.0.0.1 only)
    if (p === "/admin/state" && req.method === "GET") {
      const out = {};
      for (const c of SEED.claims) out[c.claim_no] = decisions[c.key] || null;
      return json(res, 200, out);
    }
    if (p === "/admin/reset" && req.method === "POST") {
      decisions = {};
      failNext = false;
      saveState();
      return json(res, 200, { reset: true });
    }
    if (p === "/admin/fail-next" && req.method === "POST") {
      failNext = true;
      return json(res, 200, { fail_next: true });
    }
    if (p === "/login" && req.method === "GET") {
      const next = safeNext(url.searchParams.get("next"));
      if (sessionOf(req)) return redirect(res, next);
      return send(res, 200, loginPage(next, url.searchParams.get("expired") === "1", false));
    }
    if (p === "/login" && req.method === "POST") {
      const f = await readBody(req);
      const next = safeNext(f.next);
      if (f.username !== SEED.login.username || f.password !== SEED.login.password) return send(res, 401, loginPage(next, false, true));
      const sid = crypto.randomBytes(18).toString("hex");
      sessions.set(sid, { sid, user: f.username, created: Date.now(), loads: 0, whatsNewSeen: false });
      return redirect(res, next, { "Set-Cookie": `sid=${sid}; Path=/; HttpOnly; SameSite=Lax` });
    }
    if (p === "/logout") {
      sessions.delete(cookies(req).sid || "");
      return redirect(res, "/login", { "Set-Cookie": "sid=; Path=/; Max-Age=0" });
    }
    if (p.startsWith("/api/")) {
      const s = sessionOf(req);
      if (!s) return json(res, 401, { error: "Your session has expired.", login: "/login?expired=1" });
      return await api(req, res, url, s);
    }
    if (req.method === "GET") {
      let m;
      if (p === "/") return page(req, res, url, (s) => layout("Dashboard", "dashboard", loading, s));
      if (p === "/claims") return page(req, res, url, (s) => layout("Claims", "claims", loading, s));
      if ((m = p.match(/^\/claims\/([0-9a-f]{8})$/))) {
        const c = claimByKey[m[1]];
        return page(req, res, url, (s) => (c ? layout(c.claim_no, "claim", loading, s, ` data-key="${c.key}"`) : { code: 404, html: notFound(s, "Claim") }));
      }
      if ((m = p.match(/^\/claims\/([0-9a-f]{8})\/decide$/))) {
        const c = claimByKey[m[1]];
        return page(req, res, url, (s) => (c ? decideDoc(c) : { code: 404, html: notFound(s, "Claim") }), true);
      }
      if ((m = p.match(/^\/policies\/(POL-\d+)$/))) {
        const pol = policyByNo[m[1]];
        return page(req, res, url, (s) => (pol ? layout(pol.policy_no, "policy", loading, s, ` data-policy="${pol.policy_no}"`) : { code: 404, html: notFound(s, "Policy") }));
      }
      return page(req, res, url, (s) => ({ code: 404, html: notFound(s, "Page") }));
    }
    send(res, 404, "Not found", "text/plain");
  } catch (err) {
    console.error(err);
    send(res, 500, "Server error", "text/plain");
  }
});

server.listen(PORT, "127.0.0.1", () => console.log(`Claims Desk on http://localhost:${PORT} (127.0.0.1 only)`));
