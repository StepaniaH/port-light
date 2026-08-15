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
    return 'HTTP ' + status;
  }

  let currentData = null;
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
  let refreshTimer = null;
  let meta = { hidden_unlock_required: false, hidden_ports_withheld: false, version: '', settings_readonly: false };
  let hiddenUnlock = sessionStorage.getItem('port-light-hidden-unlock') || '';
  let route = { name: 'grid' };
  let pendingAfterUnlock = null;
  let focusBack = null;
  let pendingGridFocus = null;
  let occupancyKey = '';
  let portsEtag = '';
  let portsEtagUrl = '';

  const appEl = document.getElementById('app');
  const grid = document.getElementById('grid');
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
      return p.containers && p.containers.some(function (c) { return c.status === 'running'; });
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
    if (view.status === 'running') kindFilters.add('running');
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

  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if ((settings.theme || 'system') === 'system') applyTheme();
    });
  } catch (e) {}
  window.addEventListener('resize', syncHeaderHeight);

  function parseRoute() {
    const raw = (location.hash || '#/').replace(/^#\/?/, '');
    const parts = raw.split('/').filter(Boolean);
    if (parts[0] === 'settings') return { name: 'settings' };
    if (parts[0] === 'port' && /^\d+$/.test(parts[1] || '')) {
      return { name: 'port', port: parseInt(parts[1], 10) };
    }
    return { name: 'grid' };
  }

  function applyRoute() {
    const next = parseRoute();
    if (settingsDirty && route.name === 'settings' && next.name !== 'settings') {
      if (!window.confirm(t('settings.discard'))) {
        location.hash = '#/settings';
        return;
      }
      settingsDirty = false;
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
      loadSettingsPage();
      return;
    }
    if (prev === 'settings') tick();
    if (route.name === 'port') {
      selectedPort = route.port;
      if (currentData) render();
      return;
    }
    closeDetail(true);
    if (currentData) render();
    applyPendingGridFocus();
  }

  window.addEventListener('hashchange', applyRoute);

  const skipLink = document.querySelector('.skip-link');
  if (skipLink) {
    skipLink.addEventListener('click', function (e) {
      e.preventDefault();
      const target = route.name === 'settings'
        ? document.getElementById('settings-form')
        : document.getElementById('grid');
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
        if (!ensureHiddenVisible()) return;
        kindFilters.add('hidden');
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
      if (f === 'hidden' && !ensureHiddenVisible()) return;
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

  function ensureHiddenVisible() {
    if (showHidden) return true;
    if (meta.hidden_unlock_required && !hiddenUnlock) {
      pendingAfterUnlock = function () {
        kindFilters.add('hidden');
        syncFilterUI();
        saveView();
        render();
      };
      openModal('unhide-modal');
      return false;
    }
    showHidden = true;
    syncHiddenButton();
    tick();
    return true;
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
      openModal('unhide-modal');
      return;
    }
    showHidden = true;
    syncHiddenButton();
    tick();
  });
  document.getElementById('unhide-cancel').addEventListener('click', closeModals);
  document.getElementById('unhide-form').addEventListener('submit', function (e) {
    e.preventDefault();
    unlockHidden();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (modalOpen()) { closeModals(); return; }
      if (closeLocaleMenu({ focusTrigger: true })) return;
      if (route.name === 'settings') { location.hash = '#/'; return; }
      if (searchTerm || searchPortNum !== null) {
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
    if (e.key === 'Tab' && !detailPanel.classList.contains('hidden')) {
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

  function gridCells() {
    return Array.prototype.slice.call(grid.querySelectorAll('.port-cell'));
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

  function moveGridFocus(key) {
    const cells = gridCells();
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

  grid.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'ArrowUp' &&
        e.key !== 'ArrowDown' && e.key !== 'Home' && e.key !== 'End' &&
        e.key !== 'PageUp' && e.key !== 'PageDown') return;
    if (e.target === grid) {
      const first = grid.querySelector('.port-cell');
      if (first) { e.preventDefault(); first.focus(); }
      return;
    }
    if (!e.target || !e.target.classList || !e.target.classList.contains('port-cell')) return;
    e.preventDefault();
    moveGridFocus(e.key);
  });

  document.getElementById('settings-form').addEventListener('submit', function (e) {
    e.preventDefault();
    saveSettingsPage();
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
          if (currentData) render();
          syncHeaderHeight();
        });
      }
    }
    markDirty();
  });
  document.getElementById('settings-fields').addEventListener('input', markDirty);

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
      renderScanners(body.scanners || {});
    } catch (err) {}
  }

  function renderScanners(scanners) {
    const host = document.getElementById('scanner-pills');
    const items = [
      ['proc', 'host'],
      ['docker', 'docker'],
      ['compose', 'compose'],
    ];
    host.innerHTML = items.map(function (pair) {
      const name = t('scanner.' + pair[1]);
      const ok = !!scanners[pair[0]];
      const title = t(ok ? 'scanner.available' : 'scanner.unavailable', { name: name });
      return '<span class="pill' + (ok ? ' ok' : ' bad') + '" role="img" title="' +
        escapeHtml(title) + '" aria-label="' + escapeHtml(title) + '"></span>';
    }).join('');
  }

  async function fetchSettings() {
    const res = await api('/api/settings');
    if (!res.ok) return null;
    return res.json();
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
    const nodes = root.querySelectorAll('button, input, select, textarea, a[href]');
    const list = Array.prototype.filter.call(nodes, function (el) {
      return !el.disabled && el.getClientRects().length > 0;
    });
    if (!list.length) return;
    const first = list[0];
    const last = list[list.length - 1];
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
  }

  function setSyncError(on) {
    const el = document.getElementById('sync-error');
    if (!el) return;
    el.hidden = !on;
    el.classList.toggle('hidden', !on);
    if (on) el.textContent = t('grid.refreshFailed');
  }

  async function fetchPorts() {
    if (portsAbort) portsAbort.abort();
    const ac = new AbortController();
    portsAbort = ac;
    grid.setAttribute('aria-busy', currentData ? 'false' : 'true');
    try {
      const url = '/api/ports?range_start=' + rangeStart + '&range_end=' + rangeEnd + '&include_hidden=' + showHidden;
      const headers = {};
      if (portsEtag && portsEtagUrl === url) headers['If-None-Match'] = portsEtag;
      const res = await api(url, { signal: ac.signal, headers: headers });
      if (res.status === 304) return { ok: true, unchanged: true };
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const etag = res.headers.get('etag');
      portsEtag = etag || '';
      portsEtagUrl = url;
      return { ok: true, data: await res.json() };
    } catch (err) {
      if (err && err.name === 'AbortError') return { ok: false, stale: true };
      console.error('fetch error:', err);
      return { ok: false, stale: false };
    } finally {
      if (portsAbort === ac) grid.setAttribute('aria-busy', 'false');
    }
  }

  function tick() {
    if (route.name === 'settings') return;
    loadPorts().then(function (data) {
      if (!data || route.name === 'settings') return;
      if (data === currentData && occupancyKey) return;
      currentData = data;
      const key = JSON.stringify({ ports: data.ports, summary: data.summary });
      if (key !== occupancyKey) {
        occupancyKey = key;
        render();
      }
    }).then(function () {
      fetchHealth();
    });
  }

  async function loadPorts() {
    const result = await fetchPorts();
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
    fetchSettings().then(function (doc) {
      if (!doc) return;
      settingsDoc = doc;
      renderSettingsForm(doc);
    });
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

  function renderSettingsForm(doc) {
    const values = doc.values || {};
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

    const groups = [];
    const byGroup = {};
    fields.forEach(function (f) {
      if (!byGroup[f.group]) {
        byGroup[f.group] = [];
        groups.push(f.group);
      }
      byGroup[f.group].push(f);
    });

    host.innerHTML = groups.map(function (g) {
      const rows = byGroup[g].map(function (f) {
        return renderField(f, values[f.key], doc.readonly);
      }).join('');
      return '<section class="settings-card"><header class="settings-card-head"><h2 data-i18n="settings.groups.' + g + '.title">' +
        escapeHtml(t('settings.groups.' + g + '.title')) + '</h2><p data-i18n="settings.groups.' + g + '.blurb">' +
        escapeHtml(t('settings.groups.' + g + '.blurb')) +
        '</p></header><div class="settings-card-body">' + rows + '</div></section>';
    }).join('');

    const env = doc.env_only || {};
    document.getElementById('settings-env-only').innerHTML = [
      kvRow('settings.host.composeScanDir', env.compose_scan_dir),
      kvRow('settings.host.customPortsFile', env.custom_ports_file),
      kvRow('settings.host.dataDir', env.data_dir),
      kvRow('settings.host.basicAuth', env.auth_required ? t('settings.on') : t('settings.off'), env.auth_required ? 'settings.on' : 'settings.off'),
      kvRow('settings.host.hiddenUnlock', env.hidden_unlock_required ? t('settings.on') : t('settings.off'), env.hidden_unlock_required ? 'settings.on' : 'settings.off'),
      kvRow('settings.host.settingsSource', doc.source),
    ].join('');
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
    } else if (f.type === 'choice') {
      const choices = f.choices || [];
      control = '<div class="segmented" role="radiogroup">' +
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
    return '<' + tag + ' class="setting-row"><span class="setting-copy"><span class="setting-label" data-i18n="settings.fields.' + f.key + '.label">' +
      escapeHtml(fieldLabel(f)) + '</span><span class="field-help" data-i18n="settings.fields.' + f.key + '.help">' + escapeHtml(fieldHelp(f)) +
      '</span></span><span class="setting-control">' + control + originHint(f) +
      '</span></' + tag + '>';
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
    rangeFromView = false;
    applyServerSettings(body);
    rangeFromView = true;
    saveView();
    renderSettingsForm(body);
    setupRefresh();
    status.className = 'is-ok';
    status.textContent = t('settings.saved');
    settingsDirty = false;
  }

  function setDetailOpen(open) {
    appEl.classList.toggle('detail-open', open);
    document.documentElement.classList.toggle('detail-open', open);
    detailPanel.classList.toggle('hidden', !open);
    detailBackdrop.classList.toggle('hidden', !open);
    if (open) {
      detailPanel.setAttribute('role', 'dialog');
      detailPanel.setAttribute('aria-modal', 'true');
    } else {
      detailPanel.removeAttribute('role');
      detailPanel.setAttribute('aria-modal', 'false');
    }
  }

  function render() {
    if (!currentData) return;
    renderSummary(currentData.summary);
    renderGrid(currentData.ports);
    if (selectedPort !== null) {
      const entry = currentData.ports.find(function (p) { return p.port === selectedPort; });
      if (entry) renderDetail(entry);
      else if (route.name !== 'port') closeDetail(true);
    }
  }

  function renderSummary(s) {
    function toggle(active, attrs, dot, n, label) {
      return '<button type="button" class="stat' + (active ? ' active' : '') + '" ' + attrs +
        ' aria-pressed="' + (active ? 'true' : 'false') + '">' +
        '<span class="dot ' + dot + '"></span><span class="num">' + n + '</span> ' + label + '</button>';
    }
    let html = toggle(statusFilter === 'used', 'data-status="used"', 'used', s.used, t('legend.inUse')) +
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

  function buildSearchContext(ports, hitPort) {
    const allPortNums = new Set(ports.map(function (p) { return p.port; }));
    const hitExists = allPortNums.has(hitPort);
    const result = [];

    if (!hitExists) {
      result.push({ port: hitPort, status: 'free', _synthetic: true, known_service: getKnownForFree(hitPort) });
    }

    let beforeFree = 0, afterFree = 0;
    for (let p = hitPort - 1; p >= Math.max(1, hitPort - 50) && beforeFree < 3; p--) {
      if (!allPortNums.has(p)) {
        result.unshift({ port: p, status: 'free', _synthetic: true, known_service: getKnownForFree(p) });
        beforeFree++;
      } else {
        const entry = ports.find(function (x) { return x.port === p; });
        if (entry) result.unshift(entry);
      }
    }
    for (let p = hitPort + 1; p <= hitPort + 50 && afterFree < 3; p++) {
      if (!allPortNums.has(p)) {
        result.push({ port: p, status: 'free', _synthetic: true, known_service: getKnownForFree(p) });
        afterFree++;
      } else {
        const entry = ports.find(function (x) { return x.port === p; });
        if (entry) result.push(entry);
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
    if (currentData && currentData.ports) {
      const found = currentData.ports.find(function (p) { return p.port === port; });
      if (found && found.known_service) return found.known_service;
    }
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

  function renderGrid(ports) {
    let displayPorts;

    if (searchPortNum !== null) {
      displayPorts = buildSearchContext(ports, searchPortNum);
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
                ' ' + c.image + ' ' + (c.bind_ips || []).join(' ') + ' ' + (c.protocol || '');
            }),
            ...(p.compose_configs || []).map(function (c) {
              return (c.project_name || '') + ' ' + (c.project_dir || '') + ' ' +
                c.service_name + ' ' + c.compose_file + ' ' + (c.host_ip || '');
            }),
            p.protocol || '', p.bind_scope || '', (p.ips || []).join(' '),
            ...(p.urls || []),
          ].join(' ').toLowerCase();
          if (!haystack.includes(searchTerm)) return false;
        }
        return true;
      });
    }

    displayPorts = sortPorts(displayPorts.slice());

    let restorePort = null;
    if (document.activeElement && document.activeElement.classList &&
        document.activeElement.classList.contains('port-cell')) {
      restorePort = document.activeElement.getAttribute('data-port');
    }

    if (displayPorts.length === 0) {
      const inRange = (currentData.ports || []).filter(function (p) {
        return p.port >= rangeStart && p.port <= rangeEnd && (showHidden || !p.is_hidden);
      });
      const noFacet = !searchTerm && searchPortNum === null && kindFilters.size === 0 && statusFilter === 'all';
      const key = noFacet && inRange.length === 0 ? 'grid.emptyNone' : 'grid.empty';
      grid.innerHTML = '<div class="empty">' + escapeHtml(t(key)) + '</div>';
      return;
    }

    grid.innerHTML = displayPorts.map(function (p) {
      let cls = p.status === 'used' ? 'used' : p.status === 'configured' ? 'configured' : 'free';
      if (p.is_hidden) cls = 'hidden';
      const conflict = p.conflict ? ' conflict' : '';
      const selected = p.port === selectedPort ? ' selected' : '';
      const isSearchHit = searchPortNum !== null && p.port === searchPortNum;
      const searchHit = isSearchHit ? ' search-hit' : '';
      const searchNear = searchPortNum !== null && !isSearchHit ? ' search-near' : '';
      const label = getCellLabel(p);
      const labelText = label ? '<div class="port-label">' + escapeHtml(label) + '</div>' : '';
      const statusText = settings.show_status_text && p.status !== 'free'
        ? '<span class="status-text">' + escapeHtml(t('status.' + p.status)) + '</span>' : '';
      const accessBadge = settings.show_access_badge && p.known_service && p.known_service.is_access_port
        ? '<span class="access-badge">' + escapeHtml(t('grid.web')) + '</span>' : '';
      const protoBadge = settings.show_protocol_badge && p.protocol && p.protocol !== 'tcp'
        ? '<span class="proto-badge">' + escapeHtml(p.protocol) + '</span>' : '';

      return '<button type="button" class="port-cell ' + cls + conflict + selected + searchHit + searchNear + '"' +
        ' data-port="' + p.port + '"' +
        ' title="' + escapeHtml([p.port, p.protocol, p.bind_scope, label].filter(Boolean).join(' · ')) + '">' +
        '<div class="port-num">' + p.port + '</div>' +
        labelText +
        '<div class="cell-meta"><span class="indicator"></span>' + protoBadge + accessBadge + statusText + '</div>' +
        '</button>';
    }).join('');

    grid.querySelectorAll('.port-cell').forEach(function (el) {
      el.addEventListener('click', function () {
        const port = parseInt(el.dataset.port, 10);
        if (selectedPort === port && route.name === 'port') {
          closeDetail();
          return;
        }
        selectedPort = port;
        if (location.hash !== '#/port/' + port) {
          location.hash = '#/port/' + port;
        } else {
          const entry = displayPorts.find(function (p) { return p.port === port; });
          if (entry) renderDetail(entry);
        }
        if (settings.copy_on_click) {
          navigator.clipboard.writeText(String(port)).then(function () {
            showCopyToast(el);
          }).catch(function () {});
        }
      });
    });
    if (restorePort) {
      const again = grid.querySelector('.port-cell[data-port="' + restorePort + '"]');
      if (again) again.focus({ preventScroll: true });
    }
  }

  function showCopyToast(cell) {
    cell.querySelectorAll('.copy-toast').forEach(function (tEl) { tEl.remove(); });
    const toast = document.createElement('div');
    toast.className = 'copy-toast';
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
    setDetailOpen(true);
    const name = getCellLabel(p);
    let html = '<div class="detail-head"><div><h2><button type="button" class="detail-copy-port" data-copy-port="' +
      p.port + '" title="' + escapeHtml(t('detail.copyPort')) + '">' + p.port + '</button></h2>' +
      (name ? '<div class="detail-sub">' + escapeHtml(name) + '</div>' : '') +
      '</div><button type="button" class="close-btn" data-close-detail aria-label="' +
      escapeHtml(t('detail.close')) + '">×</button></div>';

    html += '<div class="row"><span class="key">' + escapeHtml(t('detail.status')) + '</span><span class="tag ' + p.status + '">' +
      escapeHtml(t('status.' + p.status) || p.status) + '</span></div>';
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
        const tag = c.status === 'running' ? 'running' : 'exited';
        html += '<div class="row"><span class="key">' + escapeHtml(c.name) + '</span><span class="tag ' + tag + '">' +
          escapeHtml(t('status.' + tag) || c.status) + '</span></div>';
        html += '<div class="row"><span class="key">' + escapeHtml(t('detail.image')) + '</span><span class="val">' + escapeHtml(c.image) + '</span></div>';
        if (c.compose_project) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.project')) + '</span><span class="val">' + escapeHtml(c.compose_project) + '</span></div>';
        if (c.compose_service) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.service')) + '</span><span class="val">' + escapeHtml(c.compose_service) + '</span></div>';
        if (c.network_mode === 'host') html += '<div class="row"><span class="key">' + escapeHtml(t('detail.network')) + '</span><span class="val">host</span></div>';
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
        if (cc.network_mode === 'host') html += '<div class="row"><span class="key">' + escapeHtml(t('detail.network')) + '</span><span class="val">host</span></div>';
        if (cc.container_port) html += '<div class="row"><span class="key">' + escapeHtml(t('detail.containerPort')) + '</span><span class="val">' + cc.container_port + '</span></div>';
      }
    }

    if (p.manual_label) {
      html += '<div class="info-box"><span class="info-name">' + escapeHtml(t('detail.manual')) + '</span> — ' + escapeHtml(p.manual_label) + '</div>';
    }

    if (p.manual_label != null || p.source_type === 'manual') {
      html += '<form class="detail-label-form" data-label-form="' + p.port + '"><label><span class="key">' +
        escapeHtml(t('detail.label')) + '</span><input type="text" maxlength="80" value="' +
        escapeHtml(p.manual_label || '') + '" data-label-input></label><button type="submit" class="btn-secondary">' +
        escapeHtml(t('detail.saveLabel')) + '</button></form>';
    }

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

    const active = document.activeElement;
    const inDetail = active && detailContent.contains(active);
    const fromGrid = active && active.classList && active.classList.contains('port-cell');
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
    const focusEl = keep === 'close' ? closeBtn
      : keep === 'hide' ? hideBtn
      : keep === 'unhide' ? showBtn
      : keep === 'delete' ? delBtn
      : keep === 'copy' ? copyBtn
      : keep === 'label' ? labelInput
      : (fromGrid ? closeBtn : null);
    if (focusEl) focusEl.focus({ preventScroll: true });
  }

  function closeDetail(skipHash) {
    if (selectedPort !== null) pendingGridFocus = selectedPort;
    const wasOpen = !detailPanel.classList.contains('hidden') || selectedPort !== null;
    setDetailOpen(false);
    selectedPort = null;
    if (!skipHash && route.name === 'port') {
      location.hash = '#/';
      return;
    }
    if (wasOpen && currentData && route.name !== 'settings') render();
    applyPendingGridFocus();
  }

  function applyPendingGridFocus() {
    if (route.name === 'settings') {
      pendingGridFocus = null;
      return;
    }
    const port = pendingGridFocus;
    pendingGridFocus = null;
    if (!port) return;
    const cell = grid.querySelector('.port-cell[data-port="' + port + '"]');
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
    setSyncError(true);
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
    mutateDetail('/api/hidden/' + port, { method: 'POST' }, tick);
  };
  window._portLightUnhide = function (port) {
    mutateDetail('/api/hidden/' + port, { method: 'DELETE' }, tick);
  };
  window._portLightDeleteManual = function (port) {
    if (!window.confirm(t('detail.deleteConfirm', { port: port }))) return;
    mutateDetail('/api/manual-ports/' + port, { method: 'DELETE' }, function () {
      closeDetail();
      tick();
    });
  };
  window._portLightSaveLabel = function (port, label) {
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
    hiddenUnlock = password;
    sessionStorage.setItem('port-light-hidden-unlock', password);
    showHidden = true;
    const data = await loadPorts();
    if (!data) return;
    if (data.summary && data.summary.hidden_locked) {
      hiddenUnlock = '';
      sessionStorage.removeItem('port-light-hidden-unlock');
      showHidden = false;
      const input = document.getElementById('unhide-password');
      input.value = '';
      input.placeholder = t('modal.wrongPassword');
      return;
    }
    currentData = data;
    const followup = pendingAfterUnlock;
    pendingAfterUnlock = null;
    closeModals();
    document.getElementById('unhide-password').value = '';
    syncHiddenButton();
    if (followup) followup();
    else render();
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
        return window.PortLightI18n
          ? PortLightI18n.load(settings.locale || 'auto')
          : Promise.resolve();
      })
      .then(function () {
        if (window.PortLightI18n) PortLightI18n.applyDom();
        syncHiddenButton();
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
