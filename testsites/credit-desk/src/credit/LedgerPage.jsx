import React, { useEffect, useMemo, useState } from 'react';
import { Search } from 'lucide-react';
import CreditLayout from './CreditLayout.jsx';
import { supabase } from './supabaseClient.js';
import { formatRupees } from './format.js';

// Shows every retailer's credit terms and dues, with a search box on the name.
export default function LedgerPage() {
  const [retailers, setRetailers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    async function load() {
      if (!supabase) {
        setError('Supabase is not configured (check VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY).');
        setLoading(false);
        return;
      }
      const { data, error: err } = await supabase.from('retailers').select('*').order('name').order('city');
      if (err) setError(err.message);
      else setRetailers(data || []);
      setLoading(false);
    }
    load();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return retailers;
    return retailers.filter((r) => r.name.toLowerCase().includes(q));
  }, [retailers, query]);

  return (
    <CreditLayout
      active="/ledger"
      title="Retailer Ledger"
      subtitle="Credit terms and dues for each retailer. The same name can appear in more than one city."
    >
      <div className="mb-4 flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 sm:max-w-sm">
        <Search size={16} className="text-slate-400" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search retailer name"
          aria-label="Search retailer name"
          className="w-full bg-transparent text-sm outline-none"
        />
      </div>

      {error && <div className="mb-4 rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Retailer</th>
              <th className="px-4 py-3">City</th>
              <th className="px-4 py-3">Credit term</th>
              <th className="px-4 py-3 text-right">Credit limit</th>
              <th className="px-4 py-3 text-right">Outstanding</th>
              <th className="px-4 py-3 text-right">Days since last payment</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">Loading…</td></tr>
            )}
            {!loading && filtered.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">No retailers found.</td></tr>
            )}
            {filtered.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-3 font-medium text-slate-900">{r.name}</td>
                <td className="px-4 py-3">{r.city}</td>
                <td className="px-4 py-3">{r.credit_term_days} days</td>
                <td className="px-4 py-3 text-right">{formatRupees(r.credit_limit)}</td>
                <td className="px-4 py-3 text-right">{formatRupees(r.outstanding_amount)}</td>
                <td className="px-4 py-3 text-right">{r.days_since_last_payment}</td>
                <td className="px-4 py-3">
                  {r.is_blocked ? (
                    <span className="rounded-full bg-rose-100 px-2.5 py-1 text-xs font-medium text-rose-800">Blocked</span>
                  ) : (
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">Active</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CreditLayout>
  );
}
