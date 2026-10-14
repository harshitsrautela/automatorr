import React, { useEffect, useState } from "react";
import { Bot, Menu, Plus, ScrollText, X } from "lucide-react";
import { TASKS_CHANGED, navigate, usePath } from "./lib/router.js";
import { api } from "./lib/api.js";
import AgentPage from "./pages/AgentPage.jsx";
import LogsPage from "./pages/LogsPage.jsx";

const NAV = [
  { path: "/agent", label: "Agent", hint: "Build and run", Icon: Bot },
  { path: "/logs", label: "Logs", hint: "Watch runs", Icon: ScrollText },
];

// The logo and name at the top of the sidebar; `compact` shows only the logo.
function Brand({ compact }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-500 text-white">
        <Bot size={18} />
      </span>
      {!compact && (
        <span className="whitespace-nowrap leading-tight">
          <span className="block text-sm font-semibold text-white">terminautomator</span>
          <span className="block text-xs text-slate-500">Browser agent studio</span>
        </span>
      )}
    </div>
  );
}

// The saved tasks under the Agent item: a "New task" entry and one entry per task.
function TaskMenu({ tasks, path, onPick }) {
  const parts = path.split("/").filter(Boolean);
  const activeId = parts[0] === "agent" && parts[1] && parts[1] !== "new" ? Number(parts[1]) : null;
  const onAgent = parts[0] === "agent";
  const itemClass = (active) =>
    `flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] transition-colors ${
      active ? "bg-sky-500/15 text-sky-300" : "text-slate-400 hover:bg-white/5 hover:text-white"
    }`;
  const go = (to) => {
    navigate(to);
    onPick?.();
  };
  return (
    <div className="ml-5 mt-1 space-y-0.5 border-l border-white/10 pl-2">
      <button type="button" onClick={() => go("/agent")} className={itemClass(onAgent && activeId === null)}>
        <Plus size={14} className="shrink-0" /> <span className="truncate">New task</span>
      </button>
      {tasks.map((t) => (
        <button key={t.id} type="button" onClick={() => go(`/agent/${t.id}`)} title={t.name} className={itemClass(activeId === t.id)}>
          <span className="min-w-0 flex-1">
            <span className="block truncate">{t.name}</span>
            <span className="block text-[11px] text-slate-500">
              {t.record_count} row{t.record_count === 1 ? "" : "s"}
            </span>
          </span>
        </button>
      ))}
      {tasks.length === 0 && <p className="px-2.5 py-1 text-xs text-slate-500">No tasks yet.</p>}
    </div>
  );
}

// The navigation list, shared by the desktop sidebar and the mobile drawer.
function NavList({ section, collapsed, runningRunId, onPick, tasks, path }) {
  return (
    <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3">
      {NAV.map(({ path: to, label, hint, Icon }) => {
        const active = section === to;
        const link = (
          <a
            href={to}
            title={collapsed ? label : undefined}
            onClick={(e) => {
              e.preventDefault();
              navigate(to);
              onPick?.();
            }}
            className={`group relative flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors ${collapsed ? "justify-center" : ""} ${
              active ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-white"
            }`}
          >
            {active && <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full bg-sky-400" />}
            <span className="relative">
              <Icon size={18} />
              {to === "/logs" && runningRunId && (
                <span className="absolute -right-1 -top-1 h-2 w-2 animate-pulse rounded-full bg-emerald-400" title="A run is in progress" />
              )}
            </span>
            {!collapsed && (
              <span className="min-w-0 flex-1 whitespace-nowrap">
                <span className="block text-sm font-medium leading-tight">{label}</span>
                <span className="block truncate text-xs text-slate-500">{hint}</span>
              </span>
            )}
          </a>
        );
        return (
          <div key={to}>
            {link}
            {to === "/agent" && !collapsed && <TaskMenu tasks={tasks} path={path} onPick={onPick} />}
          </div>
        );
      })}
    </nav>
  );
}

// The page frame: a slim icon sidebar that opens while the cursor is over it (a slide-in drawer on phones), and the active page beside it.
export default function App() {
  const path = usePath();
  const [runningRunId, setRunningRunId] = useState(null);
  const [expanded, setExpanded] = useState(false); // the desktop sidebar: open only while the cursor is over it
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tasks, setTasks] = useState([]); // saved tasks, shown under Agent in the sidebar

  // Loads the saved tasks now and whenever the Agent page says they changed.
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listWorkflows()
        .then((list) => alive && setTasks(list))
        .catch(() => {});
    load();
    window.addEventListener(TASKS_CHANGED, load);
    return () => {
      alive = false;
      window.removeEventListener(TASKS_CHANGED, load);
    };
  }, []);

  // Lands on /agent from the bare address.
  useEffect(() => {
    if (path === "/" || path === "") navigate("/agent");
  }, [path]);

  // Closes the phone drawer whenever the page changes.
  useEffect(() => {
    setDrawerOpen(false);
  }, [path]);

  // Keeps a "running" dot on the Logs item by asking the server every few seconds.
  useEffect(() => {
    let alive = true;
    async function check() {
      try {
        const h = await api.health();
        if (alive) setRunningRunId(h.running_run_id);
      } catch {
        if (alive) setRunningRunId(null);
      }
    }
    check();
    const timer = setInterval(check, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const section = "/" + (path.split("/")[1] || "agent");

  return (
    <div className="min-h-screen bg-[#05060a] text-slate-200 md:flex">
      {/* Desktop sidebar: a fixed-width icon rail holds its place; the panel on top grows over the page while hovered or focused */}
      <div className="hidden w-[72px] shrink-0 md:block">
        <aside
          onMouseEnter={() => setExpanded(true)}
          onMouseLeave={() => setExpanded(false)}
          onFocus={() => setExpanded(true)}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setExpanded(false);
          }}
          className={`fixed inset-y-0 left-0 z-30 flex flex-col overflow-hidden border-r border-white/10 bg-[#0b0d14] transition-[width,box-shadow] duration-200 ${
            expanded ? "w-64 shadow-2xl" : "w-[72px]"
          }`}
        >
          <div className={`flex items-center py-5 ${expanded ? "px-5" : "justify-center px-3"}`}>
            <Brand compact={!expanded} />
          </div>
          <NavList section={section} collapsed={!expanded} runningRunId={runningRunId} tasks={tasks} path={path} />
          <div className="whitespace-nowrap px-5 py-4 text-xs text-slate-600">
            {expanded ? "Local · keys stay on this machine" : "\u00a0"}
          </div>
        </aside>
      </div>

      {/* Phone top bar and drawer */}
      <header className="sticky top-0 z-20 flex items-center justify-between bg-[#0b0d14] px-4 py-3 md:hidden">
        <button type="button" onClick={() => setDrawerOpen(true)} aria-label="Open menu" className="rounded-lg p-1.5 text-slate-300 hover:bg-white/10">
          <Menu size={22} />
        </button>
        <span className="text-sm font-semibold text-white">terminautomator</span>
        <span className="relative w-8">
          {runningRunId && <span className="absolute right-2 top-1/2 h-2 w-2 -translate-y-1/2 animate-pulse rounded-full bg-emerald-400" />}
        </span>
      </header>
      {drawerOpen && (
        <div className="fixed inset-0 z-30 md:hidden">
          <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/60" onClick={() => setDrawerOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-white/10 bg-[#0b0d14] shadow-xl">
            <div className="flex items-center justify-between px-5 py-5">
              <Brand compact={false} />
              <button type="button" onClick={() => setDrawerOpen(false)} aria-label="Close menu" className="rounded-lg p-1.5 text-slate-400 hover:bg-white/10">
                <X size={18} />
              </button>
            </div>
            <NavList section={section} collapsed={false} runningRunId={runningRunId} onPick={() => setDrawerOpen(false)} tasks={tasks} path={path} />
          </aside>
        </div>
      )}

      <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
        {section === "/logs" && <LogsPage path={path} />}
        {section === "/agent" && <AgentPage path={path} />}
        {!["/logs", "/agent"].includes(section) && <p className="text-sm text-slate-500">Page not found.</p>}
      </main>
    </div>
  );
}
