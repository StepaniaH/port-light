import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const indexHtml = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const entry = indexHtml.match(/app\.js\?v=(\d+)/);
const V = entry ? 'v=' + entry[1] : '';

function makeClassList() {
  const set = new Set();
  return {
    add(...cls) { cls.forEach(c => set.add(c)); },
    remove(...cls) { cls.forEach(c => set.delete(c)); },
    toggle(c, force) {
      if (force === undefined) {
        if (set.has(c)) { set.delete(c); return false; }
        set.add(c); return true;
      }
      if (force) set.add(c); else set.delete(c);
      return !!force;
    },
    contains(c) { return set.has(c); },
  };
}

const SHOW_HEADER_FIELD = {
  key: 'supporter_show_header',
  type: 'bool',
  group: 'supporter',
  origin: 'default',
};

test('index.html exposes Settings Supporter Key tab below divider and router parses #/settings/supporter', async () => {
  const { SETTINGS_PANELS } = await import('../js/state.js?' + V);
  const { parseHash } = await import('../js/router.js?' + V);

  assert.equal(SETTINGS_PANELS[SETTINGS_PANELS.length - 1], 'supporter');
  assert.deepEqual(parseHash('#/settings/supporter'), { name: 'settings', section: 'supporter' });
  assert.match(indexHtml, /id="settings-tab-supporter"/);
  assert.match(indexHtml, /id="settings-tab-supporter-dot"/);
  assert.match(indexHtml, /class="settings-nav-divider"/);
  const advIdx = indexHtml.indexOf('id="settings-tab-advanced"');
  const divIdx = indexHtml.indexOf('class="settings-nav-divider"');
  const supIdx = indexHtml.indexOf('id="settings-tab-supporter"');
  assert.ok(advIdx > 0 && divIdx > advIdx && supIdx > divIdx);
});

test('supporterCardsHtml renders activated plaque, fleet env override, readonly, inactive, and invalid recovery states', async () => {
  const { supporterCardsHtml } = await import('../js/settings-supporter.js?' + V);

  const personal = supporterCardsHtml({
    active: true,
    status: 'active',
    tier: 'personal',
    issued_at: '2026-11-25',
    key_preview: 'PL1.eyJpYXQiOiIyMD…',
    order_preview: 'ord_•••••433',
    origin: 'file',
    env_configured: false,
    can_edit: true,
    can_clear: true,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.match(personal, /supporter-plaque/);
  assert.match(personal, /supporter\.tier\.personal/);
  assert.match(personal, /PL1\.eyJpYXQiOiIyMD…/);
  assert.match(personal, /ord_•••••433/);
  assert.match(personal, /name="supporter_show_header"/);
  assert.match(personal, /<details class="supporter-manage"/);
  assert.match(personal, /data-supporter-activate/);
  assert.match(personal, /data-supporter-clear/);

  const fleetEnv = supporterCardsHtml({
    active: true,
    status: 'active',
    tier: 'fleet',
    issued_at: '2026-11-25',
    key_preview: 'PL1.eyJpYXQiOiIyMD…',
    order_preview: 'ord_•••••999',
    origin: 'env',
    env_configured: true,
    can_edit: true,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.match(fleetEnv, /supporter\.tier\.fleet/);
  assert.match(fleetEnv, /supporter\.origin\.env/);
  assert.match(fleetEnv, /supporter\.manage\.envOverrideHint/);
  assert.match(fleetEnv, /data-supporter-activate/);
  assert.doesNotMatch(fleetEnv, /data-supporter-clear/);

  const readonlyActive = supporterCardsHtml({
    active: true,
    status: 'active',
    tier: 'personal',
    issued_at: '2026-11-25',
    key_preview: 'PL1.eyJpYXQiOiIyMD…',
    order_preview: 'ord_•••••433',
    origin: 'env',
    env_configured: true,
    can_edit: false,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(readonlyActive, /supporter\.manage\.readonly/);
  assert.doesNotMatch(readonlyActive, /data-supporter-clear/);
  assert.match(readonlyActive, /name="supporter_show_header"[^>]*disabled/);

  const inactive = supporterCardsHtml({
    active: false,
    status: 'inactive',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'none',
    env_configured: false,
    can_edit: true,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.doesNotMatch(inactive, /supporter-plaque/);
  assert.match(inactive, /data-supporter-input/);
  assert.match(inactive, /data-supporter-activate/);
  assert.match(inactive, /supporter\.inactive\.composeHint/);
  assert.match(inactive, /https:\/\/portlight\.stepaniah\.me\/#support/);
  assert.match(inactive, /rel="noopener noreferrer"/);

  const savedI18n = window.PortLightI18n;
  window.PortLightI18n = {
    t(key, vars) {
      const raw = key === 'supporter.inactive.composeHint' ? 'Or set {env} in Compose' : key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? '')) : raw;
    },
  };
  try {
    const localizedInactive = supporterCardsHtml({
      active: false, status: 'inactive', tier: null, issued_at: null, key_preview: null,
      order_preview: null, origin: 'none', env_configured: false, can_edit: true, can_clear: false, show_in_header: true,
    }, SHOW_HEADER_FIELD, true, false);
    assert.match(localizedInactive, /<code>PORT_LIGHT_SUPPORTER_KEY<\/code>/);
  } finally {
    if (savedI18n) window.PortLightI18n = savedI18n;
    else delete window.PortLightI18n;
  }

  const invalidFile = supporterCardsHtml({
    active: false,
    status: 'invalid',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'file',
    env_configured: false,
    can_edit: true,
    can_clear: true,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.match(invalidFile, /supporter-alert/);
  assert.match(invalidFile, /supporter\.invalid\.file/);
  assert.match(invalidFile, /data-supporter-clear/);

  const invalidEnv = supporterCardsHtml({
    active: false,
    status: 'invalid',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'env',
    env_configured: true,
    can_edit: true,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.match(invalidEnv, /supporter-alert/);
  assert.match(invalidEnv, /supporter\.invalid\.env/);
  assert.doesNotMatch(invalidEnv, /data-supporter-clear/);
});

test('renderSettingsForm mounts #settings-panel-supporter without duplicating supporter_show_header into Advanced', async () => {
  const { renderSettingsForm } = await import('../js/settings.js?' + V);
  const host = document.createElement('div');
  host.id = 'settings-fields';
  document.body.appendChild(host);
  const lead = document.createElement('p');
  lead.id = 'settings-lead';
  document.body.appendChild(lead);
  const status = document.createElement('p');
  status.id = 'settings-status';
  document.body.appendChild(status);
  try {
    renderSettingsForm({
      values: { supporter_show_header: true },
      readonly: false,
      source: 'auto',
      env_only: {},
      origins: {},
      fields: [SHOW_HEADER_FIELD],
      supporter: {
        active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
        key_preview: 'PL1.eyJpYXQiOiIyMD…', order_preview: 'ord_•••••433',
        origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
      },
    });
    assert.ok(host.querySelector('[data-settings-panel="supporter"] .supporter-plaque'));
    assert.equal(host.querySelectorAll('[data-settings-panel="advanced"] [data-setting="supporter_show_header"]').length, 0);
  } finally {
    host.remove();
    lead.remove();
    status.remove();
  }
});

test('submitSupporterKey, clearSupporterKey, and saving supporter_show_header sync S.meta.supporter and top bar live', async () => {
  const { submitSupporterKey, clearSupporterKey } = await import('../js/settings-supporter.js?' + V);
  const { saveSettingsFields } = await import('../js/settings.js?' + V);
  const { S } = await import('../js/state.js?' + V);

  const prevFetch = globalThis.fetch;
  const prevState = {
    meta: S.meta,
    settings: { ...S.settings },
    settingsDoc: S.settingsDoc,
    settingsConfirmed: { ...S.settingsConfirmed },
    settingsDraft: { ...S.settingsDraft },
    settingsDirtyKeys: new Set(S.settingsDirtyKeys),
    settingsSubmittingKeys: new Set(S.settingsSubmittingKeys),
    settingsKeyRevisions: { ...S.settingsKeyRevisions },
  };

  const header = document.getElementById('app-header');
  header.classList = makeClassList();
  const badge = document.getElementById('supporter-badge');
  const panel = document.createElement('div');
  panel.id = 'settings-panel-supporter';
  document.body.appendChild(panel);
  const form = document.getElementById('settings-form');
  const prevElements = form.elements;

  try {
    S.meta = { supporter: { active: false, tier: null, issued_at: null, show_in_header: true } };
    S.settingsDoc = {
      readonly: false,
      values: { supporter_show_header: true },
      fields: [SHOW_HEADER_FIELD],
      supporter: { active: false, status: 'inactive', tier: null, issued_at: null, key_preview: null, order_preview: null, origin: 'none', env_configured: false, can_edit: true, can_clear: false, show_in_header: true },
    };

    // 1. Invalid submission returns false and keeps inactive
    globalThis.fetch = async () => ({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'supporter_key_invalid' }),
    });
    assert.equal(await submitSupporterKey('PL1.bad.sig'), false);
    assert.equal(S.meta.supporter.active, false);

    // 2. Valid PUT activates supporter and lights header badge live
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/supporter-key');
      assert.equal(init.method, 'PUT');
      return {
        ok: true,
        status: 200,
        json: async () => ({
          supporter: {
            active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
            key_preview: 'PL1.eyJpYXQiOiIyMD…', order_preview: 'ord_•••••777',
            origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
          },
        }),
      };
    };
    assert.equal(await submitSupporterKey('PL1.valid.sig'), true);
    assert.equal(S.meta.supporter.active, true);
    assert.equal(S.meta.supporter.tier, 'fleet');
    assert.equal(badge.hidden, false);
    assert.equal(header.classList.contains('has-supporter-beacon'), true);

    // 3. Toggling supporter_show_header via saveSettingsFields hides header badge immediately
    form.elements = { supporter_show_header: { checked: false } };
    S.settingsDirtyKeys = new Set(['supporter_show_header']);
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = { supporter_show_header: 1 };
    globalThis.fetch = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        readonly: false,
        values: { supporter_show_header: false },
        fields: [SHOW_HEADER_FIELD],
        custom_themes: [],
        supporter: {
          active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
          key_preview: 'PL1.eyJpYXQiOiIyMD…', order_preview: 'ord_•••••777',
          origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: false,
        },
      }),
    });
    assert.equal(await saveSettingsFields(), true);
    assert.equal(S.meta.supporter.show_in_header, false);
    assert.equal(badge.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);

    // 4. DELETE clears saved key and updates header + panel
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/supporter-key');
      assert.equal(init.method, 'DELETE');
      return {
        ok: true,
        status: 200,
        json: async () => ({
          supporter: {
            active: false, status: 'inactive', tier: null, issued_at: null,
            key_preview: null, order_preview: null,
            origin: 'none', env_configured: false, can_edit: true, can_clear: false, show_in_header: false,
          },
        }),
      };
    };
    assert.equal(await clearSupporterKey(), true);
    assert.equal(S.meta.supporter.active, false);
    assert.equal(badge.hidden, true);
  } finally {
    globalThis.fetch = prevFetch;
    form.elements = prevElements;
    panel.remove();
    Object.assign(S, prevState);
  }
});
