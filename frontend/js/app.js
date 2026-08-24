/* Port-Light frontend */

import { S, SETTINGS_PANELS, CARD_FIELD_KEYS, CORE_THEMES } from './state.js?v=57';
import { collate, errorText, escapeHtml, safeHref, t, tx } from './text.js?v=57';
import { KIND_MATCHERS } from './kinds.js?v=57';
import { moveChipFocus, trapTab } from './a11y.js?v=57';
import {
  appEl, grid, hostBoards, hostSwitcher, summary,
  detailPanel, detailBackdrop, detailContent,
  searchInput, rangeStartInput, rangeEndInput,
  sortSelect, unhideBtn, settingsBtn,
  syncHeaderHeight, markRefreshed, setSyncError,
} from './dom.js?v=57';
import { openModal, closeModals, modalOpen } from './modal.js?v=57';
import { applyRoute, parseHash, leaveSettingsOrStay } from './router.js?v=57';
import {
  render, renderSummary, renderHostSwitcher, renderHostBoards, portFromList,
  freeStub, pendingStub, prefetchKnown, getCellLabel, hiddenOccupancy,
  probeLockedHit, buildSearchContext, getKnownForFree, matchesFilter, sortPorts,
  renderGrid, showCopyToast, snapshotGridFocus, syncAddButton, syncFilterUI,
  syncHiddenButton, applyPendingGridFocus, gridRootFrom, moveGridFocus,
} from './grid.js?v=57';
import { bridge } from './bridge.js';
import {
  hooks, hasPeers, listedHosts, hostById, hostName,
  occupancyUrl, portApiUrl, gridHash, portHash, dataForHost,
  api, fetchMeta, fetchHealth, fetchHostHealth, fetchHosts,
  fetchPorts, retryHost, setupRefresh, loadPorts, renderScanners,
} from './api.js?v=57';

(function () {
  'use strict';

  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme) S.settings.theme = cached.theme;
    if (cached.grid_density) S.settings.grid_density = cached.grid_density;
    if (cached.locale) S.settings.locale = cached.locale;
  } catch (e) {}
  try {
    const view = JSON.parse(localStorage.getItem('port-light-view') || '{}');
    if (view.sort) S.sortMode = view.sort;
    if (view.status && view.status !== 'running') S.statusFilter = view.status;
    if (Array.isArray(view.kinds)) {
      S.kindFilters = new Set(view.kinds);
    } else if (Array.isArray(view.filters) && view.filters.length) {
      view.filters.forEach(function (f) {
        if (f === 'all') return;
        if (f === 'used' || f === 'configured') S.statusFilter = f;
        else S.kindFilters.add(f);
      });
    }
    if (typeof view.showHidden === 'boolean') S.showHidden = view.showHidden;
    if (S.kindFilters.has('hidden')) S.showHidden = true;
    if (view.rangeStart >= 1 && view.rangeStart <= 65535) {
      S.rangeStart = view.rangeStart;
      S.rangeFromView = true;
    }
    if (view.rangeEnd >= 1 && view.rangeEnd <= 65535) {
      S.rangeEnd = view.rangeEnd;
      S.rangeFromView = true;
    }
  } catch (e) {}

  function applyTheme() {
    var th = S.settings.theme || 'system';
    if (th === 'system') {
      th = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', th);
  }

  function applyAppearance() {
    applyTheme();
    document.documentElement.setAttribute('data-density', S.settings.grid_density || 'comfortable');
    try {
      localStorage.setItem('port-light-settings', JSON.stringify({
        theme: S.settings.theme,
        grid_density: S.settings.grid_density,
        locale: S.settings.locale || 'auto',
      }));
    } catch (e) {}
  }

  function saveView() {
    try {
      localStorage.setItem('port-light-view', JSON.stringify({
        sort: S.sortMode,
        status: S.statusFilter,
        kinds: Array.from(S.kindFilters),
        showHidden: S.showHidden,
        rangeStart: S.rangeStart,
        rangeEnd: S.rangeEnd,
      }));
    } catch (e) {}
  }





  function syncHeaderHeight() {
    const header = document.getElementById('app-header');
    if (!header) return;
    document.documentElement.style.setProperty('--header-h', header.offsetHeight + 'px');
  }









  function occupancyFocusTarget() {
    if (S.route.name === 'settings') return document.getElementById('settings-form');
    if (hasPeers()) {
      return document.getElementById('host-grid-' + S.focusHostId)
        || document.querySelector('.host-grid')
        || hostSwitcher;
    }
    return grid;
  }











  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if ((S.settings.theme || 'system') === 'system') applyTheme();
    });
  } catch (e) {}
  window.addEventListener('resize', function () {
    syncHeaderHeight();
    syncDetailModal();
  });
  try {
    window.matchMedia('(max-width: 900px)').addEventListener('change', syncDetailModal);
  } catch (e) {}







  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || !S.settingsDirty || S.route.name !== 'settings') return;
    const next = parseHash(a.getAttribute('href'));
    if (next.name === 'settings') return;
    if (!leaveSettingsOrStay()) e.preventDefault();
  }, true);



  window.addEventListener('hashchange', applyRoute);

  const skipLink = document.querySelector('.skip-link');
  if (skipLink) {
    skipLink.addEventListener('click', function (e) {
      e.preventDefault();
      const target = occupancyFocusTarget();
      if (!target) return;
      target.focus();
    });
  }

  summary.addEventListener('click', function (e) {
    const btn = e.target.closest('button.stat');
    if (!btn) return;
    if (btn.dataset.status) {
      const f = btn.dataset.status;
      S.statusFilter = S.statusFilter === f ? 'all' : f;
    } else if (btn.dataset.kind === 'hidden') {
      if (S.kindFilters.has('hidden')) S.kindFilters.delete('hidden');
      else {
        const vis = ensureHiddenVisible('stat');
        if (vis === 'blocked') return;
        S.kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        if (vis === 'ready') render();
        return;
      }
    }
    syncFilterUI();
    saveView();
    render();
  });

  document.getElementById('filters').addEventListener('click', function (e) {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    const f = chip.dataset.filter;
    if (S.kindFilters.has(f)) S.kindFilters.delete(f);
    else {
      if (f === 'hidden') {
        const vis = ensureHiddenVisible('chip');
        if (vis === 'blocked') return;
        S.kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        if (vis === 'ready') render();
        return;
      }
      S.kindFilters.add(f);
    }
    syncFilterUI();
    saveView();
    render();
  });

  document.getElementById('filters').addEventListener('keydown', function (e) {
    if (moveChipFocus(this, e.key)) e.preventDefault();
  });
  summary.addEventListener('keydown', function (e) {
    if (moveChipFocus(this, e.key)) e.preventDefault();
  });

  function ensureHiddenVisible(opener) {
    if (S.showHidden) return 'ready';
    if (S.meta.hidden_unlock_required && !S.hiddenUnlock) {
      S.pendingUnlockFocus = opener || 'eye';
      S.pendingAfterUnlock = function () {
        S.kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        render();
      };
      openModal('unhide-modal');
      return 'blocked';
    }
    S.showHidden = true;
    syncHiddenButton();
    saveView();
    tick();
    return 'loading';
  }

  sortSelect.addEventListener('change', function (e) {
    S.sortMode = e.target.value;
    saveView();
    render();
  });

  searchInput.addEventListener('input', function (e) {
    const val = e.target.value.trim();
    S.searchTerm = val.toLowerCase();
    S.searchPortNum = /^\d+$/.test(val) ? parseInt(val, 10) : null;
    if (S.searchPortNum !== null && (S.searchPortNum < 1 || S.searchPortNum > 65535)) {
      S.searchPortNum = null;
    }
    searchInput.classList.toggle('search-active', !!val);
    render();
  });

  rangeStartInput.addEventListener('change', updateRange);
  rangeEndInput.addEventListener('change', updateRange);
  rangeStartInput.addEventListener('keydown', applyRangeOnEnter);
  rangeEndInput.addEventListener('keydown', applyRangeOnEnter);

  function applyRangeOnEnter(e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    updateRange();
  }

  function updateRange() {
    let s = parseInt(rangeStartInput.value, 10);
    let e = parseInt(rangeEndInput.value, 10);
    if (!(s >= 1 && s <= 65535)) s = S.rangeStart;
    if (!(e >= 1 && e <= 65535)) e = S.rangeEnd;
    if (e < s) {
      const tmp = s;
      s = e;
      e = tmp;
    }
    S.rangeStart = s;
    S.rangeEnd = e;
    rangeStartInput.value = s;
    rangeEndInput.value = e;
    S.rangeFromView = true;
    saveView();
    tick();
  }

  document.getElementById('btn-refresh').addEventListener('click', function () { tick(); });

  function openModal(id) {
    S.focusBack = document.activeElement;
    document.getElementById(id).classList.remove('hidden');
    document.documentElement.classList.add('modal-open');
    const err = document.getElementById('add-error');
    if (id === 'add-modal' && err) {
      err.hidden = true;
      err.classList.add('hidden');
      err.textContent = '';
    }
    const unlockErr = document.getElementById('unhide-error');
    if (id === 'unhide-modal' && unlockErr) {
      unlockErr.hidden = true;
      unlockErr.classList.add('hidden');
      unlockErr.textContent = '';
    }
    const unlockInput = document.getElementById('unhide-password');
    if (id === 'unhide-modal' && unlockInput) {
      unlockInput.removeAttribute('aria-invalid');
    }
    const input = document.getElementById(id).querySelector('input');
    if (input) input.focus();
  }
  function closeModals() {
    document.querySelectorAll('.modal').forEach(function (m) { m.classList.add('hidden'); });
    document.documentElement.classList.remove('modal-open');
    S.pendingAfterUnlock = null;
    if (S.focusBack && typeof S.focusBack.focus === 'function') S.focusBack.focus();
    S.focusBack = null;
  }
  function modalOpen() {
    return !!document.querySelector('.modal:not(.hidden)');
  }

  document.getElementById('btn-add').addEventListener('click', function () {
    if (hasPeers() && S.focusHostId !== 'local') return;
    openModal('add-modal');
  });
  document.getElementById('add-cancel').addEventListener('click', closeModals);
  document.getElementById('add-form').addEventListener('submit', function (e) {
    e.preventDefault();
    addManualPort();
  });

  unhideBtn.addEventListener('click', function () {
    if (S.showHidden) {
      S.showHidden = false;
      S.kindFilters.delete('hidden');
      syncHiddenButton();
      syncFilterUI();
      saveView();
      tick();
      return;
    }
    if (S.meta.hidden_unlock_required && !S.hiddenUnlock) {
      S.pendingUnlockFocus = 'eye';
      openModal('unhide-modal');
      return;
    }
    S.showHidden = true;
    syncHiddenButton();
    saveView();
    tick();
  });
  document.getElementById('unhide-cancel').addEventListener('click', closeModals);
  document.getElementById('unhide-form').addEventListener('submit', function (e) {
    e.preventDefault();
    unlockHidden();
  });
  document.getElementById('unhide-password').addEventListener('input', function () {
    this.removeAttribute('aria-invalid');
    const err = document.getElementById('unhide-error');
    if (!err) return;
    err.hidden = true;
    err.classList.add('hidden');
    err.textContent = '';
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (modalOpen()) { closeModals(); return; }
      if (closeLocaleMenu({ focusTrigger: true })) return;
      if (S.route.name === 'settings') {
        if (!leaveSettingsOrStay()) return;
        location.hash = '#/';
        return;
      }
      if (S.searchTerm || S.searchPortNum !== null) {
        if (!detailPanel.classList.contains('hidden') || S.selectedPort !== null) {
          closeDetail();
          return;
        }
        searchInput.value = '';
        S.searchTerm = '';
        S.searchPortNum = null;
        searchInput.classList.remove('search-active');
        render();
        return;
      }
      closeDetail();
      return;
    }
    if (e.key === 'Tab' && modalOpen()) {
      const modal = document.querySelector('.modal:not(.hidden) .modal-content') || document.querySelector('.modal:not(.hidden)');
      trapTab(e, modal);
      return;
    }
    if (e.key === 'Tab' && !detailPanel.classList.contains('hidden') &&
        window.matchMedia('(max-width: 900px)').matches) {
      trapTab(e, detailPanel);
      return;
    }
    if (e.target && e.target.classList && e.target.classList.contains('locale-trigger') &&
        (e.key === 'ArrowDown' || e.key === 'ArrowUp') &&
        !document.querySelector('.locale-dropdown.is-open')) {
      e.preventDefault();
      e.target.click();
      return;
    }
    if (document.querySelector('.locale-dropdown.is-open')) {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveLocaleHighlight(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); moveLocaleHighlight(-1); return; }
      if (e.key === 'Home') { e.preventDefault(); moveLocaleHighlight('start'); return; }
      if (e.key === 'End') { e.preventDefault(); moveLocaleHighlight('end'); return; }
      if (e.key === 'Tab') { closeLocaleMenu(); return; }
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 's' || e.key === 'S')) {
      if (S.route.name === 'settings' && !(S.settingsDoc && S.settingsDoc.readonly)) {
        e.preventDefault();
        const form = document.getElementById('settings-form');
        if (form) form.requestSubmit();
      }
      return;
    }
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (S.route.name === 'settings' || modalOpen()) return;
    e.preventDefault();
    searchInput.focus();
  });
  window.addEventListener('beforeunload', function (e) {
    if (!S.settingsDirty) return;
    e.preventDefault();
    e.returnValue = '';
  });

  document.querySelectorAll('.modal').forEach(function (m) {
    m.addEventListener('click', function (e) { if (e.target === m) closeModals(); });
  });
  detailBackdrop.addEventListener('click', function () { closeDetail(); });









  document.getElementById('view-grid').addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'ArrowUp' &&
        e.key !== 'ArrowDown' && e.key !== 'Home' && e.key !== 'End' &&
        e.key !== 'PageUp' && e.key !== 'PageDown') return;
    const root = gridRootFrom(e.target);
    if (e.target === root || e.target === grid) {
      const first = root.querySelector('.port-cell');
      if (first) { e.preventDefault(); first.focus(); }
      return;
    }
    if (!e.target || !e.target.classList || !e.target.classList.contains('port-cell')) return;
    e.preventDefault();
    moveGridFocus(e.key, root);
  });

  if (hostSwitcher) {
    hostSwitcher.addEventListener('click', function (e) {
      const btn = e.target.closest('[data-host-switch]');
      if (!btn) return;
      const id = btn.getAttribute('data-host-switch');
      if (!id) return;
      S.focusHostId = id;
      if (S.route.name === 'port' && S.selectedHostId !== id) {
        location.hash = gridHash(id);
        return;
      }
      const want = gridHash(id);
      if ((location.hash || '#/') !== want) location.hash = want;
      else render();
    });
  }
  if (hostBoards) {
    hostBoards.addEventListener('click', function (e) {
      const retry = e.target.closest('[data-host-retry]');
      if (retry) {
        e.preventDefault();
        e.stopPropagation();
        retryHost(retry.getAttribute('data-host-retry'));
        return;
      }
      if (e.target.closest('.port-cell')) return;
      const board = e.target.closest('.host-board');
      if (!board) return;
      const id = board.getAttribute('data-host');
      if (!id || id === S.focusHostId) return;
      S.focusHostId = id;
      if (S.route.name === 'port') {
        render();
        return;
      }
      const want = gridHash(id);
      if ((location.hash || '#/') !== want) location.hash = want;
      else render();
    });
  }

  document.getElementById('settings-form').addEventListener('submit', function (e) {
    e.preventDefault();
    saveSettingsPage();
  });
  document.getElementById('settings-nav').addEventListener('click', function (e) {
    const btn = e.target.closest('[role="tab"][data-settings-panel]');
    if (!btn) return;
    e.preventDefault();
    goSettingsPanel(btn.getAttribute('data-settings-panel'));
  });
  document.getElementById('settings-nav').addEventListener('keydown', function (e) {
    const btn = e.target.closest('[role="tab"][data-settings-panel]');
    if (!btn) return;
    let i = SETTINGS_PANELS.indexOf(btn.getAttribute('data-settings-panel'));
    if (i < 0) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') i = (i + 1) % SETTINGS_PANELS.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') i = (i - 1 + SETTINGS_PANELS.length) % SETTINGS_PANELS.length;
    else if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = SETTINGS_PANELS.length - 1;
    else return;
    e.preventDefault();
    goSettingsPanel(SETTINGS_PANELS[i]);
    const nextBtn = document.getElementById('settings-tab-' + SETTINGS_PANELS[i]);
    if (nextBtn) nextBtn.focus();
  });
  document.getElementById('settings-fields').addEventListener('change', function (e) {
    const field = e.target && e.target.name;
    if (field === 'theme' || field === 'grid_density' || field === 'locale') {
      if (field === 'theme') S.settings.theme = e.target.value;
      if (field === 'grid_density') S.settings.grid_density = e.target.value;
      if (field === 'locale') S.settings.locale = e.target.value;
      applyAppearance();
      if (field === 'locale' && window.PortLightI18n) {
        PortLightI18n.load(S.settings.locale).then(function () {
          PortLightI18n.applyDom();
          syncLocaleTrigger();
          syncHiddenButton();
          if (S.settingsDoc) {
            const lead = document.getElementById('settings-lead');
            lead.textContent = t(S.settingsDoc.readonly ? 'settings.leadReadonly' : 'settings.lead');
          }
          const status = document.getElementById('settings-status');
          if (S.settingsDirty && status && !status.classList.contains('is-error')) {
            status.textContent = t('settings.unsaved');
          }
          if (S.currentData) render();
          syncHeaderHeight();
        });
      }
    }
    markDirty();
    syncDependentSettings();
  });
  document.getElementById('settings-fields').addEventListener('input', markDirty);
  document.getElementById('settings-fields').addEventListener('click', function (e) {
    const add = e.target.closest('#peer-add');
    if (add) {
      e.preventDefault();
      if (S.hostCatalog.readonly || (S.settingsDoc && S.settingsDoc.readonly)) return;
      readPeersDraftFromForm();
      if (S.peersDraft.length >= 6) return;
      S.peersDraft.push({ id: '', name: '', url: '', username: '', password: '', has_auth: false, clear_auth: false });
      renderPeersEditor(false);
      markDirty();
      return;
    }
    const row = e.target.closest('.peer-row');
    if (!row) return;
    if (e.target.closest('[data-peer-remove]')) {
      e.preventDefault();
      readPeersDraftFromForm();
      const i = parseInt(row.getAttribute('data-peer-index'), 10);
      if (!isNaN(i)) S.peersDraft.splice(i, 1);
      renderPeersEditor(!!(S.settingsDoc && S.settingsDoc.readonly) || !!S.hostCatalog.readonly);
      markDirty();
      return;
    }
    if (e.target.closest('[data-peer-clear-auth]')) {
      e.preventDefault();
      readPeersDraftFromForm();
      const i = parseInt(row.getAttribute('data-peer-index'), 10);
      if (!isNaN(i) && S.peersDraft[i]) {
        S.peersDraft[i].clear_auth = true;
        S.peersDraft[i].has_auth = false;
        S.peersDraft[i].username = '';
        S.peersDraft[i].password = '';
      }
      renderPeersEditor(false);
      markDirty();
    }
  });

  function markDirty() {
    if (S.settingsDoc && S.settingsDoc.readonly) return;
    S.settingsDirty = true;
    const status = document.getElementById('settings-status');
    status.className = '';
    status.textContent = t('settings.unsaved');
  }













  async function fetchSettings() {
    const res = await api('/api/settings');
    if (!res.ok) return null;
    return res.json();
  }

  function revertUnsavedSettings() {
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

  function applyServerSettings(doc) {
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


  function markRefreshed() {
    const el = document.getElementById('sync-age');
    if (!el) return;
    const loc = window.PortLightI18n ? PortLightI18n.locale() : undefined;
    const time = new Date().toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    el.hidden = false;
    el.dateTime = new Date().toISOString();
    el.textContent = t('grid.updated', { time: time });
    syncHeaderHeight();
  }

  function setSyncError(on) {
    const el = document.getElementById('sync-error');
    if (!el) return;
    el.hidden = !on;
    el.classList.toggle('hidden', !on);
    if (on) el.textContent = t('grid.refreshFailed');
    syncHeaderHeight();
  }





















  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (S.refreshTimer) { clearInterval(S.refreshTimer); S.refreshTimer = null; }
      return;
    }
    if (S.settings.auto_refresh) setupRefresh();
  });

  function loadSettingsPage() {
    Promise.all([fetchSettings(), fetchHosts()]).then(function (pair) {
      const doc = pair[0];
      if (!doc) return;
      S.settingsDoc = doc;
      S.peersDraft = (S.hostCatalog.peers || []).map(clonePeerRow);
      renderSettingsForm(doc);
    });
  }

  function clonePeerRow(p) {
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

  function fieldLabel(f) {
    return t('settings.fields.' + f.key + '.label');
  }
  function fieldHelp(f) {
    return t('settings.fields.' + f.key + '.help');
  }
  function choiceLabel(c) {
    return t('choice.' + c);
  }

  function settingsCard(titleKey, blurbKey, rowsHtml) {
    return '<section class="settings-card"><header class="settings-card-head"><h2 data-i18n="' + titleKey + '">' +
      escapeHtml(t(titleKey)) + '</h2><p data-i18n="' + blurbKey + '">' +
      escapeHtml(t(blurbKey)) + '</p></header><div class="settings-card-body">' + rowsHtml + '</div></section>';
  }

  function settingsPanelHtml(id, inner) {
    return '<div class="settings-panel" id="settings-panel-' + id + '" role="tabpanel" data-settings-panel="' + id +
      '" aria-labelledby="settings-tab-' + id + '">' + inner + '</div>';
  }

  function showSettingsPanel(id) {
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

  function goSettingsPanel(id) {
    if (SETTINGS_PANELS.indexOf(id) < 0) return;
    showSettingsPanel(id);
    const next = '#/settings/' + id;
    if ((location.hash || '') !== next) location.hash = next;
  }

  function syncDependentSettings() {
    const form = document.getElementById('settings-form');
    if (!form) return;
    const auto = form.elements.auto_refresh;
    const row = form.querySelector('[data-setting="refresh_ms"]');
    if (!auto || !row) return;
    row.classList.toggle('is-inactive', !auto.checked);
  }

  function renderSettingsForm(doc) {
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
    renderPeersEditor(!!doc.readonly || !!S.hostCatalog.readonly);
  }

  function kvRow(labelKey, value, valueKey) {
    const val = valueKey
      ? '<span class="kv-val" data-i18n="' + valueKey + '">' + escapeHtml(String(value == null ? '' : value)) + '</span>'
      : '<span class="kv-val">' + escapeHtml(String(value == null ? '' : value)) + '</span>';
    return '<div class="kv-row"><span class="kv-key" data-i18n="' + labelKey + '">' +
      escapeHtml(t(labelKey)) + '</span>' + val + '</div>';
  }

  function originHint(f) {
    if (!f.origin || f.origin === 'default') return '';
    const key = f.origin === 'file' ? 'settings.origin.saved' : 'settings.origin.env';
    return '<span class="origin-hint" data-i18n="' + key + '" title="' + escapeHtml(f.env) + '">' + escapeHtml(t(key)) + '</span>';
  }

  function localeCopyHtml(c) {
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

  function closeLocaleMenu(opts) {
    const drop = document.querySelector('.locale-dropdown.is-open');
    if (!drop) return false;
    drop.classList.remove('is-open');
    const btn = drop.querySelector('.locale-trigger');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    if (opts && opts.focusTrigger && btn) btn.focus();
    return true;
  }

  function moveLocaleHighlight(delta) {
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

  function syncLocaleTrigger() {
    const drop = document.querySelector('.locale-dropdown');
    if (!drop) return;
    const input = drop.querySelector('input[name="locale"]');
    const dest = drop.querySelector('.locale-trigger .locale-copy');
    if (!input || !dest) return;
    const row = drop.querySelector('.locale-row[data-value="' + input.value + '"] .locale-copy');
    if (row) dest.innerHTML = row.innerHTML;
  }

  document.getElementById('settings-fields').addEventListener('click', function (e) {
    const trigger = e.target.closest('.locale-trigger');
    if (trigger) {
      e.preventDefault();
      const drop = trigger.closest('.locale-dropdown');
      const open = !drop.classList.contains('is-open');
      closeLocaleMenu();
      if (open) {
        drop.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
        const selected = drop.querySelector('.locale-row.is-selected') || drop.querySelector('.locale-row');
        if (selected) selected.focus();
      }
      return;
    }
    const row = e.target.closest('.locale-row');
    if (!row) return;
    e.preventDefault();
    const drop = row.closest('.locale-dropdown');
    const input = drop.querySelector('input[name="locale"]');
    const value = row.getAttribute('data-value');
    if (input.value === value) {
      closeLocaleMenu({ focusTrigger: true });
      return;
    }
    input.value = value;
    drop.querySelectorAll('.locale-row').forEach(function (r) {
      const on = r === row;
      r.classList.toggle('is-selected', on);
      r.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    syncLocaleTrigger();
    closeLocaleMenu({ focusTrigger: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.locale-dropdown')) closeLocaleMenu();
  });

  function renderLocaleList(choices, value, disabled) {
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

  function renderThemePicker(choices, value, disabled) {
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

  function renderField(f, value, readonly) {
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

  function renderPeersEditor(readonly) {
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

  function readPeersDraftFromForm() {
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

  function peersPayload() {
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

  async function saveSettingsPage() {
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

  function syncDetailModal() {
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

  function setDetailOpen(open) {
    appEl.classList.toggle('detail-open', open);
    document.documentElement.classList.toggle('detail-open', open);
    detailPanel.classList.toggle('hidden', !open);
    detailBackdrop.classList.toggle('hidden', !open);
    syncDetailModal();
  }

  hooks.render = render;
















  function showPortDetail(port, fallback) {
    const hostId = S.selectedHostId || 'local';
    const local = portFromList(port, hostId);
    if (local) {
      S.detailShownPort = port;
      renderDetail(local);
      return;
    }
    if (S.detailShownPort === port && S.route.hostId === hostId) return;
    renderDetail(fallback && fallback.port === port ? fallback : pendingStub(port));
    const gen = ++S.portDetailGen;
    S.detailShownPort = port;
    const openedHost = hostId;
    api(portApiUrl(hostId, port) + '?include_hidden=true').then(function (res) {
      if (gen !== S.portDetailGen || S.selectedPort !== port || S.selectedHostId !== openedHost) return null;
      if (res.status === 404) {
        const data = dataForHost(openedHost) || S.currentData;
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
        showDetailError(t('detail.actionFailed'));
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























  function renderDetail(p) {
    const opening = detailPanel.classList.contains('hidden');
    setDetailOpen(true);
    const name = getCellLabel(p);
    let html = '<div class="detail-head"><div><h2><button type="button" class="detail-copy-port" data-copy-port="' +
      p.port + '" title="' + escapeHtml(t('detail.copyPort')) + '" aria-label="' +
      escapeHtml(t('detail.copyPort') + ': ' + p.port) + '">' + p.port + '</button></h2>' +
      (name ? '<div class="detail-sub">' + escapeHtml(name) + '</div>' : '') +
      '</div><button type="button" class="close-btn" data-close-detail aria-label="' +
      escapeHtml(t('detail.close')) + '">×</button></div>';

    if (p._pending) {
      html += '<p class="modal-hint">' + escapeHtml(t('detail.loading')) + '</p>';
    } else {
    const remote = hasPeers() && S.selectedHostId && S.selectedHostId !== 'local';
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

    if (!remote && (p.manual_label != null || p.source_type === 'manual')) {
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
    if (p.manual_label || p.source_type === 'manual') {
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

    detailContent.innerHTML = html;
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

  function closeDetail(skipHash) {
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



  function showDetailError(msg) {
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

  async function mutateDetail(url, opts, afterOk) {
    try {
      const res = await api(url, opts);
      if (!res.ok) { showDetailError(t('detail.actionFailed')); return; }
      afterOk();
    } catch (err) {
      showDetailError(t('detail.actionFailed'));
    }
  }

  window._portLightHide = function (port) {
    if (hasPeers() && S.selectedHostId !== 'local') return;
    mutateDetail('/api/hidden/' + port, { method: 'POST' }, tick);
  };
  window._portLightUnhide = function (port) {
    if (hasPeers() && S.selectedHostId !== 'local') return;
    mutateDetail('/api/hidden/' + port, { method: 'DELETE' }, tick);
  };
  window._portLightDeleteManual = function (port) {
    if (hasPeers() && S.selectedHostId !== 'local') return;
    if (!window.confirm(t('detail.deleteConfirm', { port: port }))) return;
    mutateDetail('/api/manual-ports/' + port, { method: 'DELETE' }, function () {
      closeDetail();
      tick();
    });
  };
  window._portLightSaveLabel = function (port, label) {
    if (hasPeers() && S.selectedHostId !== 'local') return;
    mutateDetail('/api/manual-ports/' + port, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: String(label || '').trim().slice(0, 80) }),
    }, tick);
  };

  async function addManualPort() {
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

  async function unlockHidden() {
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

  function startApp() {
    sortSelect.value = S.sortMode;
    rangeStartInput.value = S.rangeStart;
    rangeEndInput.value = S.rangeEnd;
    syncFilterUI();
    applyAppearance();
    fetchMeta()
      .then(fetchSettings)
      .then(function (doc) {
        if (doc) applyServerSettings(doc);
        return fetchHosts();
      })
      .then(function () {
        return window.PortLightI18n
          ? PortLightI18n.load(S.settings.locale || 'auto')
          : Promise.resolve();
      })
      .then(function () {
        if (window.PortLightI18n) PortLightI18n.applyDom();
        if (S.showHidden && S.meta.hidden_unlock_required && !S.hiddenUnlock) {
          S.showHidden = false;
          S.kindFilters.delete('hidden');
        }
        syncHiddenButton();
        syncFilterUI();
        applyRoute();
        setupRefresh();
        syncHeaderHeight();
      });
  }

  bridge.closeDetail = closeDetail;
  bridge.showPortDetail = showPortDetail;
  bridge.renderDetail = renderDetail;
  bridge.loadSettingsPage = loadSettingsPage;
  bridge.showSettingsPanel = showSettingsPanel;
  bridge.revertUnsavedSettings = revertUnsavedSettings;

  if (window.PortLightI18n) PortLightI18n.load().then(startApp);
  else {
    document.documentElement.setAttribute('data-i18n-ready', '');
    startApp();
  }

})();
