import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Eraser, FileUp, FlaskConical, Play, Save, Trash2 } from "lucide-react";
import { api } from "../lib/api.js";
import { TASKS_CHANGED, navigate, setNavGuard } from "../lib/router.js";
import { autoName, fieldNames, findUrl, parseRecords, suggestMatchKey } from "../lib/data.js";
import { CREDIT_DESK_DATA, CREDIT_DESK_PROMPT, CREDIT_DESK_VERIFY } from "../lib/example.js";
import { Badge, Banner, Button, Card, Field, inputClass } from "../components/ui.jsx";

// Settings behind the "Advanced" section; a new task starts with these.
const DEFAULT_ADVANCED = {
  max_concurrency: 2,
  use_cache: true,
  verify_kind: "none",
  verify_config: {},
  db_secret_id: null,
  reconcile: false,
  headless: false,
};

// The starting values of a new task: the Credit Desk example (prompt, data and accuracy check), or everything blank.
// Takes whether to start blank.
function freshTask(blank) {
  return {
    prompt: blank ? "" : CREDIT_DESK_PROMPT,
    dataText: blank ? "" : recordsToText(CREDIT_DESK_DATA),
    adv: blank ? { ...DEFAULT_ADVANCED } : { ...DEFAULT_ADVANCED, ...CREDIT_DESK_VERIFY },
  };
}

// Turns records back into text for the data box: one record per line.
function recordsToText(records) {
  if (!records.length) return "";
  return "[\n" + records.map((r) => "  " + JSON.stringify(r)).join(",\n") + "\n]";
}

// The Agent window: describe a task, add data, pick a key, run. Everything else is under "Advanced".
export default function AgentPage({ path }) {
  const parts = path.split("/").filter(Boolean); // ["agent"] | ["agent", "3"]
  const routeId = parts[1] && parts[1] !== "new" ? Number(parts[1]) : null;

  const [saved, setSaved] = useState(null); // the stored task being edited (null for a new one)
  const [prompt, setPrompt] = useState("");
  const [dataText, setDataText] = useState("");
  const [adv, setAdv] = useState(DEFAULT_ADVANCED);
  const [snapshot, setSnapshot] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [fullPrompt, setFullPrompt] = useState(null);
  const [secrets, setSecrets] = useState([]); // saved logins, to show which one this task uses
  const [dbUrl, setDbUrl] = useState(""); // Supabase login typed for this task (kept out of the saved form until Save)
  const [dbKey, setDbKey] = useState("");
  const [dbTouched, setDbTouched] = useState(false); // typed by hand, as opposed to filled in for local testing
  const [forceHeadless, setForceHeadless] = useState(false); // the server runs without a screen
  const fileInput = useRef(null);

  const parsed = useMemo(() => parseRecords(dataText), [dataText]);
  const records = parsed.records ?? [];
  const fields = useMemo(() => fieldNames(records), [records]);
  const url = useMemo(() => findUrl(prompt), [prompt]);
  const matchKey = useMemo(() => suggestMatchKey(records), [records]);

  // What would be saved right now; the name, address and identifying field come from the description and the data.
  const body = useMemo(
    () => ({
      name: saved?.name ?? autoName(prompt),
      target_url: url ?? "",
      master_prompt: prompt,
      match_key: matchKey,
      ...adv,
      data: records,
    }),
    [saved, prompt, url, matchKey, adv, records]
  );
  const dirty = JSON.stringify(body) !== snapshot || (dbTouched && Boolean(dbUrl || dbKey));

  const loadLists = useCallback(async () => {
    try {
      const [secs, health] = await Promise.all([api.listSecrets(), api.health()]);
      setSecrets(secs);
      window.dispatchEvent(new Event(TASKS_CHANGED));
      setForceHeadless(Boolean(health.force_headless));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // Fills the editor from a stored task, or resets it to a blank (or example) task.
  const openRoute = useCallback(async () => {
    setError("");
    setFullPrompt(null);
    setDbUrl("");
    setDbKey("");
    setDbTouched(false);
    await loadLists();
    if (routeId === null) {
      const start = freshTask(false);
      setSaved(null);
      setSnapshot("");
      setPrompt(start.prompt);
      setDataText(start.dataText);
      setAdv(start.adv);
      return;
    }
    setLoading(true);
    try {
      const wf = await api.getWorkflow(routeId);
      setSaved(wf);
      setPrompt(wf.master_prompt);
      setDataText(recordsToText(wf.data));
      const a = {
        max_concurrency: wf.max_concurrency,
        use_cache: wf.use_cache,
        verify_kind: wf.verify_kind,
        verify_config: wf.verify_config,
        db_secret_id: wf.db_secret_id ?? null,
        reconcile: wf.reconcile,
        headless: wf.headless,
      };
      setAdv(a);
      setSnapshot(
        JSON.stringify({
          name: wf.name,
          target_url: wf.target_url,
          master_prompt: wf.master_prompt,
          match_key: wf.match_key,
          ...a,
          data: wf.data,
        })
      );
    } catch (e) {
      setError(e.message);
      setSaved(null);
    } finally {
      setLoading(false);
    }
  }, [routeId, loadLists]);

  useEffect(() => {
    openRoute();
  }, [openRoute]);

  // Local testing: when the server allows it, fills the Supabase boxes with the login from its .env (a saved login is left alone).
  useEffect(() => {
    if (loading || adv.db_secret_id) return;
    let alive = true;
    api
      .devPrefill("db_credential")
      .then((p) => {
        const mine = p?.db_credential;
        if (alive && mine) {
          setDbUrl((v) => v || mine.url);
          setDbKey((v) => v || mine.key);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [routeId, loading, adv.db_secret_id]);

  // Asks before leaving this page (sidebar, links) with unsaved edits.
  useEffect(() => {
    setNavGuard(() => !(dirty && (prompt || dataText)) || window.confirm("You have unsaved changes. Leave without saving?"));
    return () => setNavGuard(null);
  }, [dirty, prompt, dataText]);

  // Warns before closing the tab with unsaved edits.
  useEffect(() => {
    const handler = (e) => {
      if (dirty && (prompt || dataText)) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, prompt, dataText]);

  const setA = (patch) => setAdv((a) => ({ ...a, ...patch }));
  const setVerify = (patch) => setAdv((a) => ({ ...a, verify_config: { ...a.verify_config, ...patch } }));

  // Checks the task and saves it (creating it first if new); returns the saved task or null.
  async function save() {
    if (!prompt.trim()) {
      setError("Describe the task first.");
      return null;
    }
    if (!url) {
      setError("Include the website address in your description, for example “Go to https://example.com”.");
      return null;
    }
    if (parsed.error) {
      setError(`Fix the data first: ${parsed.error}`);
      return null;
    }
    if (adv.verify_kind !== "none" && !matchKey) {
      setError("To check results the data needs a column whose values are all different, such as an id.");
      return null;
    }
    const typedUrl = dbUrl.trim();
    const typedKey = dbKey.trim();
    if (adv.verify_kind !== "none" && !adv.db_secret_id && (typedUrl || typedKey) && !(typedUrl && typedKey)) {
      setError("Enter both the Supabase project URL and its key, or leave both empty.");
      return null;
    }
    setBusy("save");
    setError("");
    try {
      // The Supabase login is stored encrypted on the server; the task only points to it
      let dbId = adv.db_secret_id;
      if (adv.verify_kind !== "none" && (typedUrl || typedKey)) {
        if (dbId) {
          await api.updateSecret(dbId, { url: typedUrl || undefined, key: typedKey || undefined });
        } else {
          const made = await api.createSecret({
            label: `Supabase ${typedUrl.replace(/^https?:\/\//, "").split("/")[0]} ${Math.random().toString(36).slice(2, 6)}`,
            kind: "db_credential",
            url: typedUrl,
            key: typedKey,
          });
          dbId = made.id;
        }
        setDbUrl("");
        setDbKey("");
        setDbTouched(false);
      }
      const payload = { ...body, db_secret_id: dbId };
      const stored = saved ? await api.updateWorkflow(saved.id, { ...payload, name: saved.name }) : await api.createWorkflow(payload);
      setSaved(stored);
      setAdv((x) => ({ ...x, db_secret_id: dbId }));
      setSnapshot(JSON.stringify({ ...payload, name: stored.name, data: stored.data }));
      await loadLists();
      if (!saved) {
        setNavGuard(null); // just saved: nothing to lose
        navigate(`/agent/${stored.id}`);
      }
      return stored;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy("");
    }
  }

  // Saves, then starts a run (the first row or all rows) and opens its log.
  async function run(limit) {
    const count = limit ? Math.min(limit, Math.max(records.length, 1)) : Math.max(records.length, 1);
    const what = records.length === 0 ? "the task once" : `${count} row${count === 1 ? "" : "s"}`;
    if (!window.confirm(`Run ${what}?\n\nThe agent will really act on ${url ?? "the website"}, clicking and typing as it decides.`)) return;
    const stored = await save();
    if (!stored) return;
    setBusy("run");
    try {
      const { run_id } = await api.startRun(stored.id, limit);
      setNavGuard(null);
      navigate(`/logs/${run_id}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  // Replaces the editor's content with the example or a blank task (new tasks only).
  function resetEditor(blank) {
    const hasWork = prompt.trim() || dataText.trim();
    if (hasWork && !window.confirm("Replace what is in the editor?")) return;
    const start = freshTask(blank);
    setPrompt(start.prompt);
    setDataText(start.dataText);
    setAdv(start.adv);
    setError("");
    setNotice(blank ? "" : "Credit Desk example loaded.");
  }

  async function removeTask() {
    if (!window.confirm(`Delete “${saved.name}” and all its runs and logs?`)) return;
    try {
      await api.deleteWorkflow(saved.id);
      setNavGuard(null);
      window.dispatchEvent(new Event(TASKS_CHANGED));
      navigate("/agent");
    } catch (e) {
      setError(e.message);
    }
  }

  async function resetLearned() {
    if (!window.confirm("Forget the steps learned for this task? The next run will learn them again.")) return;
    try {
      await api.resetCache(saved.id);
      setNotice("Learned steps cleared.");
    } catch (e) {
      setError(e.message);
    }
  }

  async function toggleFullPrompt() {
    if (fullPrompt) {
      setFullPrompt(null);
      return;
    }
    try {
      const out = await api.previewPrompt({ target_url: url ?? "", instructions: prompt, sample: records[0] ?? {} });
      setFullPrompt(out.prompt);
    } catch (e) {
      setError(e.message);
    }
  }

  async function onFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setDataText(await file.text());
    e.target.value = "";
  }

  function fillCreditDeskCheck() {
    setAdv((a) => ({ ...a, ...CREDIT_DESK_VERIFY }));
  }

  const cfg = adv.verify_config ?? {};
  const dbSecret = secrets.find((s) => s.id === adv.db_secret_id) ?? null;
  const canRun = Boolean(prompt.trim() && url && !parsed.error);
  const runLabel = records.length > 1 ? `Run all ${records.length}` : "Run";

  return (
    <div className="mx-auto max-w-4xl">
    <div>
      {/* Editor */}
      <div className="min-w-0 flex-1 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xl font-semibold tracking-tight text-slate-100">{saved ? saved.name : "New task"}</h2>
          <div className="flex items-center gap-1 sm:gap-2">
            {dirty && (prompt || dataText) && <Badge tone="amber">Unsaved</Badge>}
            {!saved && (
              <>
                <Button variant="ghost" onClick={() => resetEditor(false)} title="Fill in the Credit Desk example">
                  <FlaskConical size={15} /> <span className="hidden sm:inline">Example</span>
                </Button>
                <Button variant="ghost" onClick={() => resetEditor(true)} title="Clear everything and write your own">
                  <Eraser size={15} /> <span className="hidden sm:inline">Start blank</span>
                </Button>
              </>
            )}
            {saved && (
              <Button variant="danger" onClick={removeTask}>
                <Trash2 size={15} /> Delete
              </Button>
            )}
          </div>
        </div>

        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        <Banner onClose={() => setError("")}>{error}</Banner>
        <Banner tone="success" onClose={() => setNotice("")}>
          {notice}
        </Banner>

        <Card title="What should the agent do?" subtitle="Write it like you would explain it to a colleague. Include the website address and say what to do with each row of your data.">
          <textarea
            className={`${inputClass} h-72 font-mono text-xs leading-5`}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={"e.g. Go to https://example.com/orders. For each row, find the order whose number is in the id column and mark it as shipped."}
          />
          <p className={`mt-2 text-xs ${url ? "text-emerald-400" : "text-amber-400"}`}>
            {url ? `Starting page: ${url}` : "No website address found yet. Add one to your description."}
          </p>
        </Card>

        <Card
          title="Data"
          subtitle="Optional. One row per thing to process. Paste JSON or CSV, or upload a file. With no data, the task runs once."
          action={
            <div className="flex items-center gap-2">
              <Button onClick={() => fileInput.current?.click()}>
                <FileUp size={15} /> Upload
              </Button>
              {records.length > 0 && (
                <Button variant="ghost" onClick={() => setDataText("")}>
                  Clear
                </Button>
              )}
              <input ref={fileInput} type="file" accept=".json,.csv,text/csv,application/json" className="hidden" onChange={onFile} />
            </div>
          }
        >
          <textarea
            className={`${inputClass} h-28 font-mono text-xs leading-5`}
            value={dataText}
            onChange={(e) => setDataText(e.target.value)}
            placeholder={'[\n  {"id": 1},\n  {"id": 2}\n]\n\nor CSV:\nid,name\n1,Asha'}
            spellCheck={false}
          />
          {parsed.error && (
            <div className="mt-2">
              <Banner>{parsed.error}</Banner>
            </div>
          )}
          {records.length > 0 && !parsed.error && (
            <p className="mt-2 text-xs text-slate-400">
              {records.length} row{records.length === 1 ? "" : "s"} · columns: {fields.join(", ")}
            </p>
          )}
        </Card>

        <details className="rounded-xl border border-white/10 bg-white/[0.04]">
          <summary className="cursor-pointer px-5 py-4 text-sm font-semibold text-slate-100">Advanced</summary>
          <div className="space-y-6 border-t border-white/10 px-5 py-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Browsers at the same time" hint="Each opens its own Chrome window.">
                <input
                  className={inputClass}
                  type="number"
                  min={1}
                  max={10}
                  value={adv.max_concurrency}
                  onChange={(e) => setA({ max_concurrency: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })}
                />
              </Field>
            </div>

            <label className="flex items-start gap-2 text-sm text-slate-300">
              <input className="mt-1" type="checkbox" checked={adv.use_cache} onChange={(e) => setA({ use_cache: e.target.checked })} />
              <span>
                <span className="font-medium">Reuse what the agent learns on the first row</span>
                <span className="block text-xs text-slate-400">
                  If the first row is mostly typing the row's values into a form, the other rows replay it without the AI (faster, cheaper). It is skipped
                  automatically when the agent has to decide each row differently.
                </span>
                {saved && adv.use_cache && (
                  <button type="button" className="mt-1 text-xs underline" onClick={resetLearned}>
                    Forget what it learned
                  </button>
                )}
              </span>
            </label>

            <div>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-100">Check the results</h3>
                <Button variant="ghost" onClick={fillCreditDeskCheck}>
                  <FlaskConical size={14} /> Use the Credit Desk check
                </Button>
              </div>
              <Field label="How to check" hint="Gives you an accuracy number. Needs a column in your data whose values are all different (like an id).">
                <select className={inputClass} value={adv.verify_kind} onChange={(e) => setA({ verify_kind: e.target.value })}>
                  <option value="none">Don't check (trust what the agent says)</option>
                  <option value="expected_view">Compare what was written to the correct answers (a database view)</option>
                  <option value="table">Compare each row's values to a database table</option>
                </select>
              </Field>
              {adv.verify_kind !== "none" && (
                <div className="mt-4 space-y-4">
                  <div>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Supabase project URL">
                        <input
                          className={inputClass}
                          value={dbUrl}
                          onChange={(e) => {
                            setDbUrl(e.target.value);
                            setDbTouched(true);
                          }}
                          placeholder={dbSecret ? "(saved, leave blank to keep)" : "https://your-project.supabase.co"}
                          autoComplete="off"
                          spellCheck={false}
                        />
                      </Field>
                      <Field label="Supabase service-role key">
                        <input
                          className={inputClass}
                          type="password"
                          value={dbKey}
                          onChange={(e) => {
                            setDbKey(e.target.value);
                            setDbTouched(true);
                          }}
                          placeholder={dbSecret ? `${dbSecret.preview} (saved, leave blank to keep)` : "eyJ… or sb_secret_…"}
                          autoComplete="off"
                        />
                      </Field>
                    </div>
                    <p className="mt-1.5 text-xs text-slate-400">
                      {dbSecret
                        ? "Your login is stored encrypted on the server and cannot be read back. Type here to replace it. Read access is enough."
                        : "Used only to read results back for the check. Stored encrypted on the server. Leave both empty to use the server's own default login."}
                    </p>
                    {dbSecret && (
                      <button type="button" className="mt-1 text-xs text-slate-400 underline" onClick={() => setA({ db_secret_id: null })}>
                        Use the server's default login instead
                      </button>
                    )}
                  </div>
                  {adv.verify_kind === "table" && (
                    <>
                      <Field label="Table">
                        <input className={inputClass} value={cfg.table ?? ""} onChange={(e) => setVerify({ table: e.target.value })} placeholder="people" />
                      </Field>
                      <label className="flex items-center gap-2 text-sm text-slate-300">
                        <input type="checkbox" checked={adv.reconcile} onChange={(e) => setA({ reconcile: e.target.checked })} />
                        Afterwards, retry wrong fields through the site's edit form
                      </label>
                    </>
                  )}
                  {adv.verify_kind === "expected_view" && (
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label="Table the agent writes to"><input className={inputClass} value={cfg.table ?? ""} onChange={(e) => setVerify({ table: e.target.value })} /></Field>
                      <Field label="Its key column"><input className={inputClass} value={cfg.key_column ?? ""} onChange={(e) => setVerify({ key_column: e.target.value })} /></Field>
                      <Field label="Column holding the decision"><input className={inputClass} value={cfg.value_column ?? ""} onChange={(e) => setVerify({ value_column: e.target.value })} /></Field>
                      <span />
                      <Field label="View with the correct answers"><input className={inputClass} value={cfg.expected_view ?? ""} onChange={(e) => setVerify({ expected_view: e.target.value })} /></Field>
                      <Field label="Its key column"><input className={inputClass} value={cfg.expected_key_column ?? ""} onChange={(e) => setVerify({ expected_key_column: e.target.value })} /></Field>
                      <Field label="Its correct-answer column"><input className={inputClass} value={cfg.expected_value_column ?? ""} onChange={(e) => setVerify({ expected_value_column: e.target.value })} /></Field>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div>
              <Button variant="ghost" onClick={toggleFullPrompt}>
                {fullPrompt ? "Hide" : "Show"} exactly what the agent receives
              </Button>
              {fullPrompt && (
                <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-black/50 p-4 text-xs leading-5 text-slate-200">{fullPrompt}</pre>
              )}
            </div>
          </div>
        </details>
      </div>

    </div>

      {/* Action bar: stays at the bottom of the screen while the page scrolls */}
      <div className="sticky bottom-0 z-10 -mx-4 mt-6 border-t border-white/10 bg-[#0b0d14]/90 px-4 py-3 shadow-[0_-4px_12px_-6px_rgba(0,0,0,0.6)] backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <label
            className={`mr-auto flex items-center gap-2 text-sm ${forceHeadless ? "text-slate-500" : "text-slate-300"}`}
            title={forceHeadless ? "This server runs without a screen, so browsers are always hidden." : "Untick to run the browser without a visible window."}
          >
            <input
              type="checkbox"
              checked={!(adv.headless || forceHeadless)}
              disabled={forceHeadless}
              onChange={(e) => setA({ headless: !e.target.checked })}
            />
            <span>
              Show the browser<span className="hidden sm:inline"> while it works</span>
              {forceHeadless && <span className="ml-1 text-xs">(server has no screen)</span>}
            </span>
          </label>
          <Button onClick={save} busy={busy === "save"} disabled={!dirty || !prompt.trim()}>
            <Save size={15} /> <span className="hidden sm:inline">Save</span>
          </Button>
          <Button onClick={() => run(1)} busy={busy === "run"} disabled={!canRun}>
            <FlaskConical size={15} />
            {records.length > 1 ? (
              <>
                <span className="sm:hidden">Try first row</span>
                <span className="hidden sm:inline">Try on the first row</span>
              </>
            ) : (
              "Try it"
            )}
          </Button>
          {records.length > 1 && (
            <Button variant="primary" onClick={() => run(null)} busy={busy === "run"} disabled={!canRun}>
              <Play size={15} /> {runLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
