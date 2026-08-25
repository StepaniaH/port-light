/* Port-Light frontend */

import { S, SETTINGS_PANELS, CARD_FIELD_KEYS, CORE_THEMES, applyTheme, applyAppearance, saveView } from './state.js?v=68';
import { collate, errorText, escapeHtml, safeHref, t, tx } from './text.js?v=68';
import { KIND_MATCHERS } from './kinds.js?v=68';
import { moveChipFocus, trapTab } from './a11y.js?v=68';
import {
  appEl, grid, hostBoards, hostSwitcher, summary,
  detailPanel, detailBackdrop, detailContent,
  searchInput, rangeStartInput, rangeEndInput,
  sortSelect, unhideBtn, settingsBtn,
  syncHeaderHeight, markRefreshed, setSyncError,
} from './dom.js?v=68';
import { openModal, closeModals, modalOpen } from './modal.js?v=68';
import { applyRoute, parseHash, leaveSettingsOrStay } from './router.js?v=68';
import {
  render, renderSummary, renderHostSwitcher, renderHostBoards, portFromList,
  freeStub, pendingStub, prefetchKnown, getCellLabel, hiddenOccupancy,
  probeLockedHit, buildSearchContext, getKnownForFree, matchesFilter, sortPorts,
  renderGrid, showCopyToast, snapshotGridFocus, syncAddButton, syncFilterUI,
  syncHiddenButton, applyPendingGridFocus, gridRootFrom, moveGridFocus,
} from './grid.js?v=68';
import {
  hasPeers, listedHosts, hostById, hostName,
  occupancyUrl, portApiUrl, gridHash, portHash, dataForHost,
  api, fetchMeta, fetchHealth, fetchHostHealth, fetchHosts,
  fetchPorts, retryHost, setupRefresh, loadPorts, renderScanners, tick,
  startEventStream,
} from './api.js?v=68';
import {
  closeDetail, showPortDetail, showDetailError, syncDetailModal, unlockHidden,
  addManualPort,
} from './detail.js?v=68';
import {
  loadSettingsPage, showSettingsPanel, goSettingsPanel, saveSettingsPage,
  applyServerSettings, revertUnsavedSettings, markDirty, syncDependentSettings,
  fetchSettings, syncLocaleTrigger, closeLocaleMenu, moveLocaleHighlight,
  renderPeersEditor, readPeersDraftFromForm, syncPaletteAvailability,
} from './settings.js?v=68';

(function () {
  'use strict';

  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme_mode) S.settings.theme_mode = cached.theme_mode;
    if (typeof cached.theme_palette === 'string') S.settings.theme_palette = cached.theme_palette;
    if (cached.grid_density) S.settings.grid_density = cached.grid_density;
    if (Number.isFinite(cached.card_scale)) S.settings.card_scale = cached.card_scale;
    if (Number.isFinite(cached.text_scale)) S.settings.text_scale = cached.text_scale;
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
      if ((S.settings.theme_mode || 'system') === 'system') {
        applyTheme();
        syncPaletteAvailability();
      }
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

  document.getElementById('btn-free').addEventListener('click', function () {
    if (hasPeers() && S.focusHostId !== 'local') return;
    openModal('free-modal');
  });
  document.getElementById('free-cancel').addEventListener('click', closeModals);
  document.getElementById('free-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    const countInput = document.getElementById('free-count');
    const count = Math.min(64, Math.max(1, parseInt(countInput.value, 10) || 1));
    const resultsEl = document.getElementById('free-results');
    const errEl = document.getElementById('free-error');
    errEl.hidden = true; errEl.classList.add('hidden');
    resultsEl.hidden = true; resultsEl.innerHTML = '';
    try {
      const res = await api('/api/free-runs?count=' + count +
        '&start=' + S.rangeStart + '&end=' + S.rangeEnd);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const body = await res.json();
      if (!body.runs || !body.runs.length) {
        resultsEl.hidden = false;
        resultsEl.textContent = t('planner.none');
        return;
      }
      resultsEl.hidden = false;
      resultsEl.innerHTML = body.runs.map(function (r) {
        return '<div class="free-run">' +
          '<span>' + escapeHtml(t('planner.run', { start: r.start, end: r.end, size: r.size })) + '</span>' +
          '<button type="button" class="btn-secondary" data-reserve="' + r.start + ':' + r.end + '">' +
          escapeHtml(t('planner.reserve')) + '</button></div>';
      }).join('');
    } catch (err) {
      errEl.hidden = false; errEl.classList.remove('hidden');
      errEl.textContent = errorText({}, 0) ;
      console.error('free-runs error:', err);
    }
  });
  document.getElementById('free-results').addEventListener('click', async function (e) {
    const btn = e.target.closest('[data-reserve]');
    if (!btn) return;
    const parts = btn.getAttribute('data-reserve').split(':');
    const startR = parseInt(parts[0], 10), endR = parseInt(parts[1], 10);
    const want = Math.min(64, Math.max(1, parseInt(document.getElementById('free-count').value, 10) || 1));
    const lastR = Math.min(endR, startR + want - 1);
    const label = document.getElementById('free-label').value.trim();
    btn.disabled = true;
    let ok = 0;
    for (let port = startR; port <= lastR; port++) {
      try {
        const res = await api('/api/manual-ports', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ port: port, label: label }),
        });
        if (res.ok) ok++;
      } catch (err) {}
    }
    closeModals();
    tick();
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
    if (field === 'theme_mode' || field === 'theme_palette' || field === 'grid_density' || field === 'locale') {
      if (field === 'theme_mode') S.settings.theme_mode = e.target.value;
      if (field === 'theme_palette') S.settings.theme_palette = e.target.value;
      if (field === 'grid_density') S.settings.grid_density = e.target.value;
      if (field === 'locale') S.settings.locale = e.target.value;
      applyAppearance();
      if (field === 'theme_mode') syncPaletteAvailability();
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



































































  async function mutateDetail(url, opts, afterOk) {
    try {
      const res = await api(url, opts);
      if (!res.ok) { showDetailError(t('detail.actionFailed')); return; }
      afterOk();
    } catch (err) {
      showDetailError(t('detail.actionFailed'));
    }
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
        startEventStream();
        setupRefresh();
        syncHeaderHeight();
      });
  }


  if (window.PortLightI18n) PortLightI18n.load().then(startApp);
  else {
    document.documentElement.setAttribute('data-i18n-ready', '');
    startApp();
  }

})();
