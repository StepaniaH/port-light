import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { S } = await import('../js/state.js?v=' + version);
const { bindAddressView, buildSearchContext, renderGrid, renderSummary, renderScanners } = await import('../js/grid.js?v=' + version);

test('disabled scanner indicators are neutral, not failure indicators', () => {
  const root = document.createElement('div');
  renderScanners({}, root, { summary: { sources: { listen: 'ok', docker: 'failed', compose: 'disabled' } } });
  assert.equal(root.querySelectorAll('.pill.disabled').length, 1);
  assert.equal(root.querySelectorAll('.pill.bad').length, 1);
});

test('search and summary do not claim free ports from incomplete or stale data', () => {
  for (let port = 41950; port < 42051; port++) S.knownCache[port] = null;
  for (const summary of [{ scan_complete: false }, { scan_complete: true, stale: true }, {}]) {
    const rows = buildSearchContext([], 42000, { summary });
    assert.ok(rows.length > 0);
    assert.ok(rows.every(row => row.status === 'unknown' && row._unavailable));
    renderSummary({ ...summary, used: 0, configured: 0, free: 123 });
    const html = document.getElementById('summary').innerHTML;
    assert.match(html, /scanner.snapshotUnavailable/);
    assert.doesNotMatch(html, /123/);
  }
  const rows = buildSearchContext([], 42000, { summary: { scan_complete: true } });
  assert.ok(rows.every(row => row.status === 'free'));
});

test('bind address view is optional and keeps full addresses out of visual elision', () => {
  const off = bindAddressView(['10.0.0.8'], { enabled: false });
  assert.equal(off.html, '');

  const on = bindAddressView([
    '10.0.0.8', '10.0.0.9', '2001:db8:85a3::8a2e:370:7334',
  ], { enabled: true, showV4: true, showV6: true, density: 'compact' });
  assert.match(on.html, /bind-family">v4/);
  assert.match(on.html, /bind-family">v6/);
  assert.match(on.html, /2001:…:7334/);
  assert.match(on.html, /bind-more">\+1/);
  assert.ok(on.titleParts.some((part) => part.includes('2001:db8:85a3::8a2e:370:7334')));
});

test('bind address view omits wildcard-only families from cards', () => {
  const onlyWildcards = bindAddressView(['0.0.0.0', '::'], { enabled: true });
  assert.equal(onlyWildcards.html, '');
  assert.deepEqual(onlyWildcards.ariaParts, []);

  const mixed = bindAddressView(['0.0.0.0', 'fd12::19'], { enabled: true });
  assert.doesNotMatch(mixed.html, /v4/);
  assert.match(mixed.html, /v6/);
  assert.match(mixed.html, /fd12::19/);
});

test('grid cards render selected bind families and expose exact values in title', () => {
  const previous = Object.assign({}, S.settings);
  const root = document.createElement('div');
  document.body.appendChild(root);
  const row = {
    port: 5353, status: 'used', source_type: 'host', protocol: 'udp',
    ips: ['192.168.1.20', 'fd12:3456:789a:1::19'], bind_scope: 'lan',
    containers: [], compose_configs: [], known_service: { name: 'mDNS' }, urls: [],
  };
  try {
    Object.assign(S.settings, {
      show_bind_addresses: true, show_bind_ipv4: true, show_bind_ipv6: false,
      grid_density: 'standard', show_access_badge: true, show_protocol_badge: true,
    });
    renderGrid([row], root, { ports: [row], summary: { scan_complete: true } }, 'local');
    assert.equal(root.querySelectorAll('.bind-address-row').length, 1);
    assert.match(root.innerHTML, /class="bind-family">v4<\/span>/);
    const card = root.querySelector('.port-cell');
    assert.match(card.getAttribute('title'), /IPv4 192\.168\.1\.20/);
    assert.doesNotMatch(card.getAttribute('title'), /IPv6/);
  } finally {
    S.settings = previous;
    root.remove();
  }
});

test('bind address accessibility labels use complete locale templates', () => {
  const previous = window.PortLightI18n;
  try {
    for (const locale of ['en', 'zh-CN']) {
      const messages = JSON.parse(readFileSync(new URL('../locales/' + locale + '.json', import.meta.url), 'utf8'));
      window.PortLightI18n = {
        t(key, vars = {}) {
          return key.split('.').reduce((value, part) => value[part], messages)
            .replace(/\{(\w+)\}/g, (_, name) => vars[name]);
        },
      };
      const view = bindAddressView(['0.0.0.0', '192.0.2.1', '2001:db8::1'], { enabled: true });
      assert.deepEqual(view.ariaParts, [locale === 'en'
        ? 'IPv6: 2001:db8::1'
        : 'IPv6：2001:db8::1']);
    }
  } finally {
    window.PortLightI18n = previous;
  }
});
