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

const { automationCardsHtml, renderModePicker, renderPalettePicker } = await import('../js/settings.js?' + V);
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
  const html = automationCardsHtml(Object.assign({}, base, { listen_port: 8899 }));
  assert.match(html, /"command":\s*"docker",\s*"args":\s*\[\s*"exec",\s*"-i",\s*"port-light",\s*"python",\s*"mcp\/server\.py"/);
  assert.match(html, /mcp\/server\.py/);
  assert.doesNotMatch(html, /X-Agent-Token/);
});

test('docker MCP env port follows meta listen_port', () => {
  const html = automationCardsHtml(Object.assign({}, base, { listen_port: 8899 }));
  const urls = html.match(/"PORT_LIGHT_URL":\s*"[^"]+"/g) || [];
  assert.equal(urls.length, 2);
  assert.ok(urls.includes('"PORT_LIGHT_URL": "http://127.0.0.1:8899"'));
});

test('docker MCP env falls back to placeholder without listen_port', () => {
  const html = automationCardsHtml(base);
  assert.match(html, /"PORT_LIGHT_URL":\s*"http:\/\/127\.0\.0\.1:&lt;port&gt;"/);
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
    t(key, vars) {
      const dict = {
        'settings.auto.activity.total': 'Calls',
        'settings.auto.activity.activeLeases': 'Active leases',
        'settings.auto.activity.lastUsed': 'Last used: {time}',
        'settings.auto.activity.never': 'never',
      };
      const raw = dict[key] != null ? dict[key] : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    const html = automationCardsHtml(withEvents());
    assert.match(html, /data-auto-summary>Calls: 2 · Active leases: 1 · Last used: \d+m</);
    assert.doesNotMatch(html, /· settings\.auto\./);
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});

test('activity summary shows relative last-used time for recent use', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const raw = key === 'settings.auto.activity.lastUsed' ? 'Last used: {time}' : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    const html = automationCardsHtml(withEvents());
    assert.match(html, /data-auto-summary>[^<]*Last used: 5m</);
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});

test('activity summary falls back to never without last_used_at', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const raw = key === 'settings.auto.activity.lastUsed'
        ? 'Last used: {time}'
        : key === 'settings.auto.activity.never' ? 'never' : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    const events = withEvents().agent_events;
    events.last_used_at = null;
    const html = automationCardsHtml(Object.assign({}, base, { agent_events: events }));
    assert.match(html, /data-auto-summary>[^<]*Last used: never</);
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

test('mode picker renders three core radios named theme_mode', () => {
  const html = renderModePicker(['system', 'dark', 'light'], 'system', '');
  assert.match(html, /name="theme_mode" value="system"/);
  assert.match(html, /name="theme_mode" value="dark"/);
  assert.match(html, /name="theme_mode" value="light"/);
  assert.doesNotMatch(html, /name="theme"/);
});

test('palette picker renders builtin plus ten families', () => {
  const choices = ['', 'gruvbox', 'catppuccin', 'solarized', 'nord', 'dracula',
    'tokyo-night', 'one-dark', 'everforest', 'rose-pine', 'kanagawa'];
  const html = renderPalettePicker(choices, '', 'dark', '');
  assert.match(html, /name="theme_palette" value=""/);
  assert.match(html, /name="theme_palette" value="dracula"/);
  assert.equal((html.match(/label class="theme-swatch/g) || []).length, 11);
});

test('palette picker greys mismatched single-variant families', () => {
  const choices = ['', 'nord', 'dracula', 'gruvbox'];
  const light = renderPalettePicker(choices, '', 'light', '');
  assert.match(light, /is-unavailable[^>]*>\s*<input type="radio" name="theme_palette" value="dracula"[^>]*disabled/s);
  assert.doesNotMatch(light, /value="gruvbox"[^>]*disabled/);
  const dark = renderPalettePicker(choices, '', 'dark', '');
  assert.doesNotMatch(dark, /value="dracula"[^>]*disabled/);
});

test('palette preview resolves variant per current mode', () => {
  const choices = ['', 'gruvbox'];
  assert.match(renderPalettePicker(choices, '', 'light', ''), /data-theme-preview="gruvbox-light"/);
  assert.match(renderPalettePicker(choices, '', 'dark', ''), /data-theme-preview="gruvbox"(?!-)/);
});
