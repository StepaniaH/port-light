/* Tests for the Automation panel release flow in frontend/js/settings.js:
   releaseLease against a stubbed fetch, and the delegated copy handler's
   clipboard hardening. Runs on the helpers/env.mjs browser stubs. Imports
   go through the same ?v= specifier as the source modules so both sides
   share one live graph. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { releaseLease, renderSettingsForm } = await import('../js/settings.js?' + V);
const { S } = await import('../js/state.js?' + V);

const NOW = Math.floor(Date.now() / 1000);

async function withFetch(routes, fn) {
  const calls = [];
  const saved = globalThis.fetch;
  globalThis.fetch = function (url, opts) {
    const hit = String(url);
    const method = (opts && opts.method) || 'GET';
    calls.push({ url: hit, method: method });
    const route = routes.filter(function (r) { return r.url === hit && (r.method || 'GET') === method; })[0];
    if (!route) return Promise.resolve({ ok: false, status: 404, json: function () { return Promise.resolve({}); } });
    return Promise.resolve({
      ok: route.ok !== false,
      status: route.status || 200,
      json: function () { return Promise.resolve(route.body); },
    });
  };
  try {
    await fn();
  } finally {
    globalThis.fetch = saved;
  }
  return calls;
}

function useI18n() {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const dict = {
        'settings.auto.activity.total': 'Calls',
        'settings.auto.activity.activeLeases': 'Active leases',
        'settings.auto.activity.lastUsed': 'Last used: {time}',
        'settings.auto.activity.never': 'never',
        'settings.auto.leases.none': 'No active leases.',
      };
      const raw = dict[key] != null ? dict[key] : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  return function restore() {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  };
}

function metaPayload(total, activeLeases, rows) {
  return {
    automation: {
      agent_token: false,
      metrics: true,
      webhook: false,
      history_days: 7,
      events_stream: true,
      agent_events: {
        total: total,
        last_used_at: NOW - 300,
        active_leases: activeLeases,
        recent: [
          { ts: NOW - 300, count: 1, scope: 'all:1/1', label: 'smoke', leased: true },
          { ts: NOW - 120, count: 2, scope: 'all:1/1', label: 'smoke', leased: true },
        ].slice(0, total > 1 ? 2 : 1),
        lease_rows: rows,
      },
    },
  };
}

function leasedMeta() {
  return metaPayload(2, 1, [{ port: 8081, label: 'smoke', expires_at: NOW + 600 }]);
}

test('releaseLease sends DELETE to the manual port endpoint', async () => {
  S.meta = leasedMeta();
  const btn = { disabled: false };
  const calls = await withFetch([
    { url: '/api/manual-ports/8081', method: 'DELETE', body: {} },
    { url: '/api/meta', body: metaPayload(2, 0, []) },
  ], async function () {
    await releaseLease(8081, btn);
  });
  assert.deepEqual(calls[0], { url: '/api/manual-ports/8081', method: 'DELETE' });
});

test('successful release refreshes meta and re-renders activity and lease cards together', async () => {
  const restoreI18n = useI18n();
  try {
    S.meta = leasedMeta();
    const panel = document.getElementById('settings-panel-automation');
    panel.innerHTML = '<p data-stale>old</p>';
    const btn = { disabled: false };
    await withFetch([
      { url: '/api/manual-ports/8081', method: 'DELETE', body: {} },
      { url: '/api/meta', body: metaPayload(3, 0, []) },
    ], async function () {
      await releaseLease(8081, btn);
    });
    assert.equal(S.meta.automation.agent_events.active_leases, 0);
    assert.match(panel.innerHTML, /data-auto-summary>Calls: 3 · Active leases: 0 · Last used: \d+m</);
    assert.doesNotMatch(panel.innerHTML, /data-release-port="8081"/);
    assert.ok(panel.innerHTML.includes('No active leases.'));
    assert.doesNotMatch(panel.innerHTML, /data-stale/);
  } finally {
    restoreI18n();
  }
});

test('failed release re-enables the button and skips the meta refresh', async () => {
  S.meta = leasedMeta();
  const panel = document.getElementById('settings-panel-automation');
  panel.innerHTML = '<p data-keep>untouched</p>';
  const btn = { disabled: false };
  const calls = await withFetch([
    { url: '/api/manual-ports/8081', method: 'DELETE', ok: false, status: 409, body: { detail: 'nope' } },
  ], async function () {
    await releaseLease(8081, btn);
  });
  assert.equal(btn.disabled, false);
  assert.equal(calls.length, 1);
  assert.equal(panel.innerHTML, '<p data-keep>untouched</p>');
});

test('rejected fetch during release re-enables the button without an unhandled rejection', async () => {
  S.meta = leasedMeta();
  const panel = document.getElementById('settings-panel-automation');
  panel.innerHTML = '<p data-keep>untouched</p>';
  const btn = { disabled: true };
  const savedFetch = globalThis.fetch;
  globalThis.fetch = function () { return Promise.reject(new Error('unreachable')); };
  let unhandled = null;
  const onUnhandled = function (reason) { if (!unhandled) unhandled = reason; };
  process.on('unhandledRejection', onUnhandled);
  try {
    await releaseLease(8081, btn);
    await new Promise(function (resolve) { setImmediate(resolve); });
  } finally {
    process.off('unhandledRejection', onUnhandled);
    globalThis.fetch = savedFetch;
  }
  assert.equal(btn.disabled, false);
  assert.equal(panel.innerHTML, '<p data-keep>untouched</p>');
});

test('copy click survives clipboard denial without an unhandled rejection', async () => {
  const savedForm = document.getElementById('settings-form');
  savedForm.elements = {};
  const captured = {};
  const savedAdd = document.addEventListener;
  document.addEventListener = function (type, fn) {
    (captured[type] = captured[type] || []).push(fn);
  };
  try {
    renderSettingsForm({ fields: [], values: {}, readonly: true });
  } finally {
    document.addEventListener = savedAdd;
  }
  const clickHandler = (captured.click || [])[0];
  assert.ok(clickHandler, 'delegate registered');

  const src = document.getElementById('al-curl');
  src.textContent = 'curl -s http://127.0.0.1:2100';
  const copyBtn = {
    getAttribute(key) { return key === 'data-copy' ? 'al-curl' : 'Copy'; },
    textContent: '',
  };
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const fakeNavigator = { clipboard: { writeText: function () { return Promise.reject(new Error('denied')); } } };
  Object.defineProperty(globalThis, 'navigator', { value: fakeNavigator, configurable: true });

  let unhandled = null;
  const onUnhandled = function (reason) { if (!unhandled) unhandled = reason; };
  process.on('unhandledRejection', onUnhandled);
  try {
    clickHandler({ target: { closest: function (sel) { return sel === '[data-copy]' ? copyBtn : null; } } });
    await new Promise(function (resolve) { setImmediate(resolve); });
  } finally {
    process.off('unhandledRejection', onUnhandled);
    if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator);
    else delete globalThis.navigator;
  }
  assert.equal(unhandled, null);
});
