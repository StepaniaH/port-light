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

const { automationCardsHtml, formatRefreshInterval, renderField, renderModePicker, renderPalettePicker, renderPeersEditor, renderSettingsForm, syncDependentSettings, syncPaletteAvailability, syncRefreshCapacity, themeEditorHtml, updateRefreshSlider } = await import('../js/settings.js?' + V);
const { SETTINGS_PANELS, S, applyAppearance, applyDensity, persistAppearance, DENSITY_PRESETS } = await import('../js/state.js?' + V);
const { parseHash } = await import('../js/router.js?' + V);

const mod = { renderSettingsForm, themeEditorHtml, renderField };

test('invalid scanner selections show repair guidance without disabling the form', () => {
  const field = { key: 'local_scanners', type: 'multi_choice', choices: ['listen', 'docker', 'compose'] };
  const editable = renderField(field, [], false, {});
  assert.match(editable, /role="alert" data-i18n="settings.scanners.invalid"/);
  assert.doesNotMatch(editable, /<input[^>]* disabled/);
  assert.doesNotMatch(renderField(field, ['listen'], false, {}), /settings.scanners.invalid/);
  assert.match(renderField(field, [], true, {}), /<input[^>]* disabled/);
});

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

test('palette picker enables every built-in family in light and dark modes', () => {
  const choices = ['', 'nord', 'dracula', 'gruvbox', 'catppuccin', 'solarized',
    'tokyo-night', 'one-dark', 'everforest', 'rose-pine', 'kanagawa'];
  for (const mode of ['light', 'dark']) {
    const picker = renderPalettePicker(choices, '', mode, '');
    assert.doesNotMatch(picker, /is-unavailable| disabled/);
    for (const family of choices.slice(1)) {
      assert.ok(picker.includes(`data-theme-preview="${family}${mode === 'light' ? '-light' : ''}"`));
    }
  }
});

test('palette preview resolves variant per current mode', () => {
  const choices = ['', 'gruvbox'];
  assert.match(renderPalettePicker(choices, '', 'light', ''), /data-theme-preview="gruvbox-light"/);
  assert.match(renderPalettePicker(choices, '', 'dark', ''), /data-theme-preview="gruvbox"(?!-)/);
});

test('system-mode palette refresh keeps readonly settings locked', () => {
  const previous = { doc: S.settingsDoc, settings: { ...S.settings }, themes: S.customThemes };
  const queryAll = document.querySelectorAll;
  const root = document.createElement('div');
  document.body.appendChild(root);
  try {
    S.settingsDoc = { readonly: true };
    S.settings.theme_mode = 'light';
    S.customThemes = [{ id: '12345678', name: 'Light custom', mode: 'light',
      colors: { used: '#275da8', configured: '#785a00', free: '#3f651f' } }];
    root.innerHTML = renderPalettePicker(['', 'nord', 'dracula'], '', 'light', ' disabled');
    document.querySelectorAll = selector => root.querySelectorAll(selector);
    root.querySelectorAll('input').forEach(input => {
      input.value = input.getAttribute('value') || '';
      input.disabled = input.hasAttribute('disabled');
    });
    syncPaletteAvailability();
    assert.ok(root.querySelectorAll('input[name="theme_palette"]').every(input => input.disabled));
  } finally {
    document.querySelectorAll = queryAll;
    root.remove();
    S.settingsDoc = previous.doc;
    S.settings = previous.settings;
    S.customThemes = previous.themes;
  }
});

test('bind address switches omit repeated environment source hints', () => {
  for (const key of ['show_bind_addresses', 'show_bind_ipv4', 'show_bind_ipv6']) {
    const html = renderField({
      key, type: 'bool', group: 'appearance', origin: 'env', env: key.toUpperCase(),
      label: key, help: key,
    }, true, false);
    assert.doesNotMatch(html, /origin-hint/);
  }
  const ordinary = renderField({
    key: 'show_status_text', type: 'bool', group: 'appearance', origin: 'env',
    env: 'SHOW_STATUS_TEXT', label: 'Status', help: 'Status text',
  }, true, false);
  assert.match(ordinary, /origin-hint/);
});

test('local scanner field renders intent separately from runtime state', () => {
  const html = renderField({
    key: 'local_scanners', type: 'multi_choice', group: 'local', origin: 'env',
    env: 'PORT_LIGHT_SCANNERS', choices: ['listen', 'docker', 'compose'],
    label: 'Local scanners', help: 'Select scanners',
  }, ['listen', 'compose'], false, {
    local_scanning: { scanners: [
      { id: 'listen', enabled: true, state: 'ok' },
      { id: 'docker', enabled: false, state: 'disabled' },
      { id: 'compose', enabled: true, state: 'failed' },
    ] },
  });
  assert.match(html, /name="local_scanners" value="listen" checked/);
  assert.match(html, /name="local_scanners" value="docker"(?! checked)/);
  assert.match(html, /name="local_scanners" value="compose" checked/);
  assert.match(html, /scanner-state ok/);
  assert.match(html, /scanner-state failed/);
  assert.equal((html.match(/origin-hint/g) || []).length, 1);
});

test('local name and discovery controls have accessible labels', () => {
  for (const [key, type, value] of [['host_name', 'str', 'Preview'],
    ['compose_scan_depth', 'int', 1], ['compose_scan_exclude_dirs', 'string_list', []]]) {
    const html = renderField({ key, type, origin: 'default' }, value, false);
    assert.ok(html.includes('id="setting-label-' + key + '"'));
    assert.ok(html.includes('aria-labelledby="setting-label-' + key + '"'));
  }
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
    values: { show_bind_addresses: false, show_bind_ipv4: true, show_bind_ipv6: true }, readonly: false, source: 'auto',
    env_only: {}, origins: {},
    fields: [
      { key: 'host_layout', type: 'choice', group: 'appearance', choices: ['waterfall', 'tabs'], origin: 'default' },
      { key: 'theme_mode', type: 'choice', group: 'appearance', choices: ['system', 'dark', 'light'], origin: 'default' },
      { key: 'grid_density', type: 'choice', group: 'appearance', choices: ['loose', 'standard', 'compact'], origin: 'default' },
      { key: 'locale', type: 'choice', group: 'appearance', choices: ['auto', 'en'], origin: 'default' },
      { key: 'show_status_text', type: 'bool', group: 'appearance', origin: 'default' },
      { key: 'show_bind_addresses', type: 'bool', group: 'appearance', origin: 'default' },
      { key: 'show_bind_ipv4', type: 'bool', group: 'appearance', origin: 'default' },
      { key: 'show_bind_ipv6', type: 'bool', group: 'appearance', origin: 'default' },
    ],
  });
  const panels = host.querySelectorAll('[data-settings-panel="appearance"] .settings-card > header h2');
  const titles = Array.from(panels).map((el) => el.getAttribute('data-i18n'));
  assert.deepEqual(titles, ['settings.sections.language.title', 'settings.sections.theme.title', 'settings.cards.title']);
  const layoutRow = host.querySelector('[data-settings-panel="appearance"] [data-setting="host_layout"]');
  assert.ok(layoutRow, 'multi-machine layout belongs to Appearance');
  assert.equal(layoutRow.closest('.settings-card').querySelector('h2').getAttribute('data-i18n'), 'settings.cards.title');
  assert.equal(host.querySelectorAll('[data-settings-panel="occupancy"] [data-setting="host_layout"]').length, 0);
  const preview = host.querySelector('[data-settings-panel="appearance"] [data-display-preview]');
  assert.ok(preview, 'layout card should carry the live display preview');
  assert.equal(preview.querySelectorAll('.port-cell').length, 3);
  assert.ok(preview.querySelector('.port-cell.used'), 'used sample');
  assert.ok(preview.querySelector('.port-cell.configured'), 'configured sample');
  assert.ok(preview.querySelector('.port-cell.free'), 'free sample');
  assert.ok(preview.querySelector('.access-badge'), 'access badge follows show_access_badge default on');
  assert.ok(preview.querySelector('.proto-badge'), 'proto badge follows show_protocol_badge default on');
  assert.ok(!preview.querySelector('.status-text'), 'no status text while show_status_text is off');
  const bindParent = host.querySelector('input[name="show_bind_addresses"]');
  const bindFamilies = host.querySelector('#bind-address-family-options');
  const bindIpv4 = host.querySelector('input[name="show_bind_ipv4"]');
  const bindIpv6 = host.querySelector('input[name="show_bind_ipv6"]');
  assert.equal(bindParent.hasAttribute('checked'), false);
  assert.equal(bindIpv4.hasAttribute('checked'), true);
  assert.equal(bindIpv6.hasAttribute('checked'), true);
  assert.equal(bindFamilies.hasAttribute('hidden'), true);
  bindParent.checked = true;
  // The minimal DOM parser records boolean attributes but does not reflect
  // them to matching element properties or populate form.elements like a
  // browser does.
  bindIpv4.checked = true;
  bindIpv6.checked = true;
  const settingsForm = document.getElementById('settings-form');
  settingsForm.elements.show_bind_addresses = bindParent;
  settingsForm.elements.show_bind_ipv4 = bindIpv4;
  settingsForm.elements.show_bind_ipv6 = bindIpv6;
  syncDependentSettings();
  assert.equal(bindFamilies.hasAttribute('hidden'), false);
  assert.equal(bindParent.getAttribute('aria-expanded'), 'true');
  assert.equal(bindIpv4.checked, true);
  assert.equal(bindIpv6.checked, true);
  bindIpv4.checked = false;
  bindIpv6.checked = false;
  syncDependentSettings();
  assert.equal(bindParent.checked, true, 'family choices do not change the master switch');
  assert.equal(bindFamilies.hasAttribute('hidden'), false);
  bindParent.checked = false;
  syncDependentSettings();
  assert.equal(bindFamilies.hasAttribute('hidden'), true);
  bindParent.checked = true;
  syncDependentSettings();
  assert.equal(bindIpv4.checked, false, 'master switch preserves IPv4 preference');
  assert.equal(bindIpv6.checked, false, 'master switch preserves IPv6 preference');
  const panelHtml = host.innerHTML;
  const iDensity = panelHtml.indexOf('name="grid_density"');
  assert.ok(panelHtml.indexOf('name="host_layout"') < iDensity, 'machine layout precedes individual card density');
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

test('host layout and optional description use the settings schema', () => {
  const layout = renderField({ key: 'host_layout', type: 'choice', choices: ['waterfall', 'tabs'] }, 'waterfall', false);
  assert.match(layout, /name="host_layout" value="waterfall" checked/);
  assert.match(layout, /name="host_layout" value="tabs"/);
  const description = renderField({ key: 'host_description', type: 'str', max_length: 120 }, 'Tailscale 100.64.0.12', false);
  assert.match(description, /maxlength="120"/);
  assert.match(description, /aria-labelledby="setting-label-host_description"/);
});

test('host layout choices have previews, explicit selection marks and accessible descriptions', () => {
  const field = { key: 'host_layout', type: 'choice', choices: ['waterfall', 'tabs'] };
  for (const selected of field.choices) {
    const html = renderField(field, selected, false);
    const host = document.createElement('div');
    host.innerHTML = html;
    assert.equal(host.querySelectorAll('.layout-option').length, 2);
    assert.equal(host.querySelectorAll('.layout-option-selected').length, 2);
    assert.equal(host.querySelectorAll('.layout-preview[aria-hidden="true"]').length, 2);
    assert.equal(host.querySelectorAll('input[checked]').length, 1);
    assert.equal(host.querySelector('input[checked]').getAttribute('value'), selected);
    for (const choice of field.choices) {
      const radio = host.querySelector('input[value="' + choice + '"]');
      assert.equal(radio.getAttribute('aria-labelledby'), 'layout-title-' + choice);
      assert.equal(radio.getAttribute('aria-describedby'), 'layout-help-' + choice);
      assert.ok(host.querySelector('#layout-title-' + choice));
      assert.ok(host.querySelector('#layout-help-' + choice));
    }
    assert.doesNotMatch(html, /class="segmented"/);
  }
  const locked = renderField(field, 'tabs', true);
  assert.equal((locked.match(/ disabled/g) || []).length, 2);
});

test('refresh interval renders as a discrete slider and preserves custom values', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const raw = key === 'settings.refresh.seconds' ? '{count}s'
        : key === 'settings.refresh.minutes' ? '{count}m' : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    const html = renderField({ key: 'refresh_ms', type: 'int', min: 1000, max: 300000 }, 8000, false);
    assert.match(html, /type="range"/);
    assert.match(html, /data-refresh-values="5000,8000,10000,15000,30000,60000,120000,300000"/);
    assert.match(html, /type="hidden" name="refresh_ms" value="8000"/);
    assert.match(html, />8s<\/output>/);
    assert.doesNotMatch(html, /type="number"/);
    assert.equal(formatRefreshInterval(120000), '2m');
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});

test('refresh slider updates its submitted value and peer recommendation', () => {
  const savedI18n = globalThis.window.PortLightI18n;
  const savedQuery = document.querySelector;
  const savedPeers = S.peersDraft;
  const savedCatalog = S.hostCatalog;
  const root = document.createElement('div');
  document.body.appendChild(root);
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      const dict = {
        'settings.refresh.seconds': '{count}s',
        'settings.refresh.capacity': '{current}/{recommended}/{hard}',
        'settings.refresh.overCapacity': 'over:{current}/{recommended}/{hard}',
        'settings.refresh.capacityLimit': 'limit:{current}/{hard}',
      };
      const raw = dict[key] || key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, name) => String(vars[name])) : raw;
    },
  };
  try {
    S.peersDraft = Array.from({ length: 7 }, () => ({}));
    S.hostCatalog = { local: { id: 'local' }, peers: [], max_peers: 32 };
    root.innerHTML = renderField({ key: 'refresh_ms', type: 'int' }, 5000, false);
    document.querySelector = selector => root.querySelector(selector);
    const slider = root.querySelector('[data-refresh-slider]');
    const hidden = root.querySelector('[data-refresh-hidden]');
    slider.matches = selector => selector === '[data-refresh-slider]';
    slider.value = '3';
    hidden.value = '5000';
    assert.equal(updateRefreshSlider(slider), true);
    assert.equal(hidden.value, '30000');
    assert.equal(slider.getAttribute('aria-valuetext'), '30s');
    assert.equal(document.getElementById('refresh-capacity').textContent, 'limit:7/32');
    assert.equal(slider.style.getPropertyValue('--refresh-progress'), '50%');
    hidden.value = '300000';
    syncRefreshCapacity();
    assert.equal(document.getElementById('refresh-capacity').textContent, 'limit:7/32');
    hidden.value = '5000';
    syncRefreshCapacity();
    assert.equal(document.getElementById('refresh-capacity').textContent, 'over:7/6/32');
  } finally {
    document.querySelector = savedQuery;
    S.peersDraft = savedPeers;
    S.hostCatalog = savedCatalog;
    root.remove();
    if (savedI18n) globalThis.window.PortLightI18n = savedI18n;
    else delete globalThis.window.PortLightI18n;
  }
});

test('peer editor collapses saved rows and honors the backend limit', () => {
  const savedI18n = globalThis.window.PortLightI18n;
  const savedPeers = S.peersDraft;
  const savedCatalog = S.hostCatalog;
  const host = document.createElement('div');
  host.id = 'settings-peers';
  document.body.appendChild(host);
  globalThis.window.PortLightI18n = {
    t(key, vars) {
      if (key === 'hosts.max') return 'Maximum ' + vars.count;
      return key;
    },
  };
  try {
    S.hostCatalog = { local: { id: 'local' }, peers: [], max_peers: 32 };
    S.peersDraft = Array.from({ length: 7 }, (_, i) => ({
      id: 'peer000' + i, name: 'Peer ' + i, url: 'http://10.0.0.' + (i + 1) + ':2100',
    }));
    renderPeersEditor(false);
    assert.equal(host.querySelectorAll('details.peer-row').length, 7);
    assert.equal(host.querySelectorAll('input[data-peer-field="description"][maxlength="120"]').length, 7);
    assert.equal(host.querySelectorAll('details.peer-row[open]').length, 0);
    assert.match(host.innerHTML, /Maximum 32/);
    assert.equal(host.querySelector('#peer-add').hasAttribute('disabled'), false);

    S.peersDraft = Array.from({ length: 32 }, (_, i) => ({ id: 'peer' + i, name: 'P' + i, url: 'http://10.0.0.1' }));
    renderPeersEditor(false);
    assert.equal(host.querySelector('#peer-add').hasAttribute('disabled'), true);
  } finally {
    S.peersDraft = savedPeers;
    S.hostCatalog = savedCatalog;
    host.remove();
    if (savedI18n) globalThis.window.PortLightI18n = savedI18n;
    else delete globalThis.window.PortLightI18n;
  }
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
