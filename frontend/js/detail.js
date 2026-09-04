/* Port detail drawer: desktop side panel, mobile modal, hide/unhide actions. */

import { S, saveView } from './state.js?v=90';
import { t, tx, escapeHtml, safeHref, errorText } from './text.js?v=90';
import { appEl, detailPanel, detailBackdrop, detailContent, unhideBtn, syncHeaderHeight } from './dom.js?v=90';
import { trapTab } from './a11y.js?v=90';
import { api } from './api.js?v=90';
import { portApiUrl, hasPeers, hostName, gridHash, dataForHost } from './hosts.js?v=90';
import { isLease, remainingSeconds, fmtRemaining } from './leases.js?v=90';
import {
  render, syncHiddenButton, getKnownForFree, hiddenOccupancy, buildSearchContext,
  getCellLabel, showCopyToast, applyPendingGridFocus, freeStub, pendingStub,
  portFromList,
} from './grid.js?v=90';
import { closeModals, modalOpen, openModal } from './modal.js?v=90';

let tick;
let loadPorts;
export function configureDetail(actions) {
  tick = actions.refresh;
  loadPorts = actions.loadPorts;
}


  export function setDetailOpen(open) {
    appEl.classList.toggle('detail-open', open);
    document.documentElement.classList.toggle('detail-open', open);
    detailPanel.classList.toggle('hidden', !open);
    detailBackdrop.classList.toggle('hidden', !open);
    syncDetailModal();
  }

  export function syncDetailModal() {
    const open = !detailPanel.classList.contains('hidden');
    const overlay = open && window.matchMedia('(max-width: 900px)').matches;
    if (open) {
      detailPanel.setAttribute('role', 'dialog');
      detailPanel.setAttribute('aria-modal', overlay ? 'true' : 'false');
    } else {
      detailPanel.removeAttribute('role');
      detailPanel.setAttribute('aria-modal', 'false');
    }
    [
      document.querySelector('.skip-link'),
      document.getElementById('app-header'),
      document.getElementById('view-grid'),
      document.getElementById('view-settings'),
    ].forEach(function (el) {
      if (!el) return;
      if (overlay) el.setAttribute('inert', '');
      else el.removeAttribute('inert');
    });
  }

  export function showDetailError(msg) {
    const old = detailContent.querySelector('.detail-error');
    if (old) old.remove();
    const p = document.createElement('p');
    p.className = 'detail-error';
    p.setAttribute('role', 'alert');
    p.textContent = msg;
    const row = detailContent.querySelector('.action-row');
    if (row) detailContent.insertBefore(p, row);
    else detailContent.appendChild(p);
  }

  export function showPortDetail(port, fallback) {
    const hostId = S.selectedHostId || 'local';
    const gen = ++S.portDetailGen;
    const local = portFromList(port, hostId);
    if (local) {
      S.detailShownPort = port;
      renderDetail(local);
      return;
    }
    renderDetail(fallback && fallback.port === port ? fallback : pendingStub(port));
    S.detailShownPort = port;
    const openedHost = hostId;
    api(portApiUrl(hostId, port) + '?include_hidden=true').then(function (res) {
      if (gen !== S.portDetailGen || S.selectedPort !== port || S.selectedHostId !== openedHost) return null;
      if (res.status === 404) {
        const data = dataForHost(openedHost);
        const locked = !!(data && data.summary && data.summary.hidden_locked);
        renderDetail(Object.assign(freeStub(port), {
          _missing: true,
          _locked: locked,
          is_hidden: locked,
        }));
        return null;
      }
      if (!res.ok) {
        S.detailShownPort = null;
        if (res.status === 503) renderDetail({ ...pendingStub(port), _pending: false, _unavailable: true });
        else showDetailError(t('detail.actionFailed'));
        return null;
      }
      return res.json();
    }).then(function (row) {
      if (!row || gen !== S.portDetailGen || S.selectedPort !== port || S.selectedHostId !== openedHost) return;
      if (row.known_service) S.knownCache[port] = row.known_service;
      renderDetail(row);
    }).catch(function () {
      if (gen === S.portDetailGen) S.detailShownPort = null;
    });
  }

  export function renderDetail(p) {
    const opening = detailPanel.classList.contains('hidden');
    setDetailOpen(true);
    const name = getCellLabel(p);
    let html = '<div class="detail-head"><div><h2><button type="button" class="detail-copy-port" data-copy-port="' +
      p.port + '" title="' + escapeHtml(t('detail.copyPort')) + '" aria-label="' +
      escapeHtml(t('detail.copyPort') + ': ' + p.port) + '">' + p.port + '</button></h2>' +
      (name ? '<div class="detail-sub">' + escapeHtml(name) + '</div>' : '') +
      '</div><button type="button" class="close-btn" data-close-detail aria-label="' +
      escapeHtml(t('detail.close')) + '">×</button></div>';

    if (p._pending || p._unavailable || p.status === 'unknown') {
      html += '<p class="modal-hint">' + escapeHtml(t(p._unavailable || p.status === 'unknown' ? 'scanner.snapshotUnavailable' : 'detail.loading')) + '</p>';
    } else {
    const remote = S.selectedHostId && S.selectedHostId !== 'local';
    if (remote) {
      html += '<p class="modal-hint">' + escapeHtml(t('detail.remoteReadOnly', { name: hostName(S.selectedHostId) })) + '</p>';
    }
    html += '<div class="row"><span class="key">' + escapeHtml(t('detail.status')) + '</span><span class="tag ' + p.status + '">' +
      escapeHtml(t('status.' + p.status) || p.status) + '</span></div>';
    if (p._missing) {
      html += '<p class="detail-error" role="alert">' +
        escapeHtml(t(p._locked ? 'detail.hiddenLocked' : 'detail.notFound')) + '</p>';
    }
    html += '<div class="row"><span class="key">' + escapeHtml(t('detail.source')) + '</span><span class="val">' +
      escapeHtml(tx('sourceType', p.source_type || 'unknown')) + '</span></div>';
    if (p.protocol) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.protocol')) + '</span><span class="val">' + escapeHtml(p.protocol) + '</span></div>';
    if (p.ip) {
      html += '<div class="row"><span class="key">' + escapeHtml(t('detail.bind')) + '</span><span class="val">' +
        escapeHtml((p.ips && p.ips.join(', ')) || p.ip) +
        (p.bind_scope ? ' <span class="tag scope">' + escapeHtml(tx('scope', p.bind_scope)) + '</span>' : '') +
        '</span></div>';
    }

    if (p.process) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.process')) + '</span><span class="val">' + escapeHtml(p.process) + '</span></div>';
    if (p.pid) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.pid')) + '</span><span class="val">' + p.pid + '</span></div>';

    if (p.urls && p.urls.length > 0) {
      html += '<div class="section-title">' + escapeHtml(t('detail.open')) + '</div>';
      for (let i = 0; i < p.urls.length; i++) {
        const href = safeHref(p.urls[i]);
        if (!href) continue;
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.url')) + '</span><span class="val"><a class="detail-link" href="' +
          escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(href) + '</a></span></div>';
      }
    }

    if (p.known_service) {
      html += '<div class="info-box"><span class="info-name">' + escapeHtml(p.known_service.name) +
        '</span> — ' + escapeHtml(p.known_service.description) + '</div>';
      if (p.known_service.is_access_port !== undefined) {
        const isAccess = p.known_service.is_access_port;
        html += '<div class="info-box access-box"><span class="info-name">' +
          escapeHtml(t(isAccess ? 'detail.accessPort' : 'detail.internalPort')) + '</span> — ' +
          escapeHtml(t(isAccess ? 'detail.accessHint' : 'detail.internalHint')) +
          '</div>';
      }
    }

    if (p.conflict) {
      const projectCount = Array.from(new Set((p.compose_configs || []).map(function (c) {
        return c.project_dir;
      }))).length;
      html += '<div class="info-box conflict-box"><span class="info-name">' + escapeHtml(t('detail.conflict')) +
        '</span> — ' + escapeHtml(t('detail.conflictHint', { n: projectCount })) + '</div>';
    }

    if (p.bind_scope === 'public' && p.known_service && p.known_service.is_access_port) {
      html += '<div class="info-box conflict-box"><span class="info-name">' +
        escapeHtml(t('detail.publicBind')) + '</span> — ' + escapeHtml(t('detail.publicBindHint')) + '</div>';
    }

    if (p.containers && p.containers.length > 0) {
      html += '<div class="section-title">' + escapeHtml(t('detail.containers')) + '</div>';
      for (let i = 0; i < p.containers.length; i++) {
        const c = p.containers[i];
        const live = c.status === 'running' || c.status === 'paused' || c.status === 'restarting';
        const knownTag = {
          created: 1, dead: 1, removing: 1, exited: 1,
          paused: 1, restarting: 1, running: 1,
        };
        const tag = live ? c.status : (knownTag[c.status] ? c.status : 'exited');
        html += '<div class="row"><span class="key">' + escapeHtml(c.name) + '</span><span class="tag ' + tag + '">' +
          escapeHtml(t('status.' + tag) || c.status) + '</span></div>';
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.image')) + '</span><span class="val">' + escapeHtml(c.image) + '</span></div>';
        if (c.compose_project) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.project')) + '</span><span class="val">' + escapeHtml(c.compose_project) + '</span></div>';
        if (c.compose_service) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.service')) + '</span><span class="val">' + escapeHtml(c.compose_service) + '</span></div>';
        if (c.network_mode) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.network')) + '</span><span class="val">' + escapeHtml(c.network_mode) + '</span></div>';
        if (c.bind_ips && c.bind_ips.length) {
          html += '<div class="row"><span class="key">' + escapeHtml(t('detail.bind')) + '</span><span class="val">' +
            escapeHtml(c.bind_ips.join(', ')) + '</span></div>';
        }
        if (c.protocol && c.protocol !== 'tcp') {
          html += '<div class="row"><span class="key">' + escapeHtml(t('detail.protocol')) + '</span><span class="val">' +
            escapeHtml(c.protocol) + '</span></div>';
        }
        if (c.container_port) {
          html += '<div class="row"><span class="key">' + escapeHtml(t('detail.containerPort')) + '</span><span class="val">' +
            c.container_port + '</span></div>';
        }
      }
    }

    if (p.compose_configs && p.compose_configs.length > 0) {
      html += '<div class="section-title">' + escapeHtml(t('detail.compose')) + '</div>';
      for (let i = 0; i < p.compose_configs.length; i++) {
        const cc = p.compose_configs[i];
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.project')) + '</span><span class="val">' + escapeHtml(cc.project_name || cc.project_dir) + '</span></div>';
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.service')) + '</span><span class="val">' + escapeHtml(cc.service_name) + '</span></div>';
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.file')) + '</span><span class="val">' + escapeHtml(cc.compose_file) + '</span></div>';
        if (cc.host_ip) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.bind')) + '</span><span class="val">' + escapeHtml(cc.host_ip) + '</span></div>';
        if (cc.protocol && cc.protocol !== 'tcp') html += '<div class="row"><span class="key">' + escapeHtml(t('detail.protocol')) + '</span><span class="val">' + escapeHtml(cc.protocol) + '</span></div>';
        if (cc.network_mode) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.network')) + '</span><span class="val">' + escapeHtml(cc.network_mode) + '</span></div>';
        if (cc.container_port) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.containerPort')) + '</span><span class="val">' + cc.container_port + '</span></div>';
      }
    }

    if (p.manual_label) {
      html += '<div class="info-box"><span class="info-name">' + escapeHtml(t('detail.manual')) + '</span> — ' + escapeHtml(p.manual_label) + '</div>';
    }

    if (isLease(p)) {
      const left = fmtRemaining(remainingSeconds(p.expires_at));
      html += '<div class="info-box"><span class="info-name">' +
        escapeHtml(t('detail.expiresIn', { time: left })) + '</span></div>';
    }

    if (p.is_reservation) {
      html += '<p class="modal-hint">' + escapeHtml(t('detail.reservationHint')) + '</p>';
    }

    if (!remote && !p.is_reservation && (p.manual_label != null || p.source_type === 'manual')) {
      html += '<form class="detail-label-form" data-label-form="' + p.port + '"><label><span class="key">' +
        escapeHtml(t('detail.label')) + '</span><input type="text" maxlength="80" value="' +
        escapeHtml(p.manual_label || '') + '" data-label-input></label><button type="submit" class="btn-secondary">' +
        escapeHtml(t('detail.saveLabel')) + '</button></form>';
    }

    if (!remote) {
    html += '<div class="action-row">';
    if (p.is_hidden) {
      html += '<button type="button" class="btn-unhide" data-unhide-port="' + p.port + '">' + escapeHtml(t('detail.unhide')) + '</button>';
    } else {
      html += '<button type="button" class="btn-hide" data-hide-port="' + p.port + '">' + escapeHtml(t('detail.hide')) + '</button>';
    }
    if (!p.is_reservation && (p.manual_label != null || p.source_type === 'manual')) {
      html += '<button type="button" class="btn-delete" data-delete-port="' + p.port + '">' + escapeHtml(t('detail.delete')) + '</button>';
    }
    html += '</div>';
    }
    }

    const prevLabel = detailContent.querySelector('[data-label-input]');
    const prevForm = prevLabel && prevLabel.closest('[data-label-form]');
    const samePort = !!(prevForm && prevForm.getAttribute('data-label-form') === String(p.port));
    const prevDraft = samePort && prevLabel ? prevLabel.value : null;
    const prevStart = samePort && prevLabel ? prevLabel.selectionStart : null;
    const prevEnd = samePort && prevLabel ? prevLabel.selectionEnd : null;
    const labelDirty = samePort && prevDraft != null && prevDraft !== (p.manual_label || '');

    const active = document.activeElement;
    const inDetail = active && detailContent.contains(active);
    let keep = '';
    if (inDetail && active && active.getAttribute) {
      if (active.hasAttribute('data-close-detail')) keep = 'close';
      else if (active.hasAttribute('data-hide-port')) keep = 'hide';
      else if (active.hasAttribute('data-unhide-port')) keep = 'unhide';
      else if (active.hasAttribute('data-delete-port')) keep = 'delete';
      else if (active.hasAttribute('data-copy-port')) keep = 'copy';
      else if (active.hasAttribute('data-label-input')) keep = 'label';
    }

    if (!p._pending && !p._unavailable && p.status !== 'unknown' && !p._missing) html += '<section id="detail-history" hidden></section>';
    detailContent.innerHTML = html;
    if (!p._pending && !p._unavailable && p.status !== 'unknown' && !p._missing) {
      loadPortHistory(p.port, S.selectedHostId || 'local', detailContent.querySelector('#detail-history'));
    }
    const closeBtn = detailContent.querySelector('[data-close-detail]');
    if (closeBtn) closeBtn.addEventListener('click', function () { closeDetail(); });
    const hideBtn = detailContent.querySelector('[data-hide-port]');
    if (hideBtn) hideBtn.addEventListener('click', function () { window._portLightHide(p.port); });
    const showBtn = detailContent.querySelector('[data-unhide-port]');
    if (showBtn) showBtn.addEventListener('click', function () { window._portLightUnhide(p.port); });
    const delBtn = detailContent.querySelector('[data-delete-port]');
    if (delBtn) delBtn.addEventListener('click', function () { window._portLightDeleteManual(p.port); });
    const copyBtn = detailContent.querySelector('[data-copy-port]');
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        navigator.clipboard.writeText(String(p.port)).then(function () {
          showCopyToast(copyBtn);
        }).catch(function () {});
      });
    }
    const labelForm = detailContent.querySelector('[data-label-form]');
    if (labelForm) {
      labelForm.addEventListener('submit', function (e) {
        e.preventDefault();
        const input = labelForm.querySelector('[data-label-input]');
        window._portLightSaveLabel(p.port, input ? input.value : '');
      });
    }
    const labelInput = detailContent.querySelector('[data-label-input]');
    if (labelInput && (keep === 'label' || labelDirty) && prevDraft != null) {
      labelInput.value = prevDraft;
      if (keep === 'label' && prevStart != null) {
        try { labelInput.setSelectionRange(prevStart, prevEnd); } catch (e) {}
      }
    }
    const focusEl = keep === 'close' ? closeBtn
      : keep === 'hide' ? hideBtn
      : keep === 'unhide' ? showBtn
      : keep === 'delete' ? delBtn
      : keep === 'copy' ? copyBtn
      : keep === 'label' ? labelInput
      : closeBtn;
    if (keep && focusEl) focusEl.focus({ preventScroll: true });
    else if (opening && closeBtn && !detailPanel.contains(document.activeElement)) {
      closeBtn.focus({ preventScroll: true });
    }
  }

  export function closeDetail(skipHash) {
    const hostId = S.selectedHostId || S.focusHostId;
    if (S.selectedPort !== null) S.pendingGridFocus = { port: S.selectedPort, hostId: hostId };
    const wasOpen = !detailPanel.classList.contains('hidden') || S.selectedPort !== null;
    setDetailOpen(false);
    S.selectedPort = null;
    S.detailShownPort = null;
    S.portDetailGen += 1;
    if (!skipHash && S.route.name === 'port') {
      location.hash = gridHash(hostId);
      return;
    }
    if (wasOpen && (S.currentData || hasPeers()) && S.route.name !== 'settings') render();
    applyPendingGridFocus();
  }

  export async function unlockHidden() {
    const password = document.getElementById('unhide-password').value;
    if (!password) return;
    const prevUnlock = S.hiddenUnlock;
    const prevShow = S.showHidden;
    S.hiddenUnlock = password;
    S.showHidden = true;
    const data = await loadPorts({ isolated: true });
    if (!data || (data.summary && data.summary.hidden_locked)) {
      S.hiddenUnlock = prevUnlock;
      S.showHidden = prevShow;
      if (!prevUnlock) sessionStorage.removeItem('port-light-hidden-unlock');
      const input = document.getElementById('unhide-password');
      const err = document.getElementById('unhide-error');
      if (input) {
        input.value = '';
        input.setAttribute('aria-invalid', 'true');
        input.focus();
      }
      if (err) {
        err.hidden = false;
        err.classList.remove('hidden');
        err.textContent = t(!data ? 'modal.unlockFailed' : 'modal.wrongPassword');
      }
      return;
    }
    sessionStorage.setItem('port-light-hidden-unlock', password);
    S.showHidden = true;
    S.currentData = data;
    const followup = S.pendingAfterUnlock;
    S.pendingAfterUnlock = null;
    closeModals();
    document.getElementById('unhide-password').value = '';
    syncHiddenButton();
    saveView();
    if (followup) followup();
    else render();
    const focusKey = S.pendingUnlockFocus;
    S.pendingUnlockFocus = 'eye';
    let focusEl = unhideBtn;
    if (focusKey === 'chip') focusEl = document.querySelector('#filters [data-filter="hidden"]');
    else if (focusKey === 'stat') focusEl = document.querySelector('#summary button.stat[data-kind="hidden"]');
    if (focusEl) focusEl.focus({ preventScroll: true });
  }

  async function mutateDetail(url, opts, afterOk) {
    const port = S.selectedPort;
    const hostId = S.selectedHostId;
    try {
      const res = await api(url, opts);
      if (port !== S.selectedPort || hostId !== S.selectedHostId) return;
      if (res.status === 403 && S.meta.hidden_unlock_required) {
        S.pendingAfterUnlock = function () { mutateDetail(url, opts, afterOk); };
        openModal('unhide-modal');
        return;
      }
      if (!res.ok) { showDetailError(t('detail.actionFailed')); return; }
      await afterOk();
    } catch (err) {
      if (port === S.selectedPort && hostId === S.selectedHostId) showDetailError(t('detail.actionFailed'));
    }
  }

  window._portLightHide = function (port) {
    if (S.selectedHostId && S.selectedHostId !== 'local') return;
    return mutateDetail('/api/hidden/' + port, { method: 'POST' }, tick);
  };
  window._portLightUnhide = function (port) {
    if (S.selectedHostId && S.selectedHostId !== 'local') return;
    return mutateDetail('/api/hidden/' + port, { method: 'DELETE' }, tick);
  };
  window._portLightDeleteManual = function (port) {
    if (S.selectedHostId && S.selectedHostId !== 'local') return;
    if (!window.confirm(t('detail.deleteConfirm', { port: port }))) return;
    return mutateDetail('/api/manual-ports/' + port, { method: 'DELETE' }, function () {
      closeDetail();
      tick();
    });
  };
  window._portLightSaveLabel = function (port, label) {
    if (S.selectedHostId && S.selectedHostId !== 'local') return;
    return mutateDetail('/api/manual-ports/' + port, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: String(label || '').trim().slice(0, 80) }),
    }, tick);
  };

  export async function addManualPort() {
    const port = parseInt(document.getElementById('add-port').value, 10);
    const label = document.getElementById('add-label').value.trim();
    const errEl = document.getElementById('add-error');
    function showAddError(msg) {
      if (!errEl) return;
      errEl.hidden = false;
      errEl.classList.remove('hidden');
      errEl.textContent = msg;
    }
    if (!port || port < 1 || port > 65535) {
      showAddError(t('modal.invalidPort'));
      return;
    }

    const res = await api('/api/manual-ports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port: port, label: label, machine: 'localhost' }),
    });
    if (!res.ok) {
      const body = await res.json().catch(function () { return {}; });
      showAddError(errorText(body, res.status) || t('modal.addFailed'));
      return;
    }

    closeModals();
    document.getElementById('add-port').value = '';
    document.getElementById('add-label').value = '';
    tick();
  }



  const HISTORY_STATE_KEYS = {
    used: 'summary.used',
    configured: 'summary.configured',
    free: 'summary.free',
  };

  async function loadPortHistory(port, hostId, host) {
    try {
      const res = await api(portApiUrl(hostId, port) + '/history?hours=24');
      if (!res.ok) return;
      const body = await res.json();
      if (!host || S.selectedPort !== port || S.selectedHostId !== hostId
          || detailContent.querySelector('#detail-history') !== host
          || !body.events || !body.events.length) return;
      const loc = window.PortLightI18n ? PortLightI18n.locale() : undefined;
      const items = body.events.slice(-5).map(function (ev) {
        const time = new Date(ev.ts * 1000).toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' });
        const stateKey = HISTORY_STATE_KEYS[ev.state];
        const stateText = stateKey ? t(stateKey) : ev.state;
        const who = (ev.holders && ev.holders.length) ? ' · ' + ev.holders.join(', ') : '';
        return '<div class="history-event">' + escapeHtml(time + ' — ' + stateText + who) + '</div>';
      }).join('');
      host.innerHTML = '<h3 class="history-title">' + escapeHtml(t('history.title')) + '</h3>' + items;
      host.hidden = false;
    } catch (err) { /* history is best-effort */ }
  }
