/* Settings view: four panels, locale menu, theme picker, peers editor. */

import { S, SETTINGS_PANELS, CARD_FIELD_KEYS, CORE_THEMES, applyAppearance, saveView } from './state.js?v=61';
import { t, tx, escapeHtml, errorText } from './text.js?v=61';
import { settingsBtn, rangeStartInput, rangeEndInput, syncHeaderHeight } from './dom.js?v=61';
import { moveChipFocus } from './a11y.js?v=61';
import { remainingSeconds, fmtRemaining, formatAgo } from './leases.js?v=61';
import { api, hasPeers, hostById, hostName, fetchHosts, setupRefresh } from './api.js?v=61';
import { render, syncHiddenButton } from './grid.js?v=61';

  export async function fetchSettings() {
    const res = await api('/api/settings');
    if (!res.ok) return null;
    return res.json();
  }

  export function loadSettingsPage() {
    Promise.all([fetchSettings(), fetchHosts()]).then(function (pair) {
      const doc = pair[0];
      if (!doc) return;
      S.settingsDoc = doc;
      S.peersDraft = (S.hostCatalog.peers || []).map(clonePeerRow);
      renderSettingsForm(doc);
    });
  }

  export function clonePeerRow(p) {
    return {
      id: p.id || '',
      name: p.name || '',
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

  export function choiceLabel(c) {
    return t('choice.' + c);
  }

  export function settingsCard(titleKey, blurbKey, rowsHtml) {
    return '<section class="settings-card"><header class="settings-card-head"><h2 data-i18n="' + titleKey + '">' +
      escapeHtml(t(titleKey)) + '</h2><p data-i18n="' + blurbKey + '">' +
      escapeHtml(t(blurbKey)) + '</p></header><div class="settings-card-body">' + rowsHtml + '</div></section>';
  }

  export function settingsPanelHtml(id, inner) {
    return '<div class="settings-panel" id="settings-panel-' + id + '" role="tabpanel" data-settings-panel="' + id +
      '" aria-labelledby="settings-tab-' + id + '">' + inner + '</div>';
  }

  export function showSettingsPanel(id) {
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
  }

  export function goSettingsPanel(id) {
    if (SETTINGS_PANELS.indexOf(id) < 0) return;
    showSettingsPanel(id);
    const next = '#/settings/' + id;
    if ((location.hash || '') !== next) location.hash = next;
  }

  export function syncDependentSettings() {
    const form = document.getElementById('settings-form');
    if (!form) return;
    const auto = form.elements.auto_refresh;
    const row = form.querySelector('[data-setting="refresh_ms"]');
    if (!auto || !row) return;
    row.classList.toggle('is-inactive', !auto.checked);
  }

  export function markDirty() {
    if (S.settingsDoc && S.settingsDoc.readonly) return;
    S.settingsDirty = true;
    const status = document.getElementById('settings-status');
    status.className = '';
    status.textContent = t('settings.unsaved');
  }

  export function applyServerSettings(doc) {
    S.settingsDoc = doc;
    S.settings = Object.assign({}, S.settings, doc.values || {});
    if (!S.rangeFromView) {
      S.rangeStart = S.settings.port_range_start;
      S.rangeEnd = S.settings.port_range_end;
      rangeStartInput.value = S.rangeStart;
      rangeEndInput.value = S.rangeEnd;
    }
    applyAppearance();
  }

  export function revertUnsavedSettings() {
    S.settingsDirty = false;
    if (!S.settingsDoc) return;
    applyServerSettings(S.settingsDoc);
    if (!window.PortLightI18n) return;
    PortLightI18n.load(S.settings.locale || 'auto').then(function () {
      PortLightI18n.applyDom();
      syncHiddenButton();
      if (S.currentData) render();
      syncHeaderHeight();
    });
  }

  export function kvRow(labelKey, value, valueKey) {
    const val = valueKey
      ? '<span class="kv-val" data-i18n="' + valueKey + '">' + escapeHtml(String(value == null ? '' : value)) + '</span>'
      : '<span class="kv-val">' + escapeHtml(String(value == null ? '' : value)) + '</span>';
    return '<div class="kv-row"><span class="kv-key" data-i18n="' + labelKey + '">' +
      escapeHtml(t(labelKey)) + '</span>' + val + '</div>';
  }

  export function originHint(f) {
    if (!f.origin || f.origin === 'default') return '';
    const key = f.origin === 'file' ? 'settings.origin.saved' : 'settings.origin.env';
    return '<span class="origin-hint" data-i18n="' + key + '" title="' + escapeHtml(f.env) + '">' + escapeHtml(t(key)) + '</span>';
  }

  export function localeCopyHtml(c) {
    var native;
    var nativeAttr;
    var localKey;
    if (c === 'auto') {
      native = t('choice.auto');
      nativeAttr = ' data-i18n="choice.auto"';
      localKey = 'localeName.auto';
    } else {
      native = t('localeNative.' + c);
      nativeAttr = '';
      localKey = 'localeName.' + c;
    }
    return '<span class="locale-copy"><span class="locale-endonym"' + nativeAttr + '>' +
      escapeHtml(native) + '</span><span class="locale-exonym" data-i18n="' + localKey + '">' +
      escapeHtml(t(localKey)) + '</span></span>';
  }

  export function closeLocaleMenu(opts) {
    const drop = document.querySelector('.locale-dropdown.is-open');
    if (!drop) return false;
    drop.classList.remove('is-open');
    const btn = drop.querySelector('.locale-trigger');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    if (opts && opts.focusTrigger && btn) btn.focus();
    return true;
  }

  export function moveLocaleHighlight(delta) {
    const drop = document.querySelector('.locale-dropdown.is-open');
    if (!drop) return;
    const rows = Array.prototype.slice.call(drop.querySelectorAll('.locale-row'));
    if (!rows.length) return;
    let i = rows.indexOf(document.activeElement);
    if (delta === 'start') i = 0;
    else if (delta === 'end') i = rows.length - 1;
    else if (i < 0) i = 0;
    else i = (i + delta + rows.length) % rows.length;
    rows[i].focus();
  }

  export function syncLocaleTrigger() {
    const drop = document.querySelector('.locale-dropdown');
    if (!drop) return;
    const input = drop.querySelector('input[name="locale"]');
    const dest = drop.querySelector('.locale-trigger .locale-copy');
    if (!input || !dest) return;
    const row = drop.querySelector('.locale-row[data-value="' + input.value + '"] .locale-copy');
    if (row) dest.innerHTML = row.innerHTML;
  }

  export function renderLocaleList(choices, value, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : 'auto';
    const label = escapeHtml(t('settings.fields.locale.label'));
    const rows = choices.map(function (c) {
      const on = c === current;
      const id = 'locale-opt-' + c;
      return '<button type="button" class="locale-row' + (on ? ' is-selected' : '') +
        '" id="' + escapeHtml(id) + '" data-value="' + escapeHtml(c) + '" role="option" aria-selected="' + (on ? 'true' : 'false') + '"' +
        disabled + '>' + localeCopyHtml(c) + '<span class="locale-check" aria-hidden="true"></span></button>';
    }).join('');
    return '<div class="locale-dropdown">' +
      '<input type="hidden" name="locale" value="' + escapeHtml(current) + '"' + disabled + '>' +
      '<button type="button" class="locale-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="locale-menu" aria-label="' +
      label + '"' + disabled + '>' +
      localeCopyHtml(current) + '<span class="locale-caret" aria-hidden="true"></span></button>' +
      '<div class="locale-menu" id="locale-menu" role="listbox" aria-label="' + label + '">' + rows + '</div></div>';
  }

  export function renderThemePicker(choices, value, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : 'system';
    const label = escapeHtml(t('settings.fields.theme.label'));
    function swatch(c) {
      const on = c === current;
      const preview = c === 'system'
        ? '<span class="theme-swatch-preview is-system" aria-hidden="true">' +
          '<span class="theme-swatch-half dark"></span><span class="theme-swatch-half light"></span></span>'
        : '<span class="theme-swatch-preview" aria-hidden="true"><i class="used"></i><i class="configured"></i><i class="free"></i></span>';
      return '<label class="theme-swatch" data-theme-preview="' + escapeHtml(c) + '">' +
        '<input type="radio" name="theme" value="' + escapeHtml(c) + '"' +
        (on ? ' checked' : '') + disabled + '>' + preview +
        '<span class="theme-swatch-name" data-i18n="choice.' + c + '">' +
        escapeHtml(choiceLabel(c)) + '</span></label>';
    }
    const core = CORE_THEMES.filter(function (c) { return choices.indexOf(c) >= 0; });
    const palettes = choices.filter(function (c) { return CORE_THEMES.indexOf(c) < 0; });
    return '<div class="theme-picker" role="radiogroup" aria-label="' + label + '">' +
      '<div class="theme-picker-core">' + core.map(swatch).join('') + '</div>' +
      (palettes.length
        ? '<p class="theme-picker-label" data-i18n="settings.theme.palettes">' +
          escapeHtml(t('settings.theme.palettes')) + '</p>' +
          '<div class="theme-picker-palettes">' + palettes.map(swatch).join('') + '</div>'
        : '') +
      '</div>';
  }

  export function renderField(f, value, readonly) {
    const disabled = readonly ? ' disabled' : '';
    let control = '';
    let tag = 'div';
    if (f.type === 'bool') {
      tag = 'label';
      control = '<span class="switch"><input type="checkbox" name="' + f.key + '"' +
        (value ? ' checked' : '') + disabled + '><span class="track"></span></span>';
    } else if (f.key === 'locale') {
      control = renderLocaleList(f.choices || [], value, disabled);
    } else if (f.key === 'theme') {
      control = renderThemePicker(f.choices || [], value, disabled);
    } else if (f.type === 'choice') {
      const choices = f.choices || [];
      control = '<div class="segmented" role="radiogroup" aria-label="' +
        escapeHtml(fieldLabel(f)) + '">' +
        choices.map(function (c) {
          return '<label class="seg-opt"><input type="radio" name="' + f.key + '" value="' +
            escapeHtml(c) + '"' + (c === value ? ' checked' : '') + disabled +
            '><span data-i18n="choice.' + c + '">' + escapeHtml(choiceLabel(c)) + '</span></label>';
        }).join('') + '</div>';
    } else if (f.type === 'int') {
      const min = f.min != null ? ' min="' + f.min + '"' : '';
      const max = f.max != null ? ' max="' + f.max + '"' : '';
      control = '<input type="number" name="' + f.key + '" value="' + escapeHtml(String(value)) + '"' + min + max + disabled + '>';
    } else {
      control = '<input type="text" name="' + f.key + '" value="' + escapeHtml(String(value || '')) +
        '" placeholder="' + escapeHtml(t('modal.optional')) + '"' + disabled + '>';
    }
    const wide = f.key === 'theme' ? ' is-wide' : '';
    return '<' + tag + ' class="setting-row' + wide + '" data-setting="' + escapeHtml(f.key) + '"><span class="setting-copy"><span class="setting-label" data-i18n="settings.fields.' + f.key + '.label">' +
      escapeHtml(fieldLabel(f)) + '</span><span class="field-help" data-i18n="settings.fields.' + f.key + '.help">' + escapeHtml(fieldHelp(f)) +
      '</span></span><span class="setting-control">' + control + originHint(f) +
      '</span></' + tag + '>';
  }

  export function renderPeersEditor(readonly) {
    const host = document.getElementById('settings-peers');
    if (!host) return;
    const locked = !!readonly;
    const rows = (S.peersDraft || []).map(function (row, i) {
      const disabled = locked ? ' disabled' : '';
      const keep = row.has_auth && !row.clear_auth
        ? ' placeholder="' + escapeHtml(t('hosts.passwordKeep')) + '"'
        : '';
      return '<div class="peer-row" data-peer-index="' + i + '" data-peer-id="' +
        escapeHtml(row.id || '') + '" data-has-auth="' + (row.has_auth && !row.clear_auth ? '1' : '0') + '">' +
        '<label><span data-i18n="hosts.name">' + escapeHtml(t('hosts.name')) + '</span>' +
        '<input data-peer-field="name" maxlength="40" value="' + escapeHtml(row.name || '') +
        '" placeholder="' + escapeHtml(t('hosts.namePlaceholder')) + '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.url">' + escapeHtml(t('hosts.url')) + '</span>' +
        '<input data-peer-field="url" value="' + escapeHtml(row.url || '') +
        '" placeholder="' + escapeHtml(t('hosts.urlPlaceholder')) + '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.username">' + escapeHtml(t('hosts.username')) + '</span>' +
        '<input data-peer-field="username" autocomplete="off" value="' + escapeHtml(row.username || '') +
        '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.password">' + escapeHtml(t('hosts.password')) + '</span>' +
        '<input type="password" data-peer-field="password" autocomplete="new-password" value="' +
        escapeHtml(row.password || '') + '"' + keep + disabled + '></label>' +
        '<div class="peer-row-actions">' +
        (row.has_auth && !row.clear_auth
          ? '<button type="button" class="btn-secondary" data-peer-clear-auth' + disabled + '>' +
            escapeHtml(t('hosts.clearAuth')) + '</button>'
          : '') +
        '<button type="button" class="btn-secondary" data-peer-remove' + disabled + '>' +
        escapeHtml(t('hosts.remove')) + '</button></div></div>';
    }).join('');
    const canAdd = !locked && S.peersDraft.length < 6;
    host.innerHTML = '<div class="peer-list">' + rows + '</div>' +
      '<p class="field-help" data-i18n="hosts.max">' + escapeHtml(t('hosts.max')) + '</p>' +
      '<p class="field-help" data-i18n="hosts.dockerHint">' + escapeHtml(t('hosts.dockerHint')) + '</p>' +
      '<button type="button" class="btn-secondary" id="peer-add"' + (canAdd ? '' : ' disabled') + '>' +
      escapeHtml(t('hosts.add')) + '</button>';
  }

  export function readPeersDraftFromForm() {
    const host = document.getElementById('settings-peers');
    if (!host) return;
    const rows = host.querySelectorAll('.peer-row');
    const next = [];
    rows.forEach(function (row, i) {
      const prev = S.peersDraft[i] || {};
      next.push({
        id: row.getAttribute('data-peer-id') || prev.id || '',
        name: ((row.querySelector('[data-peer-field="name"]') || {}).value || ''),
        url: ((row.querySelector('[data-peer-field="url"]') || {}).value || ''),
        username: ((row.querySelector('[data-peer-field="username"]') || {}).value || ''),
        password: ((row.querySelector('[data-peer-field="password"]') || {}).value || ''),
        has_auth: row.getAttribute('data-has-auth') === '1' || !!prev.has_auth,
        clear_auth: !!prev.clear_auth,
      });
    });
    S.peersDraft = next;
  }

  export function peersPayload() {
    readPeersDraftFromForm();
    return S.peersDraft.map(function (row) {
      const name = String(row.name || '').trim();
      const url = String(row.url || '').trim();
      if (!name || !url) return null;
      const item = { name: name, url: url };
      if (row.id) item.id = row.id;
      if (row.clear_auth) {
        item.username = '';
        item.password = '';
        return item;
      }
      if (row.username) item.username = row.username;
      if (row.password) item.password = row.password;
      return item;
    }).filter(Boolean);
  }

  function snippetBlock(captionKey, id, code) {
    return '<div class="snippet"><p class="snippet-cap">' + escapeHtml(t(captionKey)) + '</p>' +
      '<pre id="' + id + '">' + escapeHtml(code) + '</pre>' +
      '<button type="button" class="btn-secondary" data-copy="' + id + '" data-label="' +
      escapeHtml(t('settings.auto.connect.copy')) + '">' +
      escapeHtml(t('settings.auto.connect.copy')) + '</button></div>';
  }

  export function automationCardsHtml(a) {
    const origin = location.origin;
    const mcpDocker = JSON.stringify({
      mcpServers: {
        'port-light': {
          command: 'docker',
          args: ['exec', '-i', 'port-light', 'python', 'mcp/server.py'],
          env: { PORT_LIGHT_URL: 'http://127.0.0.1:2100' },
        },
      },
    }, null, 2);
    const mcpSource = JSON.stringify({
      mcpServers: {
        'port-light': {
          command: 'python',
          args: ['/path/to/port-light/mcp/server.py'],
          env: { PORT_LIGHT_URL: origin },
        },
      },
    }, null, 2);
    let curl = 'curl -s "' + origin + '/api/ports/suggest?count=2&reserve=true&ttl=3600&label=preview"';
    if (a.agent_token) curl += ' \\\n  -H "X-Agent-Token: <your-token>"';

    const connect =
      snippetBlock('settings.auto.connect.mcpDocker', 'al-mcp-docker', mcpDocker) +
      snippetBlock('settings.auto.connect.mcpSource', 'al-mcp-src', mcpSource) +
      snippetBlock('settings.auto.connect.skill', 'al-skill',
        'docker exec port-light cat /app/skills/port-light/SKILL.md' +
        ' > ~/.claude/skills/port-light/SKILL.md') +
      '<p class="muted">' + escapeHtml(t('settings.auto.connect.skillHint')) + '</p>' +
      snippetBlock('settings.auto.connect.curl', 'al-curl', curl) +
      (a.agent_token ? '<p class="muted">' + escapeHtml(t('settings.auto.connect.curlToken')) + '</p>' : '');

    const statusRows = [
      kvRow('settings.auto.agentToken',
        t(a.agent_token ? 'settings.on' : 'settings.off'),
        a.agent_token ? 'settings.on' : 'settings.off'),
      kvRow('settings.auto.suggest', t('settings.auto.suggestValue'), 'settings.auto.suggestValue'),
      kvRow('settings.auto.metrics', t(a.metrics ? 'settings.on' : 'settings.off'), ''),
      kvRow('settings.auto.webhook', t(a.webhook ? 'settings.on' : 'settings.off'), ''),
      kvRow('settings.auto.history', a.history_days > 0 ? String(a.history_days) : t('settings.off'), ''),
      kvRow('settings.auto.events', t(a.events_stream ? 'settings.on' : 'settings.off'), ''),
    ].join('');

    const ev = a.agent_events || null;
    const activity = ev
      ? '<p class="auto-summary" data-auto-summary>' +
        escapeHtml(t('settings.auto.activity.total')) + ': ' + ev.total + ' · ' +
        escapeHtml(t('settings.auto.activity.activeLeases')) + ': ' + (ev.active_leases || 0) + ' · ' +
        escapeHtml(t('settings.auto.activity.lastUsed', {
          time: ev.last_used_at ? formatAgo(ev.last_used_at) : t('settings.auto.activity.never'),
        })) + '</p>' +
        '<table class="auto-table"><thead><tr>' +
        ['thTime', 'thCount', 'thScope', 'thLabel', 'thLeased']
          .map(k => '<th>' + escapeHtml(t('settings.auto.activity.' + k)) + '</th>').join('') +
        '</tr></thead><tbody>' +
        (ev.recent || []).map(r =>
          '<tr><td>' + new Date(r.ts * 1000).toLocaleString() + '</td><td>' + r.count +
          '</td><td>' + escapeHtml(r.scope) + '</td><td>' + escapeHtml(r.label || '—') +
          '</td><td>' + (r.leased ? '✓' : '—') + '</td></tr>').join('') +
        '</tbody></table>'
      : '<p class="muted" data-auto="activity-disabled">' +
        escapeHtml(t('settings.auto.activity.disabled')) + '</p>';

    const leases = ev && (ev.lease_rows || []).length
      ? (ev.lease_rows).map(l =>
        '<div class="lease-row"><span class="lease-port">' + l.port + '</span>' +
        '<span class="lease-label">' + escapeHtml(l.label || '—') + '</span>' +
        '<span class="lease-left">' + escapeHtml(t('settings.auto.leases.remaining',
          { time: fmtRemaining(remainingSeconds(l.expires_at)) })) + '</span>' +
        '<button type="button" class="btn-delete" data-release-port="' + l.port + '">' +
        escapeHtml(t('settings.auto.leases.release')) + '</button></div>').join('')
      : '<p class="muted">' + escapeHtml(t('settings.auto.leases.none')) + '</p>';

    return settingsCard('settings.auto.connect.title', 'settings.auto.connect.blurb', connect) +
      settingsCard('settings.auto.status.title', 'settings.auto.status.blurb', statusRows) +
      settingsCard('settings.auto.activity.title', 'settings.auto.activity.blurb', activity) +
      settingsCard('settings.auto.leases.title', 'settings.auto.leases.blurb', leases);
  }

  export async function releaseLease(port, btn) {
    btn.disabled = true;
    try {
      const res = await api('/api/manual-ports/' + port, { method: 'DELETE' });
      if (!res.ok) {
        btn.disabled = false;
        return;
      }
      const metaRes = await api('/api/meta');
      if (metaRes.ok) S.meta = await metaRes.json();
    } catch (err) {
      btn.disabled = false;
      return;
    }
    rerenderAutomationCards();
  }

  function rerenderAutomationCards() {
    const panel = document.getElementById('settings-panel-automation');
    if (!panel || !S.meta) return;
    panel.innerHTML = automationCardsHtml(S.meta.automation || {});
  }

  let _delegated = false;
  function ensureAutomationDelegates() {
    if (_delegated) return;
    _delegated = true;
    document.addEventListener('click', function (e) {
      const copyBtn = e.target.closest('[data-copy]');
      if (copyBtn) {
        const src = document.getElementById(copyBtn.getAttribute('data-copy'));
        if (!src) return;
        navigator.clipboard.writeText(src.textContent.trim()).then(function () {
          copyBtn.textContent = t('settings.auto.connect.copied');
          setTimeout(function () {
            copyBtn.textContent = copyBtn.getAttribute('data-label') ||
              t('settings.auto.connect.copy');
          }, 1200);
        }).catch(function () {});
        return;
      }
      const relBtn = e.target.closest('[data-release-port]');
      if (relBtn) releaseLease(Number(relBtn.getAttribute('data-release-port')), relBtn);
    });
  }

  export function renderSettingsForm(doc) {
    const values = Object.assign({}, doc.values || {});
    if (S.rangeFromView) {
      values.port_range_start = S.rangeStart;
      values.port_range_end = S.rangeEnd;
    }
    const fields = doc.fields || [];
    const host = document.getElementById('settings-fields');
    const lead = document.getElementById('settings-lead');
    const saveBtn = document.getElementById('settings-save');
    const status = document.getElementById('settings-status');
    S.settingsDirty = false;
    status.className = '';
    status.textContent = '';
    lead.textContent = t(doc.readonly ? 'settings.leadReadonly' : 'settings.lead');
    saveBtn.disabled = !!doc.readonly;

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
        return renderField(f, values[f.key], doc.readonly);
      }).join('');
    }

    const appearanceFields = byGroup.appearance || [];
    const lookFields = appearanceFields.filter(function (f) { return !CARD_FIELD_KEYS[f.key]; });
    const cardFields = appearanceFields.filter(function (f) { return CARD_FIELD_KEYS[f.key]; });
    const knownGroups = { appearance: true, grid: true, scanning: true, links: true };
    const extraAdvanced = groupOrder.filter(function (g) { return !knownGroups[g]; }).map(function (g) {
      return settingsCard('settings.groups.' + g + '.title', 'settings.groups.' + g + '.blurb', rowsFor(byGroup[g]));
    }).join('');

    host.innerHTML =
      settingsPanelHtml('appearance',
        settingsCard('settings.groups.appearance.title', 'settings.groups.appearance.blurb', rowsFor(lookFields)) +
        settingsCard('settings.cards.title', 'settings.cards.blurb', rowsFor(cardFields))) +
      settingsPanelHtml('occupancy',
        settingsCard('settings.groups.grid.title', 'settings.groups.grid.blurb', rowsFor(byGroup.grid || [])) +
        settingsCard('hosts.title', 'hosts.blurb', '<div id="settings-peers"></div>')) +
      settingsPanelHtml('automation', automationCardsHtml(S.meta && S.meta.automation ? S.meta.automation : {})) +
      settingsPanelHtml('advanced',
        settingsCard('settings.groups.scanning.title', 'settings.groups.scanning.blurb', rowsFor(byGroup.scanning || [])) +
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
    renderPeersEditor(!!doc.readonly || !!S.hostCatalog.readonly);
  }

  export async function saveSettingsPage() {
    if (S.settingsDoc && S.settingsDoc.readonly) return;
    const status = document.getElementById('settings-status');
    const form = document.getElementById('settings-form');
    const patch = {};
    const fields = S.settingsDoc && S.settingsDoc.fields ? S.settingsDoc.fields : [];
    for (let i = 0; i < fields.length; i++) {
      const f = fields[i];
      const el = form.elements[f.key];
      if (!el || el.disabled) continue;
      if (f.type === 'bool') patch[f.key] = el.checked;
      else if (f.type === 'int') {
        const n = parseInt(el.value, 10);
        if (isNaN(n)) {
          status.className = 'is-error';
          status.textContent = t('settings.mustBeNumber', { label: fieldLabel(f) });
          return;
        }
        patch[f.key] = n;
      } else patch[f.key] = el.value;
    }
    status.className = '';
    status.textContent = t('settings.saving');
    const res = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      status.className = 'is-error';
      status.textContent = errorText(body, res.status);
      return;
    }
    const saved = S.settingsDoc && S.settingsDoc.values ? S.settingsDoc.values : S.settings;
    const rangeEdited = patch.port_range_start !== saved.port_range_start
      || patch.port_range_end !== saved.port_range_end;
    if (rangeEdited) S.rangeFromView = false;
    applyServerSettings(body);
    S.rangeFromView = true;
    saveView();
    if (!(S.hostCatalog && S.hostCatalog.readonly)) {
      const hostRes = await api('/api/hosts', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ peers: peersPayload() }),
      });
      const hostBody = await hostRes.json().catch(function () { return {}; });
      if (!hostRes.ok) {
        renderSettingsForm(body);
        status.className = 'is-error';
        status.textContent = errorText(hostBody, hostRes.status);
        return;
      }
      S.hostCatalog = {
        local: hostBody.local || S.hostCatalog.local,
        peers: Array.isArray(hostBody.peers) ? hostBody.peers : [],
        readonly: !!hostBody.readonly,
      };
      S.peersDraft = (S.hostCatalog.peers || []).map(clonePeerRow);
    }
    renderSettingsForm(body);
    setupRefresh();
    status.className = 'is-ok';
    status.textContent = t('settings.saved');
    S.settingsDirty = false;
  }
