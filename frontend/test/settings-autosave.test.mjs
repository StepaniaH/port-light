/* Autosave guards for drafts, navigation, and overlapping responses. */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { isAutosavedSetting, loadSettingsPage, markDirty, markPeersDirty, peersPayload, refreshThemeChoices,
  renderPeersEditor, restoreInheritedSetting, savePeersPage, saveSettingsFields, saveSettingsPage,
  syncSavedPeerRows, syncSettingsMetadata } =
  await import('../js/settings.js?v=' + version);
const { S } = await import('../js/state.js?v=' + version);

async function withSettingsState(run) {
  const savedState = {
    ...S,
    settingsConfirmed: { ...S.settingsConfirmed },
    settingsDraft: { ...S.settingsDraft },
    settingsDirtyKeys: new Set(S.settingsDirtyKeys),
    settingsResetKeys: new Set(S.settingsResetKeys),
    settingsSubmittingKeys: new Set(S.settingsSubmittingKeys),
    settingsKeyRevisions: { ...S.settingsKeyRevisions },
  };
  const savedFetch = globalThis.fetch;
  const savedQuery = document.querySelector;
  const savedQueryAll = document.querySelectorAll;
  const savedActive = document.activeElement;
  const form = document.getElementById('settings-form');
  const savedElements = form.elements;
  const fields = document.getElementById('settings-fields');
  const savedMarkup = fields.innerHTML;
  try {
    await run(form, fields);
  } finally {
    globalThis.fetch = savedFetch;
    document.querySelector = savedQuery;
    document.querySelectorAll = savedQueryAll;
    document.activeElement = savedActive;
    Object.assign(S, savedState);
    form.elements = savedElements;
    fields.innerHTML = savedMarkup;
    document.getElementById('settings-peers').innerHTML = '';
  }
}

function renderDraft() {
  renderPeersEditor(false);
  document.getElementById('settings-peers').querySelectorAll('input').forEach(input => {
    input.value = input.getAttribute('value') || '';
  });
}

test('autosave ignores custom-theme drafts and accepts settings, peers, and the refresh slider', async () => {
  await withSettingsState(async () => {
    S.settingsDoc = { fields: [{ key: 'host_name' }] };
    const control = (name, marker = '') => ({ name, matches: selector => !!marker && selector.includes(marker) });
    assert.equal(isAutosavedSetting(control('host_name')), true);
    assert.equal(isAutosavedSetting(control('', 'data-peer-field')), true);
    assert.equal(isAutosavedSetting(control('', 'data-refresh-slider')), true);
    assert.equal(isAutosavedSetting(control('', 'data-editor-color')), false);
    assert.equal(isAutosavedSetting(control('')), false);
    assert.equal(isAutosavedSetting(null), false);
  });
});

test('returning to Settings preserves a pending autosave instead of reloading the form', async () => {
  await withSettingsState(async (_form, fields) => {
    S.settingsDirty = true;
    fields.innerHTML = '<input name="host_name" value="New name">';
    globalThis.fetch = async () => { throw new Error('A pending draft must not be reloaded'); };
    await loadSettingsPage();
    assert.equal(S.settingsDirty, true);
    assert.match(fields.innerHTML, /New name/);
  });
});

test('a settings load cannot replace an edit made while the request was in flight', async () => {
  await withSettingsState(async (_form, fields) => {
    S.settingsDirty = false;
    S.settingsRevision = 10;
    const currentDoc = { fields: [], values: { host_name: 'Current' } };
    S.settingsDoc = currentDoc;
    fields.innerHTML = '<input name="host_name" value="New name">';
    globalThis.fetch = async url => {
      if (String(url) === '/api/settings') markDirty();
      return { ok: true, status: 200, json: async () => ({ fields: [], values: { host_name: 'Old' } }) };
    };
    await loadSettingsPage();
    assert.equal(S.settingsDirty, true);
    assert.equal(S.settingsDoc, currentDoc);
    assert.match(fields.innerHTML, /New name/);
  });
});

test('ordinary settings saves send only the dirty field', async () => {
  await withSettingsState(async form => {
    const fields = [
      { key: 'host_name', type: 'str', origin: 'default' },
      { key: 'theme_mode', type: 'choice', origin: 'default', choices: ['system', 'dark', 'light'] },
    ];
    form.elements = {
      host_name: { name: 'host_name', value: 'Renamed host' },
      theme_mode: { name: 'theme_mode', value: 'dark' },
    };
    S.settingsDoc = { readonly: false, fields, values: { host_name: 'Old host', theme_mode: 'dark' }, custom_themes: [] };
    S.settingsDraft = { host_name: 'Old host', theme_mode: 'dark' };
    S.settingsDirtyKeys = new Set();
    S.settingsResetKeys = new Set();
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = {};
    markDirty(form.elements.host_name);
    let submitted;
    globalThis.fetch = async (_url, opts) => {
      submitted = JSON.parse(opts.body);
      return { ok: true, status: 200, json: async () => ({
        readonly: false, fields, values: { host_name: 'Renamed host', theme_mode: 'dark' }, custom_themes: [],
      }) };
    };
    assert.equal(await saveSettingsFields(), true);
    assert.deepEqual(submitted, { host_name: 'Renamed host' });
    assert.equal(S.settingsDirtyKeys.size, 0);
  });
});

test('restoring an inherited setting submits null and adopts the resolved value', async () => {
  await withSettingsState(async form => {
    const savedField = {
      key: 'host_name', type: 'str', origin: 'file', can_reset: true,
      inherited_value: 'Environment host', inherited_origin: 'env', env: 'PORT_LIGHT_HOST_NAME',
    };
    form.elements = { host_name: { name: 'host_name', value: 'Saved host' } };
    S.settingsDoc = { readonly: false, fields: [savedField], values: { host_name: 'Saved host' }, custom_themes: [] };
    S.settingsDraft = { host_name: 'Saved host' };
    S.settingsDirtyKeys = new Set();
    S.settingsResetKeys = new Set();
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = {};
    let submitted;
    globalThis.fetch = async (_url, opts) => {
      submitted = JSON.parse(opts.body);
      return { ok: true, status: 200, json: async () => ({
        readonly: false,
        fields: [{ ...savedField, origin: 'env', can_reset: false, inherited_value: 'Environment host' }],
        values: { host_name: 'Environment host' }, custom_themes: [],
      }) };
    };
    assert.equal(restoreInheritedSetting('host_name'), true);
    assert.equal(form.elements.host_name.value, 'Environment host');
    assert.equal(await saveSettingsFields(), true);
    assert.deepEqual(submitted, { host_name: null });
    assert.equal(S.settingsDraft.host_name, 'Environment host');
  });
});

test('settings and machine saves keep independent success and failure state', async () => {
  await withSettingsState(async form => {
    const field = { key: 'host_name', type: 'str', origin: 'default' };
    form.elements = { host_name: { name: 'host_name', value: 'Renamed host' } };
    S.settingsDoc = { readonly: false, fields: [field], values: { host_name: 'Old host' }, custom_themes: [] };
    S.settingsDraft = { host_name: 'Old host' };
    S.settingsDirtyKeys = new Set();
    S.settingsResetKeys = new Set();
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = {};
    markDirty(form.elements.host_name);
    S.hostCatalog = { readonly: false, peers: [{ id: 'peer0001', name: 'Peer', url: 'http://10.0.0.2:2100' }] };
    S.peersDraft = [{ id: 'peer0001', name: 'Peer', url: 'http://10.0.0.2:2100' }];
    markPeersDirty();
    globalThis.fetch = async url => {
      if (String(url) === '/api/settings') return {
        ok: true, status: 200,
        json: async () => ({ readonly: false, fields: [field], values: { host_name: 'Renamed host' }, custom_themes: [] }),
      };
      return { ok: false, status: 503, json: async () => ({ detail: 'Machine store unavailable' }) };
    };
    assert.equal(await saveSettingsPage(), true);
    assert.equal(S.settingsDirtyKeys.size, 0);
    assert.equal(S.peersDirty, true);
    assert.equal(S.settingsDirty, true);
    assert.equal(document.getElementById('settings-status').textContent, 'settings.saved');
    assert.equal(document.getElementById('peers-status').textContent, 'Machine store unavailable');
  });
});

test('a change during the hosts request preserves the newer clear-auth draft for the next save', async () => {
  await withSettingsState(async form => {
    const doc = { readonly: false, fields: [{ key: 'theme_mode', type: 'choice' }], values: { theme_mode: 'dark' }, custom_themes: [] };
    form.elements = { theme_mode: { value: 'dark', disabled: false } };
    S.settingsDoc = doc;
    S.settings = { ...S.settings, theme_mode: 'dark' };
    S.peersDirty = true;
    S.peersRevision = 10;
    S.settingsDirty = true;
    S.rangeFromView = true;
    const savedPeer = { id: 'peer0001', name: 'Peer', description: '', url: 'http://10.0.0.2:2100', username: 'admin', has_auth: true };
    const newerDraft = { ...savedPeer, username: '', password: '', has_auth: false, clear_auth: true };
    S.hostCatalog = { local: { id: 'local', name: 'Local', local: true }, peers: [savedPeer], readonly: false, max_peers: 32 };
    S.peersDraft = [{ ...savedPeer, password: '', clear_auth: false }];
    renderDraft();
    globalThis.fetch = async url => {
      if (String(url) === '/api/hosts') {
        S.peersDraft = [newerDraft];
        renderDraft();
        markPeersDirty();
        return { ok: true, status: 200, json: async () => ({ ...S.hostCatalog, peers: [savedPeer] }) };
      }
      return { ok: true, status: 200, json: async () => doc };
    };
    assert.equal(await savePeersPage(), true);
    assert.equal(S.settingsDirty, true);
    assert.equal(S.peersRevision, 11);
    assert.equal(document.getElementById('peers-status').textContent, 'settings.unsaved');
    assert.deepEqual(S.peersDraft, [newerDraft]);
    assert.deepEqual(peersPayload(), [{ id: 'peer0001', name: 'Peer', description: '', url: 'http://10.0.0.2:2100', username: '', password: '' }]);
  });
});

test('theme catalog refresh leaves pending ordinary settings and their input nodes intact', async () => {
  await withSettingsState(async (_form, fields) => {
    S.settingsDirty = true;
    S.settings = { ...S.settings, host_name: 'New name', theme_palette: '' };
    S.settingsDoc = { fields: [{ key: 'host_name' }, { key: 'theme_palette', choices: [''] }] };
    fields.innerHTML = '<input name="host_name" value="New name">';
    const input = fields.querySelector('input');
    input.value = 'New name';
    document.querySelector = selector => fields.querySelector(selector);
    const themes = [{ id: 'test', name: 'Test', colors: {} }];
    globalThis.fetch = async () => ({ ok: true, json: async () => ({
      fields: [{ key: 'host_name' }, { key: 'theme_palette', choices: ['', '@custom:test'] }],
      values: { host_name: 'Old name' }, custom_themes: themes,
    }) });
    await refreshThemeChoices();
    assert.equal(fields.querySelector('input'), input);
    assert.equal(input.value, 'New name');
    assert.equal(S.settings.host_name, 'New name');
    assert.equal(S.settingsDirty, true);
    assert.deepEqual(S.customThemes, themes);
    assert.deepEqual(S.settingsDoc.fields[1].choices, ['', '@custom:test']);
  });
});

test('an overlapping settings response does not restore an older custom-theme catalog', async () => {
  await withSettingsState(async form => {
    const doc = { fields: [{ key: 'theme_palette', type: 'choice', choices: [''] }], values: { theme_palette: '' }, custom_themes: [] };
    form.elements = { theme_palette: { value: '', disabled: false } };
    S.settingsDoc = doc;
    S.customThemes = [];
    S.settingsDraft = { theme_palette: '' };
    S.settingsDirtyKeys = new Set(['theme_palette']);
    S.settingsResetKeys = new Set();
    S.settingsSubmittingKeys = new Set();
    S.settingsKeyRevisions = { theme_palette: 10 };
    S.settingsDirty = true;
    S.hostCatalog = { readonly: true };
    const themes = [{ id: 'test', name: 'Test', colors: {} }];
    globalThis.fetch = async () => {
      S.customThemes = themes;
      S.settingsDoc = { ...doc, fields: [{ key: 'theme_palette', type: 'choice', choices: ['', '@custom:test'] }] };
      return { ok: true, json: async () => doc };
    };
    assert.equal(await saveSettingsFields(), true);
    assert.deepEqual(S.customThemes, themes);
    assert.deepEqual(S.settingsDoc.fields[0].choices, ['', '@custom:test']);
  });
});

test('saved metadata updates scanner badges and source hints without replacing controls', async () => {
  await withSettingsState(async (_form, fields) => {
    fields.innerHTML = '<div data-setting="host_name"><div class="setting-control"><input name="host_name"></div></div>' +
      '<label class="scanner-option"><input value="ss"><span class="scanner-copy"><span class="scanner-remediation"></span></span>' +
      '<span class="scanner-state failed"></span></label>';
    document.querySelector = selector => fields.querySelector(selector);
    document.querySelectorAll = selector => fields.querySelectorAll(selector);
    const name = fields.querySelector('input[name="host_name"]');
    const scanner = fields.querySelector('input[value="ss"]');
    scanner.value = 'ss';
    scanner.checked = true;
    syncSettingsMetadata({ fields: [{ key: 'host_name', origin: 'file', env: 'PORT_LIGHT_HOST_NAME' }],
      local_scanning: { scanners: [{ id: 'ss', state: 'ok' }] } });
    assert.equal(fields.querySelector('input[name="host_name"]'), name);
    assert.equal(fields.querySelector('.origin-hint').getAttribute('data-i18n'), 'settings.origin.saved');
    assert.equal(fields.querySelector('.scanner-state').className, 'scanner-state ok');
    assert.equal(fields.querySelector('.scanner-remediation'), null);
  });
});

test('temporarily incomplete peer fields do not remove the saved peer', async () => {
  await withSettingsState(async () => {
    const peer = { id: 'peer0001', name: 'Peer', url: 'http://10.0.0.2:2100' };
    S.settingsDoc = { readonly: false, fields: [] };
    S.hostCatalog = { readonly: false, peers: [peer] };
    S.peersDraft = [{ ...peer, name: '' }];
    S.peersDirty = true;
    S.settingsDirty = true;
    renderDraft();
    globalThis.fetch = async () => { throw new Error('Incomplete rows must not be submitted'); };
    assert.equal(await savePeersPage(), false);
    assert.equal(S.settingsDirty, true);
    assert.deepEqual(S.hostCatalog.peers, [peer]);
    assert.equal(document.getElementById('peers-status').textContent, 'hosts.incomplete');
  });
});

test('new peers retain the same id across overlapping saves, including plain HTTP', async () => {
  await withSettingsState(async () => {
    S.hostCatalog = { readonly: false, peers: [] };
    S.peersDraft = [{ name: 'Peer', url: 'http://10.0.0.2:2100' }];
    renderDraft();
    const first = peersPayload();
    assert.match(first[0].id, /^[0-9a-f]{8}$/);
    assert.equal(peersPayload()[0].id, first[0].id);
  });
});

test('saved peer credentials update actions and clear inactive passwords without replacing inputs', async () => {
  await withSettingsState(async () => {
    S.hostCatalog = { readonly: false, peers: [] };
    S.peersDraft = [{ id: 'peer0001', name: 'Peer', url: 'http://10.0.0.2:2100', has_auth: false }];
    renderDraft();
    const row = document.getElementById('settings-peers').querySelector('.peer-row');
    const password = row.querySelector('[data-peer-field="password"]');
    password.value = 'test-password';
    S.peersDraft[0].has_auth = true;
    document.activeElement = password;
    syncSavedPeerRows();
    assert.equal(password.value, 'test-password');
    assert.ok(row.querySelector('[data-peer-clear-auth]'));
    assert.equal(password.getAttribute('placeholder'), 'hosts.passwordKeep');
    document.activeElement = null;
    syncSavedPeerRows();
    assert.equal(password.value, '');
    assert.equal(password.getAttribute('value'), null);
    assert.equal(row.querySelector('[data-peer-field="password"]'), password);
    assert.equal(row.querySelectorAll('[data-peer-clear-auth]').length, 1);
    S.peersDraft[0].has_auth = false;
    syncSavedPeerRows();
    assert.equal(row.querySelector('[data-peer-clear-auth]'), null);
  });
});
