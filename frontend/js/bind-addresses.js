/* Compact, stable bind-address summaries for occupancy cards. */

function stripBrackets(raw) {
  const text = String(raw == null ? '' : raw).trim();
  if (text.startsWith('[') && text.endsWith(']')) return text.slice(1, -1);
  return text;
}

function splitZone(text) {
  const zoneAt = text.indexOf('%');
  return {
    base: zoneAt >= 0 ? text.slice(0, zoneAt) : text,
    zone: zoneAt >= 0 ? text.slice(zoneAt) : '',
  };
}

function compressIpv6(raw) {
  const { base, zone } = splitZone(stripBrackets(raw));
  if (!/^[0-9a-f:.]+$/i.test(base)) return null;
  try {
    // URL parsing validates and canonicalizes IPv6 without making a request.
    return new URL('http://[' + base + ']/').hostname.slice(1, -1) + zone;
  } catch {
    return null;
  }
}

function normalizedAddress(raw) {
  const text = stripBrackets(raw);
  if (text.indexOf(':') >= 0) {
    const value = compressIpv6(text);
    if (!value) return null;
    const mapped = value.match(/^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
    if (mapped) {
      const octets = mapped.slice(1).flatMap(function (part) {
        const n = parseInt(part, 16);
        return [n >> 8, n & 255];
      });
      return { family: 'v4', value: octets.join('.') };
    }
    return { family: 'v6', value };
  }
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(text)) {
    const octets = text.split('.').map(Number);
    if (octets.every(function (n) { return n <= 255; })) return { family: 'v4', value: octets.join('.') };
  }
  return null;
}

function isWildcard({ family, value }) {
  return family === 'v4' ? value === '0.0.0.0' : value === '::';
}

function isLoopback({ family, value }) {
  return family === 'v4' ? value.startsWith('127.') : value === '::1';
}

function addressRank(row) {
  if (isWildcard(row)) return 0;
  if (!isLoopback(row)) return 1;
  return 2;
}

const IPV6_DENSITIES = {
  loose: { max: 24, prefixCount: 3 },
  standard: { max: 19, prefixCount: 2 },
  compact: { max: 14, prefixCount: 1 },
};

export function compactIpv6(raw, density) {
  const value = compressIpv6(raw) || stripBrackets(raw);
  const { max, prefixCount } = IPV6_DENSITIES[density] || IPV6_DENSITIES.standard;
  if (value.length <= max || value === '::' || value === '::1') return value;
  const { base, zone } = splitZone(value);
  const parts = base.split(':').filter(Boolean);
  if (parts.length < 2) return value;
  const prefix = (base.startsWith('::') ? '::' : '') +
    parts.slice(0, Math.min(prefixCount, parts.length - 1)).join(':');
  const suffix = base.endsWith('::') ? '::' : ':' + parts[parts.length - 1];
  return prefix + ':…' + suffix + zone;
}

export function cardBindAddresses(row) {
  if (row.source_type === 'manual' || row.status === 'free' || row.status === 'unknown') return [];
  // Configured rows may carry a synthetic 0.0.0.0 fallback in older APIs.
  // Only used rows provide observations; configured rows need declarations.
  const addresses = row.status === 'used' ? (row.ips || (row.ip ? [row.ip] : [])).slice() : [];
  (row.containers || []).forEach(function (container) {
    addresses.push(...(container.bind_ips || []));
  });
  (row.compose_configs || []).forEach(function (compose) {
    if (compose.host_ip) addresses.push(compose.host_ip);
    else if (compose.network_mode !== 'host' && !(compose.network_mode || '').startsWith('ns:')) {
      addresses.push('0.0.0.0');
    }
  });
  return addresses;
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
    if (options.omitWildcards && isWildcard(row)) return;
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
      wildcard: isWildcard(primary),
      additional: rows.length - 1,
      addresses: rows.map(function (row) { return row.value; }),
    }];
  });
}
