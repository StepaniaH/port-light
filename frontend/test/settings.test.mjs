/* Tests for frontend/js/settings.js — HTML builders for the Automation panel
   (string assertions) plus one render test for the appearance panel that runs
   on the helpers/env.mjs DOM stubs. Imports go through the same ?v=
   specifier as the source modules so both sides share one live graph. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { automationCardsHtml, renderField, renderModePicker, renderPalettePicker, renderSettingsForm, themeEditorHtml } = await import('../js/settings.js?' + V);
const { SETTINGS_PANELS, S, applyAppearance, applyDensity, persistAppearance, DENSITY_PRESETS } = await import('../js/state.js?' + V);
const { parseHash } = await import('../js/router.js?' + V);

const mod = { renderSettingsForm, themeEditorHtml, renderField };

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

test('appearance panel renders theme/language/layout sections in order', () => {
  const { renderSettingsForm } = mod;
  const host = document.createElement('div');
  host.id = 'settings-fields';
  document.body.appendChild(host);
  const lead = document.createElement('p');
  lead.id = 'settings-lead';
  document.body.appendChild(lead);
  const status = document.createElement('p');
  status.id = 'settings-status';
  document.body.appendChild(status);
  const save = document.createElement('button');
  save.id = 'settings-save';
  document.body.appendChild(save);
  globalThis.window.PortLightI18n = {
    t(key) { return key; },
    load() { return Promise.resolve('en'); },
    applyDom() {},
  };
  renderSettingsForm({
    values: {}, readonly: false, source: 'auto',
    env_only: {}, origins: {},
    fields: [
      { key: 'theme_mode', type: 'choice', group: 'appearance', choices: ['system', 'dark', 'light'], origin: 'default' },
      { key: 'grid_density', type: 'choice', group: 'appearance', choices: ['loose', 'standard', 'compact'], origin: 'default' },
      { key: 'locale', type: 'choice', group: 'appearance', choices: ['auto', 'en'], origin: 'default' },
      { key: 'show_status_text', type: 'bool', group: 'appearance', origin: 'default' },
    ],
  });
  const panels = host.querySelectorAll('[data-settings-panel="appearance"] .settings-card > header h2');
  const titles = Array.from(panels).map((el) => el.getAttribute('data-i18n'));
  assert.deepEqual(titles, ['settings.sections.language.title', 'settings.sections.theme.title', 'settings.cards.title']);
  const preview = host.querySelector('[data-settings-panel="appearance"] [data-display-preview]');
  assert.ok(preview, 'layout card should carry the live display preview');
  assert.equal(preview.querySelectorAll('.port-cell').length, 3);
  assert.ok(preview.querySelector('.port-cell.used'), 'used sample');
  assert.ok(preview.querySelector('.port-cell.configured'), 'configured sample');
  assert.ok(preview.querySelector('.port-cell.free'), 'free sample');
  assert.ok(preview.querySelector('.access-badge'), 'access badge follows show_access_badge default on');
  assert.ok(preview.querySelector('.proto-badge'), 'proto badge follows show_protocol_badge default on');
  assert.ok(!preview.querySelector('.status-text'), 'no status text while show_status_text is off');
  const panelHtml = host.innerHTML;
  const iDensity = panelHtml.indexOf('name="grid_density"');
  const iPrev = panelHtml.indexOf('data-display-preview');
  const iStatus = panelHtml.indexOf('name="show_status_text"');
  assert.ok(iDensity > -1 && iDensity < iPrev, 'density segmented control renders ahead of the preview');
  assert.ok(iPrev < iStatus, 'preview precedes the card toggles');
  host.remove(); lead.remove(); status.remove(); save.remove();
});

test('theme editor renders 15 color rows and controls', () => {
  const { themeEditorHtml } = mod;
  const out = themeEditorHtml(false);
  const rows = out.match(/data-editor-color=/g) || [];
  assert.equal(rows.length, 15);
  assert.ok(out.includes('id="theme-editor"'));
  assert.ok(out.includes('data-editor-preset'));
  assert.ok(out.includes('data-editor-save'));
  assert.ok(out.includes('data-editor-import'));
  assert.ok(out.includes('data-editor-export'));
  const locked = themeEditorHtml(true);
  assert.match(locked, /disabled/);
});

test('density renders as a segmented radiogroup', () => {
  const { renderField } = mod;
  const html = renderField({ key: 'grid_density', type: 'choice', group: 'appearance', choices: ['loose', 'standard', 'compact'], origin: 'default' }, 'standard', false);
  assert.match(html, /class="segmented"/);
  assert.match(html, /name="grid_density"/);
  assert.match(html, /value="loose"/);
  assert.match(html, /value="standard" checked/);
  assert.match(html, /value="compact"/);
});

test('applyDensity applies presets exactly and falls back to standard', () => {
  const html = document.documentElement;
  applyDensity('loose');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '164px');
  assert.equal(html.style.getPropertyValue('--cell-gap'), '10px');
  applyDensity('standard');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '138px');
  assert.equal(html.style.getPropertyValue('--cell-min-h'), '64px');
  assert.equal(html.style.getPropertyValue('--cell-pad-t'), '10px');
  assert.equal(html.style.getPropertyValue('--cell-pad-b'), '12px');
  applyDensity('compact');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '112px');
  assert.equal(html.style.getPropertyValue('--cell-gap'), '6px');
  assert.equal(html.style.getPropertyValue('--cell-pad-t'), '8px');
  assert.equal(html.style.getPropertyValue('--cell-pad-b'), '8px');
  applyDensity('nonsense');
  assert.equal(html.style.getPropertyValue('--cell-min-w'), '138px');
});

test('applyAppearance previews without persisting; persistAppearance mirrors density', () => {
  localStorage.removeItem('port-light-settings');
  S.settings.grid_density = 'compact';
  applyAppearance();
  assert.equal(localStorage.getItem('port-light-settings'), null);
  persistAppearance();
  const stored = JSON.parse(localStorage.getItem('port-light-settings'));
  assert.equal(stored.grid_density, 'compact');
  assert.ok(!('card_scale' in stored));
  assert.ok(!('text_scale' in stored));
});

test('inline bootstrap table matches DENSITY_PRESETS', async () => {
  const fs = await import('node:fs');
  const src = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  for (const name of Object.keys(DENSITY_PRESETS)) {
    for (const [k, v] of Object.entries(DENSITY_PRESETS[name])) {
      assert.ok(src.includes(k + ':' + v), name + ' ' + k + ':' + v + ' present in bootstrap');
    }
  }
});

test('palette picker lists custom themes with badge and delete hook', () => {
  S.customThemes = [{
    id: 'abcd1234', name: 'Smoke Amber', basedOn: 'gruvbox', mode: 'dark',
    colors: { used: '#e8a33d', configured: '#ffd166', free: '#9ccc65' },
  }];
  try {
    const html = renderPalettePicker(['gruvbox'], '', 'dark', '');
    assert.match(html, /Smoke Amber/);
    assert.match(html, /data-delete-theme="abcd1234"/);
    assert.match(html, /settings\.theme\.customBadge/);
    assert.match(html, /@custom:abcd1234/);
  } finally {
    S.customThemes = [];
  }
});
