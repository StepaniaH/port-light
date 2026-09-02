/* Compact, stable bind-address summaries for occupancy cards. */

function stripBrackets(raw) {
  const text = String(raw == null ? '' : raw).trim();
  if (text.startsWith('[') && text.endsWith(']')) return text.slice(1, -1);
  return text;
}

function compressIpv6(raw) {
  const text = stripBrackets(raw).toLowerCase();
  const zoneAt = text.indexOf('%');
  const base = zoneAt >= 0 ? text.slice(0, zoneAt) : text;
  const zone = zoneAt >= 0 ? text.slice(zoneAt) : '';
  if (!base || base.indexOf(':') < 0) return text;

  const halves = base.split('::');
  if (halves.length > 2) return text;
  const left = halves[0] ? halves[0].split(':') : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  if (left.concat(right).some(function (part) { return !/^[0-9a-f]{1,4}$/.test(part); })) {
    return text;
  }
  const missing = halves.length === 2 ? 8 - left.length - right.length : 0;
  const parts = halves.length === 2
    ? left.concat(Array(Math.max(0, missing)).fill('0'), right)
    : left;
  if (parts.length !== 8) return text;
  const normalized = parts.map(function (part) {
    return parseInt(part, 16).toString(16);
  });

  let bestStart = -1;
  let bestLength = 0;
  for (let i = 0; i < normalized.length;) {
    if (normalized[i] !== '0') { i++; continue; }
    let j = i;
    while (j < normalized.length && normalized[j] === '0') j++;
    if (j - i > bestLength && j - i >= 2) {
      bestStart = i;
      bestLength = j - i;
    }
    i = j;
  }
  if (bestStart < 0) return normalized.join(':') + zone;
  const before = normalized.slice(0, bestStart).join(':');
  const after = normalized.slice(bestStart + bestLength).join(':');
  return before + '::' + after + zone;
}

function normalizedAddress(raw) {
  let text = stripBrackets(raw);
  const mapped = text.match(/^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i);
  if (mapped) return { family: 'v4', value: mapped[1] };
  if (text.indexOf(':') >= 0) return { family: 'v6', value: compressIpv6(text) };
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(text)) return { family: 'v4', value: text };
  return null;
}

function isWildcard(family, value) {
  return family === 'v4' ? value === '0.0.0.0' : value === '::';
}

function isLoopback(family, value) {
  return family === 'v4' ? value.startsWith('127.') : value === '::1';
}

function addressRank(row) {
  if (isWildcard(row.family, row.value)) return 0;
  if (!isLoopback(row.family, row.value)) return 1;
  return 2;
}

export function compactIpv6(raw, density) {
  const value = compressIpv6(raw);
  const max = density === 'loose' ? 24 : density === 'compact' ? 14 : 19;
  if (value.length <= max || value === '::' || value === '::1') return value;
  const zoneAt = value.indexOf('%');
  const base = zoneAt >= 0 ? value.slice(0, zoneAt) : value;
  const zone = zoneAt >= 0 ? value.slice(zoneAt) : '';
  const parts = base.split(':').filter(Boolean);
  if (parts.length < 2) return value;
  const prefixCount = density === 'loose' ? 3 : density === 'compact' ? 1 : 2;
  const prefix = parts.slice(0, Math.min(prefixCount, parts.length - 1)).join(':');
  return prefix + ':…:' + parts[parts.length - 1] + zone;
}

export function summarizeBindAddresses(ips, options) {
  options = options || {};
  const enabled = {
    v4: options.showV4 !== false,
    v6: options.showV6 !== false,
  };
  const seen = { v4: new Map(), v6: new Map() };
  (Array.isArray(ips) ? ips : []).forEach(function (raw) {
    const row = normalizedAddress(raw);
    if (!row || !enabled[row.family] || seen[row.family].has(row.value)) return;
    seen[row.family].set(row.value, row);
  });

  return ['v4', 'v6'].flatMap(function (family) {
    const rows = Array.from(seen[family].values()).sort(function (a, b) {
      return addressRank(a) - addressRank(b) || a.value.localeCompare(b.value);
    });
    if (!rows.length) return [];
    const primary = rows[0];
    return [{
      family,
      label: family === 'v4' ? 'IPv4' : 'IPv6',
      primary: primary.value,
      display: family === 'v6' ? compactIpv6(primary.value, options.density) : primary.value,
      wildcard: isWildcard(family, primary.value),
      additional: rows.length - 1,
      addresses: rows.map(function (row) { return row.value; }),
    }];
  });
}
