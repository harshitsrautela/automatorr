import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api.js";
import { navigate } from "../lib/router.js";
import "./logs.css";

// ---------------------------------------------------------------- icons (the v2 set, one per log category)
const ICON_PATHS = {
  hit: <path d="M3 8.3l3.2 3.2L13 4.7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
  miss: (
    <>
      <path d="M8 1.8l6.3 11H1.7L8 1.8z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M8 6.4v3M8 11.2h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  error: (
    <>
      <circle cx="8" cy="8" r="6.3" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),
  step: <path d="M6 3.3l5 4.7-5 4.7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />,
  record: (
    <>
      <circle cx="8" cy="5.6" r="2.4" stroke="currentColor" strokeWidth="1.3" />
      <path d="M3 13c0-2.5 2.2-4 5-4s5 1.5 5 4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </>
  ),
  answer: <path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />,
  batch: (
    <>
      <path d="M8 2l6 3.2-6 3.2-6-3.2L8 2z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M2 8.6l6 3.2 6-3.2" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M2 11.8l6 3.2 6-3.2" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
    </>
  ),
};
const ICON_COLOR = { hit: "green", miss: "yellow", error: "red", step: "blue", record: "purple", answer: "purple", batch: "muted" };

// Draws one category icon.
function Icon({ cat }) {
  return (
    <span className="icon" style={{ color: `var(--${ICON_COLOR[cat] ?? "muted"})` }}>
      <svg viewBox="0 0 16 16" fill="none">{ICON_PATHS[cat] ?? ICON_PATHS.batch}</svg>
    </span>
  );
}

// Only these categories are switched by the filter chips; record, answer and batch lines always stay visible.
const FILTERABLE = ["hit", "miss", "error", "step"];
const CHIPS = [
  { key: "step", label: "Steps" },
  { key: "hit", label: "Hits" },
  { key: "miss", label: "Misses" },
  { key: "error", label: "Errors" },
];

const MODE_TEXT = { cold: "learning, using the live agent", live: "live agent", replay: "replaying from cache", recipe: "using the learned recipe (one AI call)", plan: "using the learned plan (a few small AI calls)" };
const MODE_PILL = { cold: "Learning", live: "Live", replay: "Replay", recipe: "Recipe", plan: "Plan" };
const STATUS_LABEL = { running: "Running", verifying: "Checking results", done: "Finished", aborted: "Stopped", error: "Failed" };

// Formats an ISO time as 24-hour hh:mm:ss.
function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString("en-GB", { hour12: false }) : "";
}

// Formats a number of seconds as m:ss.
function fmtElapsed(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Turns a run's event list into log lines, running totals, the "now doing" card and reconciliation progress.
function buildLog(events) {
  const idxById = {};
  const nameById = {};
  const lines = [];
  const totals = { succeeded: 0, failed: 0, hits: 0, misses: 0, llm: 0 };
  let now = null;
  let recon = null;
  let latestName = null;
  let doneCount = 0;

  const tag = (rid) => (idxById[rid] ? `#${idxById[rid]}` : rid ?? "");
  const label = (rid, word) => (tag(rid) ? `${tag(rid)} ${word}` : word);
  const add = (e, cat, tagText, msg, extra = {}) => lines.push({ id: e.seq, ts: fmtTime(e.at), cat, tag: tagText, msg, ...extra });

  for (const e of events) {
    switch (e.type) {
      case "note":
        add(e, "batch", "RECIPE", e.message);
        break;
      case "batch_start":
        add(e, "batch", "BATCH", `Starting batch of ${e.total} record${e.total === 1 ? "" : "s"}.`);
        break;
      case "record_start":
        idxById[e.record_id] = e.index;
        nameById[e.record_id] = e.name;
        latestName = e.name;
        now = { mode: e.mode, text: `${e.name} — ${MODE_TEXT[e.mode] ?? e.mode}`, strong: e.name };
        add(e, "record", `#${e.index}/${e.total}`, `${e.name} — ${MODE_TEXT[e.mode] ?? e.mode}`, { divider: true });
        break;
      case "step": {
        const subs = [];
        for (const a of e.actions ?? []) subs.push({ kind: "action", text: `→ ${a}` });
        if (e.evaluation) subs.push({ kind: "reason", text: e.evaluation });
        if (e.memory) subs.push({ kind: "reason", text: `memory: ${e.memory}` });
        if (e.thinking) subs.push({ kind: "reason", text: e.thinking });
        if (e.instruction) now = { ...(now ?? {}), mode: now?.mode, text: e.instruction, strong: e.instruction };
        add(e, "step", label(e.record_id, "step"), e.instruction || `(step ${e.step_index})`, { subs });
        break;
      }
      case "field_hit":
        totals.hits++;
        now = { ...(now ?? {}), text: `${e.field} — cache hit`, strong: e.field };
        add(e, "hit", label(e.record_id, "hit"), `${e.field}`);
        break;
      case "field_miss":
        totals.misses++;
        if (e.reason === "circuit_open_skipped") {
          add(e, "miss", label(e.record_id, "skip"), `${e.field} — failing repeatedly, not retrying right now`);
        } else {
          now = { ...(now ?? {}), text: `${e.field} — cache miss (${e.reason || "unknown"}), asking the AI`, strong: e.field };
          add(e, "miss", label(e.record_id, "miss"), `${e.field} — ${e.reason || "unknown"} — falling back to the live agent`);
        }
        break;
      case "error":
        add(e, "error", label(e.record_id, "error"), `${e.field ? e.field + ": " : ""}${e.message}`);
        break;
      case "record_done": {
        doneCount++;
        if (e.success) totals.succeeded++;
        else totals.failed++;
        totals.llm += e.llm_steps || 0;
        add(
          e,
          e.success ? "hit" : "error",
          e.success ? "done" : "FAILED",
          `Record ${e.index ?? tag(e.record_id)}${e.total ? `/${e.total}` : ""} (${e.record_id}) — ${e.mode}, ${e.cache_hits ?? 0} hits / ${e.cache_misses ?? 0} misses, ${e.llm_steps ?? 0} AI steps${e.success ? "" : " — " + (e.error_message || "failed")}`
        );
        if (e.summary) add(e, "answer", label(e.record_id, "answer"), e.summary);
        break;
      }
      case "verification_done": {
        const note = e.aborted ? " (the run was stopped early)" : "";
        add(e, "batch", "VERIFY", `Database check: ${e.correct_fields}/${e.total_fields} correct (${(e.field_accuracy * 100).toFixed(1)}%), ${e.unmatched_records} not found.${note}`);
        break;
      }
      case "batch_done":
        add(e, "batch", "BATCH", `${e.aborted ? "Stopped" : "Done"}: ${e.succeeded} succeeded, ${e.failed} failed.`);
        break;
      case "batch_error":
        add(e, "error", "ERROR", e.message);
        break;
      case "reconciliation_start":
        recon = { total: e.total, fixed: 0, skipped: 0, text: `Retrying ${e.total} field(s) that landed incorrectly, through the site's edit form.` };
        add(e, "batch", "RECONCILE", recon.text);
        break;
      case "reconciliation_fix":
        if (recon) e.success ? recon.fixed++ : recon.skipped++;
        add(e, e.success ? "hit" : "miss", e.success ? "fixed" : "still broken", `${e.record_id} / ${e.field}`);
        break;
      case "reconciliation_done":
        if (recon) recon.text = `Cleanup pass finished — ${e.fixed} fixed, ${e.skipped} still not fixable right now.`;
        add(e, "batch", "RECONCILE", `Cleanup pass finished: ${e.fixed} fixed, ${e.skipped} still not fixable right now.`);
        break;
      case "reconciliation_error":
        add(e, "error", "RECONCILE", `Cleanup pass failed: ${e.message}`);
        break;
      default:
        break;
    }
  }
  return { lines, totals, now, recon, doneCount, latestName, nameById, idxById };
}

// Flattens a verification report into table rows: every wrong field, and one row per fully-correct record.
function resultRows(report, idxById, nameById) {
  if (!report) return [];
  const rows = [];
  for (const rv of report.per_record_results) {
    const who = { idx: idxById[rv.record_id] ?? Number.MAX_SAFE_INTEGER, record: nameById[rv.record_id] ?? rv.record_id };
    const fields = rv.field_results ?? [];
    const wrong = fields.filter((f) => !f.passed);
    if (!rv.matched_in_db) rows.push({ ...who, field: "—", expected: "—", actual: "not found", ok: false, note: rv.match_reason });
    else if (wrong.length) wrong.forEach((f) => rows.push({ ...who, field: f.field_name, expected: f.expected_value, actual: f.actual_value, ok: false, note: f.reason }));
    else if (fields.length === 1) rows.push({ ...who, field: fields[0].field_name, expected: fields[0].expected_value, actual: fields[0].actual_value, ok: true });
    else rows.push({ ...who, field: `all ${fields.length} fields`, expected: "", actual: "", ok: true });
  }
  return rows.sort((a, b) => a.idx - b.idx);
}

// One summary tile; it pulses briefly when its number changes.
function Stat({ cls, value, label }) {
  const [bump, setBump] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current !== value) {
      prev.current = value;
      setBump(true);
      const t = setTimeout(() => setBump(false), 350);
      return () => clearTimeout(t);
    }
  }, [value]);
  return (
    <div className={`lg-stat ${cls} ${bump ? "bump" : ""}`}>
      <div className="num">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

// The Logs window: one run at a time, in the v2 dashboard's dark glass style.
export default function LogsPage({ path }) {
  const routeId = path.split("/")[2] ? Number(path.split("/")[2]) : null;
  const [runs, setRuns] = useState(null);
  const [run, setRun] = useState(null);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [filters, setFilters] = useState(() => new Set(FILTERABLE));
  const [showReasoning, setShowReasoning] = useState(false);
  const [onlyWrong, setOnlyWrong] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const [celebrate, setCelebrate] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const [copied, setCopied] = useState("");
  const [aborting, setAborting] = useState(false);
  const logRef = useRef(null);
  const nearBottom = useRef(true);
  const lastStatus = useRef(null);

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await api.listRuns());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    loadRuns();
    const t = setInterval(loadRuns, 3000);
    return () => clearInterval(t);
  }, [loadRuns]);

  const activeId = routeId ?? runs?.[0]?.id ?? null;

  // Follows the selected run: fetch it and any new events every second until it has finished.
  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    let timer;
    let after = 0;
    let finalPass = false;
    setRun(null);
    setEvents([]);
    setAborting(false);
    lastStatus.current = null;
    nearBottom.current = true;

    async function poll() {
      try {
        const [r, ev] = await Promise.all([api.getRun(activeId), api.runEvents(activeId, after)]);
        if (stop) return;
        setRun(r);
        setError("");
        if (ev.events.length) {
          after = ev.next;
          setEvents((prev) => [...prev, ...ev.events]);
        }
        const active = r.status === "running" || r.status === "verifying";
        if (!active && ev.events.length === 0) {
          if (finalPass) return;
          finalPass = true; // one more look in case the last events landed just after the status changed
        } else {
          finalPass = false;
        }
      } catch (e) {
        if (!stop) setError(e.message);
      }
      if (!stop) timer = setTimeout(poll, 1000);
    }
    poll();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [activeId]);

  const active = run && (run.status === "running" || run.status === "verifying");

  // Ticks every second while a run is active so the timer moves.
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);

  const model = useMemo(() => buildLog(events), [events]);
  const rows = useMemo(() => resultRows(run?.report, model.idxById, model.nameById), [run, model]);
  const visibleRows = onlyWrong ? rows.filter((r) => !r.ok) : rows;
  const wrongCount = rows.filter((r) => !r.ok).length;

  // A brief green glow when a run ends with every record succeeding.
  useEffect(() => {
    if (!run) return;
    if (lastStatus.current && ["running", "verifying"].includes(lastStatus.current) && run.status === "done" && model.totals.failed === 0 && model.totals.succeeded > 0) {
      setCelebrate(true);
      const t = setTimeout(() => setCelebrate(false), 2600);
      return () => clearTimeout(t);
    }
    lastStatus.current = run.status;
  }, [run, model.totals]);

  const visibleLines = model.lines.filter((l) => !FILTERABLE.includes(l.cat) || filters.has(l.cat));

  // Follows new lines only when the reader hasn't scrolled up; otherwise offers a "New activity" button.
  useLayoutEffect(() => {
    const el = logRef.current;
    if (!el) return;
    if (nearBottom.current) {
      el.scrollTop = el.scrollHeight;
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [visibleLines.length]);

  function onLogScroll() {
    const el = logRef.current;
    nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (nearBottom.current) setShowJump(false);
  }

  function toggleFilter(key) {
    setFilters((f) => {
      const next = new Set(f);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const plainLog = () => model.lines.map((l) => `[${l.ts}] ${l.tag}  ${l.msg}`).join("\n");

  async function copyLog() {
    try {
      await navigator.clipboard.writeText(plainLog());
      setCopied("copy");
      setTimeout(() => setCopied(""), 600);
    } catch {
      setError("Could not copy; your browser blocked clipboard access.");
    }
  }

  function downloadLog() {
    const url = URL.createObjectURL(new Blob([plainLog()], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `terminautomator-run-${run?.id ?? "log"}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setCopied("download");
    setTimeout(() => setCopied(""), 600);
  }

  async function abort() {
    setAborting(true);
    try {
      await api.abortRun(run.id);
    } catch (e) {
      setError(e.message);
      setAborting(false);
    }
  }

  // ---- derived display values
  const total = run?.total ?? 0;
  const { totals, doneCount } = model;
  const hitDenom = totals.hits + totals.misses;
  const finished = run && !active;
  const failedState = run && (run.status === "error" || run.status === "aborted");
  const dotClass = !run ? "" : active ? "running" : failedState ? "error" : "done";
  let statusText = "Idle";
  if (run) {
    if (run.status === "running") statusText = `Running — ${doneCount} / ${total}${model.latestName ? `: ${model.latestName}` : ""}`;
    else if (run.status === "verifying") statusText = "Checking results against the database…";
    else if (run.status === "done") statusText = `Done — ${totals.succeeded} succeeded, ${totals.failed} failed`;
    else if (run.status === "aborted") statusText = `Stopped — ${totals.succeeded} succeeded, ${totals.failed} failed`;
    else statusText = `Failed — ${run.error ?? "see the log"}`;
  }
  const progress = total ? Math.min(100, (doneCount / total) * 100) : 0;
  const startMs = run ? new Date(run.created_at).getTime() : 0;
  const endMs = finished && run.finished_at ? new Date(run.finished_at).getTime() : tick;
  const accuracy = run?.accuracy;

  return (
    <div className="lg">
      <div className="lg-blob a" />
      <div className="lg-blob b" />
      <div className="lg-blob c" />
      <div className="lg-wrap">
        <div className="lg-header">
          <div className="lg-title">
            <h1>Logs</h1>
            <p>{run ? `${run.workflow_name} · run #${run.id}` : "Every run, live."}</p>
          </div>
          <div className="lg-header-right">
            {runs && runs.length > 0 && (
              <select className="lg-select" value={activeId ?? ""} onChange={(e) => navigate(`/logs/${e.target.value}`)} aria-label="Choose a run">
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} · {r.workflow_name} · {STATUS_LABEL[r.status]}
                    {r.accuracy !== null && r.accuracy !== undefined ? ` · ${Math.round(r.accuracy * 100)}%` : ""}
                  </option>
                ))}
              </select>
            )}
            {run?.status === "running" && (
              <button type="button" className="lg-abort" onClick={abort} disabled={aborting}>
                {aborting ? "Aborting…" : "Abort"}
              </button>
            )}
          </div>
        </div>

        {runs && runs.length === 0 && (
          <div className="lg-panel lg-empty-panel">
            <h2>No runs yet</h2>
            <p>Runs appear here live. Start one from the Agent page.</p>
            <button type="button" className="lg-btn" onClick={() => navigate("/agent")}>
              Go to Agent
            </button>
          </div>
        )}

        {error && <div className="lg-banner">{error}</div>}
        {run?.error && run.status !== "running" && <div className="lg-banner">{run.error}</div>}

        {run && (
          <>
            <div className="lg-panel">
              <div className="lg-panel-head">
                <h2>Status</h2>
                <span className="lg-elapsed">
                  <svg viewBox="0 0 16 16" fill="none">
                    <circle cx="8" cy="8" r="6.2" stroke="currentColor" strokeWidth="1.3" />
                    <path d="M8 4.6V8l2.4 1.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                  </svg>
                  {fmtElapsed((endMs - startMs) / 1000)}
                </span>
              </div>
              <div className="lg-status-line">
                <span className={`lg-dot ${dotClass}`} />
                <span>{statusText}</span>
              </div>
              <div className="lg-bar-outer">
                <div className={`lg-bar-inner ${failedState ? "error" : ""}`} style={{ width: `${finished && run.status === "done" ? 100 : progress}%` }} />
              </div>
              {active && model.now && (
                <div className="lg-now">
                  {model.now.mode && <span className={`lg-pill ${model.now.mode}`}>{MODE_PILL[model.now.mode] ?? model.now.mode}</span>}
                  <span className="lg-now-text">
                    <b>{model.now.text}</b>
                  </span>
                </div>
              )}
            </div>

            <div className={`lg-panel ${celebrate ? "celebrate" : ""}`}>
              <h2>Summary</h2>
              <div className="lg-grid">
                <Stat cls="green" value={totals.succeeded} label="Succeeded" />
                <Stat cls="red" value={totals.failed} label="Failed" />
                <Stat cls="blue" value={totals.hits} label="Cache hits" />
                <Stat cls="" value={totals.misses} label="Cache misses" />
                <Stat cls="" value={totals.llm} label="AI steps" />
                <Stat cls="purple" value={hitDenom ? `${Math.round((totals.hits / hitDenom) * 100)}%` : "—"} label="Hit rate" />
                <Stat cls={accuracy === null || accuracy === undefined ? "" : accuracy === 1 ? "green" : "red"} value={accuracy === null || accuracy === undefined ? "—" : `${(accuracy * 100).toFixed(1)}%`} label="Accuracy" />
              </div>
            </div>

            {model.recon && (
              <div className="lg-panel">
                <h2>Reconciliation</h2>
                <div className="lg-recon-line">{model.recon.text}</div>
                <div className="lg-bar-outer">
                  <div className="lg-bar-inner" style={{ width: `${model.recon.total ? ((model.recon.fixed + model.recon.skipped) / model.recon.total) * 100 : 0}%` }} />
                </div>
                <div className="lg-recon-counts">
                  <span className="fixed">
                    Fixed: <b>{model.recon.fixed}</b>
                  </span>
                  <span className="skipped">
                    Still open: <b>{model.recon.skipped}</b>
                  </span>
                </div>
              </div>
            )}

            <div className="lg-panel">
              <div className="lg-panel-head">
                <h2>Live log</h2>
                <div className="lg-toolbar">
                  <span className={`lg-chip ${filters.size === FILTERABLE.length ? "active" : ""}`} onClick={() => setFilters(new Set(FILTERABLE))}>
                    All
                  </span>
                  {CHIPS.map((c) => (
                    <span key={c.key} data-cat={c.key} className={`lg-chip ${filters.has(c.key) ? "active" : ""}`} onClick={() => toggleFilter(c.key)}>
                      {c.label}
                    </span>
                  ))}
                  <span data-cat="reasoning" className={`lg-chip ${showReasoning ? "active" : ""}`} onClick={() => setShowReasoning((v) => !v)} title="Show what the agent thought about each step">
                    Reasoning
                  </span>
                  <span className="lg-spacer" />
                  <span className="lg-icon-btn" onClick={copyLog} title="Copy log" style={copied === "copy" ? { color: "var(--green)" } : undefined}>
                    <svg viewBox="0 0 16 16" fill="none">
                      <rect x="5.5" y="5.5" width="8" height="8" rx="1.4" stroke="currentColor" strokeWidth="1.3" />
                      <path d="M3.5 10.5V3.9A1.4 1.4 0 0 1 4.9 2.5h6.1" stroke="currentColor" strokeWidth="1.3" />
                    </svg>
                  </span>
                  <span className="lg-icon-btn" onClick={downloadLog} title="Download log" style={copied === "download" ? { color: "var(--green)" } : undefined}>
                    <svg viewBox="0 0 16 16" fill="none">
                      <path d="M8 2.5v7.4M5.2 7.4L8 10.2l2.8-2.8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                      <path d="M3 12.5h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
                    </svg>
                  </span>
                </div>
              </div>
              <div className="lg-log-wrap">
                <div className="lg-log" ref={logRef} onScroll={onLogScroll}>
                  {visibleLines.length === 0 && <div className="lg-empty">{active ? "Waiting for the first record to start…" : "No log lines."}</div>}
                  {visibleLines.map((l) => (
                    <div key={l.id + l.tag + l.cat} className={`lg-line ${l.divider ? "divider" : ""} ${l.cat === "answer" ? "answer" : ""}`}>
                      <Icon cat={l.cat} />
                      <span className="ts">{l.ts}</span>
                      <span className={`tag ${l.cat}`}>{l.tag}</span>
                      <span className="msg">
                        {l.msg}
                        {l.subs?.filter((s) => s.kind === "action" || showReasoning).map((s, i) => (
                          <span key={i} className={`sub ${s.kind}`}>
                            {s.text}
                          </span>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
                {showJump && (
                  <button
                    type="button"
                    className="lg-jump"
                    onClick={() => {
                      logRef.current.scrollTop = logRef.current.scrollHeight;
                      nearBottom.current = true;
                      setShowJump(false);
                    }}
                  >
                    <svg viewBox="0 0 16 16" fill="none">
                      <path d="M3.5 6.5L8 11l4.5-4.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    New activity
                  </button>
                )}
              </div>
            </div>

            {rows.length > 0 && (
              <div className="lg-panel">
                <div className="lg-panel-head">
                  <h2>Results</h2>
                  <div className="lg-toolbar">
                    <span className="dim" style={{ color: "var(--muted)", fontSize: 12 }}>
                      {wrongCount === 0 ? "Everything matches" : `${wrongCount} wrong`}
                    </span>
                    <span className={`lg-chip ${onlyWrong ? "active" : ""}`} data-cat="error" onClick={() => setOnlyWrong((v) => !v)}>
                      Only wrong
                    </span>
                  </div>
                </div>
                <div className="lg-table-wrap">
                  <table className="lg-table">
                    <thead>
                      <tr>
                        <th>Record</th>
                        <th>Field</th>
                        <th>Expected</th>
                        <th>Actual</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {visibleRows.map((r, i) => (
                        <tr key={i} className={r.ok ? "" : "wrong"}>
                          <td>{r.record}</td>
                          <td className="dim">{r.field}</td>
                          <td>{String(r.expected ?? "")}</td>
                          <td className={r.ok ? "" : "bad"}>{r.actual === null || r.actual === undefined ? "(nothing)" : String(r.actual)}</td>
                          <td className={r.ok ? "ok" : "bad"}>{r.ok ? "✓" : r.note || "✗"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
