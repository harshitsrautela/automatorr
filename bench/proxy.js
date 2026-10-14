// Gemini meter: a local proxy every tool in the comparison sends its model calls through, so calls are counted the same way
// for all of them (one line per upstream request in logs/calls.jsonl).
//
//   node proxy.js                       # listens on http://127.0.0.1:8899
//
// A tool points its Gemini base URL at http://127.0.0.1:8899/<tool>/ (native API: .../<tool>/v1beta/models/...;
// OpenAI-compatible API: .../<tool>/v1beta/openai/chat/completions). The first path part is the tool's tag.
//
// Keys: a request whose key is "bench" (x-goog-api-key, ?key= or "Authorization: Bearer bench") gets one of the real keys
// from ../v3/.env (GEMINI_API_KEY, GEMINI_API_KEY_2, ...), in turn, paced to stay under PER_MINUTE requests per key and
// minute (the free tier allows 15 per project and model; the two keys are in different projects). A per-minute 429 is
// retried on the other key; a daily-quota 429 marks the key out for that model. Any other key is passed through untouched
// (v3 brings its own key pool).

const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.BENCH_PROXY_PORT || 8899);
const UPSTREAM = "https://generativelanguage.googleapis.com";
const PER_MINUTE = Number(process.env.BENCH_PER_MINUTE || 13);
const LOG = path.join(__dirname, "logs", "calls.jsonl");
const PLACEHOLDER = "bench";

// Reads the real keys from v3/.env (never written anywhere else, never logged).
function readKeys() {
  const env = fs.readFileSync(path.join(__dirname, "..", "v3", ".env"), "utf8");
  const keys = [];
  for (const line of env.split("\n")) {
    const m = line.match(/^\s*GEMINI_API_KEY(?:_\d+)?\s*=\s*"?([^"\s#]+)"?/);
    if (m && !keys.includes(m[1])) keys.push(m[1]);
  }
  if (!keys.length) throw new Error("no GEMINI_API_KEY in v3/.env");
  return keys;
}

const KEYS = readKeys();
const mask = (k) => "…" + k.slice(-4);
const sent = new Map(); // key -> timestamps of requests in the last minute
const coolUntil = new Map(); // key|model -> ms
const outToday = new Set(); // key|model
let turn = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Picks a key that is not out for the day and has room in its per-minute window; waits when none has.
async function pickKey(model, avoid) {
  for (;;) {
    const now = Date.now();
    const open = KEYS.filter((k) => !outToday.has(k + "|" + model));
    if (!open.length) return null;
    let soonest = 60000;
    for (let i = 0; i < open.length; i++) {
      const k = open[(turn + i) % open.length];
      if (avoid && k === avoid && open.length > 1) continue;
      const times = (sent.get(k) || []).filter((t) => now - t < 60000);
      sent.set(k, times);
      const cool = coolUntil.get(k + "|" + model) || 0;
      if (cool > now) {
        soonest = Math.min(soonest, cool - now);
        continue;
      }
      if (times.length < PER_MINUTE) {
        times.push(now);
        turn++;
        return k;
      }
      soonest = Math.min(soonest, 60000 - (now - times[0]) + 50);
    }
    await sleep(Math.max(200, soonest));
  }
}

function log(entry) {
  fs.appendFileSync(LOG, JSON.stringify(entry) + "\n");
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function modelOf(urlPath, body) {
  const m = urlPath.match(/models\/([^:/?]+)/);
  if (m) return m[1];
  try {
    return JSON.parse(body.toString()).model || "?";
  } catch {
    return "?";
  }
}

const server = http.createServer(async (req, res) => {
  const started = Date.now();
  const url = new URL(req.url, "http://x");
  const parts = url.pathname.split("/").filter(Boolean);
  const tool = parts.shift() || "?";
  const upstreamPath = "/" + parts.join("/");
  const body = await readBody(req);
  const model = modelOf(upstreamPath, body);

  const headers = { ...req.headers };
  delete headers.host;
  delete headers["content-length"];
  delete headers["accept-encoding"];
  const given =
    headers["x-goog-api-key"] ||
    url.searchParams.get("key") ||
    (headers.authorization || "").replace(/^Bearer\s+/i, "");
  const managed = given === PLACEHOLDER;

  let attempt = 0;
  let avoid = null;
  for (;;) {
    attempt++;
    let key = given;
    if (managed) {
      key = await pickKey(model, avoid);
      if (!key) {
        log({ at: new Date().toISOString(), tool, model, status: 429, note: "every key out of daily quota", ms: 0 });
        res.writeHead(429, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: { code: 429, message: "bench proxy: every key is out of daily quota for " + model, status: "RESOURCE_EXHAUSTED" } }));
        return;
      }
    }
    const target = new URL(UPSTREAM + upstreamPath + url.search);
    const h = { ...headers };
    if (target.searchParams.has("key")) target.searchParams.set("key", key);
    if (h["x-goog-api-key"]) h["x-goog-api-key"] = key;
    if (h.authorization) h.authorization = "Bearer " + key;
    if (!h["x-goog-api-key"] && !h.authorization && !target.searchParams.has("key")) h["x-goog-api-key"] = key;

    let up;
    try {
      up = await fetch(target, { method: req.method, headers: h, body: ["GET", "HEAD"].includes(req.method) ? undefined : body });
    } catch (err) {
      log({ at: new Date().toISOString(), tool, model, status: 0, note: String(err).slice(0, 200), key: mask(key), ms: Date.now() - started });
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { code: 502, message: "bench proxy: " + err } }));
      return;
    }
    const entry = { at: new Date().toISOString(), tool, model, status: up.status, key: mask(key), attempt, path: upstreamPath.slice(0, 80) };

    if (up.status === 429 && managed) {
      const text = await up.text();
      const daily = /PerDay/i.test(text);
      if (daily) outToday.add(key + "|" + model);
      else coolUntil.set(key + "|" + model, Date.now() + 45000);
      log({ ...entry, note: daily ? "daily quota" : "per-minute limit", ms: Date.now() - started });
      if (attempt < 8) {
        avoid = key;
        continue;
      }
      res.writeHead(429, { "content-type": "application/json" });
      res.end(text);
      return;
    }

    const outHeaders = {};
    up.headers.forEach((v, k) => {
      if (!["content-encoding", "content-length", "transfer-encoding", "connection"].includes(k)) outHeaders[k] = v;
    });
    res.writeHead(up.status, outHeaders);
    let usage = null;
    let tail = "";
    if (up.body) {
      for await (const chunk of up.body) {
        res.write(chunk);
        tail = (tail + Buffer.from(chunk).toString()).slice(-4000);
      }
    }
    res.end();
    const pt = tail.match(/"promptTokenCount"\s*:\s*(\d+)/g) || tail.match(/"prompt_tokens"\s*:\s*(\d+)/g);
    const ct = tail.match(/"candidatesTokenCount"\s*:\s*(\d+)/g) || tail.match(/"completion_tokens"\s*:\s*(\d+)/g);
    if (pt) usage = { in: Number(pt[pt.length - 1].match(/\d+/)[0]), out: ct ? Number(ct[ct.length - 1].match(/\d+/)[0]) : null };
    log({ ...entry, ms: Date.now() - started, usage });
    return;
  }
});

server.listen(PORT, "127.0.0.1", () => console.log(`bench proxy on http://127.0.0.1:${PORT} (${KEYS.length} keys, ${PER_MINUTE}/min/key)`));
