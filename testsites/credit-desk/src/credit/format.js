// Formats a number as rupees with Indian digit grouping, e.g. 125000 -> "₹1,25,000".
const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});

export function formatRupees(value) {
  return inr.format(Number(value) || 0);
}

// Formats an ISO date string (2026-09-02) as "02 Sep 2026".
export function formatDate(value) {
  if (!value) return '';
  const d = new Date(`${value}T00:00:00`);
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}
