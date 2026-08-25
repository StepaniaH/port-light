/* Tests for frontend/js/settings.js — pure HTML builders for the Automation
   panel. String assertions only; no DOM. Imports go through the same ?v=
   specifier as the source modules so both sides share one live graph. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { automationCardsHtml } = await import('../js/settings.js?' + V);
const { SETTINGS_PANELS } = await import('../js/state.js?' + V);
const { parseHash } = await import('../js/router.js?' + V);

const base = {
  agent_token: false,
  metrics: true,
  webhook: false,
  history_days: 7,
  events_stream: true,
  suggest_peers: false,
};

const NOW = Math.floor(Date.now() / 1000);

const withEvents = () => Object.assign({}, base, {
  agent_events: {
    total: 2,
    last_used_at: NOW - 300,
    active_leases: 1,
    recent: [{ ts: NOW - 300, count: 1, scope: 'all:1/1', label: 'preview', leased: true }],
    lease_rows: [{ port: 8081, label: 'preview', expires_at: NOW + 600 }],
  },
});

test('connect card renders both MCP variants and curl without token', () => {
  const html = automationCardsHtml(base);
  assert.match(html, /"command":\s*"docker",\s*"args":\s*\[\s*"exec",\s*"-i",\s*"port-light",\s*"python",\s*"mcp\/server\.py"/);
  assert.match(html, /mcp\/server\.py/);
  assert.doesNotMatch(html, /X-Agent-Token/);
});

test('token gate adds placeholder header line only', () => {
  const html = automationCardsHtml(Object.assign({}, base, { agent_token: true }));
  assert.match(html, /X-Agent-Token: &lt;your-token&gt;/);
  assert.doesNotMatch(html, /sekrit/);
});

test('activity disabled note when history off', () => {
  const html = automationCardsHtml(Object.assign({}, base, { history_days: 0 }));
  assert.ok(html.includes('data-auto="activity-disabled"'));
});

test('lease rows render ports with release hooks', () => {
  const html = automationCardsHtml(withEvents());
  assert.match(html, /data-release-port="8081"/);
  assert.ok(html.includes('>preview<'));
  assert.match(html, /data-auto-summary/);
});

test('activity summary resolves labels through the activity subtree', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key) {
      const dict = {
        'settings.auto.activity.total': 'Calls',
        'settings.auto.activity.activeLeases': 'Active leases',
      };
      return dict[key] || key;
    },
  };
  try {
    const html = automationCardsHtml(withEvents());
    assert.match(html, /data-auto-summary>Calls: 2 · Active leases: 1</);
    assert.doesNotMatch(html, /· settings\.auto\./);
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});

test('lease rows show remaining time through the remaining key', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const raw = key === 'settings.auto.leases.remaining' ? '{time} left' : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    const html = automationCardsHtml(withEvents());
    assert.match(html, /class="lease-left">10m left</);
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});

test('automation panel is registered between occupancy and advanced', () => {
  assert.deepEqual(SETTINGS_PANELS, ['appearance', 'occupancy', 'automation', 'advanced']);
  assert.deepEqual(parseHash('#/settings/automation'), { name: 'settings', section: 'automation' });
});
