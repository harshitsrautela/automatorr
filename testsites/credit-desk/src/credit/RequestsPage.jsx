import React, { useCallback, useEffect, useState } from 'react';
import { Check, X, Minus, RotateCcw } from 'lucide-react';
import CreditLayout from './CreditLayout.jsx';
import { supabase } from './supabaseClient.js';
import { formatDate, formatRupees } from './format.js';

const PAGE_SIZE = 10;

const BUTTONS = [
  { decision: 'approve', label: 'Approve', Icon: Check, style: 'text-emerald-700 hover:bg-emerald-50 border-emerald-200' },
  { decision: 'reject', label: 'Reject', Icon: X, style: 'text-rose-700 hover:bg-rose-50 border-rose-200' },
  { decision: 'hold', label: 'Hold', Icon: Minus, style: 'text-amber-700 hover:bg-amber-50 border-amber-200' },
];

const BADGE_STYLE = {
  approve: 'bg-emerald-100 text-emerald-800',
  reject: 'bg-rose-100 text-rose-800',
  hold: 'bg-amber-100 text-amber-800',
};

const BADGE_TEXT = { approve: 'Approved', reject: 'Rejected', hold: 'On hold' };

// Lists credit requests, ten per page, with approve / reject / hold buttons on each row.
export default function RequestsPage() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(1);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    if (!supabase) {
      setError('Supabase is not configured (check VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY).');
      setLoading(false);
      return;
    }
    setLoading(true);
    const { data, error: err } = await supabase.from('credit_requests').select('*').order('id');
    if (err) setError(err.message);
    else {
      setError('');
      setRequests(data || []);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Saves a decision (or clears it when decision is null) for one request.
  async function decide(id, decision) {
    setBusyId(id);
    const decided_at = decision ? new Date().toISOString() : null;
    const { error: err } = await supabase
      .from('credit_requests')
      .update({ decision, decided_at })
      .eq('id', id);
    setBusyId(null);
    if (err) {
      setError(err.message);
      return;
    }
    setError('');
    setRequests((rows) => rows.map((r) => (r.id === id ? { ...r, decision, decided_at } : r)));
  }

  // Clears every decision so the test can be run again.
  async function resetAll() {
    if (!window.confirm('Clear all decisions?')) return;
    const { error: err } = await supabase
      .from('credit_requests')
      .update({ decision: null, decided_at: null })
      .neq('id', -1);
    if (err) setError(err.message);
    else await load();
  }

  const totalPages = Math.max(1, Math.ceil(requests.length / PAGE_SIZE));
  const visible = requests.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const decidedCount = requests.filter((r) => r.decision).length;

  return (
    <CreditLayout
      active="/"
      title="Credit Requests"
      subtitle="Retailers asking for goods on credit. Check each retailer in the Retailer Ledger, then decide."
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-4 text-sm text-slate-600">
          <span className="inline-flex items-center gap-1"><Check size={16} className="text-emerald-700" /> Approve</span>
          <span className="inline-flex items-center gap-1"><X size={16} className="text-rose-700" /> Reject</span>
          <span className="inline-flex items-center gap-1"><Minus size={16} className="text-amber-700" /> Hold</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-slate-500">
            {decidedCount} of {requests.length} decided
          </span>
          <button
            type="button"
            onClick={resetAll}
            className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-1.5 text-slate-700 hover:bg-slate-100"
          >
            <RotateCcw size={14} /> Reset all decisions
          </button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Retailer</th>
              <th className="px-4 py-3">City</th>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3 text-right">Amount requested</th>
              <th className="px-4 py-3">Note</th>
              <th className="px-4 py-3">Decision</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">Loading…</td></tr>
            )}
            {!loading && visible.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">No requests.</td></tr>
            )}
            {visible.map((r) => (
              <tr key={r.id} data-request-id={r.id}>
                <td className="px-4 py-3 font-mono text-slate-500">#{r.id}</td>
                <td className="px-4 py-3 font-medium text-slate-900">{r.retailer_name}</td>
                <td className="px-4 py-3">{r.retailer_city}</td>
                <td className="px-4 py-3">{formatDate(r.request_date)}</td>
                <td className="px-4 py-3 text-right font-medium">{formatRupees(r.requested_amount)}</td>
                <td className="px-4 py-3 text-slate-500">{r.note}</td>
                <td className="px-4 py-3">
                  {r.decision ? (
                    <div className="flex items-center gap-2">
                      <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${BADGE_STYLE[r.decision]}`}>
                        {BADGE_TEXT[r.decision]}
                      </span>
                      <button
                        type="button"
                        disabled={busyId === r.id}
                        onClick={() => decide(r.id, null)}
                        className="text-xs text-slate-500 underline hover:text-slate-800"
                      >
                        Undo
                      </button>
                    </div>
                  ) : (
                    <div className="flex gap-1.5">
                      {BUTTONS.map(({ decision, label, Icon, style }) => (
                        <button
                          key={decision}
                          type="button"
                          title={label}
                          aria-label={`${label} request ${r.id}`}
                          disabled={busyId === r.id}
                          onClick={() => decide(r.id, decision)}
                          className={`rounded-md border p-1.5 disabled:opacity-50 ${style}`}
                        >
                          <Icon size={16} />
                        </button>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
        <span>
          Page {page} of {totalPages}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100 disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-md border border-slate-300 px-3 py-1.5 hover:bg-slate-100 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </CreditLayout>
  );
}
