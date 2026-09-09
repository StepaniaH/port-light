import { automationCardsHtml, ensureAutomationDelegates } from './settings-automation.js?v=93';
export { automationCardsHtml, releaseLease, rerenderAutomationCards, ensureAutomationDelegates } from './settings-automation.js?v=93';
import { closeLocaleMenu, syncLocaleTrigger, renderLocaleList, renderModePicker, currentMode, renderPalettePicker, syncPaletteAvailability } from './settings-appearance.js?v=93';
export { localeCopyHtml, closeLocaleMenu, moveLocaleHighlight, syncLocaleTrigger, renderLocaleList, renderModePicker, currentMode, renderPalettePicker, syncPaletteAvailability } from './settings-appearance.js?v=93';
import { choiceLabel, settingsCard, kvRow } from './settings-format.js?v=93';
export { choiceLabel, settingsCard, kvRow } from './settings-format.js?v=93';
import { renderPeersEditor as renderPeersEditorView, readPeersDraftFromForm, peersPayload, syncSavedPeerRows } from './settings-peers.js?v=93';
export { readPeersDraftFromForm, peersPayload, syncSavedPeerRows } from './settings-peers.js?v=93';
/* Settings view: four panels, locale menu, theme picker, peers editor. */

import { S, SETTINGS_PANELS, LIVE_APPLY_KEYS, CARD_FIELD_KEYS, CUSTOM_PREFIX, applyAppearance, persistAppearance, saveView } from './state.js?v=93';
import { t, escapeHtml, errorText } from './text.js?v=93';
import { rangeStartInput, rangeEndInput } from './dom.js?v=93';
import { api, fetchHosts, fetchSettings } from './api.js?v=93';
import { bindAddressView } from './grid.js?v=93';
import { recommendedPeerLimit, refreshChoices } from './fleet.js?v=93';

const BIND_FAMILY_KEYS = ['show_bind_ipv4', 'show_bind_ipv6'];
const statusTimers = {};

function setPageStatus(id, key, className, clearAfter) {
  const status = document.getElementById(id);
  if (!status) return;
  if (statusTimers[id]) clearTimeout(statusTimers[id]);
  statusTimers[id] = null;
  status.className = className || '';
  status.textContent = key ? t(key) : '';
  if (key && clearAfter) {
    const timer = setTimeout(function () {
      if (status.textContent === t(key)) {
        status.className = '';
        status.textContent = '';
      }
      statusTimers[id] = null;
    }, clearAfter);
    if (timer && typeof timer.unref === 'function') timer.unref();
    statusTimers[id] = timer;
  }
}

function syncDirtyFlag() {
  S.settingsDirty = !!((S.settingsDirtyKeys && S.settingsDirtyKeys.size) || S.peersDirty);
  return S.settingsDirty;
}

function resetDraftState(doc) {
  S.settingsConfirmed = Object.assign({}, doc.values || {});
  S.settingsDraft = Object.assign({}, doc.values || {});
  if (S.rangeFromView) {
    S.settingsDraft.port_range_start = S.rangeStart;
    S.settingsDraft.port_range_end = S.rangeEnd;
  }
  S.settingsDirtyKeys = new Set();
  S.settingsResetKeys = new Set();
  S.settingsSubmittingKeys = new Set();
  S.settingsKeyRevisions = {};
  S.peersDirty = false;
  S.peersSubmitting = false;
  syncDirtyFlag();
}

function fieldByKey(key) {
  return (S.settingsDoc && S.settingsDoc.fields || []).find(function (field) {
    return field.key === key;
  }) || null;
}

function settingKeyForInput(input) {
  if (!input) return '';
  if (input.matches && input.matches('[data-refresh-slider]')) return 'refresh_ms';
  return fieldByKey(input.name) ? input.name : '';
}

function readFieldValue(field) {
  const form = document.getElementById('settings-form');
  if (!form || !field) return undefined;
  const el = form.elements[field.key];
  if (field.type === 'multi_choice') {
    return Array.prototype.slice.call(form.querySelectorAll('input[name="' + field.key + '"]:checked'))
      .map(function (input) { return input.value; });
  }
  if (!el) return undefined;
  if (field.type === 'string_list') {
    return String(el.value || '').split('\n').map(function (line) { return line.trim(); }).filter(Boolean);
  }
  if (field.type === 'bool') return !!el.checked;
  if (field.type === 'int') {
    const n = parseInt(el.value, 10);
    return isNaN(n) ? NaN : n;
  }
  return el.value;
}

function updateDraftForInput(input) {
  const key = settingKeyForInput(input);
  const field = fieldByKey(key);
  if (!field) return '';
  S.settingsDraft[key] = readFieldValue(field);
  return key;
}

function settingValuesEqual(left, right) {
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && JSON.stringify(left) === JSON.stringify(right);
  }
  return left === right;
}

  export function loadSettingsPage() {
    if (S.settingsDirty) {
      showSettingsPanel(S.settingsPanel);
      return Promise.resolve();
    }
    const loadRevision = S.settingsRevision;
    return Promise.all([fetchSettings(), fetchHosts()]).then(function (pair) {
      if (S.settingsDirty || loadRevision !== S.settingsRevision) return;
      const doc = pair[0];
      if (pair[1]) S.hostCatalog = pair[1];
      if (!doc) return;
      S.settingsDoc = doc;
      S.customThemes = Array.isArray(doc.custom_themes) ? doc.custom_themes : [];
      S.peersDraft = (S.hostCatalog.peers || []).map(clonePeerRow);
      renderSettingsForm(doc);
    });
  }

  export function clonePeerRow(p) {
    return {
      id: p.id || '',
      name: p.name || '',
      description: p.description || '',
      url: p.url || '',
      username: p.username || '',
      password: '',
      has_auth: !!p.has_auth,
      clear_auth: false,
    };
  }

  export function fieldLabel(f) {
    return t('settings.fields.' + f.key + '.label');
  }

  export function fieldHelp(f) {
    return t('settings.fields.' + f.key + '.help');
  }

  export function formatRefreshInterval(value) {
    const ms = Math.max(1000, Number(value) || 5000);
    if (ms >= 60000 && ms % 60000 === 0) {
      return t('settings.refresh.minutes', { count: ms / 60000 });
    }
    return t('settings.refresh.seconds', { count: ms / 1000 });
  }

  function refreshControlHtml(f, value, readonly, labelId) {
    const choices = refreshChoices(value);
    const current = Math.max(1000, Math.min(300000, Number(value) || 5000));
    const index = choices.indexOf(current);
    const hard = Number(S.hostCatalog.max_peers) || 32;
    const capacityIndex = choices.findIndex(function (interval) { return recommendedPeerLimit(interval, hard) >= hard; });
    const capacityPoint = capacityIndex > 0 && capacityIndex < choices.length - 1
      ? '<span class="refresh-capacity-point" data-refresh-capacity-point data-capacity-interval="' + choices[capacityIndex] +
        '" style="left:' + (capacityIndex / (choices.length - 1) * 100) + '%">' + escapeHtml(t('settings.refresh.capacityPoint', {
          interval: formatRefreshInterval(choices[capacityIndex]), hard: hard,
        })) + '</span>' : '';
    const disabled = readonly ? ' disabled' : '';
    return '<div class="refresh-control"><div class="refresh-value-row"><output for="refresh-slider" ' +
      'id="refresh-value" data-refresh-value>' + escapeHtml(formatRefreshInterval(current)) + '</output></div>' +
      '<input type="range" id="refresh-slider" min="0" max="' + (choices.length - 1) + '" step="1" value="' +
      index + '" style="--refresh-progress:' + (index / (choices.length - 1) * 100) + '%" data-refresh-slider data-refresh-values="' + choices.join(',') + '" aria-labelledby="' +
      escapeHtml(labelId) + '" aria-describedby="refresh-capacity" aria-valuetext="' +
      escapeHtml(formatRefreshInterval(current)) + '"' + disabled + '>' +
      '<input type="hidden" name="' + escapeHtml(f.key) + '" value="' + current + '" data-refresh-hidden>' +
      '<div class="refresh-scale" aria-hidden="true"><span data-refresh-endpoint="' + choices[0] + '">' + escapeHtml(formatRefreshInterval(choices[0])) +
      '</span>' + capacityPoint + '<span data-refresh-endpoint="' + choices[choices.length - 1] + '">' + escapeHtml(formatRefreshInterval(choices[choices.length - 1])) + '</span></div>' +
      '<p class="refresh-capacity field-help" id="refresh-capacity" aria-live="polite"></p></div>';
  }

  export function syncRefreshCapacity() {
    const hidden = document.querySelector('[data-refresh-hidden]');
    const capacity = document.getElementById('refresh-capacity');
    const slider = document.querySelector('[data-refresh-slider]');
    const output = document.querySelector('[data-refresh-value]');
    const maxNote = document.querySelector('[data-peer-limit]');
    const capacityPoint = document.querySelector('[data-refresh-capacity-point]');
    const hard = Number(S.hostCatalog.max_peers) || 32;
    if (maxNote) maxNote.textContent = t('hosts.max', { count: hard });
    document.querySelectorAll('[data-refresh-endpoint]').forEach(function (endpoint) {
      endpoint.textContent = formatRefreshInterval(endpoint.getAttribute('data-refresh-endpoint'));
    });
    if (capacityPoint) capacityPoint.textContent = t('settings.refresh.capacityPoint', {
      interval: formatRefreshInterval(capacityPoint.getAttribute('data-capacity-interval')), hard: hard,
    });
    if (hidden && output) {
      output.textContent = formatRefreshInterval(Number(hidden.value));
      if (slider) slider.setAttribute('aria-valuetext', output.textContent);
    }
    if (!hidden || !capacity) return;
    const count = Array.isArray(S.peersDraft) ? S.peersDraft.length : (S.hostCatalog.peers || []).length;
    const recommended = recommendedPeerLimit(Number(hidden.value), hard);
    const over = count > recommended;
    const atLimit = recommended >= hard;
    capacity.classList.toggle('is-warning', over);
    capacity.textContent = t(atLimit ? 'settings.refresh.capacityLimit'
      : over ? 'settings.refresh.overCapacity' : 'settings.refresh.capacity', {
      current: count,
      recommended: recommended,
      hard: hard,
    });
  }

  export function updateRefreshSlider(input) {
    if (!input || !input.matches('[data-refresh-slider]')) return false;
    const choices = String(input.getAttribute('data-refresh-values') || '').split(',').map(Number);
    const value = choices[Number(input.value)];
    const hidden = document.querySelector('[data-refresh-hidden]');
    const output = document.querySelector('[data-refresh-value]');
    if (!Number.isFinite(value) || !hidden || !output) return false;
    hidden.value = String(value);
    input.style.setProperty('--refresh-progress', (Number(input.value) / (choices.length - 1) * 100) + '%');
    output.textContent = formatRefreshInterval(value);
    input.setAttribute('aria-valuetext', output.textContent);
    syncRefreshCapacity();
    return true;
  }

  export function settingsPanelHtml(id, inner) {
    return '<div class="settings-panel" id="settings-panel-' + id + '" role="tabpanel" data-settings-panel="' + id +
      '" aria-labelledby="settings-tab-' + id + '">' + inner + '</div>';
  }

  export function showSettingsPanel(id, resetScroll) {
    if (SETTINGS_PANELS.indexOf(id) < 0) id = 'appearance';
    S.settingsPanel = id;
    document.querySelectorAll('#settings-nav [role="tab"]').forEach(function (btn) {
      const on = btn.getAttribute('data-settings-panel') === id;
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      btn.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll('#settings-fields .settings-panel').forEach(function (panel) {
      panel.hidden = panel.getAttribute('data-settings-panel') !== id;
    });
    if (resetScroll) window.scrollTo(0, 0);
  }

  export function goSettingsPanel(id) {
    if (SETTINGS_PANELS.indexOf(id) < 0) return;
    showSettingsPanel(id, true);
    const next = '#/settings/' + id;
    if ((location.hash || '') !== next) location.hash = next;
  }

  export function syncDependentSettings() {
    const form = document.getElementById('settings-form');
    if (!form) return;
    const auto = form.elements.auto_refresh;
    const row = form.querySelector('[data-setting="refresh_ms"]');
    if (auto && row) row.classList.toggle('is-inactive', !auto.checked);

    const scannerInputs = Array.prototype.slice.call(
      form.querySelectorAll('input[name="local_scanners"]'));
    const selected = scannerInputs.filter(function (input) { return input.checked; });
    scannerInputs.forEach(function (input) {
      const locked = input.getAttribute('data-locked') === '1';
      input.disabled = locked || (selected.length === 1 && input.checked);
    });
    const compose = scannerInputs.find(function (input) { return input.value === 'compose'; });
    form.querySelectorAll('[data-compose-option]').forEach(function (optionRow) {
      const inactive = !!compose && !compose.checked;
      optionRow.classList.toggle('is-inactive', inactive);
      optionRow.querySelectorAll('input, textarea, select').forEach(function (control) {
        control.disabled = control.getAttribute('data-locked') === '1' || inactive;
      });
    });

    const parent = form.elements.show_bind_addresses;
    const familyGroup = document.getElementById('bind-address-family-options');
    if (parent && familyGroup) {
      if (parent.checked) familyGroup.removeAttribute('hidden');
      else familyGroup.setAttribute('hidden', '');
      parent.setAttribute('aria-expanded', parent.checked ? 'true' : 'false');
    }
  }

  export function markDirty(input) {
    if (S.settingsDoc && S.settingsDoc.readonly) return;
    const key = updateDraftForInput(input);
    S.settingsRevision += 1;
    if (key) {
      S.settingsResetKeys.delete(key);
      const matchesConfirmed = settingValuesEqual(S.settingsDraft[key], S.settingsConfirmed[key]);
      if (matchesConfirmed && !S.settingsSubmittingKeys.has(key)) {
        S.settingsDirtyKeys.delete(key);
        delete S.settingsKeyRevisions[key];
      } else {
        S.settingsDirtyKeys.add(key);
        S.settingsKeyRevisions[key] = S.settingsRevision;
      }
      syncDirtyFlag();
    } else {
      S.settingsDirty = true;
    }
    setPageStatus('settings-status', key && !S.settingsDirtyKeys.size ? '' : 'settings.unsaved', '', 0);
  }

  export function markPeersDirty() {
    if ((S.settingsDoc && S.settingsDoc.readonly) || (S.hostCatalog && S.hostCatalog.readonly)) return;
    S.peersDirty = true;
    S.peersRevision += 1;
    S.settingsRevision += 1;
    syncDirtyFlag();
    setPageStatus('peers-status', 'settings.unsaved', 'action-status', 0);
  }

  export function isAutosavedSetting(input) {
    if (!input) return false;
    if (input.matches('[data-peer-field], [data-refresh-slider]')) return true;
    return (S.settingsDoc && S.settingsDoc.fields || []).some(function (field) {
      return field.key === input.name;
    });
  }

  export function applyServerSettings(doc) {
    S.settingsDoc = doc;
    S.settings = Object.assign({}, S.settings, doc.values || {});
    S.settingsConfirmed = Object.assign({}, doc.values || {});
    S.settingsDraft = Object.assign({}, doc.values || {});
    S.customThemes = Array.isArray(doc.custom_themes) ? doc.custom_themes : [];
    if (!S.rangeFromView) {
      S.rangeStart = S.settings.port_range_start;
      S.rangeEnd = S.settings.port_range_end;
      rangeStartInput.value = S.rangeStart;
      rangeEndInput.value = S.rangeEnd;
    }
    applyAppearance();
    persistAppearance();
  }

  function originMetaContents(f) {
    const sourceKey = f.origin === 'file' ? 'settings.origin.saved'
      : f.origin === 'env' ? 'settings.origin.env' : '';
    const source = sourceKey
      ? '<span class="origin-hint" data-i18n="' + sourceKey + '" title="' + escapeHtml(f.env || '') + '">' + escapeHtml(t(sourceKey)) + '</span>'
      : '';
    const reset = f.can_reset
      ? '<button type="button" class="setting-reset" data-reset-setting="' + escapeHtml(f.key) +
        '" data-i18n="settings.origin.restore">' + escapeHtml(t('settings.origin.restore')) + '</button>'
      : '';
    return source + reset;
  }

  export function originHint(f) {
    const contents = originMetaContents(f);
    return contents ? '<span class="setting-meta">' + contents + '</span>' : '';
  }

  export function syncSettingsMetadata(doc) {
    (doc.fields || []).forEach(function (field) {
      if (field.key === 'show_bind_addresses' || BIND_FAMILY_KEYS.includes(field.key)) return;
      const row = document.querySelector('[data-setting="' + field.key + '"]');
      if (!row) return;
      let meta = row.querySelector('.setting-meta');
      const contents = originMetaContents(field);
      if (!contents) {
        if (meta) meta.remove();
        return;
      }
      if (!meta) {
        meta = document.createElement('span');
        meta.className = 'setting-meta';
        (row.querySelector('.setting-control') || row).appendChild(meta);
      }
      meta.innerHTML = contents;
    });
    document.querySelectorAll('.scanner-option').forEach(function (option) {
      const input = option.querySelector('input');
      const badge = option.querySelector('.scanner-state');
      if (!input || !badge) return;
      const state = input.checked ? scannerRuntime(doc, input.value).state : 'disabled';
      badge.className = 'scanner-state ' + state;
      badge.setAttribute('data-i18n', 'settings.scanners.state.' + state);
      badge.textContent = t('settings.scanners.state.' + state);
      let help = option.querySelector('.scanner-remediation');
      if (state !== 'failed') {
        if (help) help.remove();
      } else {
        if (!help) {
          help = document.createElement('span');
          help.className = 'scanner-remediation';
          option.querySelector('.scanner-copy').appendChild(help);
        }
        const key = 'settings.scanners.' + input.value + '.remediation';
        help.setAttribute('data-i18n', key);
        help.textContent = t(key);
      }
    });
  }

  const EDITOR_VARS = [
    'bg', 'elevated', 'card', 'cardHover', 'border', 'text', 'textDim', 'used',
    'configured', 'free', 'accent', 'conflict', 'access', 'hidden', 'danger',
  ].map(function (key) {
    return { key: key, labelKey: 'settings.editor.vars.' + key };
  });

  export function editorDefaults() {
    const out = {};
    EDITOR_VARS.forEach(function (row) { out[row.key] = '#000000'; });
    return out;
  }

  function themeTargetsHtml() {
    return ['<option value="">' + escapeHtml(t('settings.editor.new')) + '</option>']
      .concat((S.customThemes || []).map(function (th) {
        return '<option value="' + escapeHtml(th.id) + '">' + escapeHtml(th.name) + '</option>';
      })).join('');
  }

  export function themeEditorHtml(readonly) {
    const dis = readonly ? ' disabled' : '';
    const rows = EDITOR_VARS.map(function (row) {
      return '<div class="editor-row"><label for="ed-' + row.key + '" data-i18n="' + row.labelKey + '">' +
        escapeHtml(t(row.labelKey)) + '</label>' +
        '<input type="color" id="ed-' + row.key + '" data-editor-color="' + row.key + '" value="#000000"' + dis + '>' +
        '<input type="text" class="range-input" maxlength="9" data-editor-hex="' + row.key + '" value="#000000"' + dis + '>' +
        '</div>';
    }).join('');
    return '<details id="theme-editor"><summary data-i18n="settings.editor.summary">' +
      escapeHtml(t('settings.editor.summary')) + '</summary>' +
      '<p class="muted" data-i18n="settings.editor.hint">' + escapeHtml(t('settings.editor.hint')) + '</p>' +
      '<div class="editor-actions">' +
      '<button type="button" class="btn-secondary" data-editor-preset' + dis + '>' +
      escapeHtml(t('settings.editor.preset')) + '</button>' +
      '<select class="dropdown" id="editor-target"' + dis + '>' + themeTargetsHtml() + '</select>' +
      '<input type="text" class="range-input" id="editor-name" maxlength="40" placeholder="' +
      escapeHtml(t('modal.optional')) + '"' + dis + '>' +
      '<button type="button" class="btn-secondary" data-editor-export' + dis + '>' +
      escapeHtml(t('settings.editor.export')) + '</button>' +
      '<input type="file" id="editor-file" accept=".json,application/json" hidden' + dis + '>' +
      '<button type="button" class="btn-secondary" data-editor-import' + dis + '>' +
      escapeHtml(t('settings.editor.import')) + '</button>' +
      '<button type="button" class="btn-primary" data-editor-save' + dis + '>' +
      escapeHtml(t('settings.editor.save')) + '</button>' +
      '</div>' + rows + '</details><p id="theme-editor-status" class="action-status" role="status" aria-live="polite"></p>';
  }

  function rgbToHex(raw) {
    const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(raw || '');
    if (!m) return /^#[0-9a-fA-F]{3,8}$/.test((raw || '').trim()) ? raw.trim() : '#000000';
    return '#' + m.slice(1).map(function (n) {
      return ('0' + Number(n).toString(16)).slice(-2);
    }).join('');
  }

  const CUSTOM_CSS_NAMES = {
    bg: '--bg', elevated: '--elevated', card: '--card', cardHover: '--card-hover',
    border: '--border', text: '--text', textDim: '--text-dim', used: '--used',
    configured: '--configured', free: '--free', accent: '--accent',
    conflict: '--conflict', access: '--access', hidden: '--hidden', danger: '--danger',
  };

  export function fillEditorFromPreset() {
    const cs = getComputedStyle(document.documentElement);
    EDITOR_VARS.forEach(function (row) {
      const hex = rgbToHex(cs.getPropertyValue(CUSTOM_CSS_NAMES[row.key]));
      const color = document.querySelector('[data-editor-color="' + row.key + '"]');
      const hexInput = document.querySelector('[data-editor-hex="' + row.key + '"]');
      if (color) color.value = hex.slice(0, 7);
      if (hexInput) hexInput.value = hex;
    });
  }

  function collectEditorColors() {
    const out = {};
    let ok = true;
    EDITOR_VARS.forEach(function (row) {
      const hexInput = document.querySelector('[data-editor-hex="' + row.key + '"]');
      const value = (hexInput && hexInput.value || '').trim();
      if (/^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(value)) {
        out[row.key] = value;
      } else ok = false;
    });
    return ok ? out : null;
  }

  function previewEditorColor(key, hex) {
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return;
    document.documentElement.style.setProperty(CUSTOM_CSS_NAMES[key], hex);
  }

  export async function refreshThemeChoices(deletedId) {
    const doc = await fetchSettings();
    if (!doc) return;
    S.customThemes = Array.isArray(doc.custom_themes) ? doc.custom_themes : [];
    const paletteField = (doc.fields || []).find(function (field) { return field.key === 'theme_palette'; });
    S.settingsDoc = Object.assign({}, S.settingsDoc, {
      custom_themes: S.customThemes,
      fields: (S.settingsDoc && S.settingsDoc.fields || []).map(function (field) {
        return field.key === 'theme_palette' && paletteField ? paletteField : field;
      }),
    });
    const removedSelection = deletedId && S.settings.theme_palette === CUSTOM_PREFIX + deletedId;
    if (removedSelection) S.settings.theme_palette = '';
    const control = document.querySelector('[data-setting="theme_palette"] .setting-control');
    if (control && paletteField) {
      control.innerHTML = renderPalettePicker(paletteField.choices || [], S.settings.theme_palette || '', currentMode(),
        S.settingsDoc.readonly ? ' disabled' : '') + originHint(paletteField);
    }
    const target = document.getElementById('editor-target');
    if (target) {
      const selected = target.value;
      target.innerHTML = themeTargetsHtml();
      target.value = S.customThemes.some(function (theme) { return theme.id === selected; }) ? selected : '';
    }
    applyAppearance();
    if (removedSelection && control) {
      const builtin = control.querySelector('input[value=""]');
      if (builtin) builtin.dispatchEvent(new Event('change', { bubbles: true }));
    } else if (!S.settingsDirty) persistAppearance();
  }

  async function saveEditorTheme(btn) {
    const colors = collectEditorColors();
    const name = ((document.getElementById('editor-name') || {}).value || '').trim();
    const target = (document.getElementById('editor-target') || {}).value || '';
    if (!colors || !name) {
      setPageStatus('theme-editor-status', 'settings.editor.invalid', 'action-status is-error', 0);
      return;
    }
    const payload = { name: name, basedOn: '', mode: currentMode(), colors: colors };
    const url = target ? '/api/custom-themes/' + encodeURIComponent(target) : '/api/custom-themes';
    try {
      const res = await api(url, {
        method: target ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(function () { return {}; });
      if (!res.ok) {
        const status = document.getElementById('theme-editor-status');
        if (status) { status.className = 'action-status is-error'; status.textContent = errorText(body, res.status); }
        return;
      }
      await refreshThemeChoices();
      setPageStatus('theme-editor-status', 'settings.saved', 'action-status is-ok', 1800);
    } catch (err) {
      setPageStatus('theme-editor-status', 'settings.editor.saveFailed', 'action-status is-error', 0);
    }
  }

  function exportEditorTheme() {
    const colors = collectEditorColors() || editorDefaults();
    const name = ((document.getElementById('editor-name') || {}).value || 'port-light-theme');
    const blob = new Blob([JSON.stringify({ name: name, basedOn: '', mode: currentMode(), colors: colors }, null, 2)],
      { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = String(name).replace(/[^a-z0-9_-]+/gi, '-') + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function importEditorThemeFile(file) {
    const f = file.files && file.files[0];
    if (!f) return;
    try {
      const payload = JSON.parse(await f.text());
      const res = await api('/api/custom-themes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: String(payload.name || f.name.replace(/\.json$/i, '')),
          basedOn: String(payload.basedOn || ''),
          mode: payload.mode === 'light' ? 'light' : 'dark',
          colors: payload.colors || {},
        }),
      });
      if (!res.ok) throw new Error(String(res.status));
      await refreshThemeChoices();
      setPageStatus('theme-editor-status', 'settings.saved', 'action-status is-ok', 1800);
    } catch (err) {
      setPageStatus('theme-editor-status', 'settings.editor.importFailed', 'action-status is-error', 0);
    }
  }

  function scannerRuntime(doc, scanner) {
    const rows = doc && doc.local_scanning && doc.local_scanning.scanners;
    return (rows || []).find(function (row) { return row.id === scanner; }) || {
      id: scanner, enabled: false, state: 'checking',
    };
  }

  export function renderScannerField(f, value, readonly, doc) {
    const selected = Array.isArray(value) ? value : [];
    const invalid = selected.length ? '' :
      '<p class="scanner-remediation" role="alert" data-i18n="settings.scanners.invalid">' +
      escapeHtml(t('settings.scanners.invalid')) + '</p>';
    const rows = (f.choices || []).map(function (scanner) {
      const checked = selected.indexOf(scanner) >= 0;
      const runtime = scannerRuntime(doc, scanner);
      const state = checked ? (runtime.state || 'checking') : 'disabled';
      const disabled = readonly ? ' disabled' : '';
      const remediation = state === 'failed'
        ? '<span class="scanner-remediation" data-i18n="settings.scanners.' + scanner + '.remediation">' +
          escapeHtml(t('settings.scanners.' + scanner + '.remediation')) + '</span>'
        : '';
      return '<label class="scanner-option"><span class="scanner-option-main">' +
        '<input type="checkbox" name="' + escapeHtml(f.key) + '" value="' + escapeHtml(scanner) + '"' +
        (checked ? ' checked' : '') + ' data-locked="' + (readonly ? '1' : '0') + '"' + disabled + '>' +
        '<span class="scanner-copy"><span class="scanner-name" data-i18n="settings.scanners.' + scanner + '.label">' +
        escapeHtml(t('settings.scanners.' + scanner + '.label')) + '</span>' +
        '<span class="field-help" data-i18n="settings.scanners.' + scanner + '.help">' +
        escapeHtml(t('settings.scanners.' + scanner + '.help')) + '</span>' + remediation + '</span></span>' +
        '<span class="scanner-state ' + escapeHtml(state) + '" data-i18n="settings.scanners.state.' + state + '">' +
        escapeHtml(t('settings.scanners.state.' + state)) + '</span></label>';
    }).join('');
    return '<fieldset class="setting-row is-wide scanner-setting" data-setting="' + escapeHtml(f.key) + '">' +
      '<legend class="setting-copy"><span class="setting-label" data-i18n="settings.fields.' + f.key + '.label">' +
      escapeHtml(fieldLabel(f)) + '</span><span class="field-help" data-i18n="settings.fields.' + f.key + '.help">' +
      escapeHtml(fieldHelp(f)) + '</span></legend><div class="setting-control scanner-options">' + rows +
      invalid + originHint(f) + '</div></fieldset>';
  }

  function renderHostLayoutPicker(f, value, disabled) {
    const board = '<span class="layout-preview-board"><i></i><i></i><i></i><i></i></span>';
    const shortBoard = '<span class="layout-preview-board is-short"><i></i><i></i></span>';
    const previews = {
      waterfall: '<span class="layout-preview-columns"><span>' + board + shortBoard +
        '</span><span>' + shortBoard + board + '</span></span>',
      tabs: '<span class="layout-preview-tabs"><i></i><i></i><i></i></span>' + board,
    };
    return '<div class="layout-picker" role="radiogroup" aria-labelledby="setting-label-host_layout">' +
      (f.choices || []).map(function (choice) {
        const titleId = 'layout-title-' + choice;
        const helpId = 'layout-help-' + choice;
        const helpKey = 'settings.layout.' + choice;
        return '<label class="layout-option"><input type="radio" name="host_layout" value="' +
          escapeHtml(choice) + '"' + (choice === value ? ' checked' : '') + disabled +
          ' aria-labelledby="' + escapeHtml(titleId) + '" aria-describedby="' + escapeHtml(helpId) + '">' +
          '<span class="layout-option-header"><span class="layout-option-mark" aria-hidden="true">✓</span>' +
          '<span class="layout-option-title" id="' + escapeHtml(titleId) + '" data-i18n="choice.' + escapeHtml(choice) + '">' +
          escapeHtml(choiceLabel(choice)) + '</span><span class="layout-option-selected" aria-hidden="true" data-i18n="settings.layout.selected">' +
          escapeHtml(t('settings.layout.selected')) + '</span></span>' +
          '<span class="layout-preview" aria-hidden="true">' + (previews[choice] || '') + '</span>' +
          '<span class="field-help" id="' + escapeHtml(helpId) + '" data-i18n="' + escapeHtml(helpKey) + '">' +
          escapeHtml(t(helpKey)) + '</span></label>';
      }).join('') + '</div>';
  }

  export function renderField(f, value, readonly, doc) {
    if (f.type === 'multi_choice') return renderScannerField(f, value, readonly, doc);
    const disabled = readonly ? ' disabled' : '';
    const labelId = 'setting-label-' + f.key;
    const labelledBy = ' aria-labelledby="' + escapeHtml(labelId) + '"';
    let control = '';
    let tag = 'div';
    if (f.type === 'bool') {
      tag = 'label';
      const dependency = f.key === 'show_bind_addresses'
        ? ' aria-controls="bind-address-family-options" aria-expanded="' + (value ? 'true' : 'false') + '"'
        : '';
      control = '<span class="switch"><input type="checkbox" name="' + f.key + '"' +
        (value ? ' checked' : '') + dependency + disabled + '><span class="track"></span></span>';
    } else if (f.key === 'locale') {
      control = renderLocaleList(f.choices || [], value, disabled);
    } else if (f.key === 'theme_mode') {
      control = renderModePicker(f.choices || [], value, disabled);
    } else if (f.key === 'theme_palette') {
      control = renderPalettePicker(f.choices || [], value, currentMode(), disabled);
    } else if (f.key === 'host_layout') {
      control = renderHostLayoutPicker(f, value, disabled);
    } else if (f.type === 'choice') {
      const choices = f.choices || [];
      control = '<div class="segmented" role="radiogroup" aria-label="' +
        escapeHtml(fieldLabel(f)) + '">' +
        choices.map(function (c) {
          return '<label class="seg-opt"><input type="radio" name="' + f.key + '" value="' +
            escapeHtml(c) + '"' + (c === value ? ' checked' : '') + disabled +
            '><span data-i18n="choice.' + c + '">' + escapeHtml(choiceLabel(c)) + '</span></label>';
        }).join('') + '</div>';
    } else if (f.key === 'refresh_ms') {
      control = refreshControlHtml(f, value, readonly, labelId);
    } else if (f.type === 'int') {
      const min = f.min != null ? ' min="' + f.min + '"' : '';
      const max = f.max != null ? ' max="' + f.max + '"' : '';
      control = '<input type="number" name="' + f.key + '" value="' + escapeHtml(String(value)) + '"' + min + max +
        ' data-locked="' + (readonly ? '1' : '0') + '"' + labelledBy + disabled + '>';
    } else if (f.type === 'string_list') {
      const lines = Array.isArray(value) ? value.join('\n') : '';
      control = '<textarea name="' + f.key + '" rows="3" data-locked="' + (readonly ? '1' : '0') + '"' + labelledBy + disabled + '>' +
        escapeHtml(lines) + '</textarea>';
    } else {
      const maxLength = f.max_length != null ? ' maxlength="' + f.max_length + '"' : '';
      control = '<input type="text" name="' + f.key + '" value="' + escapeHtml(String(value || '')) +
        '" placeholder="' + escapeHtml(t('modal.optional')) + '"' + maxLength + labelledBy + disabled + '>';
    }
    const wide = ['theme_mode', 'theme_palette', 'host_layout', 'refresh_ms', 'host_description'].includes(f.key) ? ' is-wide' : '';
    const composeOption = f.key.indexOf('compose_scan_') === 0 ? ' data-compose-option' : '';
    const sourceHint = f.key === 'show_bind_addresses' || BIND_FAMILY_KEYS.includes(f.key)
      ? '' : originHint(f);
    return '<' + tag + ' class="setting-row' + wide + '" data-setting="' + escapeHtml(f.key) + '"' + composeOption + '><span class="setting-copy"><span class="setting-label" id="' + escapeHtml(labelId) + '" data-i18n="settings.fields.' + f.key + '.label">' +
      escapeHtml(fieldLabel(f)) + '</span><span class="field-help" data-i18n="settings.fields.' + f.key + '.help">' + escapeHtml(fieldHelp(f)) +
      '</span></span><span class="setting-control">' + control + sourceHint +
      '</span></' + tag + '>';
  }

  function setFieldControlValue(field, value) {
    const form = document.getElementById('settings-form');
    if (!form || !field) return;
    if (field.type === 'multi_choice') {
      form.querySelectorAll('input[name="' + field.key + '"]').forEach(function (input) {
        input.checked = Array.isArray(value) && value.indexOf(input.value) >= 0;
      });
      return;
    }
    if (field.type === 'choice') {
      form.querySelectorAll('input[type="radio"][name="' + field.key + '"]').forEach(function (input) {
        input.checked = input.value === value;
      });
    }
    const el = form.elements[field.key];
    if (!el) return;
    if (field.type === 'bool') el.checked = !!value;
    else if (field.type === 'string_list') el.value = Array.isArray(value) ? value.join('\n') : '';
    else el.value = value == null ? '' : String(value);
    if (field.key === 'refresh_ms') {
      const hidden = document.querySelector('[data-refresh-hidden]');
      const slider = document.querySelector('[data-refresh-slider]');
      if (hidden) hidden.value = String(value);
      if (slider) {
        const choices = String(slider.getAttribute('data-refresh-values') || '').split(',').map(Number);
        const index = choices.indexOf(Number(value));
        if (index >= 0) {
          slider.value = String(index);
          updateRefreshSlider(slider);
        }
      }
    }
  }

  export function restoreInheritedSetting(key) {
    const field = fieldByKey(key);
    if (!field || !field.can_reset || (S.settingsDoc && S.settingsDoc.readonly)) return false;
    setFieldControlValue(field, field.inherited_value);
    S.settingsRevision += 1;
    S.settingsDraft[key] = field.inherited_value;
    S.settingsDirtyKeys.add(key);
    S.settingsResetKeys.add(key);
    S.settingsKeyRevisions[key] = S.settingsRevision;
    if (LIVE_APPLY_KEYS.indexOf(key) >= 0) {
      S.settings[key] = field.inherited_value;
      applyAppearance();
      if (key === 'theme_mode') syncPaletteAvailability();
    }
    syncDirtyFlag();
    syncDependentSettings();
    setPageStatus('settings-status', 'settings.unsaved', '', 0);
    return true;
  }

  let _themeDelegated = false;
  function ensureThemeDelegates() {
    if (_themeDelegated) return;
    _themeDelegated = true;
    document.addEventListener('click', async function (e) {
      const presetBtn = e.target.closest('[data-editor-preset]');
      if (presetBtn) { fillEditorFromPreset(); return; }
      const saveBtn = e.target.closest('[data-editor-save]');
      if (saveBtn) { saveEditorTheme(saveBtn); return; }
      const exportBtn = e.target.closest('[data-editor-export]');
      if (exportBtn) {
        try {
          exportEditorTheme();
          setPageStatus('theme-editor-status', 'settings.editor.exported', 'action-status is-ok', 1800);
        } catch (err) {
          setPageStatus('theme-editor-status', 'settings.editor.exportFailed', 'action-status is-error', 0);
        }
        return;
      }
      const importBtn = e.target.closest('[data-editor-import]');
      if (importBtn) {
        const file = document.getElementById('editor-file');
        if (file) { file.value = ''; file.click(); }
        return;
      }
      const btn = e.target.closest('[data-delete-theme]');
      if (!btn) return;
      const id = btn.getAttribute('data-delete-theme');
      try {
        const res = await api('/api/custom-themes/' + id, { method: 'DELETE' });
        if (!res.ok) {
          setPageStatus('theme-editor-status', 'settings.editor.deleteFailed', 'action-status is-error', 0);
          return;
        }
        await refreshThemeChoices(id);
        setPageStatus('theme-editor-status', 'settings.saved', 'action-status is-ok', 1800);
      } catch (err) {
        setPageStatus('theme-editor-status', 'settings.editor.deleteFailed', 'action-status is-error', 0);
      }
    });
    /* The file input and hex rows are rebuilt on every renderSettingsForm, so
       these two are wired as document-level delegates rather than bound to
       today's elements. */
    document.addEventListener('change', function (e) {
      if (e.target && e.target.name && CARD_FIELD_KEYS[e.target.name]) {
        updateDisplayPreview();
        return;
      }
      if (e.target && e.target.id === 'editor-file') importEditorThemeFile(e.target);
    });
    document.addEventListener('input', function (e) {
      const colorRow = e.target && e.target.closest ? e.target.closest('[data-editor-color]') : null;
      if (colorRow) {
        const key = colorRow.getAttribute('data-editor-color');
        const hexInput = document.querySelector('[data-editor-hex="' + key + '"]');
        if (hexInput) hexInput.value = colorRow.value;
        previewEditorColor(key, colorRow.value);
        return;
      }
      const hexInput = e.target && e.target.closest ? e.target.closest('[data-editor-hex]') : null;
      if (!hexInput || !/^#[0-9a-fA-F]{6}$/.test(hexInput.value)) return;
      const key = hexInput.getAttribute('data-editor-hex');
      const color = document.querySelector('[data-editor-color="' + key + '"]');
      if (color) color.value = hexInput.value;
      previewEditorColor(key, hexInput.value);
    });
  }

  function previewFlags() {
    const form = document.getElementById('settings-form');
    const read = function (key, fallback) {
      const el = form && form.elements ? form.elements[key] : null;
      return el && typeof el.checked === 'boolean' ? el.checked : fallback;
    };
    return {
      status_text: read('show_status_text', !!S.settings.show_status_text),
      access_badge: read('show_access_badge', S.settings.show_access_badge !== false),
      proto_badge: read('show_protocol_badge', S.settings.show_protocol_badge !== false),
      bind_addresses: read('show_bind_addresses', !!S.settings.show_bind_addresses),
      bind_ipv4: read('show_bind_ipv4', S.settings.show_bind_ipv4 !== false),
      bind_ipv6: read('show_bind_ipv6', S.settings.show_bind_ipv6 !== false),
    };
  }

  export function updateDisplayPreview() {
    const host = document.querySelector('[data-display-preview]');
    if (host) host.outerHTML = displayPreviewHtml();
  }

  export function displayPreviewHtml() {
    const f = previewFlags();
    const bind = bindAddressView([
      '192.168.1.24', '10.0.0.24', 'fd12:3456:789a:1::19', '2001:db8:85a3::8a2e:370:7334',
    ], {
      enabled: f.bind_addresses,
      showV4: f.bind_ipv4,
      showV6: f.bind_ipv6,
      density: S.settings.grid_density || 'standard',
    });
    const cell = function (cls, port, labelKey, bindHtml, extra) {
      return '<div class="port-cell ' + cls + '"><div class="port-num">' + port + '</div>' +
        '<div class="port-label">' + escapeHtml(t(labelKey)) + '</div>' + (bindHtml || '') +
        '<div class="cell-meta"><span class="indicator"></span>' + (extra || '') + '</div></div>';
    };
    const statusText = function (key) {
      return f.status_text ? '<span class="status-text">' + escapeHtml(t(key)) + '</span>' : '';
    };
    const badges = function (access, proto) {
      return (f.access_badge && access ? '<span class="access-badge">' + escapeHtml(t('grid.web')) + '</span>' : '') +
        (f.proto_badge && proto ? '<span class="proto-badge">udp</span>' : '');
    };
    return '<div class="display-preview" data-display-preview aria-hidden="true">' +
      '<div class="host-grid">' +
      cell('used', 8080, 'status.used', bind.html, statusText('status.used') + badges(true, true)) +
      cell('configured', 3000, 'status.configured', '', statusText('status.configured')) +
      cell('free', 5432, 'status.free', '', '') +
      '</div></div>';
  }

  export function renderSettingsForm(doc) {
    resetDraftState(doc);
    const values = Object.assign({}, S.settingsDraft);
    const fields = doc.fields || [];
    const host = document.getElementById('settings-fields');
    const lead = document.getElementById('settings-lead');
    const status = document.getElementById('settings-status');
    status.className = '';
    status.textContent = doc.readonly ? '' : t('settings.autosave');
    lead.textContent = t(doc.readonly ? 'settings.leadReadonly' : 'settings.lead');

    const byGroup = {};
    const groupOrder = [];
    fields.forEach(function (f) {
      if (!byGroup[f.group]) {
        byGroup[f.group] = [];
        groupOrder.push(f.group);
      }
      byGroup[f.group].push(f);
    });
    function rowsFor(list) {
      return (list || []).map(function (f) {
        return renderField(f, values[f.key], doc.readonly, doc);
      }).join('');
    }

    const appearanceFields = byGroup.appearance || [];
    const themeFields = appearanceFields.filter(function (f) {
      return !CARD_FIELD_KEYS[f.key] && f.key !== 'locale' && f.key !== 'grid_density' && f.key !== 'host_layout';
    });
    const languageFields = appearanceFields.filter(function (f) { return f.key === 'locale'; });
    const densityFields = appearanceFields.filter(function (f) { return f.key === 'grid_density'; });
    const layoutFields = appearanceFields.filter(function (f) { return f.key === 'host_layout'; });
    const cardFields = appearanceFields.filter(function (f) { return CARD_FIELD_KEYS[f.key]; });
    const bindChildren = cardFields.filter(function (f) {
      return BIND_FAMILY_KEYS.includes(f.key);
    });
    const primaryCardFields = cardFields.filter(function (f) {
      return !BIND_FAMILY_KEYS.includes(f.key);
    });
    const bindFamilyOptions = bindChildren.length
      ? '<fieldset class="setting-children" id="bind-address-family-options"' +
        (values.show_bind_addresses ? '' : ' hidden') + '><legend data-i18n="settings.bindFamilies">' +
        escapeHtml(t('settings.bindFamilies')) + '</legend>' + rowsFor(bindChildren) + '</fieldset>'
      : '';
    const knownGroups = { appearance: true, grid: true, local: true, scanning: true, links: true };
    const extraAdvanced = groupOrder.filter(function (g) { return !knownGroups[g]; }).map(function (g) {
      return settingsCard('settings.groups.' + g + '.title', 'settings.groups.' + g + '.blurb', rowsFor(byGroup[g]));
    }).join('');

    host.innerHTML =
      settingsPanelHtml('appearance',
        settingsCard('settings.sections.language.title', 'settings.sections.language.blurb',
          rowsFor(languageFields)) +
        settingsCard('settings.sections.theme.title', 'settings.sections.theme.blurb',
          '<div data-appearance-section="theme">' + rowsFor(themeFields) + themeEditorHtml(!!doc.readonly) + '</div>') +
        settingsCard('settings.cards.title', 'settings.cards.blurb',
          rowsFor(layoutFields) + rowsFor(densityFields) + displayPreviewHtml() + rowsFor(primaryCardFields) + bindFamilyOptions)) +
      settingsPanelHtml('occupancy',
        settingsCard('settings.groups.local.title', 'settings.groups.local.blurb', rowsFor(byGroup.local || [])) +
        settingsCard('settings.groups.grid.title', 'settings.groups.grid.blurb', rowsFor(byGroup.grid || [])) +
        settingsCard('settings.groups.scanning.title', 'settings.groups.scanning.blurb', rowsFor(byGroup.scanning || [])) +
        settingsCard('hosts.title', 'hosts.blurb', '<div id="settings-peers"></div><p id="peers-status" class="action-status" role="status" aria-live="polite"></p>')) +
      settingsPanelHtml('automation', automationCardsHtml(S.meta && S.meta.automation ? S.meta.automation : {})) +
      settingsPanelHtml('advanced',
        settingsCard('settings.groups.links.title', 'settings.groups.links.blurb', rowsFor(byGroup.links || [])) +
        extraAdvanced +
        settingsCard('settings.host.title', 'settings.host.blurb', '<div id="settings-env-only"></div>'));

    const env = doc.env_only || {};
    const envHost = document.getElementById('settings-env-only');
    if (envHost) {
      envHost.innerHTML = [
        kvRow('settings.host.composeScanDir', env.compose_scan_dir),
        kvRow('settings.host.customPortsFile', env.custom_ports_file),
        kvRow('settings.host.dataDir', env.data_dir),
        kvRow('settings.host.basicAuth', env.auth_required ? t('settings.on') : t('settings.off'), env.auth_required ? 'settings.on' : 'settings.off'),
        kvRow('settings.host.hiddenUnlock', env.hidden_unlock_required ? t('settings.on') : t('settings.off'), env.hidden_unlock_required ? 'settings.on' : 'settings.off'),
        kvRow(
          'settings.host.settingsSource',
          t('settings.source.' + doc.source),
          (doc.source === 'auto' || doc.source === 'env' || doc.source === 'file') ? 'settings.source.' + doc.source : '',
        ),
      ].join('');
    }
    showSettingsPanel(S.route.section || S.settingsPanel);
    syncDependentSettings();
    ensureAutomationDelegates();
    ensureThemeDelegates();
    renderPeersEditor(!!doc.readonly || !!S.hostCatalog.readonly);
  }

  function currentDirtySettingKeys() {
    return Array.from(S.settingsDirtyKeys || []).filter(function (key) { return !!fieldByKey(key); });
  }

  function collectSettingsPatch(keys) {
    const patch = {};
    for (const key of keys) {
      const field = fieldByKey(key);
      if (!field) continue;
      if (S.settingsResetKeys.has(key)) {
        patch[key] = null;
        continue;
      }
      const value = readFieldValue(field);
      S.settingsDraft[key] = value;
      if (field.type === 'multi_choice' && !value.length) {
        setPageStatus('settings-status', 'settings.scanners.required', 'is-error', 0);
        return null;
      }
      if (field.type === 'int' && Number.isNaN(value)) {
        const status = document.getElementById('settings-status');
        if (status) {
          status.className = 'is-error';
          status.textContent = t('settings.mustBeNumber', { label: fieldLabel(field) });
        }
        return null;
      }
      patch[key] = value;
    }
    return patch;
  }

  function acceptSettingsResponse(body, keys, revisions, themesAtStart) {
    if (S.customThemes !== themesAtStart) {
      body.custom_themes = S.customThemes;
      const oldPalette = fieldByKey('theme_palette');
      if (oldPalette) {
        (body.fields || []).forEach(function (field) {
          if (field.key === 'theme_palette') field.choices = oldPalette.choices;
        });
      }
    }
    S.settingsDoc = body;
    S.settingsConfirmed = Object.assign({}, body.values || {});
    S.customThemes = Array.isArray(body.custom_themes) ? body.custom_themes : [];
    keys.forEach(function (key) {
      S.settingsSubmittingKeys.delete(key);
      if (S.settingsKeyRevisions[key] === revisions[key]) {
        S.settingsDirtyKeys.delete(key);
        S.settingsResetKeys.delete(key);
        S.settingsDraft[key] = body.values[key];
      }
    });
    Object.keys(body.values || {}).forEach(function (key) {
      if (!S.settingsDirtyKeys.has(key)) S.settingsDraft[key] = body.values[key];
    });
    S.settings = Object.assign({}, S.settings, body.values || {});
    LIVE_APPLY_KEYS.forEach(function (key) {
      if (S.settingsDirtyKeys.has(key)) S.settings[key] = S.settingsDraft[key];
    });
    if (keys.includes('port_range_start') || keys.includes('port_range_end')) {
      if (!S.settingsDirtyKeys.has('port_range_start') && !S.settingsDirtyKeys.has('port_range_end')) {
        S.rangeStart = body.values.port_range_start;
        S.rangeEnd = body.values.port_range_end;
        S.rangeFromView = false;
        rangeStartInput.value = S.rangeStart;
        rangeEndInput.value = S.rangeEnd;
        saveView();
      }
    }
    applyAppearance();
    persistAppearance();
    syncSettingsMetadata(body);
    if (body.values && body.values.local_scanners && body.values.local_scanners.length) {
      const repair = document.querySelector('[data-i18n="settings.scanners.invalid"]');
      if (repair) repair.remove();
    }
    syncDirtyFlag();
  }

  export async function saveSettingsFields() {
    if (S.settingsDoc && S.settingsDoc.readonly) return false;
    const keys = currentDirtySettingKeys();
    if (!keys.length) return false;
    const patch = collectSettingsPatch(keys);
    if (!patch) return false;
    const revisions = {};
    keys.forEach(function (key) {
      revisions[key] = S.settingsKeyRevisions[key];
      S.settingsSubmittingKeys.add(key);
    });
    const themesAtStart = S.customThemes;
    setPageStatus('settings-status', 'settings.saving', '', 0);
    let res;
    let body;
    try {
      res = await api('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      body = await res.json().catch(function () { return {}; });
    } catch (err) {
      keys.forEach(function (key) { S.settingsSubmittingKeys.delete(key); });
      setPageStatus('settings-status', 'settings.saveFailed', 'is-error', 0);
      return false;
    }
    if (!res.ok) {
      keys.forEach(function (key) { S.settingsSubmittingKeys.delete(key); });
      const status = document.getElementById('settings-status');
      if (status) { status.className = 'is-error'; status.textContent = errorText(body, res.status); }
      return false;
    }
    acceptSettingsResponse(body, keys, revisions, themesAtStart);
    if (S.settingsDirtyKeys.size) setPageStatus('settings-status', 'settings.unsaved', '', 0);
    else setPageStatus('settings-status', 'settings.saved', 'is-ok', 1800);
    return true;
  }

  export async function savePeersPage() {
    if (!S.peersDirty || (S.hostCatalog && S.hostCatalog.readonly)) return false;
    const revision = S.peersRevision;
    const peers = peersPayload();
    if (peers.some(function (peer) { return !peer.name || !peer.url; })) {
      setPageStatus('peers-status', 'hosts.incomplete', 'action-status is-error', 0);
      return false;
    }
    S.peersSubmitting = true;
    setPageStatus('peers-status', 'settings.saving', 'action-status', 0);
    let res;
    let body;
    try {
      res = await api('/api/hosts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ peers: peers }),
      });
      body = await res.json().catch(function () { return {}; });
    } catch (err) {
      S.peersSubmitting = false;
      setPageStatus('peers-status', 'hosts.saveFailed', 'action-status is-error', 0);
      return false;
    }
    S.peersSubmitting = false;
    if (!res.ok) {
      const status = document.getElementById('peers-status');
      if (status) { status.className = 'action-status is-error'; status.textContent = errorText(body, res.status); }
      return false;
    }
    S.hostCatalog = {
      local: body.local || S.hostCatalog.local,
      peers: Array.isArray(body.peers) ? body.peers : [],
      readonly: !!body.readonly,
      max_peers: Number(body.max_peers) || Number(S.hostCatalog.max_peers) || 32,
    };
    if (revision === S.peersRevision) {
      S.peersDirty = false;
      S.peersDraft = (S.hostCatalog.peers || []).map(clonePeerRow);
      syncSavedPeerRows();
      setPageStatus('peers-status', 'settings.saved', 'action-status is-ok', 1800);
    } else {
      setPageStatus('peers-status', 'settings.unsaved', 'action-status', 0);
    }
    syncDirtyFlag();
    syncRefreshCapacity();
    return true;
  }

  export async function saveSettingsPage() {
    const results = await Promise.all([saveSettingsFields(), savePeersPage()]);
    return results.some(Boolean);
  }

  export function mountSettingsPage(root, options) {
    options = options || {};
    if (!root) throw new Error('settings root is required');
    const nav = document.getElementById('settings-nav');
    const fields = document.getElementById('settings-fields');
    let settingsTimer = null;
    let peersTimer = null;
    let settingsQueue = Promise.resolve(false);
    let peersQueue = Promise.resolve(false);

    function afterSave(saved, kind) {
      if (saved && options.onSaved) options.onSaved(kind);
      return saved;
    }
    function runSettingsSave() {
      settingsQueue = settingsQueue.catch(function () { return false; }).then(saveSettingsFields)
        .then(function (saved) { return afterSave(saved, 'settings'); });
      return settingsQueue;
    }
    function runPeersSave() {
      peersQueue = peersQueue.catch(function () { return false; }).then(savePeersPage)
        .then(function (saved) { return afterSave(saved, 'peers'); });
      return peersQueue;
    }
    function scheduleSettings(delay) {
      if (S.settingsDoc && S.settingsDoc.readonly) return;
      if (settingsTimer) clearTimeout(settingsTimer);
      settingsTimer = setTimeout(function () { settingsTimer = null; runSettingsSave(); }, Math.max(0, Number(delay) || 0));
    }
    function schedulePeers(delay) {
      if (S.hostCatalog && S.hostCatalog.readonly) return;
      if (peersTimer) clearTimeout(peersTimer);
      peersTimer = setTimeout(function () { peersTimer = null; runPeersSave(); }, Math.max(0, Number(delay) || 0));
    }
    function applyLocale() {
      if (!window.PortLightI18n) return Promise.resolve();
      return PortLightI18n.load(S.settings.locale).then(function () {
        PortLightI18n.applyDom();
        syncLocaleTrigger();
        syncRefreshCapacity();
        if (S.settingsDoc) {
          const lead = document.getElementById('settings-lead');
          if (lead) lead.textContent = t(S.settingsDoc.readonly ? 'settings.leadReadonly' : 'settings.lead');
        }
        if (options.onLocaleApplied) options.onLocaleApplied();
      });
    }

    root.addEventListener('submit', function (event) { event.preventDefault(); controller.flush(); });
    nav.addEventListener('click', function (event) {
      const button = event.target.closest('[role="tab"][data-settings-panel]');
      if (!button) return;
      event.preventDefault();
      goSettingsPanel(button.getAttribute('data-settings-panel'));
    });
    nav.addEventListener('keydown', function (event) {
      const button = event.target.closest('[role="tab"][data-settings-panel]');
      if (!button) return;
      let index = SETTINGS_PANELS.indexOf(button.getAttribute('data-settings-panel'));
      if (index < 0) return;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') index = (index + 1) % SETTINGS_PANELS.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') index = (index - 1 + SETTINGS_PANELS.length) % SETTINGS_PANELS.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = SETTINGS_PANELS.length - 1;
      else return;
      event.preventDefault();
      goSettingsPanel(SETTINGS_PANELS[index]);
      const next = document.getElementById('settings-tab-' + SETTINGS_PANELS[index]);
      if (next) next.focus();
    });
    fields.addEventListener('change', function (event) {
      if (!isAutosavedSetting(event.target)) return;
      if (event.target.matches('[data-peer-field]')) {
        readPeersDraftFromForm();
        markPeersDirty();
        schedulePeers(180);
        return;
      }
      const key = settingKeyForInput(event.target);
      if (LIVE_APPLY_KEYS.indexOf(key) >= 0) {
        S.settings[key] = event.target.value;
        applyAppearance();
        if (key === 'theme_mode') syncPaletteAvailability();
        if (key === 'locale') applyLocale();
      }
      markDirty(event.target);
      syncDependentSettings();
      scheduleSettings(180);
    });
    fields.addEventListener('input', function (event) {
      if (!isAutosavedSetting(event.target)) return;
      if (event.target.matches('[data-peer-field]')) {
        readPeersDraftFromForm();
        markPeersDirty();
        schedulePeers(700);
        return;
      }
      updateRefreshSlider(event.target);
      markDirty(event.target);
      scheduleSettings(700);
    });
    fields.addEventListener('click', function (event) {
      const reset = event.target.closest('[data-reset-setting]');
      if (reset) {
        event.preventDefault();
        const key = reset.getAttribute('data-reset-setting');
        if (!restoreInheritedSetting(key)) return;
        if (key === 'locale') applyLocale();
        if (CARD_FIELD_KEYS[key]) updateDisplayPreview();
        scheduleSettings(0);
        return;
      }
      const add = event.target.closest('#peer-add');
      if (add) {
        event.preventDefault();
        if (S.hostCatalog.readonly || (S.settingsDoc && S.settingsDoc.readonly)) return;
        readPeersDraftFromForm();
        if (S.peersDraft.length >= (Number(S.hostCatalog.max_peers) || 32)) return;
        S.peersDraft.push({ id: '', name: '', description: '', url: '', username: '', password: '', has_auth: false, clear_auth: false });
        renderPeersEditor(false);
        markPeersDirty();
        return;
      }
      const row = event.target.closest('.peer-row');
      if (row && event.target.closest('[data-peer-remove]')) {
        event.preventDefault();
        readPeersDraftFromForm();
        const index = parseInt(row.getAttribute('data-peer-index'), 10);
        if (!isNaN(index)) S.peersDraft.splice(index, 1);
        renderPeersEditor(!!(S.settingsDoc && S.settingsDoc.readonly) || !!S.hostCatalog.readonly);
        markPeersDirty();
        schedulePeers(180);
        return;
      }
      if (row && event.target.closest('[data-peer-clear-auth]')) {
        event.preventDefault();
        readPeersDraftFromForm();
        const index = parseInt(row.getAttribute('data-peer-index'), 10);
        if (!isNaN(index) && S.peersDraft[index]) {
          S.peersDraft[index].clear_auth = true;
          S.peersDraft[index].has_auth = false;
          S.peersDraft[index].username = '';
          S.peersDraft[index].password = '';
        }
        renderPeersEditor(false);
        markPeersDirty();
        schedulePeers(180);
        return;
      }
      const trigger = event.target.closest('.locale-trigger');
      if (trigger) {
        event.preventDefault();
        const drop = trigger.closest('.locale-dropdown');
        const open = !drop.classList.contains('is-open');
        closeLocaleMenu();
        if (open) {
          drop.classList.add('is-open');
          trigger.setAttribute('aria-expanded', 'true');
          const selected = drop.querySelector('.locale-row.is-selected') || drop.querySelector('.locale-row');
          if (selected) selected.focus({ preventScroll: true });
        }
        return;
      }
      const localeRow = event.target.closest('.locale-row');
      if (!localeRow) return;
      event.preventDefault();
      const drop = localeRow.closest('.locale-dropdown');
      const input = drop.querySelector('input[name="locale"]');
      const value = localeRow.getAttribute('data-value');
      if (input.value === value) {
        closeLocaleMenu({ focusTrigger: true });
        return;
      }
      input.value = value;
      drop.querySelectorAll('.locale-row').forEach(function (rowEl) {
        const on = rowEl === localeRow;
        rowEl.classList.toggle('is-selected', on);
        rowEl.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      syncLocaleTrigger();
      closeLocaleMenu({ focusTrigger: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    document.addEventListener('click', function (event) {
      if (!event.target.closest('.locale-dropdown')) closeLocaleMenu();
    });

    const controller = {
      open(section) {
        S.settingsPanel = SETTINGS_PANELS.includes(section) ? section : 'appearance';
        return loadSettingsPage();
      },
      show(section) { showSettingsPanel(section, true); },
      syncPaletteAvailability,
      closeTransient(opts) { return closeLocaleMenu(opts); },
      hasPending() {
        return syncDirtyFlag() || !!settingsTimer || !!peersTimer ||
          !!S.peersSubmitting || !!(S.settingsSubmittingKeys && S.settingsSubmittingKeys.size);
      },
      flush() {
        if (settingsTimer) { clearTimeout(settingsTimer); settingsTimer = null; }
        if (peersTimer) { clearTimeout(peersTimer); peersTimer = null; }
        return Promise.all([runSettingsSave(), runPeersSave()]);
      },
    };
    return controller;
  }

export function renderPeersEditor(readonly) { return renderPeersEditorView(readonly, syncRefreshCapacity); }
