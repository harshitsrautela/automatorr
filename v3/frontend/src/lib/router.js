import { useEffect, useState } from "react";

// A tiny client-side router: pages are plain paths like /agent/3 or /logs/12, changed with history.pushState.

// Fired whenever the list of saved tasks may have changed, so the sidebar can reload it.
export const TASKS_CHANGED = "tasks-changed";

// A page can register a function that returns false to cancel a move (for example, unsaved edits).
let guard = null;
export function setNavGuard(fn) {
  guard = fn;
}

// Moves to a new path without reloading the page; a registered guard may cancel it.
export function navigate(path) {
  if (path === window.location.pathname) return;
  if (guard && !guard(path)) return;
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

// Returns the current path and re-renders when it changes (links, back and forward buttons).
export function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onChange = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onChange);
    return () => window.removeEventListener("popstate", onChange);
  }, []);
  return path;
}
