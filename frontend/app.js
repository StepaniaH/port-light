/* Port-Light frontend */

(function () {
  'use strict';

  function t(key, vars) {
    return window.PortLightI18n ? window.PortLightI18n.t(key, vars) : key;
  }

  function tx(prefix, value) {
    if (!value) return '';
    const key = prefix + '.' + value;
    const out = t(key);
    return out === key ? value : out;
  }

  function collate(a, b) {
    const loc = window.PortLightI18n ? PortLightI18n.locale() : undefined;
    try {
      return String(a || '').localeCompare(String(b || ''), loc, { numeric: true, sensitivity: 'base' });
    } catch (err) {
      return String(a || '').localeCompare(String(b || ''));
    }
  }

  function errorText(body, status) {
    const detail = body && body.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map(function (item) {
        if (typeof item === 'string') return item;
        if (item && item.msg) return item.msg;
        return '';
      }).filter(Boolean);
      if (parts.length) return parts.join('; ');
    }
    return t('error.httpStatus', { status: status });
  }

  let currentData = null;
  let hostCatalog = { local: { id: 'local', name: '', local: true }, peers: [], readonly: false };
  let hostMaps = {};
  let focusHostId = 'local';
  let selectedHostId = 'local';
  const hostAborts = {};
  const hostEtags = {};
  const hostRetrying = {};
  let peersDraft = [];
  let statusFilter = 'all';
  let kindFilters = new Set();
  let sortMode = 'port-asc';
  let searchTerm = '';
  let searchPortNum = null;
  let selectedPort = null;
  let rangeStart = 1;
  let rangeEnd = 9999;
  let rangeFromView = false;
  let showHidden = false;
  let settings = {
    locale: 'auto',
    theme: 'system',
    grid_density: 'comfortable',
    show_status_text: false,
    show_access_badge: true,
    show_protocol_badge: true,
    copy_on_click: true,
    auto_refresh: true,
    refresh_ms: 5000,
    port_range_start: 1,
    port_range_end: 9999,
    guess_urls: true,
    url_host: '',
    url_scheme: 'auto',
  };
  let settingsDoc = null;
  let settingsDirty = false;
  let settingsPanel = 'appearance';
  const SETTINGS_PANELS = ['appearance', 'occupancy', 'advanced'];
  const CARD_FIELD_KEYS = {
    show_status_text: true,
    show_access_badge: true,
    show_protocol_badge: true,
  };
  const CORE_THEMES = ['system', 'dark', 'light'];
  let refreshTimer = null;
  let meta = { hidden_unlock_required: false, hidden_ports_withheld: false, version: '', settings_readonly: false };
  let hiddenUnlock = sessionStorage.getItem('port-light-hidden-unlock') || '';
  let route = { name: 'grid' };
  let pendingAfterUnlock = null;
  let pendingUnlockFocus = 'eye';
  let focusBack = null;
  let pendingGridFocus = null;
  let occupancyKey = '';
  let portsEtag = '';
  let portsEtagUrl = '';
  let portDetailGen = 0;
  let detailShownPort = null;
  const knownCache = {};
  const knownInflight = {};
  let knownRenderFrame = 0;
  const lockedHitCache = {};
  const lockedHitInflight = {};
  let lastHiddenLocked = null;

  const appEl = document.getElementById('app');
  const grid = document.getElementById('grid');
  const hostBoards = document.getElementById('host-boards');
  const hostSwitcher = document.getElementById('host-switcher');
  const summary = document.getElementById('summary');
  const detailPanel = document.getElementById('detail-panel');
  const detailBackdrop = document.getElementById('detail-backdrop');
  const detailContent = document.getElementById('detail-content');
  const searchInput = document.getElementById('search');
  const rangeStartInput = document.getElementById('range-start');
  const rangeEndInput = document.getElementById('range-end');
  const sortSelect = document.getElementById('sort-select');
  const unhideBtn = document.getElementById('btn-unhide');
  const settingsBtn = document.getElementById('btn-settings');

  const KIND_MATCHERS = {
    running: function (p) {
      return p.containers && p.containers.some(function (c) {
        return c.status === 'running' || c.status === 'paused' || c.status === 'restarting';
      });
    },
    system: function (p) {
      return p.source_type === 'system' || (p.known_service && p.known_service.category === 'system');
    },
    docker: function (p) {
      return p.source_type === 'docker' || (p.containers && p.containers.length > 0);
    },
    access: function (p) {
      return p.known_service && p.known_service.is_access_port;
    },
    udp: function (p) {
      return (p.protocol || '').indexOf('udp') !== -1;
    },
    localhost: function (p) {
      return p.bind_scope === 'localhost';
    },
    public: function (p) {
      return p.bind_scope === 'public';
    },
    hidden: function (p) {
      return !!p.is_hidden;
    },
  };

  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme) settings.theme = cached.theme;
    if (cached.grid_density) settings.grid_density = cached.grid_density;
    if (cached.locale) settings.locale = cached.locale;
  } catch (e) {}
  try {
    const view = JSON.parse(localStorage.getItem('port-light-view') || '{}');
    if (view.sort) sortMode = view.sort;
    if (view.status && view.status !== 'running') statusFilter = view.status;
    if (Array.isArray(view.kinds)) {
      kindFilters = new Set(view.kinds);
    } else if (Array.isArray(view.filters) && view.filters.length) {
      view.filters.forEach(function (f) {
        if (f === 'all') return;
        if (f === 'used' || f === 'configured') statusFilter = f;
        else kindFilters.add(f);
      });
    }
    if (typeof view.showHidden === 'boolean') showHidden = view.showHidden;
    if (kindFilters.has('hidden')) showHidden = true;
    if (view.rangeStart >= 1 && view.rangeStart <= 65535) {
      rangeStart = view.rangeStart;
      rangeFromView = true;
    }
    if (view.rangeEnd >= 1 && view.rangeEnd <= 65535) {
      rangeEnd = view.rangeEnd;
      rangeFromView = true;
    }
  } catch (e) {}

  function applyTheme() {
    var th = settings.theme || 'system';
    if (th === 'system') {
      th = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', th);
  }

  function applyAppearance() {
    applyTheme();
    document.documentElement.setAttribute('data-density', settings.grid_density || 'comfortable');
    try {
      localStorage.setItem('port-light-settings', JSON.stringify({
        theme: settings.theme,
        grid_density: settings.grid_density,
        locale: settings.locale || 'auto',
      }));
    } catch (e) {}
  }

  function saveView() {
    try {
      localStorage.setItem('port-light-view', JSON.stringify({
        sort: sortMode,
        status: statusFilter,
        kinds: Array.from(kindFilters),
        showHidden: showHidden,
        rangeStart: rangeStart,
        rangeEnd: rangeEnd,
      }));
    } catch (e) {}
  }

  function syncFilterUI() {
    document.querySelectorAll('#filters .chip').forEach(function (c) {
      const on = kindFilters.has(c.dataset.filter);
      c.classList.toggle('active', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function syncHiddenButton() {
    unhideBtn.classList.toggle('active', showHidden);
    unhideBtn.setAttribute('aria-pressed', showHidden ? 'true' : 'false');
    const title = t(showHidden ? 'action.hiddenVisible' : 'action.showHidden');
    unhideBtn.title = title;
    unhideBtn.setAttribute('aria-label', title);
  }

  function syncHeaderHeight() {
    const header = document.getElementById('app-header');
    if (!header) return;
    document.documentElement.style.setProperty('--header-h', header.offsetHeight + 'px');
  }

  function hasPeers() {
    return !!(hostCatalog.peers && hostCatalog.peers.length);
  }

  function listedHosts() {
    const local = hostCatalog.local || { id: 'local', name: '', local: true };
    return [local].concat(hostCatalog.peers || []);
  }

  function hostById(id) {
    const hosts = listedHosts();
    for (let i = 0; i < hosts.length; i++) {
      if (hosts[i].id === id) return hosts[i];
    }
    return null;
  }

  function hostName(id) {
    const row = hostById(id);
    return (row && row.name) || id || t('hosts.thisMachine');
  }

  function occupancyFocusTarget() {
    if (route.name === 'settings') return document.getElementById('settings-form');
    if (hasPeers()) {
      return document.getElementById('host-grid-' + focusHostId)
        || document.querySelector('.host-grid')
        || hostSwitcher;
    }
    return grid;
  }

  function occupancyUrl(hostId) {
    const q = 'range_start=' + rangeStart + '&range_end=' + rangeEnd + '&include_hidden=' + showHidden;
    if (!hasPeers() && hostId === 'local') return '/api/ports?' + q;
    return '/api/hosts/' + encodeURIComponent(hostId) + '/ports?' + q;
  }

  function portApiUrl(hostId, port) {
    if (!hasPeers() && hostId === 'local') return '/api/ports/' + port;
    return '/api/hosts/' + encodeURIComponent(hostId || 'local') + '/ports/' + port;
  }

  function gridHash(hostId) {
    hostId = hostId || focusHostId;
    if (!hasPeers() || hostId === 'local') return '#/';
    return '#/h/' + hostId;
  }

  function portHash(hostId, port) {
    hostId = hostId || selectedHostId || focusHostId;
    if (!hasPeers() || hostId === 'local') return '#/port/' + port;
    return '#/h/' + hostId + '/port/' + port;
  }

  function dataForHost(hostId) {
    if (!hasPeers() || hostId === 'local') {
      if (hostMaps.local && hostMaps.local.data) return hostMaps.local.data;
      return currentData;
    }
    return hostMaps[hostId] && hostMaps[hostId].data;
  }

  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if ((settings.theme || 'system') === 'system') applyTheme();
    });
  } catch (e) {}
  window.addEventListener('resize', function () {
    syncHeaderHeight();
    syncDetailModal();
  });
  try {
    window.matchMedia('(max-width: 900px)').addEventListener('change', syncDetailModal);
  } catch (e) {}

  function parseHash(hash) {
    const raw = String(hash || '#/').replace(/^#\/?/, '');
    const parts = raw.split('/').filter(Boolean);
    if (parts[0] === 'settings') {
      let section = parts[1];
      if (SETTINGS_PANELS.indexOf(section) < 0) {
        section = route.name === 'settings' && settingsPanel ? settingsPanel : 'appearance';
      }
      return { name: 'settings', section: section };
    }
    let hostId = 'local';
    let rest = parts;
    if (parts[0] === 'h' && parts[1]) {
      hostId = parts[1];
      rest = parts.slice(2);
      if (hostId !== 'local' && !/^[a-z0-9]{8,16}$/.test(hostId)) hostId = 'local';
    }
    if (rest[0] === 'port' && /^\d+$/.test(rest[1] || '')) {
      const n = parseInt(rest[1], 10);
      if (n >= 1 && n <= 65535) return { name: 'port', port: n, hostId: hostId };
    }
    return { name: 'grid', hostId: hostId };
  }

  function parseRoute() {
    return parseHash(location.hash);
  }

  function leaveSettingsOrStay() {
    if (!settingsDirty || route.name !== 'settings') return true;
    if (!window.confirm(t('settings.discard'))) return false;
    revertUnsavedSettings();
    return true;
  }

  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || !settingsDirty || route.name !== 'settings') return;
    const next = parseHash(a.getAttribute('href'));
    if (next.name === 'settings') return;
    if (!leaveSettingsOrStay()) e.preventDefault();
  }, true);

  function applyRoute() {
    const next = parseRoute();
    if (settingsDirty && route.name === 'settings' && next.name !== 'settings') {
      if (!leaveSettingsOrStay()) {
        const stay = '#/settings/' + settingsPanel;
        if ((location.hash || '') !== stay) location.hash = stay;
        return;
      }
    }
    const prev = route.name;
    route = next;
    const onSettings = route.name === 'settings';
    document.getElementById('view-grid').classList.toggle('hidden', onSettings);
    document.getElementById('view-settings').classList.toggle('hidden', !onSettings);
    appEl.classList.toggle('page-settings', onSettings);
    settingsBtn.classList.toggle('active', onSettings);
    settingsBtn.setAttribute('aria-current', onSettings ? 'page' : 'false');
    syncHeaderHeight();
    if (onSettings) {
      pendingGridFocus = null;
      closeDetail(true);
      settingsPanel = route.section || 'appearance';
      const want = '#/settings/' + settingsPanel;
      if ((location.hash || '') !== want) history.replaceState(null, '', want);
      if (prev === 'settings') {
        showSettingsPanel(settingsPanel);
        return;
      }
      loadSettingsPage();
      return;
    }
    if (prev === 'settings') tick();
    if (route.hostId && hostById(route.hostId)) focusHostId = route.hostId;
    else if (route.name !== 'settings') focusHostId = 'local';
    if (route.name === 'port') {
      selectedPort = route.port;
      selectedHostId = route.hostId || 'local';
      focusHostId = selectedHostId;
      if (currentData || hasPeers()) render();
      else showPortDetail(route.port);
      return;
    }
    closeDetail(true);
    if (currentData || hasPeers()) render();
    applyPendingGridFocus();
  }

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
      statusFilter = statusFilter === f ? 'all' : f;
    } else if (btn.dataset.kind === 'hidden') {
      if (kindFilters.has('hidden')) kindFilters.delete('hidden');
      else {
        const vis = ensureHiddenVisible('stat');
        if (vis === 'blocked') return;
        kindFilters.add('hidden');
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
    if (kindFilters.has(f)) kindFilters.delete(f);
    else {
      if (f === 'hidden') {
        const vis = ensureHiddenVisible('chip');
        if (vis === 'blocked') return;
        kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        if (vis === 'ready') render();
        return;
      }
      kindFilters.add(f);
    }
    syncFilterUI();
    saveView();
    render();
  });

  function moveChipFocus(container, key) {
    const chips = Array.prototype.slice.call(container.querySelectorAll('button'));
    const idx = chips.indexOf(document.activeElement);
    if (idx < 0) return false;
    let next = idx;
    if (key === 'ArrowRight' || key === 'ArrowDown') next = Math.min(chips.length - 1, idx + 1);
    else if (key === 'ArrowLeft' || key === 'ArrowUp') next = Math.max(0, idx - 1);
    else if (key === 'Home') next = 0;
    else if (key === 'End') next = chips.length - 1;
    else return false;
    if (chips[next]) chips[next].focus();
    return true;
  }

  document.getElementById('filters').addEventListener('keydown', function (e) {
    if (moveChipFocus(this, e.key)) e.preventDefault();
  });
  summary.addEventListener('keydown', function (e) {
    if (moveChipFocus(this, e.key)) e.preventDefault();
  });

  function ensureHiddenVisible(opener) {
    if (showHidden) return 'ready';
    if (meta.hidden_unlock_required && !hiddenUnlock) {
      pendingUnlockFocus = opener || 'eye';
      pendingAfterUnlock = function () {
        kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        render();
      };
      openModal('unhide-modal');
      return 'blocked';
    }
    showHidden = true;
    syncHiddenButton();
    saveView();
    tick();
    return 'loading';
  }

  sortSelect.addEventListener('change', function (e) {
    sortMode = e.target.value;
    saveView();
    render();
  });

  searchInput.addEventListener('input', function (e) {
    const val = e.target.value.trim();
    searchTerm = val.toLowerCase();
    searchPortNum = /^\d+$/.test(val) ? parseInt(val, 10) : null;
    if (searchPortNum !== null && (searchPortNum < 1 || searchPortNum > 65535)) {
      searchPortNum = null;
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
    if (!(s >= 1 && s <= 65535)) s = rangeStart;
    if (!(e >= 1 && e <= 65535)) e = rangeEnd;
    if (e < s) {
      const tmp = s;
      s = e;
      e = tmp;
    }
    rangeStart = s;
    rangeEnd = e;
    rangeStartInput.value = s;
    rangeEndInput.value = e;
    rangeFromView = true;
    saveView();
    tick();
  }

  document.getElementById('btn-refresh').addEventListener('click', function () { tick(); });

  function openModal(id) {
    focusBack = document.activeElement;
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
    pendingAfterUnlock = null;
    if (focusBack && typeof focusBack.focus === 'function') focusBack.focus();
    focusBack = null;
  }
  function modalOpen() {
    return !!document.querySelector('.modal:not(.hidden)');
  }

  document.getElementById('btn-add').addEventListener('click', function () {
    if (hasPeers() && focusHostId !== 'local') return;
    openModal('add-modal');
  });
  document.getElementById('add-cancel').addEventListener('click', closeModals);
  document.getElementById('add-form').addEventListener('submit', function (e) {
    e.preventDefault();
    addManualPort();
  });

  unhideBtn.addEventListener('click', function () {
    if (showHidden) {
      showHidden = false;
      kindFilters.delete('hidden');
      syncHiddenButton();
      syncFilterUI();
      saveView();
      tick();
      return;
    }
    if (meta.hidden_unlock_required && !hiddenUnlock) {
      pendingUnlockFocus = 'eye';
      openModal('unhide-modal');
      return;
    }
    showHidden = true;
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
      if (route.name === 'settings') {
        if (!leaveSettingsOrStay()) return;
        location.hash = '#/';
        return;
      }
      if (searchTerm || searchPortNum !== null) {
        if (!detailPanel.classList.contains('hidden') || selectedPort !== null) {
          closeDetail();
          return;
        }
        searchInput.value = '';
        searchTerm = '';
        searchPortNum = null;
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
      if (route.name === 'settings' && !(settingsDoc && settingsDoc.readonly)) {
        e.preventDefault();
        const form = document.getElementById('settings-form');
        if (form) form.requestSubmit();
      }
      return;
    }
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (route.name === 'settings' || modalOpen()) return;
    e.preventDefault();
    searchInput.focus();
  });
  window.addEventListener('beforeunload', function (e) {
    if (!settingsDirty) return;
    e.preventDefault();
    e.returnValue = '';
  });

  document.querySelectorAll('.modal').forEach(function (m) {
    m.addEventListener('click', function (e) { if (e.target === m) closeModals(); });
  });
  detailBackdrop.addEventListener('click', function () { closeDetail(); });

  function gridRootFrom(el) {
    if (!el || !el.closest) return grid;
    return el.closest('.host-grid, #grid') || grid;
  }

  function gridCells(root) {
    root = root || gridRootFrom(document.activeElement);
    return Array.prototype.slice.call(root.querySelectorAll('.port-cell'));
  }

  function cellsByRow(cells) {
    const rows = [];
    let lastTop = null;
    let row = [];
    for (let i = 0; i < cells.length; i++) {
      const top = Math.round(cells[i].getBoundingClientRect().top);
      if (lastTop === null || Math.abs(top - lastTop) > 16) {
        row = [];
        rows.push(row);
        lastTop = top;
      }
      row.push(cells[i]);
    }
    return rows;
  }

  function moveGridFocus(key, root) {
    const cells = gridCells(root);
    if (!cells.length) return;
    const current = document.activeElement;
    const idx = cells.indexOf(current);
    if (idx < 0) return;
    let next = null;
    if (key === 'ArrowLeft') next = cells[Math.max(0, idx - 1)];
    else if (key === 'ArrowRight') next = cells[Math.min(cells.length - 1, idx + 1)];
    else if (key === 'Home') next = cells[0];
    else if (key === 'End') next = cells[cells.length - 1];
    else if (key === 'ArrowUp' || key === 'ArrowDown' || key === 'PageUp' || key === 'PageDown') {
      const rows = cellsByRow(cells);
      let foundR = -1;
      let foundC = -1;
      for (let r = 0; r < rows.length; r++) {
        const c = rows[r].indexOf(current);
        if (c >= 0) { foundR = r; foundC = c; break; }
      }
      if (foundR < 0) return;
      const page = Math.max(1, Math.min(8, rows.length - 1));
      const delta = (key === 'PageUp' || key === 'PageDown') ? page : 1;
      let destR = (key === 'ArrowDown' || key === 'PageDown') ? foundR + delta : foundR - delta;
      if (key === 'PageUp' || key === 'PageDown') {
        destR = Math.max(0, Math.min(rows.length - 1, destR));
      }
      if (destR < 0 || destR >= rows.length || destR === foundR) return;
      const destRow = rows[destR];
      const from = current.getBoundingClientRect();
      const fromMid = (from.left + from.right) / 2;
      next = destRow[Math.min(foundC, destRow.length - 1)];
      let best = Infinity;
      for (let c = 0; c < destRow.length; c++) {
        const box = destRow[c].getBoundingClientRect();
        const d = Math.abs((box.left + box.right) / 2 - fromMid);
        if (d < best) { best = d; next = destRow[c]; }
      }
    }
    if (next && next !== current) next.focus();
  }

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
      focusHostId = id;
      if (route.name === 'port' && selectedHostId !== id) {
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
      if (!id || id === focusHostId) return;
      focusHostId = id;
      if (route.name === 'port') {
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
      if (field === 'theme') settings.theme = e.target.value;
      if (field === 'grid_density') settings.grid_density = e.target.value;
      if (field === 'locale') settings.locale = e.target.value;
      applyAppearance();
      if (field === 'locale' && window.PortLightI18n) {
        PortLightI18n.load(settings.locale).then(function () {
          PortLightI18n.applyDom();
          syncLocaleTrigger();
          syncHiddenButton();
          if (settingsDoc) {
            const lead = document.getElementById('settings-lead');
            lead.textContent = t(settingsDoc.readonly ? 'settings.leadReadonly' : 'settings.lead');
          }
          const status = document.getElementById('settings-status');
          if (settingsDirty && status && !status.classList.contains('is-error')) {
            status.textContent = t('settings.unsaved');
          }
          if (currentData) render();
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
      if (hostCatalog.readonly || (settingsDoc && settingsDoc.readonly)) return;
      readPeersDraftFromForm();
      if (peersDraft.length >= 6) return;
      peersDraft.push({ id: '', name: '', url: '', username: '', password: '', has_auth: false, clear_auth: false });
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
      if (!isNaN(i)) peersDraft.splice(i, 1);
      renderPeersEditor(!!(settingsDoc && settingsDoc.readonly) || !!hostCatalog.readonly);
      markDirty();
      return;
    }
    if (e.target.closest('[data-peer-clear-auth]')) {
      e.preventDefault();
      readPeersDraftFromForm();
      const i = parseInt(row.getAttribute('data-peer-index'), 10);
      if (!isNaN(i) && peersDraft[i]) {
        peersDraft[i].clear_auth = true;
        peersDraft[i].has_auth = false;
        peersDraft[i].username = '';
        peersDraft[i].password = '';
      }
      renderPeersEditor(false);
      markDirty();
    }
  });

  function markDirty() {
    if (settingsDoc && settingsDoc.readonly) return;
    settingsDirty = true;
    const status = document.getElementById('settings-status');
    status.className = '';
    status.textContent = t('settings.unsaved');
  }

  function apiHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (hiddenUnlock) headers['X-Hidden-Unlock'] = hiddenUnlock;
    return headers;
  }

  async function api(url, opts) {
    opts = opts || {};
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts, {
      headers: apiHeaders(opts.headers),
    }));
    return res;
  }

  async function fetchMeta() {
    try {
      const res = await api('/api/meta');
      if (res.ok) meta = await res.json();
    } catch (err) {
      console.error('meta error:', err);
    }
    const ver = document.getElementById('app-version');
    if (ver && meta.version) ver.textContent = 'v' + meta.version;
  }

  async function fetchHealth() {
    try {
      const res = await api('/api/health');
      if (!res.ok) return;
      const body = await res.json();
      renderScanners(body.scanners || {}, document.getElementById('scanner-pills'), currentData);
    } catch (err) {}
  }

  async function fetchHostHealth(hostId) {
    const url = hostId === 'local' ? '/api/health' : '/api/hosts/' + encodeURIComponent(hostId) + '/health';
    try {
      const res = await api(url);
      if (!res.ok) return;
      const body = await res.json();
      if (!hostMaps[hostId]) hostMaps[hostId] = {};
      hostMaps[hostId].scanners = body.scanners || {};
    } catch (err) {}
  }

  function renderScanners(scanners, hostEl, data) {
    const host = hostEl || document.getElementById('scanner-pills');
    if (!host) return;
    const items = [
      ['proc', 'host'],
      ['docker', 'docker'],
      ['compose', 'compose'],
    ];
    const truncated = !!(data && data.summary && data.summary.compose_truncated);
    const incomplete = !!(data && data.summary && data.summary.compose_incomplete);
    host.innerHTML = items.map(function (pair) {
      const name = t('scanner.' + pair[1]);
      const ok = !!scanners[pair[0]];
      const source = scanners.listen_source;
      let title = t(ok ? 'scanner.available' : 'scanner.unavailable', { name: name });
      if (pair[0] === 'proc' && ok && source && source !== 'none') {
        const via = t('scanner.via.' + source, { name: name });
        if (via && via.indexOf('scanner.via.') === -1) title = via;
      }
      if (pair[0] === 'compose' && truncated) {
        title = t('scanner.truncated', { name: name });
      } else if (pair[0] === 'compose' && incomplete) {
        title = t('scanner.incomplete', { name: name });
      }
      const warn = pair[0] === 'compose' && (truncated || incomplete);
      return '<span class="pill' + (ok ? ' ok' : ' bad') + (warn ? ' warn' : '') + '" role="img" title="' +
        escapeHtml(title) + '" aria-label="' + escapeHtml(title) + '"></span>';
    }).join('');
  }

  async function fetchSettings() {
    const res = await api('/api/settings');
    if (!res.ok) return null;
    return res.json();
  }

  function revertUnsavedSettings() {
    settingsDirty = false;
    if (!settingsDoc) return;
    applyServerSettings(settingsDoc);
    if (!window.PortLightI18n) return;
    PortLightI18n.load(settings.locale || 'auto').then(function () {
      PortLightI18n.applyDom();
      syncHiddenButton();
      if (currentData) render();
      syncHeaderHeight();
    });
  }

  function applyServerSettings(doc) {
    settingsDoc = doc;
    settings = Object.assign({}, settings, doc.values || {});
    if (!rangeFromView) {
      rangeStart = settings.port_range_start;
      rangeEnd = settings.port_range_end;
      rangeStartInput.value = rangeStart;
      rangeEndInput.value = rangeEnd;
    }
    applyAppearance();
  }

  let portsAbort = null;

  function trapTab(e, root) {
    if (!root) return;
    const nodes = root.querySelectorAll(
      'button, input, select, textarea, a[href], summary, [tabindex]:not([tabindex="-1"])'
    );
    const list = Array.prototype.filter.call(nodes, function (el) {
      if (el.disabled) return false;
      if (el.closest && el.closest('[inert]')) return false;
      return el.getClientRects().length > 0 || el === document.activeElement;
    });
    if (!list.length) return;
    const first = list[0];
    const last = list[list.length - 1];
    if (!root.contains(document.activeElement)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
      return;
    }
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
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

  async function fetchHosts() {
    try {
      const res = await api('/api/hosts');
      if (!res.ok) return hostCatalog;
      const body = await res.json();
      hostCatalog = {
        local: body.local || { id: 'local', name: '', local: true },
        peers: Array.isArray(body.peers) ? body.peers : [],
        readonly: !!body.readonly,
      };
    } catch (err) {}
    if (!hostById(focusHostId)) focusHostId = 'local';
    return hostCatalog;
  }

  async function fetchPorts(opts) {
    const isolated = !!(opts && opts.isolated);
    if (portsAbort) portsAbort.abort();
    const ac = new AbortController();
    if (!isolated) {
      portsAbort = ac;
      grid.setAttribute('aria-busy', 'true');
    }
    try {
      const url = '/api/ports?range_start=' + rangeStart + '&range_end=' + rangeEnd + '&include_hidden=' + showHidden;
      const headers = {};
      if (!isolated && portsEtag && portsEtagUrl === url) headers['If-None-Match'] = portsEtag;
      const res = await api(url, { signal: ac.signal, headers: headers });
      if (res.status === 304) return { ok: true, unchanged: true };
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const etag = res.headers.get('etag');
      const data = await res.json();
      if (!isolated) {
        portsEtag = etag || '';
        portsEtagUrl = url;
      } else if (data.summary && !data.summary.hidden_locked) {
        portsEtag = etag || '';
        portsEtagUrl = url;
      }
      return { ok: true, data: data };
    } catch (err) {
      if (err && err.name === 'AbortError') return { ok: false, stale: true };
      console.error('fetch error:', err);
      return { ok: false, stale: false };
    } finally {
      if (!isolated && portsAbort === ac) grid.setAttribute('aria-busy', 'false');
    }
  }

  function occupancyFingerprint() {
    if (!hasPeers()) {
      if (!currentData) return '';
      return JSON.stringify({ ports: currentData.ports, summary: currentData.summary });
    }
    return JSON.stringify(listedHosts().map(function (h) {
      const m = hostMaps[h.id] || {};
      return {
        id: h.id,
        ports: m.data && m.data.ports,
        summary: m.data && m.data.summary,
        error: m.error || '',
        scanners: m.scanners || null,
      };
    }));
  }

  async function fetchHostOccupancy(hostId, opts) {
    const isolated = !!(opts && opts.isolated);
    if (hostAborts[hostId]) hostAborts[hostId].abort();
    const ac = new AbortController();
    hostAborts[hostId] = ac;
    const root = document.getElementById('host-grid-' + hostId) || (hostId === 'local' ? grid : null);
    if (!isolated && root) root.setAttribute('aria-busy', 'true');
    try {
      const url = occupancyUrl(hostId);
      const headers = {};
      const tag = hostEtags[hostId];
      if (!isolated && tag && tag.url === url && tag.etag) headers['If-None-Match'] = tag.etag;
      const res = await api(url, { signal: ac.signal, headers: headers });
      if (res.status === 304) return { ok: true, unchanged: true };
      if (res.status === 502) {
        const body = await res.json().catch(function () { return {}; });
        const auth = String(body.detail || '').toLowerCase().indexOf('auth') >= 0;
        return { ok: false, stale: false, auth: auth };
      }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const etag = res.headers.get('etag');
      const data = await res.json();
      if (!isolated || (data.summary && !data.summary.hidden_locked)) {
        hostEtags[hostId] = { etag: etag || '', url: url };
      }
      return { ok: true, data: data };
    } catch (err) {
      if (err && err.name === 'AbortError') return { ok: false, stale: true };
      console.error('fetch error:', err);
      return { ok: false, stale: false };
    } finally {
      if (!isolated && hostAborts[hostId] === ac && root) root.setAttribute('aria-busy', 'false');
    }
  }

  async function loadAllOccupancy(opts) {
    const ids = listedHosts().map(function (h) { return h.id; });
    const results = await Promise.all(ids.map(function (id) {
      return fetchHostOccupancy(id, opts);
    }));
    let localOk = false;
    ids.forEach(function (id, i) {
      const result = results[i];
      if (!hostMaps[id]) hostMaps[id] = {};
      if (!result || result.stale) return;
      if (result.unchanged) {
        if (id === 'local') localOk = true;
        return;
      }
      if (result.ok && result.data) {
        hostMaps[id].data = result.data;
        hostMaps[id].error = '';
        if (id === 'local') localOk = true;
      } else {
        hostMaps[id].error = result.auth ? 'auth' : 'down';
      }
    });
    await Promise.all(ids.map(fetchHostHealth));
    if (localOk) {
      setSyncError(false);
      markRefreshed();
    } else {
      setSyncError(true);
    }
    const focused = dataForHost(focusHostId);
    if (focused) currentData = focused;
    else if (hostMaps.local && hostMaps.local.data) currentData = hostMaps.local.data;
    return currentData;
  }

  async function retryHost(hostId) {
    if (!hostId || hostRetrying[hostId]) return;
    hostRetrying[hostId] = true;
    const btn = hostBoards && hostBoards.querySelector('[data-host-retry="' + hostId + '"]');
    if (btn) btn.disabled = true;
    let shouldRender = false;
    try {
      const result = await fetchHostOccupancy(hostId);
      if (!hostMaps[hostId]) hostMaps[hostId] = {};
      if (result && !result.stale) {
        if (result.unchanged) {
          hostMaps[hostId].error = '';
        } else if (result.ok && result.data) {
          hostMaps[hostId].data = result.data;
          hostMaps[hostId].error = '';
        } else {
          hostMaps[hostId].error = result.auth ? 'auth' : 'down';
        }
      }
      if (!result || result.stale) return;
      await fetchHostHealth(hostId);
      occupancyKey = occupancyFingerprint();
      if (hostId === focusHostId && hostMaps[hostId].data) {
        currentData = hostMaps[hostId].data;
      }
      if (hostId === 'local') setSyncError(!!hostMaps[hostId].error);
      if (!hostMaps[hostId].error) markRefreshed();
      shouldRender = true;
    } finally {
      hostRetrying[hostId] = false;
    }
    if (shouldRender) render();
  }

  function tick() {
    if (route.name === 'settings') return;
    if (modalOpen()) return;
    const wantHidden = showHidden;
    loadPorts().then(function (data) {
      if (route.name === 'settings') return;
      if (showHidden !== wantHidden) return;
      if (!hasPeers()) {
        if (!data) return;
        if (data === currentData && occupancyKey) return;
        currentData = data;
      } else if (data) {
        currentData = dataForHost(focusHostId) || data;
      } else if (!currentData) {
        return;
      }
      const lockedNow = !!(currentData && currentData.summary && currentData.summary.hidden_locked);
      if (lockedNow !== lastHiddenLocked) {
        lastHiddenLocked = lockedNow;
        Object.keys(lockedHitCache).forEach(function (k) { delete lockedHitCache[k]; });
        Object.keys(lockedHitInflight).forEach(function (k) { delete lockedHitInflight[k]; });
      }
      const key = occupancyFingerprint();
      if (key !== occupancyKey) {
        occupancyKey = key;
        render();
      }
    }).then(function () {
      if (!hasPeers()) fetchHealth();
    });
  }

  async function loadPorts(opts) {
    if (hasPeers()) return loadAllOccupancy(opts);
    const result = await fetchPorts(opts);
    if (!result || result.stale) return null;
    if (result.unchanged) {
      setSyncError(false);
      markRefreshed();
      return currentData;
    }
    if (!result.ok || !result.data) {
      setSyncError(true);
      return null;
    }
    setSyncError(false);
    markRefreshed();
    return result.data;
  }

  function setupRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    if (settings.auto_refresh) {
      tick();
      refreshTimer = setInterval(tick, settings.refresh_ms || 5000);
    } else {
      tick();
    }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
      return;
    }
    if (settings.auto_refresh) setupRefresh();
  });

  function loadSettingsPage() {
    Promise.all([fetchSettings(), fetchHosts()]).then(function (pair) {
      const doc = pair[0];
      if (!doc) return;
      settingsDoc = doc;
      peersDraft = (hostCatalog.peers || []).map(clonePeerRow);
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
    settingsPanel = id;
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
    if (rangeFromView) {
      values.port_range_start = rangeStart;
      values.port_range_end = rangeEnd;
    }
    const fields = doc.fields || [];
    const host = document.getElementById('settings-fields');
    const lead = document.getElementById('settings-lead');
    const saveBtn = document.getElementById('settings-save');
    const status = document.getElementById('settings-status');
    settingsDirty = false;
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
    showSettingsPanel(route.section || settingsPanel);
    syncDependentSettings();
    renderPeersEditor(!!doc.readonly || !!hostCatalog.readonly);
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
    const rows = (peersDraft || []).map(function (row, i) {
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
    const canAdd = !locked && peersDraft.length < 6;
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
      const prev = peersDraft[i] || {};
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
    peersDraft = next;
  }

  function peersPayload() {
    readPeersDraftFromForm();
    return peersDraft.map(function (row) {
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
    if (settingsDoc && settingsDoc.readonly) return;
    const status = document.getElementById('settings-status');
    const form = document.getElementById('settings-form');
    const patch = {};
    const fields = settingsDoc && settingsDoc.fields ? settingsDoc.fields : [];
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
    const saved = settingsDoc && settingsDoc.values ? settingsDoc.values : settings;
    const rangeEdited = patch.port_range_start !== saved.port_range_start
      || patch.port_range_end !== saved.port_range_end;
    if (rangeEdited) rangeFromView = false;
    applyServerSettings(body);
    rangeFromView = true;
    saveView();
    if (!(hostCatalog && hostCatalog.readonly)) {
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
      hostCatalog = {
        local: hostBody.local || hostCatalog.local,
        peers: Array.isArray(hostBody.peers) ? hostBody.peers : [],
        readonly: !!hostBody.readonly,
      };
      peersDraft = (hostCatalog.peers || []).map(clonePeerRow);
    }
    renderSettingsForm(body);
    setupRefresh();
    status.className = 'is-ok';
    status.textContent = t('settings.saved');
    settingsDirty = false;
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

  function render() {
    const multi = hasPeers();
    appEl.classList.toggle('multi-host', multi);
    syncAddButton();
    if (multi) {
      grid.hidden = true;
      grid.classList.add('hidden');
      hostBoards.hidden = false;
      hostBoards.classList.remove('hidden');
      hostSwitcher.hidden = false;
      hostSwitcher.classList.remove('hidden');
      renderHostSwitcher();
      renderHostBoards();
      const focused = dataForHost(focusHostId);
      if (focused && focused.summary) {
        currentData = focused;
        renderSummary(focused.summary);
      } else if (currentData && currentData.summary) {
        renderSummary(currentData.summary);
      }
    } else {
      grid.hidden = false;
      grid.classList.remove('hidden');
      if (hostBoards) {
        hostBoards.hidden = true;
        hostBoards.classList.add('hidden');
        hostBoards.innerHTML = '';
      }
      if (hostSwitcher) {
        hostSwitcher.hidden = true;
        hostSwitcher.classList.add('hidden');
        hostSwitcher.innerHTML = '';
      }
      if (!currentData) return;
      renderSummary(currentData.summary);
      renderGrid(currentData.ports, grid, currentData, 'local');
    }
    if (selectedPort !== null) {
      const entry = portFromList(selectedPort, selectedHostId);
      if (entry) renderDetail(entry);
      else if (route.name === 'port') {
        detailShownPort = null;
        showPortDetail(selectedPort);
      } else closeDetail(true);
    }
  }

  function syncAddButton() {
    const btn = document.getElementById('btn-add');
    if (!btn) return;
    const local = !hasPeers() || focusHostId === 'local';
    btn.disabled = !local;
    const title = local ? t('action.add') : t('hosts.localOnly');
    btn.title = title;
    btn.setAttribute('aria-label', title);
  }

  function snapshotGridFocus() {
    const el = document.activeElement;
    if (!el || !el.classList || !el.classList.contains('port-cell')) return null;
    return { port: el.getAttribute('data-port'), host: el.getAttribute('data-host') || 'local' };
  }

  function renderHostSwitcher() {
    if (!hostSwitcher) return;
    hostSwitcher.innerHTML = listedHosts().map(function (h) {
      const on = h.id === focusHostId;
      return '<button type="button" class="host-chip' + (on ? ' active' : '') +
        '" role="tab" aria-selected="' + (on ? 'true' : 'false') +
        '" data-host-switch="' + escapeHtml(h.id) + '">' +
        escapeHtml(h.name || h.id) + '</button>';
    }).join('');
  }

  function renderHostBoards() {
    if (!hostBoards) return;
    const restore = snapshotGridFocus();
    const hosts = listedHosts();
    hostBoards.innerHTML = hosts.map(function (h) {
      return '<article class="host-board' + (h.id === focusHostId ? ' is-active' : '') +
        '" data-host="' + escapeHtml(h.id) + '">' +
        '<header class="host-board-head"><div class="host-board-title">' +
        escapeHtml(h.name || h.id) +
        (h.local ? ' <span class="host-local">' + escapeHtml(t('hosts.thisMachine')) + '</span>' : '') +
        '</div><div class="host-board-pills scanner-dots" data-host-pills="' +
        escapeHtml(h.id) + '"></div></header>' +
        '<p class="host-board-counts" data-host-counts="' + escapeHtml(h.id) + '"></p>' +
        '<div class="host-board-error hidden" hidden data-host-error="' + escapeHtml(h.id) + '">' +
        '<span></span><button type="button" class="btn-secondary" data-host-retry="' +
        escapeHtml(h.id) + '">' + escapeHtml(t('hosts.retry')) + '</button></div>' +
        '<div class="host-grid" id="host-grid-' + escapeHtml(h.id) +
        '" tabindex="-1" role="region" data-i18n-aria="grid.aria"></div></article>';
    }).join('');
    hosts.forEach(function (h) {
      const map = hostMaps[h.id] || {};
      const root = document.getElementById('host-grid-' + h.id);
      const err = hostBoards.querySelector('[data-host-error="' + h.id + '"]');
      const counts = hostBoards.querySelector('[data-host-counts="' + h.id + '"]');
      const pills = hostBoards.querySelector('[data-host-pills="' + h.id + '"]');
      if (map.error && err) {
        err.hidden = false;
        err.classList.remove('hidden');
        const text = err.querySelector('span');
        if (text) text.textContent = t(map.error === 'auth' ? 'hosts.authFailed' : 'hosts.unreachable');
        const retry = err.querySelector('[data-host-retry]');
        if (retry) retry.disabled = !!hostRetrying[h.id];
      }
      if (map.data && map.data.summary && counts) {
        counts.textContent = t('hosts.counts', {
          used: map.data.summary.used,
          configured: map.data.summary.configured,
        });
      }
      if (pills) renderScanners(map.scanners || {}, pills, map.data);
      if (map.data && root) renderGrid(map.data.ports, root, map.data, h.id, restore);
      else if (root && !map.error) {
        root.innerHTML = '<div class="empty">' + escapeHtml(t('hosts.loading')) + '</div>';
      }
    });
  }

  function portFromList(port, hostId) {
    const data = dataForHost(hostId || selectedHostId || 'local') || currentData;
    if (!data || !data.ports) return null;
    return data.ports.find(function (p) { return p.port === port; }) || null;
  }

  function freeStub(port) {
    return {
      port: port,
      status: 'free',
      source_type: 'unknown',
      known_service: knownCache[port] || null,
      containers: [],
      compose_configs: [],
      urls: [],
      conflict: false,
      is_hidden: false,
    };
  }

  function pendingStub(port) {
    return {
      port: port,
      status: 'free',
      source_type: 'unknown',
      known_service: knownCache[port] || null,
      containers: [],
      compose_configs: [],
      urls: [],
      conflict: false,
      is_hidden: false,
      _pending: true,
    };
  }

  function showPortDetail(port, fallback) {
    const hostId = selectedHostId || 'local';
    const local = portFromList(port, hostId);
    if (local) {
      detailShownPort = port;
      renderDetail(local);
      return;
    }
    if (detailShownPort === port && route.hostId === hostId) return;
    renderDetail(fallback && fallback.port === port ? fallback : pendingStub(port));
    const gen = ++portDetailGen;
    detailShownPort = port;
    const openedHost = hostId;
    api(portApiUrl(hostId, port) + '?include_hidden=true').then(function (res) {
      if (gen !== portDetailGen || selectedPort !== port || selectedHostId !== openedHost) return null;
      if (res.status === 404) {
        const data = dataForHost(openedHost) || currentData;
        const locked = !!(data && data.summary && data.summary.hidden_locked);
        renderDetail(Object.assign(freeStub(port), {
          _missing: true,
          _locked: locked,
          is_hidden: locked,
        }));
        return null;
      }
      if (!res.ok) {
        detailShownPort = null;
        showDetailError(t('detail.actionFailed'));
        return null;
      }
      return res.json();
    }).then(function (row) {
      if (!row || gen !== portDetailGen || selectedPort !== port || selectedHostId !== openedHost) return;
      if (row.known_service) knownCache[port] = row.known_service;
      renderDetail(row);
    }).catch(function () {
      if (gen === portDetailGen) detailShownPort = null;
    });
  }

  function prefetchKnown(port) {
    if (knownCache[port] !== undefined || knownInflight[port]) return;
    knownInflight[port] = true;
    api('/api/known-ports/' + port).then(function (res) {
      if (res.status === 404) {
        knownCache[port] = null;
        return null;
      }
      if (!res.ok) {
        knownCache[port] = null;
        return null;
      }
      return res.json();
    }).then(function (body) {
      delete knownInflight[port];
      if (!body) return;
      knownCache[port] = {
        name: body.name,
        description: body.description,
        category: body.category,
        is_access_port: body.is_access_port,
      };
      if (currentData && searchPortNum !== null && !knownRenderFrame) {
        knownRenderFrame = requestAnimationFrame(function () {
          knownRenderFrame = 0;
          if (currentData && searchPortNum !== null) render();
        });
      }
    }).catch(function () {
      knownCache[port] = null;
      delete knownInflight[port];
    });
  }

  function renderSummary(s) {
    function toggle(active, attrs, dot, n, label) {
      return '<button type="button" class="stat' + (active ? ' active' : '') + '" ' + attrs +
        ' aria-pressed="' + (active ? 'true' : 'false') + '">' +
        '<span class="dot ' + dot + '"></span><span class="num">' + n + '</span> ' + label + '</button>';
    }
    let html = '';
    if (hasPeers()) {
      html += '<span class="legend-host">' + escapeHtml(t('hosts.legendHost', { name: hostName(focusHostId) })) + '</span>';
    }
    html += toggle(statusFilter === 'used', 'data-status="used"', 'used', s.used, t('legend.inUse')) +
      toggle(statusFilter === 'configured', 'data-status="configured"', 'configured', s.configured, t('legend.configured')) +
      '<span class="stat is-static"><span class="dot free"></span><span class="num">' + s.free + '</span> ' + t('legend.free') + '</span>';
    if (s.hidden > 0) {
      html += toggle(kindFilters.has('hidden'), 'data-kind="hidden"', 'hidden', s.hidden,
        t('legend.hidden') + (s.hidden_locked ? ' (' + t('legend.locked') + ')' : ''));
    }
    summary.innerHTML = html;
  }

  function getCellLabel(p) {
    if (p.containers && p.containers.length > 0) return p.containers[0].name;
    if (p.process) return p.process;
    if (p.manual_label) return p.manual_label;
    if (p.compose_configs && p.compose_configs.length > 0) return p.compose_configs[0].service_name;
    if (p.known_service) return p.known_service.name;
    return '';
  }

  function hiddenOccupancy(port, dataCtx) {
    const rows = (dataCtx && dataCtx.summary && dataCtx.summary.hidden_occupancy) || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].port === port) {
        const status = rows[i].status;
        if (status === 'used' || status === 'configured' || status === 'free') return status;
        return 'free';
      }
    }
    return 'free';
  }

  function probeLockedHit(port, hostId) {
    const key = (hostId || 'local') + ':' + port;
    if (lockedHitCache[key] || lockedHitInflight[key]) return;
    lockedHitInflight[key] = true;
    api(portApiUrl(hostId || 'local', port)).then(function (res) {
      lockedHitCache[key] = res.status === 404 ? 'locked' : 'free';
      delete lockedHitInflight[key];
      if (searchPortNum === port) render();
    }).catch(function () {
      delete lockedHitInflight[key];
    });
  }

  function buildSearchContext(ports, hitPort, dataCtx, hostId) {
    dataCtx = dataCtx || currentData;
    hostId = hostId || 'local';
    const allPortNums = new Set(ports.map(function (p) { return p.port; }));
    const hitExists = allPortNums.has(hitPort);
    const hiddenNums = new Set((dataCtx && dataCtx.summary && dataCtx.summary.hidden_ports) || []);
    const locked = !!(dataCtx && dataCtx.summary && dataCtx.summary.hidden_locked);
    const result = [];

    function synthetic(port, hidden) {
      prefetchKnown(port);
      if (!hidden) {
        return { port: port, status: 'free', _synthetic: true, known_service: getKnownForFree(port) };
      }
      return {
        port: port,
        status: hiddenOccupancy(port, dataCtx),
        is_hidden: true,
        _synthetic: true,
        known_service: getKnownForFree(port),
      };
    }

    if (!hitExists) {
      if (hiddenNums.has(hitPort)) {
        result.push(synthetic(hitPort, true));
      } else if (locked) {
        const kind = lockedHitCache[(hostId || 'local') + ':' + hitPort];
        if (kind === 'free') {
          result.push(synthetic(hitPort, false));
        } else {
          prefetchKnown(hitPort);
          if (!kind) probeLockedHit(hitPort, hostId);
          result.push({
            port: hitPort,
            status: 'free',
            is_hidden: true,
            _synthetic: true,
            _locked: true,
            known_service: getKnownForFree(hitPort),
          });
        }
      } else {
        result.push(synthetic(hitPort, false));
      }
    }

    let before = 0, after = 0;
    const neighborCap = 3;
    for (let p = hitPort - 1; p >= Math.max(1, hitPort - 50) && before < neighborCap; p--) {
      if (allPortNums.has(p)) {
        const entry = ports.find(function (x) { return x.port === p; });
        if (entry) {
          result.unshift(entry);
          before++;
        }
      } else if (hiddenNums.has(p)) {
        result.unshift(synthetic(p, true));
        before++;
      } else if (!locked) {
        result.unshift(synthetic(p, false));
        before++;
      }
    }
    for (let p = hitPort + 1; p <= Math.min(65535, hitPort + 50) && after < neighborCap; p++) {
      if (allPortNums.has(p)) {
        const entry = ports.find(function (x) { return x.port === p; });
        if (entry) {
          result.push(entry);
          after++;
        }
      } else if (hiddenNums.has(p)) {
        result.push(synthetic(p, true));
        after++;
      } else if (!locked) {
        result.push(synthetic(p, false));
        after++;
      }
    }

    if (hitExists) {
      const hit = ports.find(function (p) { return p.port === hitPort; });
      result.push(hit);
    }

    result.sort(function (a, b) { return a.port - b.port; });
    const seen = new Set();
    return result.filter(function (p) {
      if (seen.has(p.port)) return false;
      seen.add(p.port);
      return true;
    });
  }

  function getKnownForFree(port) {
    if (Object.prototype.hasOwnProperty.call(knownCache, port)) return knownCache[port];
    const found = portFromList(port);
    if (found && found.known_service) return found.known_service;
    return null;
  }

  function matchesFilter(p) {
    if (statusFilter === 'used' && p.status !== 'used') return false;
    if (statusFilter === 'configured' && p.status !== 'configured') return false;
    const kinds = Array.from(kindFilters);
    for (let i = 0; i < kinds.length; i++) {
      const match = KIND_MATCHERS[kinds[i]];
      if (match && !match(p)) return false;
    }
    return true;
  }

  function sortPorts(arr) {
    switch (sortMode) {
      case 'port-desc': return arr.sort(function (a, b) { return b.port - a.port; });
      case 'name-asc':
        return arr.sort(function (a, b) { return collate(getCellLabel(a) || '~', getCellLabel(b) || '~'); });
      case 'name-desc':
        return arr.sort(function (a, b) { return collate(getCellLabel(b) || '~', getCellLabel(a) || '~'); });
      case 'status':
        return arr.sort(function (a, b) {
          const order = { used: 0, configured: 1, free: 2 };
          return (order[a.status] || 9) - (order[b.status] || 9) || a.port - b.port;
        });
      default: return arr.sort(function (a, b) { return a.port - b.port; });
    }
  }

  function renderGrid(ports, rootEl, dataCtx, hostId, restore) {
    rootEl = rootEl || grid;
    dataCtx = dataCtx || currentData;
    hostId = hostId || 'local';
    ports = ports || [];
    let displayPorts;

    if (searchPortNum !== null) {
      displayPorts = buildSearchContext(ports, searchPortNum, dataCtx, hostId).filter(function (p) {
        if (p.port === searchPortNum) return true;
        return matchesFilter(p);
      });
    } else {
      displayPorts = ports.filter(function (p) {
        if (!searchTerm && (p.port < rangeStart || p.port > rangeEnd)) return false;
        if (!showHidden && p.is_hidden) return false;
        if (!matchesFilter(p)) return false;
        if (searchTerm) {
          const haystack = [
            String(p.port), p.process || '', p.manual_label || '',
            p.known_service ? p.known_service.name : '',
            p.known_service ? p.known_service.description : '',
            ...(p.containers || []).map(function (c) {
              return c.name + ' ' + (c.compose_project || '') + ' ' + (c.compose_service || '') +
                ' ' + c.image + ' ' + (c.status || '') + ' ' + (c.bind_ips || []).join(' ') + ' ' + (c.protocol || '');
            }),
            ...(p.compose_configs || []).map(function (c) {
              return (c.project_name || '') + ' ' + (c.project_dir || '') + ' ' +
                c.service_name + ' ' + c.compose_file + ' ' + (c.host_ip || '');
            }),
            p.protocol || '', p.bind_scope || '', p.source_type || '', p.machine || '',
            p.pid ? String(p.pid) : '', (p.ips || []).join(' '),
            p.known_service ? (p.known_service.category || '') : '',
            ...(p.urls || []),
          ].join(' ').toLowerCase();
          if (!haystack.includes(searchTerm)) return false;
        }
        return true;
      });
    }

    displayPorts = sortPorts(displayPorts.slice());

    let restorePort = restore && restore.port;
    let restoreHost = restore && restore.host;
    if (!restore && document.activeElement && document.activeElement.classList &&
        document.activeElement.classList.contains('port-cell')) {
      restorePort = document.activeElement.getAttribute('data-port');
      restoreHost = document.activeElement.getAttribute('data-host') || hostId;
    }

    const cellSelected = function (p) {
      return p.port === selectedPort && (selectedHostId || 'local') === hostId;
    };

    if (displayPorts.length === 0) {
      const inRange = (dataCtx.ports || []).filter(function (p) {
        return p.port >= rangeStart && p.port <= rangeEnd && (showHidden || !p.is_hidden);
      });
      const noFacet = !searchTerm && searchPortNum === null && kindFilters.size === 0 && statusFilter === 'all';
      const key = noFacet && inRange.length === 0 ? 'grid.emptyNone' : 'grid.empty';
      const active = document.activeElement;
      const gridHadFocus = active === rootEl || (active && rootEl.contains(active));
      rootEl.innerHTML = '<div class="empty">' + escapeHtml(t(key)) + '</div>';
      if (gridHadFocus) rootEl.focus({ preventScroll: true });
      return;
    }

    rootEl.innerHTML = displayPorts.map(function (p) {
      const lockedHit = !!(p._locked && p._synthetic);
      let cls = lockedHit ? 'locked'
        : p.status === 'used' ? 'used' : p.status === 'configured' ? 'configured' : 'free';
      if (p.is_hidden) cls += ' hidden';
      const conflict = p.conflict ? ' conflict' : '';
      const selected = cellSelected(p) ? ' selected' : '';
      const isSearchHit = searchPortNum !== null && p.port === searchPortNum;
      const searchHit = isSearchHit ? ' search-hit' : '';
      const searchNear = searchPortNum !== null && !isSearchHit ? ' search-near' : '';
      const label = getCellLabel(p);
      const labelText = label ? '<div class="port-label">' + escapeHtml(label) + '</div>' : '';
      const statusLabel = lockedHit ? t('legend.locked') : t('status.' + p.status);
      const statusText = settings.show_status_text && !lockedHit && p.status !== 'free'
        ? '<span class="status-text">' + escapeHtml(t('status.' + p.status)) + '</span>' : '';
      const accessBadge = settings.show_access_badge && p.known_service && p.known_service.is_access_port
        ? '<span class="access-badge">' + escapeHtml(t('grid.web')) + '</span>' : '';
      const protoBadge = settings.show_protocol_badge && p.protocol && p.protocol !== 'tcp'
        ? '<span class="proto-badge">' + escapeHtml(p.protocol) + '</span>' : '';

      const ariaParts = [String(p.port), statusLabel, label, p.protocol].filter(Boolean);
      return '<button type="button" class="port-cell ' + cls + conflict + selected + searchHit + searchNear + '"' +
        ' data-port="' + p.port + '" data-host="' + escapeHtml(hostId) + '"' +
        ' aria-label="' + escapeHtml(ariaParts.join(', ')) + '"' +
        ' aria-selected="' + (cellSelected(p) ? 'true' : 'false') + '"' +
        ' title="' + escapeHtml([p.port, p.protocol, p.bind_scope, label].filter(Boolean).join(' · ')) + '">' +
        '<div class="port-num">' + p.port + '</div>' +
        labelText +
        '<div class="cell-meta"><span class="indicator"></span>' + protoBadge + accessBadge + statusText + '</div>' +
        '</button>';
    }).join('');

    rootEl.querySelectorAll('.port-cell').forEach(function (el) {
      el.addEventListener('click', function () {
        const port = parseInt(el.dataset.port, 10);
        const hid = el.dataset.host || 'local';
        if (selectedPort === port && (selectedHostId || 'local') === hid && route.name === 'port') {
          closeDetail();
          return;
        }
        selectedPort = port;
        selectedHostId = hid;
        focusHostId = hid;
        const want = portHash(hid, port);
        if (location.hash !== want) {
          location.hash = want;
        } else {
          const entry = displayPorts.find(function (p) { return p.port === port; });
          showPortDetail(port, entry);
        }
        if (settings.copy_on_click) {
          navigator.clipboard.writeText(String(port)).then(function () {
            const live = document.querySelector('.detail-copy-port[data-copy-port="' + port + '"]')
              || rootEl.querySelector('.port-cell[data-port="' + port + '"]');
            if (live) showCopyToast(live);
          }).catch(function () {});
        }
      });
    });
    if (restorePort && (!restoreHost || restoreHost === hostId)) {
      const again = rootEl.querySelector('.port-cell[data-port="' + restorePort + '"]');
      if (again) again.focus({ preventScroll: true });
    }
  }

  function showCopyToast(cell) {
    cell.querySelectorAll('.copy-toast').forEach(function (tEl) { tEl.remove(); });
    const toast = document.createElement('div');
    toast.className = 'copy-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.textContent = t('grid.copied');
    cell.appendChild(toast);
    requestAnimationFrame(function () { toast.classList.add('show'); });
    clearTimeout(cell._copyToastTimer);
    cell._copyToastTimer = setTimeout(function () {
      toast.classList.remove('show');
      setTimeout(function () { toast.remove(); }, 200);
    }, 900);
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
    const remote = hasPeers() && selectedHostId && selectedHostId !== 'local';
    if (remote) {
      html += '<p class="modal-hint">' + escapeHtml(t('detail.remoteReadOnly', { name: hostName(selectedHostId) })) + '</p>';
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
    const hostId = selectedHostId || focusHostId;
    if (selectedPort !== null) pendingGridFocus = { port: selectedPort, hostId: hostId };
    const wasOpen = !detailPanel.classList.contains('hidden') || selectedPort !== null;
    setDetailOpen(false);
    selectedPort = null;
    detailShownPort = null;
    portDetailGen += 1;
    if (!skipHash && route.name === 'port') {
      location.hash = gridHash(hostId);
      return;
    }
    if (wasOpen && (currentData || hasPeers()) && route.name !== 'settings') render();
    applyPendingGridFocus();
  }

  function applyPendingGridFocus() {
    if (route.name === 'settings') {
      pendingGridFocus = null;
      return;
    }
    const pending = pendingGridFocus;
    pendingGridFocus = null;
    if (!pending) return;
    const port = pending.port || pending;
    const hostId = pending.hostId || 'local';
    const root = document.getElementById('host-grid-' + hostId) || grid;
    const cell = root.querySelector('.port-cell[data-port="' + port + '"]');
    if (cell) cell.focus({ preventScroll: true });
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
    if (hasPeers() && selectedHostId !== 'local') return;
    mutateDetail('/api/hidden/' + port, { method: 'POST' }, tick);
  };
  window._portLightUnhide = function (port) {
    if (hasPeers() && selectedHostId !== 'local') return;
    mutateDetail('/api/hidden/' + port, { method: 'DELETE' }, tick);
  };
  window._portLightDeleteManual = function (port) {
    if (hasPeers() && selectedHostId !== 'local') return;
    if (!window.confirm(t('detail.deleteConfirm', { port: port }))) return;
    mutateDetail('/api/manual-ports/' + port, { method: 'DELETE' }, function () {
      closeDetail();
      tick();
    });
  };
  window._portLightSaveLabel = function (port, label) {
    if (hasPeers() && selectedHostId !== 'local') return;
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
    const prevUnlock = hiddenUnlock;
    const prevShow = showHidden;
    hiddenUnlock = password;
    showHidden = true;
    const data = await loadPorts({ isolated: true });
    if (!data || (data.summary && data.summary.hidden_locked)) {
      hiddenUnlock = prevUnlock;
      showHidden = prevShow;
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
    showHidden = true;
    currentData = data;
    const followup = pendingAfterUnlock;
    pendingAfterUnlock = null;
    closeModals();
    document.getElementById('unhide-password').value = '';
    syncHiddenButton();
    saveView();
    if (followup) followup();
    else render();
    const focusKey = pendingUnlockFocus;
    pendingUnlockFocus = 'eye';
    let focusEl = unhideBtn;
    if (focusKey === 'chip') focusEl = document.querySelector('#filters [data-filter="hidden"]');
    else if (focusKey === 'stat') focusEl = document.querySelector('#summary button.stat[data-kind="hidden"]');
    if (focusEl) focusEl.focus({ preventScroll: true });
  }

  function escapeHtml(text) {
    if (text === 0) return '0';
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

  function safeHref(url) {
    if (!url) return '';
    const text = String(url).trim();
    const lower = text.toLowerCase();
    if (lower.indexOf('http://') !== 0 && lower.indexOf('https://') !== 0) return '';
    if (/[\s<>]/.test(text)) return '';
    return text;
  }

  function startApp() {
    sortSelect.value = sortMode;
    rangeStartInput.value = rangeStart;
    rangeEndInput.value = rangeEnd;
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
          ? PortLightI18n.load(settings.locale || 'auto')
          : Promise.resolve();
      })
      .then(function () {
        if (window.PortLightI18n) PortLightI18n.applyDom();
        if (showHidden && meta.hidden_unlock_required && !hiddenUnlock) {
          showHidden = false;
          kindFilters.delete('hidden');
        }
        syncHiddenButton();
        syncFilterUI();
        applyRoute();
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
