/* Shared text helpers: i18n lookups, collation, error text, escaping. */

export function t(key, vars) {
  return window.PortLightI18n ? window.PortLightI18n.t(key, vars) : key;
}

export function tx(prefix, value) {
  if (!value) return '';
  const key = prefix + '.' + value;
  const out = t(key);
  return out === key ? value : out;
}

export function collate(a, b) {
  const loc = window.PortLightI18n ? PortLightI18n.locale() : undefined;
  try {
    return String(a || '').localeCompare(String(b || ''), loc, { numeric: true, sensitivity: 'base' });
  } catch (err) {
    return String(a || '').localeCompare(String(b || ''));
  }
}

export function errorText(body, status) {
  const detail = body && body.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map(function (item) {
      if (typeof item === 'string') return item;
      if (item && item.msg) return item.msg;
      return '';
    }).filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  return t('error.httpStatus', { status: status });
}

export function escapeHtml(text) {
  if (text === 0) return '0';
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

export function safeHref(url) {
  if (!url) return '';
  const text = String(url).trim();
  const lower = text.toLowerCase();
  if (lower.indexOf('http://') !== 0 && lower.indexOf('https://') !== 0) return '';
  if (/[\s<>]/.test(text)) return '';
  return text;
}
