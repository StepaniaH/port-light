/* Lease helpers shared by grid badges, drawer countdowns, and the
   Automation panel. Numeric output only — wording lives in locales. */

export function isLease(row) {
  const exp = row && row.expires_at;
  return typeof exp === 'number' && Number.isFinite(exp)
    && exp > Date.now() / 1000;
}

export function remainingSeconds(expiresAt, nowSec) {
  const now = nowSec != null ? nowSec : Date.now() / 1000;
  return Math.max(0, Math.round(expiresAt - now));
}

export function fmtRemaining(secs) {
  if (secs > 86400) return Math.round(secs / 86400) + 'd';
  if (secs >= 7200) return Math.round(secs / 3600) + 'h';
  if (secs >= 60) return Math.round(secs / 60) + 'm';
  return '<1m';
}
