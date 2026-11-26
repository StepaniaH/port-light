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
  assert.match(personal, /plaque-eyebrow/);
  assert.match(personal, /supporter\.plaque\.eyebrow/);
  assert.match(personal, /class="beacon-glyph"/);
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
  assert.match(inactive, /supporter-showcase/);
  assert.match(inactive, /supporter-inactive/);
  assert.ok(inactive.indexOf('supporter-showcase') < inactive.indexOf('supporter-inactive'), 'normal unactivated state puts showcase before activation card');
  assert.match(inactive, /data-supporter-preview/);
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

  const invalidFileReadonly = supporterCardsHtml({
    active: false,
    status: 'invalid',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'file',
    env_configured: true,
    can_edit: false,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(invalidFileReadonly, /supporter\.invalid\.fileReadonly/);
  assert.doesNotMatch(invalidFileReadonly, /data-supporter-clear/);
  assert.doesNotMatch(invalidFileReadonly, /supporter\.manage\.readonly/, 'readonly invalid file card must not show contradictory env guidance');
  assert.doesNotMatch(invalidFileReadonly, /supporter\.inactive\.composeHint/, 'readonly invalid file card must not repeat Compose env hint');

  const invalidEnvReadonly = supporterCardsHtml({
    active: false,
    status: 'invalid',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'env',
    env_configured: true,
    can_edit: false,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(invalidEnvReadonly, /supporter\.invalid\.envReadonly/);
  assert.doesNotMatch(invalidEnvReadonly, /supporter\.manage\.readonly/, 'readonly invalid env card must not duplicate readonly guidance');
  assert.doesNotMatch(invalidEnvReadonly, /supporter\.inactive\.composeHint/);

  const inactiveReadonly = supporterCardsHtml({
    active: false,
    status: 'inactive',
    tier: null,
    issued_at: null,
    key_preview: null,
    order_preview: null,
    origin: 'none',
    env_configured: false,
    can_edit: false,
    can_clear: false,
    show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(inactiveReadonly, /supporter\.manage\.readonly/);
  assert.match(inactiveReadonly, /supporter\.inactive\.composeHint/);
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

test('concurrent settings autosave and supporter key mutations preserve newer state and lock in-flight requests', async () => {
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
    supporterRevision: S.supporterRevision,
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
    // Race 1: PUT /api/settings started while active, then DELETE /api/supporter-key completes first.
    // The stale PUT /api/settings response must NOT resurrect the removed badge.
    S.meta = { supporter: { active: true, tier: 'personal', issued_at: '2026-11-25', show_in_header: true } };
    S.settingsDoc = {
      readonly: false,
      values: { supporter_show_header: true },
      fields: [SHOW_HEADER_FIELD],
      supporter: {
        active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
        key_preview: 'PL1.abc…', order_preview: 'ord_•••••333',
        origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
      },
    };
    S.settingsDraft = { supporter_show_header: false };
    S.settingsConfirmed = { supporter_show_header: true };
    S.settingsDirtyKeys = new Set(['supporter_show_header']);
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = { supporter_show_header: 1 };
    // Simulate missing DOM checkbox (removed by clearSupporterKey before saveSettingsFields flushes)
    form.elements = {};

    let resolveSettingsPut;
    let sentSettingsPatch = null;
    globalThis.fetch = async (url, init) => {
      if (url === '/api/settings' && init.method === 'PUT') {
        sentSettingsPatch = JSON.parse(init.body);
        return await new Promise(resolve => { resolveSettingsPut = resolve; });
      }
      if (url === '/api/supporter-key' && init.method === 'DELETE') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            supporter: {
              active: false, status: 'inactive', tier: null, issued_at: null,
              key_preview: null, order_preview: null,
              origin: 'none', env_configured: false, can_edit: true, can_clear: false, show_in_header: true,
            },
          }),
        };
      }
      throw new Error('Unexpected fetch: ' + url);
    };

    const settingsSavePromise = saveSettingsFields();
    // Race 2 check: patch must still carry supporter_show_header: false from S.settingsDraft!
    assert.deepEqual(sentSettingsPatch, { supporter_show_header: false });

    // Clear key finishes while PUT /api/settings is still in flight
    assert.equal(await clearSupporterKey(), true);
    assert.equal(S.meta.supporter.active, false);
    assert.equal(badge.hidden, true);

    // Now stale PUT /api/settings resolves carrying old active supporter snapshot
    resolveSettingsPut({
      ok: true,
      status: 200,
      json: async () => ({
        readonly: false,
        values: { supporter_show_header: false },
        fields: [SHOW_HEADER_FIELD],
        custom_themes: [],
        supporter: {
          active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
          key_preview: 'PL1.abc…', order_preview: 'ord_•••••333',
          origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: false,
        },
      }),
    });
    assert.equal(await settingsSavePromise, true);
    assert.equal(S.settingsDoc.supporter.active, false, 'stale settings response must not resurrect removed supporter');
    assert.equal(S.meta.supporter.active, false);
    assert.equal(badge.hidden, true);

    // Race 3: Double submitSupporterKey while in-flight is ignored + dirty supporter_show_header preserved
    S.settingsDraft = { supporter_show_header: false };
    S.settingsDirtyKeys = new Set(['supporter_show_header']);
    let putCalls = 0;
    let releaseKeyPut;
    globalThis.fetch = async (url, init) => {
      if (url === '/api/supporter-key' && init.method === 'PUT') {
        putCalls += 1;
        return await new Promise(resolve => { releaseKeyPut = resolve; });
      }
      throw new Error('Unexpected fetch: ' + url);
    };
    const p1 = submitSupporterKey('PL1.first.sig');
    const p2 = submitSupporterKey('PL1.second.sig');
    assert.equal(await p2, false, 'concurrent submission while in-flight must be ignored');
    assert.equal(putCalls, 1);
    releaseKeyPut({
      ok: true,
      status: 200,
      json: async () => ({
        supporter: {
          active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
          key_preview: 'PL1.first…', order_preview: 'ord_•••••999',
          origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
        },
      }),
    });
    assert.equal(await p1, true);
    assert.equal(S.meta.supporter.show_in_header, false, 'dirty supporter_show_header draft must stay applied in header');
    assert.equal(badge.hidden, true);
  } finally {
    globalThis.fetch = prevFetch;
    form.elements = prevElements;
    panel.remove();
    Object.assign(S, prevState);
  }
});

test('invalid supporter state prioritizes recovery card above showcase, and header preview toggles and clears cleanly', async () => {
  const { supporterCardsHtml, toggleSupporterPreview, clearSupporterPreview } = await import('../js/settings-supporter.js?' + V);
  const { S } = await import('../js/state.js?' + V);

  const invalidHtml = supporterCardsHtml({
    active: false, status: 'invalid', tier: null, issued_at: null, key_preview: null,
    order_preview: null, origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
  }, SHOW_HEADER_FIELD, true, false);
  assert.match(invalidHtml, /supporter-alert/);
  assert.match(invalidHtml, /supporter-showcase/);
  assert.ok(invalidHtml.indexOf('supporter-inactive') < invalidHtml.indexOf('supporter-showcase'),
    'invalid key state must place recovery card before showcase');

  const header = document.getElementById('app-header');
  header.classList = makeClassList();
  const badge = document.getElementById('supporter-badge');
  const prevActive = S.meta.supporter;
  const prevPreview = S.supporterPreviewHeader;
  try {
    S.meta.supporter = { active: false, tier: null, issued_at: null, show_in_header: true };
    S.supporterPreviewHeader = false;
    assert.equal(toggleSupporterPreview(), true);
    assert.equal(S.supporterPreviewHeader, true);
    assert.equal(badge.hidden, false);
    assert.equal(header.classList.contains('has-supporter-beacon'), true);

    assert.equal(clearSupporterPreview(), true);
    assert.equal(S.supporterPreviewHeader, false);
    assert.equal(badge.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);
    assert.equal(clearSupporterPreview(), false);
  } finally {
    S.meta.supporter = prevActive;
    S.supporterPreviewHeader = prevPreview;
  }
});

test('readonly file-origin supporter shows file-specific guidance, proxy HTML 200 fails cleanly, and slow loadSettingsPage preserves new activation', async () => {
  const { supporterCardsHtml, submitSupporterKey, clearSupporterKey } = await import('../js/settings-supporter.js?' + V);
  const { loadSettingsPage } = await import('../js/settings.js?' + V);
  const { S } = await import('../js/state.js?' + V);

  // 1. Readonly active key from file vs env shows distinct accurate guidance
  const roFile = supporterCardsHtml({
    active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
    key_preview: 'PL1.abc…', order_preview: 'ord_•••••333',
    origin: 'file', env_configured: true, can_edit: false, can_clear: false, show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(roFile, /supporter\.manage\.readonlyFile/);
  const roEnv = supporterCardsHtml({
    active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
    key_preview: 'PL1.abc…', order_preview: 'ord_•••••999',
    origin: 'env', env_configured: true, can_edit: false, can_clear: false, show_in_header: true,
  }, SHOW_HEADER_FIELD, true, true);
  assert.match(roEnv, /supporter\.manage\.readonly"/);

  // 2. Proxy returning HTTP 200 HTML (non-JSON) must NEVER report success on activate or remove
  const prevFetch = globalThis.fetch;
  const prevState = {
    settingsDoc: S.settingsDoc,
    meta: S.meta,
    supporterRevision: S.supporterRevision,
    settingsDirty: S.settingsDirty,
    settingsPanel: S.settingsPanel,
  };
  const panel = document.createElement('div');
  panel.id = 'settings-panel-supporter';
  const statusEl = document.createElement('p');
  statusEl.id = 'supporter-action-status';
  panel.appendChild(statusEl);
  document.body.appendChild(panel);
  try {
    S.meta = { supporter: { active: false, tier: null, issued_at: null, show_in_header: true } };
    S.settingsDoc = { readonly: false, values: { supporter_show_header: true }, fields: [SHOW_HEADER_FIELD], supporter: { active: false, status: 'inactive' } };
    globalThis.fetch = async () => ({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError('Unexpected token < in JSON'); },
    });
    assert.equal(await submitSupporterKey('PL1.some.key'), false, 'HTML 200 must fail activation');
    assert.equal(S.meta.supporter.active, false);
    assert.match(statusEl.className, /is-error/);
    assert.equal(await clearSupporterKey(), false, 'HTML 200 must fail removal');

    // 3. Slow GET /api/settings during loadSettingsPage must NOT overwrite a newer activation
    let releaseGetSettings;
    const staleGetPromise = new Promise(resolve => { releaseGetSettings = resolve; });
    globalThis.fetch = async (url, init) => {
      if (url === '/api/settings' && (!init || !init.method || init.method === 'GET')) {
        return await staleGetPromise;
      }
      if (url === '/api/hosts') {
        return { ok: true, status: 200, json: async () => ({ local: { id: 'local', name: '', local: true }, peers: [], readonly: false, max_peers: 32 }) };
      }
      if (url === '/api/supporter-key' && init && init.method === 'PUT') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            supporter: {
              active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
              key_preview: 'PL1.new…', order_preview: 'ord_•••••333',
              origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
            },
          }),
        };
      }
      throw new Error('Unexpected fetch: ' + url);
    };
    S.settingsDirty = false;
    S.settingsPanel = 'supporter';
    const loadPromise = loadSettingsPage();
    assert.equal(await submitSupporterKey('PL1.fresh.key'), true);
    assert.equal(S.settingsDoc.supporter.active, true);

    // Now the stale GET /api/settings arrives with inactive supporter snapshot
    releaseGetSettings({
      ok: true,
      status: 200,
      json: async () => ({
        readonly: false,
        values: { supporter_show_header: true },
        fields: [SHOW_HEADER_FIELD],
        custom_themes: [],
        supporter: {
          active: false, status: 'inactive', tier: null, issued_at: null,
          key_preview: null, order_preview: null, origin: 'none',
          env_configured: false, can_edit: true, can_clear: false, show_in_header: true,
        },
      }),
    });
    await loadPromise;
    assert.equal(S.settingsDoc.supporter.active, true, 'stale GET /api/settings must preserve newer activation');
    assert.equal(S.meta.supporter.active, true);
  } finally {
    globalThis.fetch = prevFetch;
    panel.remove();
    Object.assign(S, prevState);
  }
});

test('late PUT/DELETE /api/supporter-key response preserves supporter_show_header saved during flight (both orderings)', async () => {
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
    settingsRevision: S.settingsRevision,
    supporterRevision: S.supporterRevision,
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
    // Scenario 1 (P2 exact repro): Replace Personal -> Fleet while show_in_header=true;
    // user unchecks show_in_header and PUT /api/settings succeeds BEFORE PUT /api/supporter-key returns.
    S.meta = { supporter: { active: true, tier: 'personal', issued_at: '2026-11-25', show_in_header: true } };
    S.settings = { ...S.settings, supporter_show_header: true };
    S.settingsConfirmed = { supporter_show_header: true };
    S.settingsDraft = { supporter_show_header: true };
    S.settingsDirtyKeys = new Set();
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = {};
    S.settingsDoc = {
      readonly: false,
      values: { supporter_show_header: true },
      fields: [SHOW_HEADER_FIELD],
      supporter: {
        active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
        key_preview: 'PL1.old…', order_preview: 'ord_•••••111',
        origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
      },
    };

    let releaseFleetPut;
    globalThis.fetch = async (url, init) => {
      if (url === '/api/supporter-key' && init.method === 'PUT') {
        return await new Promise(resolve => { releaseFleetPut = resolve; });
      }
      if (url === '/api/settings' && init.method === 'PUT') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            readonly: false,
            values: { supporter_show_header: false },
            fields: [SHOW_HEADER_FIELD],
            custom_themes: [],
            supporter: {
              active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
              key_preview: 'PL1.old…', order_preview: 'ord_•••••111',
              origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: false,
            },
          }),
        };
      }
      throw new Error('Unexpected fetch: ' + url);
    };

    const fleetPromise = submitSupporterKey('PL1.fleet.key');
    // User turns off header badge and autosave completes while Fleet PUT is still in flight
    form.elements = { supporter_show_header: { checked: false } };
    S.settingsRevision = (S.settingsRevision || 0) + 1;
    S.settingsDraft.supporter_show_header = false;
    S.settingsDirtyKeys.add('supporter_show_header');
    S.settingsKeyRevisions.supporter_show_header = S.settingsRevision;
    assert.equal(await saveSettingsFields(), true);
    assert.equal(S.settingsDirtyKeys.has('supporter_show_header'), false, 'dirty key cleared after save');
    assert.equal(S.meta.supporter.show_in_header, false);
    assert.equal(badge.hidden, true);

    // Late Fleet response arrives carrying stale show_in_header: true
    releaseFleetPut({
      ok: true,
      status: 200,
      json: async () => ({
        supporter: {
          active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
          key_preview: 'PL1.new…', order_preview: 'ord_•••••999',
          origin: 'file', env_configured: false, can_edit: true, can_clear: true, show_in_header: true,
        },
      }),
    });
    assert.equal(await fleetPromise, true);
    assert.equal(S.meta.supporter.tier, 'fleet', 'tier must still upgrade to fleet');
    assert.equal(S.meta.supporter.show_in_header, false, 'late key receipt must NOT re-light hidden header');
    assert.equal(S.settingsDoc.supporter.show_in_header, false);
    assert.equal(badge.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);

    // Scenario 2 (opposite arrival order): user re-enables show_in_header; key PUT finishes BEFORE settings PUT.
    let releaseSettingsOn;
    globalThis.fetch = async (url, init) => {
      if (url === '/api/settings' && init.method === 'PUT') {
        return await new Promise(resolve => { releaseSettingsOn = resolve; });
      }
      if (url === '/api/supporter-key' && init.method === 'DELETE') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            supporter: {
              active: true, status: 'active', tier: 'personal', issued_at: '2026-11-25',
              key_preview: 'PL1.env…', order_preview: 'ord_•••••222',
              origin: 'env', env_configured: true, can_edit: true, can_clear: false, show_in_header: false,
            },
          }),
        };
      }
      throw new Error('Unexpected fetch: ' + url);
    };
    form.elements = { supporter_show_header: { checked: true } };
    S.settingsRevision = (S.settingsRevision || 0) + 1;
    S.settingsDraft.supporter_show_header = true;
    S.settingsDirtyKeys.add('supporter_show_header');
    S.settingsKeyRevisions.supporter_show_header = S.settingsRevision;
    const settingsOnPromise = saveSettingsFields();
    assert.equal(await clearSupporterKey(), true);
    assert.equal(S.meta.supporter.tier, 'personal');
    assert.equal(S.meta.supporter.show_in_header, true, 'in-flight show_in_header=true draft kept on DELETE fallback');
    assert.equal(badge.hidden, false);

    releaseSettingsOn({
      ok: true,
      status: 200,
      json: async () => ({
        readonly: false,
        values: { supporter_show_header: true },
        fields: [SHOW_HEADER_FIELD],
        custom_themes: [],
        supporter: {
          active: true, status: 'active', tier: 'fleet', issued_at: '2026-11-25',
          key_preview: 'PL1.new…', order_preview: 'ord_•••••999',
          origin: 'file', env_configured: true, can_edit: true, can_clear: true, show_in_header: true,
        },
      }),
    });
    assert.equal(await settingsOnPromise, true);
    assert.equal(S.settingsDoc.supporter.tier, 'personal', 'stale settings PUT keeps newer env fallback tier');
    assert.equal(S.settingsDoc.supporter.show_in_header, true, 'committed show_in_header=true applied after dirty key cleared');
    assert.equal(S.meta.supporter.show_in_header, true);
    assert.equal(badge.hidden, false);
  } finally {
    globalThis.fetch = prevFetch;
    form.elements = prevElements;
    panel.remove();
    Object.assign(S, prevState);
  }
});

test('supportUrl and showcase CTA pass active UI locale as ?lang=<locale>#support and update on rerender', async () => {
  const { S } = await import('../js/state.js?' + V);
  const { supporterCardsHtml, rerenderSupporter, supportUrl } = await import('../js/settings-supporter.js?' + V);

  const prevI18n = window.PortLightI18n;
  const prevSettingsDoc = S.settingsDoc;
  const prevSettings = S.settings;
  const prevDraft = S.settingsDraft;
  const panel = document.getElementById('settings-panel-supporter');
  const prevHtml = panel.innerHTML;
  const supported = ['en', 'fr', 'de', 'es', 'zh-CN', 'zh-TW', 'ja'];
  const inactiveDoc = {
    active: false, status: 'inactive', tier: null, issued_at: null, key_preview: null,
    order_preview: null, origin: 'none', env_configured: false, can_edit: true, can_clear: false, show_in_header: true,
  };

  try {
    delete window.PortLightI18n;
    assert.equal(supportUrl(), 'https://portlight.stepaniah.me/#support', 'falls back to clean base URL without i18n');

    let currentLocale = 'en';
    window.PortLightI18n = {
      supported,
      locale: () => currentLocale,
      t: (k) => k,
    };

    for (const code of supported) {
      currentLocale = code;
      const expected = 'https://portlight.stepaniah.me/?lang=' + code + '#support';
      assert.equal(supportUrl(), expected);
      const markup = supporterCardsHtml(inactiveDoc, SHOW_HEADER_FIELD, true, false);
      assert.ok(markup.includes('href="' + expected + '"'), 'CTA href matches ' + code);
    }

    for (const invalid of ['', 'auto', 'xx-INVALID', 'zh-CN&foo=1', 'javascript:alert(1)']) {
      currentLocale = invalid;
      assert.equal(supportUrl(), 'https://portlight.stepaniah.me/#support', 'rejects invalid locale: ' + invalid);
    }

    S.settings = { supporter_show_header: true };
    S.settingsDraft = null;
    S.settingsDoc = { readonly: false, fields: [SHOW_HEADER_FIELD], supporter: inactiveDoc };

    currentLocale = 'en';
    rerenderSupporter();
    assert.equal(panel.querySelector('.supporter-cta-btn').getAttribute('href'), 'https://portlight.stepaniah.me/?lang=en#support');

    currentLocale = 'zh-CN';
    rerenderSupporter();
    assert.equal(panel.querySelector('.supporter-cta-btn').getAttribute('href'), 'https://portlight.stepaniah.me/?lang=zh-CN#support');

    currentLocale = 'ja';
    rerenderSupporter();
    assert.equal(panel.querySelector('.supporter-cta-btn').getAttribute('href'), 'https://portlight.stepaniah.me/?lang=ja#support');
  } finally {
    if (prevI18n) window.PortLightI18n = prevI18n;
    else delete window.PortLightI18n;
    S.settingsDoc = prevSettingsDoc;
    S.settings = prevSettings;
    S.settingsDraft = prevDraft;
    panel.innerHTML = prevHtml;
  }
});
