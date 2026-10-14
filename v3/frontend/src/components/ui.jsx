import React from "react";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";

// A white card with an optional title row.
export function Card({ title, subtitle, action, children, className = "" }) {
  return (
    <section className={`rounded-2xl border border-white/10 bg-white/[0.04] ${className}`}>
      {(title || action) && (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 px-4 py-4 sm:px-5">
          <div>
            <h2 className="text-sm font-semibold text-slate-100">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="px-4 py-4 sm:px-5">{children}</div>
    </section>
  );
}

const BUTTON_STYLES = {
  primary: "bg-sky-500 text-white hover:bg-sky-400 disabled:bg-white/10 disabled:text-slate-500",
  secondary: "border border-white/15 bg-white/5 text-slate-200 hover:bg-white/10 disabled:text-slate-600",
  danger: "border border-rose-400/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20 disabled:text-rose-900",
  ghost: "text-slate-300 hover:bg-white/10 disabled:text-slate-600",
};

// A button with a style variant and an optional spinner while busy.
export function Button({ variant = "secondary", busy = false, disabled, children, className = "", ...rest }) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-300 disabled:cursor-not-allowed ${BUTTON_STYLES[variant]} ${className}`}
      {...rest}
    >
      {busy && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  );
}

// A labelled form control; `hint` appears under the control.
export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs font-medium text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-white/15 bg-black/30 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-400/20 disabled:opacity-60";

// A small coloured pill.
export function Badge({ tone = "slate", children }) {
  const tones = {
    slate: "bg-white/10 text-slate-300",
    green: "bg-emerald-500/15 text-emerald-300",
    red: "bg-rose-500/15 text-rose-300",
    amber: "bg-amber-500/15 text-amber-300",
    blue: "bg-sky-500/15 text-sky-300",
  };
  return <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}

// A coloured banner for errors and notices.
export function Banner({ tone = "error", children, onClose }) {
  if (!children) return null;
  const styles = {
    error: "bg-rose-500/10 text-rose-300 border-rose-400/30",
    success: "bg-emerald-500/10 text-emerald-300 border-emerald-400/30",
    info: "bg-sky-500/10 text-sky-300 border-sky-400/30",
    warn: "bg-amber-500/10 text-amber-300 border-amber-400/30",
  };
  const Icon = tone === "success" ? CheckCircle2 : AlertCircle;
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${styles[tone]}`}>
      <Icon size={16} className="mt-0.5 shrink-0" />
      <div className="flex-1">{children}</div>
      {onClose && (
        <button type="button" onClick={onClose} className="text-xs underline">
          Dismiss
        </button>
      )}
    </div>
  );
}

// Page title block.
export function PageHeader({ title, subtitle, action }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

// Formats an ISO timestamp as a short local date and time.
export function formatWhen(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
