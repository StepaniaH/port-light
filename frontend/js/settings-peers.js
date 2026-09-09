/* Peer editor and payload serialization. */
import { S } from './state.js?v=95';
import { t, escapeHtml } from './text.js?v=95';

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
        (row.has_auth && !row.clear_auth
          ? '<button type="button" class="btn-secondary" data-peer-clear-auth' + disabled + '>' +
            escapeHtml(t('hosts.clearAuth')) + '</button>'
          : '') +
        '<button type="button" class="btn-secondary" data-peer-remove' + disabled + '>' +
        escapeHtml(t('hosts.remove')) + '</button></div></div></details>';
    }).join('');
    const maxPeers = Number(S.hostCatalog.max_peers) || 32;
    const canAdd = !locked && S.peersDraft.length < maxPeers;
    host.innerHTML = '<div class="peer-list">' + rows + '</div>' +
      '<p class="field-help" data-peer-limit>' + escapeHtml(t('hosts.max', { count: maxPeers })) + '</p>' +
      '<p class="field-help" data-i18n="hosts.dockerHint">' + escapeHtml(t('hosts.dockerHint')) + '</p>' +
      '<button type="button" class="btn-secondary" id="peer-add"' + (canAdd ? '' : ' disabled') + '>' +
      escapeHtml(t('hosts.add')) + '</button>';
    syncRefreshCapacity();
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
