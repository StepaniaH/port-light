/* Peer editor and payload serialization. */
import { S } from './state.js?v=121';
import { t, escapeHtml } from './text.js?v=121';
import { api } from './api.js?v=121';

const testRevisions = new WeakMap();

  export function renderPeersEditor(readonly, syncRefreshCapacity = () => {}) {
    const host = document.getElementById('settings-peers');
    if (!host) return;
    const locked = !!readonly;
    const rows = (S.peersDraft || []).map(function (row, i) {
      const disabled = locked ? ' disabled' : '';
      const keep = row.has_auth && !row.clear_auth
        ? ' placeholder="' + escapeHtml(t('hosts.passwordKeep')) + '"'
        : '';
      const open = row.id ? '' : ' open';
      return '<details class="peer-row" data-peer-index="' + i + '" data-peer-id="' +
        escapeHtml(row.id || '') + '" data-has-auth="' + (row.has_auth && !row.clear_auth ? '1' : '0') + '"' + open + '>' +
        '<summary class="peer-row-summary"><span class="peer-summary-name">' +
        escapeHtml(row.name || t('hosts.namePlaceholder')) + '</span><span class="peer-summary-url">' +
        escapeHtml(row.url || t('hosts.urlPlaceholder')) + '</span></summary><div class="peer-row-fields">' +
        '<label><span data-i18n="hosts.name">' + escapeHtml(t('hosts.name')) + '</span>' +
        '<input data-peer-field="name" maxlength="40" value="' + escapeHtml(row.name || '') +
        '" placeholder="' + escapeHtml(t('hosts.namePlaceholder')) + '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.url">' + escapeHtml(t('hosts.url')) + '</span>' +
        '<input data-peer-field="url" value="' + escapeHtml(row.url || '') +
        '" placeholder="' + escapeHtml(t('hosts.urlPlaceholder')) + '"' + disabled + '></label>' +
        '<label class="peer-description-field"><span data-i18n="hosts.description">' +
        escapeHtml(t('hosts.description')) + '</span>' +
        '<input data-peer-field="description" maxlength="120" value="' + escapeHtml(row.description || '') +
        '" placeholder="' + escapeHtml(t('hosts.descriptionPlaceholder')) + '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.username">' + escapeHtml(t('hosts.username')) + '</span>' +
        '<input data-peer-field="username" autocomplete="off" value="' + escapeHtml(row.username || '') +
        '"' + disabled + '></label>' +
        '<label><span data-i18n="hosts.password">' + escapeHtml(t('hosts.password')) + '</span>' +
        '<input type="password" data-peer-field="password" autocomplete="new-password" value="' +
        escapeHtml(row.password || '') + '"' + keep + disabled + '></label>' +
        '<div class="peer-row-actions">' +
        '<button type="button" class="btn-secondary" data-peer-test data-i18n="hosts.test"' + (row.url ? '' : ' disabled') + '>' +
        escapeHtml(t('hosts.test')) + '</button>' +
        (row.has_auth && !row.clear_auth
          ? '<button type="button" class="btn-secondary" data-peer-clear-auth data-i18n="hosts.clearAuth"' + disabled + '>' +
            escapeHtml(t('hosts.clearAuth')) + '</button>'
          : '') +
        '<button type="button" class="btn-secondary" data-peer-remove data-i18n="hosts.remove"' + disabled + '>' +
        escapeHtml(t('hosts.remove')) + '</button></div>' +
        '<p class="peer-test-status action-status" data-peer-test-status role="status" aria-live="polite" hidden></p>' +
        '</div></details>';
    }).join('');
    const maxPeers = Number(S.hostCatalog.max_peers) || 32;
    const canAdd = !locked && S.peersDraft.length < maxPeers;
    host.innerHTML = '<div class="peer-list">' + rows + '</div><div class="peer-editor-footer"><div class="peer-editor-help">' +
      '<p class="field-help" data-peer-limit>' + escapeHtml(t('hosts.max', { count: maxPeers })) + '</p>' +
      '<p class="field-help" data-i18n="hosts.dockerHint">' + escapeHtml(t('hosts.dockerHint')) + '</p>' +
      '</div><button type="button" class="btn-secondary" id="peer-add" data-i18n="hosts.add"' + (canAdd ? '' : ' disabled') + '>' +
      escapeHtml(t('hosts.add')) + '</button></div>';
    host.querySelectorAll('.peer-row').forEach(function (row) {
      row.addEventListener('input', function () {
        testRevisions.set(row, (testRevisions.get(row) || 0) + 1);
        row.querySelector('[data-peer-test-status]').hidden = true;
        const test = row.querySelector('[data-peer-test]');
        test.disabled = !!test.dataset.testing || !row.querySelector('[data-peer-field="url"]').value.trim();
      });
    });
    syncRefreshCapacity();
    if (window.PortLightI18n?.applySampleCopies) window.PortLightI18n.applySampleCopies();
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
        description: ((row.querySelector('[data-peer-field="description"]') || {}).value || ''),
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
    const rows = document.getElementById('settings-peers').querySelectorAll('.peer-row');
    return S.peersDraft.map(function (row, index) {
      const name = String(row.name || '').trim();
      const url = String(row.url || '').trim();
      if (!row.id && name && url) {
        row.id = Array.from(crypto.getRandomValues(new Uint8Array(4)), function (byte) {
          return byte.toString(16).padStart(2, '0');
        }).join('');
        rows[index].setAttribute('data-peer-id', row.id);
      }
      const item = { name: name, url: url };
      if (row.id) item.id = row.id;
      item.description = String(row.description || '').trim();
      if (row.clear_auth) {
        item.username = '';
        item.password = '';
        return item;
      }
      if (row.username) item.username = row.username;
      if (row.password) item.password = row.password;
      return item;
    });
  }

  export function syncSavedPeerRows() {
    document.getElementById('settings-peers').querySelectorAll('.peer-row').forEach(function (row, index) {
      const peer = S.peersDraft[index];
      if (!peer) return;
      row.setAttribute('data-peer-id', peer.id);
      row.setAttribute('data-has-auth', peer.has_auth ? '1' : '0');
      row.querySelector('.peer-summary-name').textContent = peer.name;
      row.querySelector('.peer-summary-url').textContent = peer.url;
      const password = row.querySelector('[data-peer-field="password"]');
      password.setAttribute('placeholder', peer.has_auth ? t('hosts.passwordKeep') : '');
      // A pause while typing can trigger a save; do not interrupt that input.
      if (document.activeElement !== password) {
        password.value = '';
        password.removeAttribute('value');
      }
      const clear = row.querySelector('[data-peer-clear-auth]');
      if (!peer.has_auth && clear) clear.remove();
      if (peer.has_auth && !clear) {
        const button = document.createElement('button');
        button.setAttribute('type', 'button');
        button.className = 'btn-secondary';
        button.setAttribute('data-peer-clear-auth', '');
        button.setAttribute('data-i18n', 'hosts.clearAuth');
        button.textContent = t('hosts.clearAuth');
        row.querySelector('.peer-row-actions').insertBefore(button, row.querySelector('[data-peer-remove]'));
      }
    });
  }

  export async function testPeerConnection(row) {
    const button = row.querySelector('[data-peer-test]');
    if (!button || button.disabled || button.dataset.testing) return;
    const status = row.querySelector('[data-peer-test-status]');
    const value = field => row.querySelector('[data-peer-field="' + field + '"]').value;
    const index = Number(row.getAttribute('data-peer-index'));
    const body = {
      id: row.getAttribute('data-peer-id') || '', url: value('url').trim(),
      username: value('username').trim(), clear_auth: !!S.peersDraft[index]?.clear_auth,
    };
    if (value('password')) body.password = value('password');
    const revision = testRevisions.get(row) || 0;
    button.dataset.testing = '1';
    button.disabled = true;
    button.textContent = t('hosts.testing');
    button.setAttribute('data-i18n', 'hosts.testing');
    status.hidden = false;
    status.className = 'peer-test-status action-status';
    status.textContent = t('hosts.testing');
    let result;
    try {
      const response = await api('/api/hosts/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
      });
      result = await response.json();
      if (!response.ok && !result.code) result = { code: 'peer_unreachable' };
    } catch (error) {
      result = { code: error?.name === 'TimeoutError' ? 'peer_timeout' : 'peer_unreachable' };
    } finally {
      delete body.password;
    }
    if (!row.isConnected) return;
    delete button.dataset.testing;
    button.disabled = !value('url').trim();
    button.textContent = t('hosts.test');
    button.setAttribute('data-i18n', 'hosts.test');
    if ((testRevisions.get(row) || 0) !== revision) return;
    const connected = result?.status === 'connected' && typeof result.version === 'string';
    const errors = {
      peer_auth: 'testAuth', peer_timeout: 'testTimeout', peer_unreachable: 'testUnreachable',
      peer_incompatible: 'testIncompatible', credentials_required: 'testCredentials',
      invalid_url: 'testURL', invalid_input: 'testURL',
    };
    status.className = 'peer-test-status action-status ' + (connected ? 'is-ok' : 'is-error');
    status.textContent = connected ? t('hosts.testConnected', { version: result.version })
      : t('hosts.' + (errors[result?.code] || 'testUnreachable'));
    status.hidden = false;
  }
