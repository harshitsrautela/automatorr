// Stagehand contender for the AP Desk comparison: runs each bench record, one at a time, through
// stagehand.agent().execute(<task text>) with Stagehand's own agent cache turned on (cacheDir).
//
//   ./run.sh --clear-cache                 # all 5 records, variables mode (default), fresh cache
//   ./run.sh --limit 2                     # first 2 records
//   ./run.sh --only INV-26-2820            # one record (comma-separate for several)
//   ./run.sh --mode text                   # exact bench task text per record (cache key differs per record -> no reuse)
//   ./run.sh --dry-run                     # print instructions + cache-key inputs, no browser, no model calls
//
// Options: --mode variables|text, --agent-mode dom|hybrid, --max-steps N (default 40), --clear-cache, --no-cache,
//          --headed, --verbose 0|1|2
//
// Writes results/<UTC timestamp>.json and results/<UTC timestamp>.summary.txt.

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import type { LogLine } from "@browserbasehq/stagehand";
import { BENCH, HERE, MODEL, BASE_URL, TOOL_TAG, calls, chromePath, makeStagehand } from "./lib.ts";

// ---------- args ----------
const argv = process.argv.slice(2);
function opt(name: string): string | undefined {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
}
const flag = (name: string) => argv.includes(name);

const mode = (opt("--mode") ?? "variables") as "variables" | "text";
const agentMode = (opt("--agent-mode") ?? "dom") as "dom" | "hybrid";
const maxSteps = Number(opt("--max-steps") ?? 40);
const limit = opt("--limit") ? Number(opt("--limit")) : undefined;
const only = opt("--only")?.split(",").map((s) => s.trim()).filter(Boolean);
const useCache = !flag("--no-cache");
const clearCache = flag("--clear-cache");
const headed = flag("--headed");
const dryRun = flag("--dry-run");
const verbose = Number(opt("--verbose") ?? 1) as 0 | 1 | 2;
if (!["variables", "text"].includes(mode)) throw new Error(`--mode must be variables or text, got ${mode}`);
if (!["dom", "hybrid"].includes(agentMode)) throw new Error(`--agent-mode must be dom or hybrid, got ${agentMode}`);

const CACHE_DIR = path.join(HERE, "cache");
const RESULTS_DIR = path.join(HERE, "results");
const CALLS_LOG = path.join(BENCH, "logs", "calls.jsonl");

// ---------- records + task text ----------
type Rec = { invoice_no: string };
let records: Rec[] = JSON.parse(fs.readFileSync(path.join(BENCH, "records.json"), "utf8"));
if (only) records = records.filter((r) => only.includes(r.invoice_no));
if (limit !== undefined) records = records.slice(0, limit);
if (!records.length) throw new Error("no records selected");

const taskText = (inv: string) => fs.readFileSync(path.join(BENCH, "tasks", `${inv}.txt`), "utf8");

// variables mode: the bench text with the record's invoice number replaced by Stagehand's %invoice_no% variable token.
// Every record then gives the same instruction string, which is what Stagehand's agent cache keys on.
function buildInstruction(inv: string): { instruction: string; variables?: Record<string, any> } {
  const text = taskText(inv);
  if (mode === "text") return { instruction: text };
  if (!text.includes(inv)) throw new Error(`task text for ${inv} does not contain its invoice number`);
  return {
    instruction: text.split(inv).join("%invoice_no%"),
    variables: { invoice_no: { value: inv, description: "the record's invoice number (type it as %invoice_no%)" } },
  };
}

const sha = (s: string) => crypto.createHash("sha256").update(s).digest("hex").slice(0, 16);
const cacheFiles = () => (fs.existsSync(CACHE_DIR) ? fs.readdirSync(CACHE_DIR).filter((f) => f.endsWith(".json")).sort() : []);

// ---------- proxy log ----------
function proxyCalls(fromIso: string, toIso: string) {
  if (!fs.existsSync(CALLS_LOG)) return { all: 0, ok: 0, tokens_in: 0, tokens_out: 0 };
  const rows = fs
    .readFileSync(CALLS_LOG, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l))
    .filter((r) => r.tool === TOOL_TAG && r.at >= fromIso && r.at <= toIso);
  return {
    all: rows.length,
    ok: rows.filter((r) => r.status >= 200 && r.status < 300).length,
    tokens_in: rows.reduce((a, r) => a + (r.usage?.in ?? 0), 0),
    tokens_out: rows.reduce((a, r) => a + (r.usage?.out ?? 0), 0),
  };
}

// ---------- dry run ----------
if (dryRun) {
  console.log(`mode=${mode} agent-mode=${agentMode} model=${MODEL} baseURL=${BASE_URL} chrome=${chromePath() ?? "(chrome-launcher default)"}`);
  for (const r of records) {
    const { instruction, variables } = buildInstruction(r.invoice_no);
    const keyInputs = JSON.stringify({ instruction: instruction.trim(), variableKeys: variables ? Object.keys(variables).sort() : [] });
    console.log(`${r.invoice_no}: instruction sha=${sha(instruction.trim())} cache-key-inputs sha=${sha(keyInputs)} vars=${variables ? Object.keys(variables).join(",") : "-"}`);
  }
  console.log("\n--- instruction for the first record ---\n" + buildInstruction(records[0].invoice_no).instruction);
  process.exit(0);
}

// ---------- run ----------
if (clearCache && fs.existsSync(CACHE_DIR)) fs.rmSync(CACHE_DIR, { recursive: true, force: true });
if (useCache) fs.mkdirSync(CACHE_DIR, { recursive: true });
fs.mkdirSync(RESULTS_DIR, { recursive: true });

const stamp = new Date().toISOString().replace(/[:.]/g, "-").replace(/-\d{3}Z$/, "Z");
const runStarted = new Date().toISOString();
const cacheAtStart = useCache ? cacheFiles() : [];
if (cacheAtStart.length) console.error(`note: cache/ already holds ${cacheAtStart.length} entr${cacheAtStart.length === 1 ? "y" : "ies"} (use --clear-cache for a from-scratch run)`);

const out: any[] = [];
for (const [i, r] of records.entries()) {
  const inv = r.invoice_no;
  const { instruction, variables } = buildInstruction(inv);
  const logs: LogLine[] = [];
  const cacheBefore = useCache ? cacheFiles() : [];
  console.error(`\n[${i + 1}/${records.length}] ${inv}  (mode=${mode}, agent=${agentMode})`);

  const sh = makeStagehand({ cacheDir: useCache ? CACHE_DIR : undefined, headless: !headed, verbose, onLog: (l) => logs.push(l) });
  calls.n = 0;
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  let result: any = null;
  let error: string | null = null;
  let metrics: any = null;
  try {
    await sh.init();
    // Same start page for every record (it is part of Stagehand's agent cache key).
    await sh.context.pages()[0].goto("about:blank");
    const agent = sh.agent({ mode: agentMode });
    result = await agent.execute({ instruction, maxSteps, ...(variables ? { variables } : {}) });
  } catch (e: any) {
    error = String(e?.stack ?? e).slice(0, 2000);
  }
  try {
    metrics = await sh.metrics;
  } catch {}
  try {
    await sh.close();
  } catch {}
  const endedAt = new Date().toISOString();
  await new Promise((res) => setTimeout(res, 1500)); // let the proxy finish writing its log line
  const proxy = proxyCalls(startedAt, new Date(Date.now()).toISOString());

  const cacheMsgs = logs.filter((l) => l.category === "cache").map((l) => l.message);
  const cacheHit = result?.metadata?.cacheHit === true;
  const actions = (result?.actions ?? []) as any[];
  const actionTypes: Record<string, number> = {};
  for (const a of actions) actionTypes[a.type] = (actionTypes[a.type] ?? 0) + 1;
  const cacheAfter = useCache ? cacheFiles() : [];

  const row = {
    invoice_no: inv,
    started_at: startedAt,
    ended_at: endedAt,
    seconds: Math.round((Date.now() - t0) / 100) / 10,
    success: !!result?.success && !!result?.completed && !error,
    agent_success: result?.success ?? null,
    agent_completed: result?.completed ?? null,
    cache_hit: cacheHit,
    // On a cache hit Stagehand returns record 1's stored AgentResult: the message and actions below are the cached copy,
    // not something produced for this record.
    cached_entry_timestamp: result?.metadata?.cacheTimestamp ?? null,
    cache_events: cacheMsgs,
    cache_entries_written: cacheAfter.filter((f) => !cacheBefore.includes(f)),
    steps: actions.length,
    action_types: actionTypes,
    actions: actions.map((a) => ({
      type: a.type,
      ...(a.instruction ? { instruction: String(a.instruction).slice(0, 300) } : {}),
      ...(a.action ? { action: String(a.action).slice(0, 300) } : {}),
      ...(a.url ? { url: a.url } : {}),
      ...(a.reasoning ? { reasoning: String(a.reasoning).slice(0, 300) } : {}),
      ...(a.timeMs ? { timeMs: a.timeMs } : {}),
    })),
    final_message: result?.message ?? null,
    error,
    model_calls_counted_locally: calls.n, // Stagehand's model middleware: every generate/stream call it made
    proxy_calls: proxy, // bench proxy log, tool=stagehand, inside this record's time window
    stagehand_usage: result?.usage ?? null, // AgentResult.usage (zeroed on cache replay)
    stagehand_metrics: metrics, // stagehand.metrics (per-instance, so per record)
    stagehand_errors: logs.filter((l) => l.level === 0).map((l) => `${l.category ?? ""}: ${l.message}`).slice(0, 30),
  };
  out.push(row);
  console.error(
    `  -> success=${row.success} cache_hit=${row.cache_hit} steps=${row.steps} calls(local)=${row.model_calls_counted_locally} calls(proxy)=${proxy.all} ${row.seconds}s` +
      (error ? `\n  error: ${error.split("\n")[0]}` : "") +
      `\n  message: ${(row.final_message ?? "").slice(0, 400)}`,
  );
}

// ---------- summary ----------
const summary = {
  records: out.length,
  succeeded_by_agent: out.filter((r) => r.success).length,
  cache_hits: out.filter((r) => r.cache_hit).length,
  total_seconds: Math.round(out.reduce((a, r) => a + r.seconds, 0) * 10) / 10,
  model_calls_local: out.reduce((a, r) => a + r.model_calls_counted_locally, 0),
  proxy_calls: out.reduce((a, r) => a + r.proxy_calls.all, 0),
  proxy_tokens_in: out.reduce((a, r) => a + r.proxy_calls.tokens_in, 0),
  proxy_tokens_out: out.reduce((a, r) => a + r.proxy_calls.tokens_out, 0),
  reusable_artifact: useCache ? cacheFiles().map((f) => path.join("cache", f)) : [],
  note: "agent success is the agent's own claim; the site's real score comes from: python3 ../bench/grade.py",
};
const report = {
  tool: "stagehand",
  stagehand_version: JSON.parse(fs.readFileSync(path.join(HERE, "node_modules", "@browserbasehq", "stagehand", "package.json"), "utf8")).version,
  started_at: runStarted,
  ended_at: new Date().toISOString(),
  config: { mode, agent_mode: agentMode, max_steps: maxSteps, model: MODEL, base_url: BASE_URL, cache: useCache ? CACHE_DIR : null, cache_entries_at_start: cacheAtStart, chrome: chromePath() ?? null },
  summary,
  records: out,
};
const jsonPath = path.join(RESULTS_DIR, `${stamp}.json`);
fs.writeFileSync(jsonPath, JSON.stringify(report, null, 2));

const lines = [
  `Stagehand ${report.stagehand_version}  mode=${mode} agent=${agentMode} model=${MODEL}  ${runStarted}`,
  `${"invoice".padEnd(12)} ${"ok".padEnd(5)} ${"cache".padEnd(5)} ${"steps".padStart(5)} ${"calls".padStart(5)} ${"proxy".padStart(5)} ${"sec".padStart(6)}  message`,
  ...out.map(
    (r) =>
      `${r.invoice_no.padEnd(12)} ${String(r.success).padEnd(5)} ${(r.cache_hit ? "HIT" : "-").padEnd(5)} ${String(r.steps).padStart(5)} ${String(r.model_calls_counted_locally).padStart(5)} ${String(r.proxy_calls.all).padStart(5)} ${String(r.seconds).padStart(6)}  ${(r.error ? "ERROR " + r.error.split("\n")[0] : r.final_message ?? "").replace(/\s+/g, " ").slice(0, 140)}`,
  ),
  `total: ${summary.succeeded_by_agent}/${summary.records} agent-success, ${summary.cache_hits} cache hit(s), ${summary.model_calls_local} model calls (proxy ${summary.proxy_calls}), ${summary.total_seconds}s`,
  `cache entries: ${summary.reusable_artifact.join(", ") || "none"}`,
  `grade on the site: python3 ../bench/grade.py`,
];
fs.writeFileSync(path.join(RESULTS_DIR, `${stamp}.summary.txt`), lines.join("\n") + "\n");
console.log("\n" + lines.join("\n"));
console.log(`\nwrote ${path.relative(HERE, jsonPath)}`);
