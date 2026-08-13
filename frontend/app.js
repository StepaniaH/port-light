/* Port-Light frontend */

(function () {
  'use strict';

  let currentData = null;
  let activeFilters = new Set(['all']);
  let sortMode = 'port-asc';
  let searchTerm = '';
  let searchPortNum = null;
  let selectedPort = null;
  let rangeStart = 1;
  let rangeEnd = 9999;
  let showHidden = false;
  let settings = {
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
  let refreshTimer = null;
  let meta = { hidden_unlock_required: false, hidden_ports_withheld: false, version: '', settings_readonly: false };
  let hiddenUnlock = sessionStorage.getItem('port-light-hidden-unlock') || '';
  let route = { name: 'grid' };

  const grid = document.getElementById('grid');
  const summary = document.getElementById('summary');
  const detailPanel = document.getElementById('detail-panel');
  const detailContent = document.getElementById('detail-content');
  const searchInput = document.getElementById('search');
  const rangeStartInput = document.getElementById('range-start');
  const rangeEndInput = document.getElementById('range-end');
  const sortSelect = document.getElementById('sort-select');
  const GROUP_LABELS = {
    appearance: 'Appearance',
    grid: 'Grid',
    scanning: 'Compose scan',
    links: 'Access URLs',
  };

  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme) settings.theme = cached.theme;
    if (cached.grid_density) settings.grid_density = cached.grid_density;
  } catch (e) {}
  try {
    const view = JSON.parse(localStorage.getItem('port-light-view') || '{}');
    if (view.sort) sortMode = view.sort;
    if (Array.isArray(view.filters) && view.filters.length) activeFilters = new Set(view.filters);
  } catch (e) {}
  sortSelect.value = sortMode;
  document.querySelectorAll('.chip').forEach(function (c) {
    c.classList.toggle('active', activeFilters.has(c.dataset.filter));
  });
  applyAppearance();

  function applyTheme() {
    var t = settings.theme || 'system';
    if (t === 'system') {
      t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', t);
  }

  function applyAppearance() {
    applyTheme();
    document.documentElement.setAttribute('data-density', settings.grid_density || 'comfortable');
    try {
      localStorage.setItem('port-light-settings', JSON.stringify({
        theme: settings.theme,
        grid_density: settings.grid_density,
      }));
    } catch (e) {}
  }

  function saveView() {
    try {
      localStorage.setItem('port-light-view', JSON.stringify({
        sort: sortMode,
        filters: Array.from(activeFilters),
      }));
    } catch (e) {}
  }

  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if ((settings.theme || 'system') === 'system') applyTheme();
    });
  } catch (e) {}

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
    route = parseRoute();
    const onSettings = route.name === 'settings';
    document.getElementById('view-grid').classList.toggle('hidden', onSettings);
    document.getElementById('view-settings').classList.toggle('hidden', !onSettings);
    document.getElementById('btn-settings').classList.toggle('active', onSettings);
    if (onSettings) {
      closeDetail(true);
      loadSettingsPage();
      return;
    }
    if (route.name === 'port') {
      selectedPort = route.port;
      if (currentData) render();
    }
  }

  window.addEventListener('hashchange', applyRoute);

  document.getElementById('filter-chips').addEventListener('click', e => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    const f = chip.dataset.filter;
    if (f === 'all') {
      activeFilters = new Set(['all']);
      document.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.filter === 'all'));
    } else {
      activeFilters.delete('all');
      chip.classList.toggle('active');
      if (chip.classList.contains('active')) activeFilters.add(f);
      else activeFilters.delete(f);
      if (activeFilters.size === 0) {
        activeFilters.add('all');
        document.querySelector('.chip[data-filter="all"]').classList.add('active');
      } else {
        document.querySelector('.chip[data-filter="all"]').classList.remove('active');
      }
    }
    saveView();
    render();
  });

  sortSelect.addEventListener('change', e => {
    sortMode = e.target.value;
    saveView();
    render();
  });

  searchInput.addEventListener('input', e => {
    const val = e.target.value.trim();
    searchTerm = val.toLowerCase();
    searchPortNum = /^\d+$/.test(val) ? parseInt(val, 10) : null;
    searchInput.classList.toggle('search-active', !!val);
    render();
  });

  rangeStartInput.addEventListener('change', updateRange);
  rangeEndInput.addEventListener('change', updateRange);

  function updateRange() {
    const s = parseInt(rangeStartInput.value, 10);
    const e = parseInt(rangeEndInput.value, 10);
    if (s >= 1 && s <= 65535) rangeStart = s;
    if (e >= 1 && e <= 65535 && e >= rangeStart) rangeEnd = e;
    tick();
  }

  document.getElementById('btn-refresh').addEventListener('click', () => { tick(); });

  document.getElementById('btn-add').addEventListener('click', () => {
    document.getElementById('add-modal').classList.remove('hidden');
    document.getElementById('add-port').focus();
  });
  document.getElementById('add-cancel').addEventListener('click', () => {
    document.getElementById('add-modal').classList.add('hidden');
  });
  document.getElementById('add-confirm').addEventListener('click', addManualPort);

  document.getElementById('btn-unhide').addEventListener('click', () => {
    if (meta.hidden_unlock_required && !hiddenUnlock) {
      document.getElementById('unhide-modal').classList.remove('hidden');
      document.getElementById('unhide-password').focus();
      return;
    }
    showHidden = !showHidden;
    document.getElementById('btn-unhide').classList.toggle('active', showHidden);
    tick();
  });
  document.getElementById('unhide-cancel').addEventListener('click', () => {
    document.getElementById('unhide-modal').classList.add('hidden');
  });
  document.getElementById('unhide-confirm').addEventListener('click', unlockHidden);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal').forEach(function (m) { m.classList.add('hidden'); });
      if (route.name === 'settings') {
        location.hash = '#/';
        return;
      }
      closeDetail();
      return;
    }
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (route.name === 'settings') return;
    e.preventDefault();
    searchInput.focus();
  });

  document.querySelectorAll('.modal').forEach(m => {
    m.addEventListener('click', e => { if (e.target === m) m.classList.add('hidden'); });
  });

  document.addEventListener('click', e => {
    if (route.name === 'settings') return;
    if (!detailPanel.contains(e.target) && !e.target.closest('.port-cell')) {
      closeDetail();
    }
  });

  document.getElementById('settings-form').addEventListener('submit', function (e) {
    e.preventDefault();
    saveSettingsPage();
  });

  document.getElementById('settings-fields').addEventListener('change', function (e) {
    const field = e.target && e.target.name;
    if (field === 'theme' || field === 'grid_density') {
      if (field === 'theme') settings.theme = e.target.value;
      if (field === 'grid_density') settings.grid_density = e.target.value;
      applyAppearance();
    }
  });

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
      ['proc', 'Listen table'],
      ['docker', 'Docker'],
      ['compose', 'Compose'],
    ];
    host.innerHTML = items.map(function (pair) {
      const ok = !!scanners[pair[0]];
      return '<span class="pill' + (ok ? ' ok' : ' bad') + '" title="' + pair[1] + '">' +
        '<span class="pill-dot"></span>' + pair[1] + '</span>';
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
    rangeStart = settings.port_range_start;
    rangeEnd = settings.port_range_end;
    rangeStartInput.value = rangeStart;
    rangeEndInput.value = rangeEnd;
    applyAppearance();
  }

  async function fetchPorts() {
    try {
      const url = `/api/ports?range_start=${rangeStart}&range_end=${rangeEnd}&include_hidden=${showHidden}`;
      const res = await api(url);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return await res.json();
    } catch (err) {
      console.error('fetch error:', err);
      return null;
    }
  }

  function tick() {
    if (route.name === 'settings') return;
    fetchPorts().then(data => {
      if (data) { currentData = data; render(); }
    });
    fetchHealth();
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

  function loadSettingsPage() {
    fetchSettings().then(function (doc) {
      if (!doc) return;
      settingsDoc = doc;
      renderSettingsForm(doc);
    });
  }

  function renderSettingsForm(doc) {
    const values = doc.values || {};
    const fields = doc.fields || [];
    const host = document.getElementById('settings-fields');
    const lead = document.getElementById('settings-lead');
    const saveBtn = document.getElementById('settings-save');
    lead.textContent = doc.readonly
      ? 'Locked by PORT_LIGHT_SETTINGS_SOURCE=env (or SETTINGS_READONLY). Change values in Compose and recreate the container.'
      : 'Saved values live in the data volume and override Compose env for the same key. Set PORT_LIGHT_SETTINGS_SOURCE=env to make Compose the only source.';
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
      return '<fieldset class="settings-group"><legend>' + escapeHtml(GROUP_LABELS[g] || g) + '</legend>' + rows + '</fieldset>';
    }).join('');

    const envHost = document.getElementById('settings-env-only');
    const env = doc.env_only || {};
    envHost.innerHTML = [
      envRow('COMPOSE_SCAN_DIR', env.compose_scan_dir),
      envRow('CUSTOM_PORTS_FILE', env.custom_ports_file),
      envRow('PORT_LIGHT_DATA_DIR', env.data_dir),
      envRow('AUTH_USER / AUTH_PASSWORD', env.auth_required ? 'configured' : 'unset (open LAN)'),
      envRow('HIDDEN_UNLOCK_PASSWORD', env.hidden_unlock_required ? 'configured' : 'unset'),
      envRow('PORT_LIGHT_SETTINGS_SOURCE', doc.source),
    ].join('');
  }

  function envRow(label, value) {
    return '<div class="setting-row readonly"><div class="setting-copy"><span class="setting-label">' +
      escapeHtml(label) + '</span></div><code class="setting-value">' + escapeHtml(String(value == null ? '' : value)) +
      '</code></div>';
  }

  function renderField(f, value, readonly) {
    const origin = f.origin || 'default';
    const disabled = readonly ? ' disabled' : '';
    let control = '';
    if (f.type === 'bool') {
      control = '<input type="checkbox" name="' + f.key + '"' + (value ? ' checked' : '') + disabled + '>';
    } else if (f.type === 'choice') {
      const opts = (f.choices || []).map(function (c) {
        return '<option value="' + escapeHtml(c) + '"' + (c === value ? ' selected' : '') + '>' + escapeHtml(c) + '</option>';
      }).join('');
      control = '<select name="' + f.key + '" class="dropdown"' + disabled + '>' + opts + '</select>';
    } else if (f.type === 'int') {
      const min = f.min != null ? ' min="' + f.min + '"' : '';
      const max = f.max != null ? ' max="' + f.max + '"' : '';
      control = '<input type="number" name="' + f.key + '" value="' + escapeHtml(String(value)) + '"' + min + max + disabled + '>';
    } else {
      control = '<input type="text" name="' + f.key + '" value="' + escapeHtml(String(value || '')) +
        '" placeholder="optional"' + disabled + '>';
    }
    return '<label class="setting-row">' +
      '<span class="setting-copy"><span class="setting-label">' + escapeHtml(f.label) +
      '</span><span class="origin-tag" title="Source">' + escapeHtml(origin) + '</span>' +
      '<span class="field-help">' + escapeHtml(f.help) + ' <code>' + escapeHtml(f.env) + '</code></span></span>' +
      control + '</label>';
  }

  async function saveSettingsPage() {
    const status = document.getElementById('settings-status');
    const form = document.getElementById('settings-form');
    const patch = {};
    (settingsDoc && settingsDoc.fields ? settingsDoc.fields : []).forEach(function (f) {
      const el = form.elements[f.key];
      if (!el || el.disabled) return;
      if (f.type === 'bool') patch[f.key] = el.checked;
      else if (f.type === 'int') patch[f.key] = parseInt(el.value, 10);
      else patch[f.key] = el.value;
    });
    status.textContent = 'Saving…';
    const res = await api('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      status.textContent = body.detail || ('HTTP ' + res.status);
      return;
    }
    applyServerSettings(body);
    renderSettingsForm(body);
    setupRefresh();
    status.textContent = 'Saved.';
  }

  fetchMeta()
    .then(fetchSettings)
    .then(function (doc) {
      if (doc) applyServerSettings(doc);
    })
    .then(function () {
      applyRoute();
      setupRefresh();
    });

  function render() {
    if (!currentData) return;
    renderSummary(currentData.summary);
    renderGrid(currentData.ports);
    if (selectedPort !== null) {
      const entry = currentData.ports.find(p => p.port === selectedPort);
      if (entry) renderDetail(entry);
      else if (route.name !== 'port') closeDetail(true);
    }
  }

  function renderSummary(s) {
    summary.innerHTML = `
      <span class="stat"><span class="dot used"></span> In Use: ${s.used}</span>
      <span class="stat"><span class="dot configured"></span> Configured: ${s.configured}</span>
      <span class="stat"><span class="dot free"></span> Free: ${s.free}</span>
      ${s.hidden > 0 ? `<span class="stat"><span class="dot hidden"></span> Hidden: ${s.hidden}${s.hidden_locked ? ' (locked)' : ''}</span>` : ''}
    `;
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
    const allPortNums = new Set(ports.map(p => p.port));
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
        const entry = ports.find(x => x.port === p);
        if (entry) result.unshift(entry);
      }
    }
    for (let p = hitPort + 1; p <= hitPort + 50 && afterFree < 3; p++) {
      if (!allPortNums.has(p)) {
        result.push({ port: p, status: 'free', _synthetic: true, known_service: getKnownForFree(p) });
        afterFree++;
      } else {
        const entry = ports.find(x => x.port === p);
        if (entry) result.push(entry);
      }
    }

    if (hitExists) {
      const hit = ports.find(p => p.port === hitPort);
      result.push(hit);
    }

    result.sort((a, b) => a.port - b.port);
    const seen = new Set();
    return result.filter(p => {
      if (seen.has(p.port)) return false;
      seen.add(p.port);
      return true;
    });
  }

  function getKnownForFree(port) {
    if (currentData && currentData.ports) {
      const found = currentData.ports.find(p => p.port === port);
      if (found && found.known_service) return found.known_service;
    }
    return null;
  }

  function matchesFilter(p) {
    if (activeFilters.has('all')) return true;

    let matched = false;
    for (const f of activeFilters) {
      switch (f) {
        case 'running':
          if (p.containers && p.containers.some(c => c.status === 'running')) matched = true;
          break;
        case 'used':
          if (p.status === 'used') matched = true;
          break;
        case 'configured':
          if (p.status === 'configured') matched = true;
          break;
        case 'system':
          if (p.source_type === 'system' || (p.known_service && p.known_service.category === 'system')) matched = true;
          break;
        case 'docker':
          if (p.source_type === 'docker' || (p.containers && p.containers.length > 0)) matched = true;
          break;
        case 'access':
          if (p.known_service && p.known_service.is_access_port) matched = true;
          break;
        case 'udp':
          if ((p.protocol || '').indexOf('udp') !== -1) matched = true;
          break;
        case 'localhost':
          if (p.bind_scope === 'localhost') matched = true;
          break;
        case 'hidden':
          if (p.is_hidden) matched = true;
          break;
      }
    }
    return matched;
  }

  function sortPorts(arr) {
    switch (sortMode) {
      case 'port-desc': return arr.sort((a, b) => b.port - a.port);
      case 'name-asc':
        return arr.sort((a, b) => (getCellLabel(a) || '~').localeCompare(getCellLabel(b) || '~'));
      case 'name-desc':
        return arr.sort((a, b) => (getCellLabel(b) || '~').localeCompare(getCellLabel(a) || '~'));
      case 'status':
        return arr.sort((a, b) => {
          const order = { used: 0, configured: 1, free: 2 };
          return (order[a.status] || 9) - (order[b.status] || 9) || a.port - b.port;
        });
      default: return arr.sort((a, b) => a.port - b.port);
    }
  }

  function renderGrid(ports) {
    let displayPorts;

    if (searchPortNum !== null) {
      displayPorts = buildSearchContext(ports, searchPortNum);
    } else {
      displayPorts = ports.filter(p => {
        if (p.port < rangeStart || p.port > rangeEnd) return false;
        if (!showHidden && p.is_hidden) return false;
        if (!matchesFilter(p)) return false;
        if (searchTerm) {
          const haystack = [
            String(p.port), p.process || '', p.manual_label || '',
            p.known_service ? p.known_service.name : '',
            p.known_service ? p.known_service.description : '',
            ...(p.containers || []).map(c => c.name + ' ' + (c.compose_project || '') + ' ' + (c.compose_service || '') + ' ' + c.image),
            ...(p.compose_configs || []).map(c => c.project_dir + ' ' + c.service_name + ' ' + c.compose_file),
            p.protocol || '', p.bind_scope || '', (p.ips || []).join(' '),
            ...(p.urls || []),
          ].join(' ').toLowerCase();
          if (!haystack.includes(searchTerm)) return false;
        }
        return true;
      });
    }

    displayPorts = sortPorts([...displayPorts]);

    if (displayPorts.length === 0) {
      grid.innerHTML = '<div class="loading">No ports match the current filter.</div>';
      return;
    }

    grid.innerHTML = displayPorts.map(p => {
      let cls = p.status === 'used' ? 'used' : p.status === 'configured' ? 'configured' : 'free';
      if (p.is_hidden) cls = 'hidden';
      const conflict = p.conflict ? ' conflict' : '';
      const selected = p.port === selectedPort ? ' selected' : '';
      const isSearchHit = searchPortNum !== null && p.port === searchPortNum;
      const searchHit = isSearchHit ? ' search-hit' : '';
      const searchNear = searchPortNum !== null && !isSearchHit ? ' search-near' : '';
      const label = getCellLabel(p);
      const labelText = label ? `<div class="port-label">${escapeHtml(label)}</div>` : '';

      let statusText = '';
      if (settings.show_status_text) {
        const st = p.status === 'used' ? 'USE' : p.status === 'configured' ? 'CFG' : '';
        if (st) statusText = `<div class="status-text">${st}</div>`;
      }

      let accessBadge = '';
      if (settings.show_access_badge && p.known_service && p.known_service.is_access_port) {
        accessBadge = '<div class="access-badge">🔓</div>';
      }

      let protoBadge = '';
      if (settings.show_protocol_badge && p.protocol && p.protocol !== 'tcp') {
        protoBadge = `<div class="proto-badge">${escapeHtml(p.protocol)}</div>`;
      }

      return `
        <div class="port-cell ${cls}${conflict}${selected}${searchHit}${searchNear}"
             data-port="${p.port}"
             title="${escapeHtml([p.port, p.protocol, p.bind_scope, label].filter(Boolean).join(' · '))}">
          <div class="port-num">${p.port}</div>
          ${labelText}
          <div class="indicator"></div>
          ${statusText}${accessBadge}${protoBadge}
        </div>
      `;
    }).join('');

    grid.querySelectorAll('.port-cell').forEach(el => {
      el.addEventListener('click', () => {
        const port = parseInt(el.dataset.port, 10);
        selectedPort = port;
        location.hash = '#/port/' + port;
        const entry = displayPorts.find(p => p.port === port);
        if (entry) renderDetail(entry);
        if (settings.copy_on_click) {
          navigator.clipboard.writeText(String(port)).then(() => {
            showCopyToast(el);
          }).catch(() => {});
        }
      });
    });
  }

  function showCopyToast(cell) {
    cell.querySelectorAll('.copy-toast').forEach(t => t.remove());
    const toast = document.createElement('div');
    toast.className = 'copy-toast';
    toast.textContent = 'Copied';
    cell.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    clearTimeout(cell._copyToastTimer);
    cell._copyToastTimer = setTimeout(() => {
      toast.classList.remove('show');
      toast.classList.add('hide');
      setTimeout(() => toast.remove(), 400);
    }, 900);
  }

  function renderDetail(p) {
    detailPanel.classList.remove('hidden');
    const statusIcon = p.status === 'used' ? '🔵' : p.status === 'configured' ? '🟡' : '🟢';
    let html = `
      <button class="close-btn" onclick="location.hash='#/'">✕</button>
      <h3>${statusIcon} Port ${p.port}</h3>
    `;

    html += `<div class="row"><span class="key">Status</span><span class="tag ${p.status}">${p.status}</span></div>`;
    html += `<div class="row"><span class="key">Source</span><span class="val">${escapeHtml(p.source_type || 'unknown')}</span></div>`;
    if (p.protocol) html += `<div class="row"><span class="key">Protocol</span><span class="val">${escapeHtml(p.protocol)}</span></div>`;
    if (p.ip) html += `<div class="row"><span class="key">Bind</span><span class="val">${escapeHtml((p.ips && p.ips.join(', ')) || p.ip)}${p.bind_scope ? ' (' + p.bind_scope + ')' : ''}</span></div>`;
    if (p.process) html += `<div class="row"><span class="key">Process</span><span class="val">${escapeHtml(p.process)}</span></div>`;
    if (p.pid) html += `<div class="row"><span class="key">PID</span><span class="val">${p.pid}</span></div>`;

    if (p.urls && p.urls.length > 0) {
      html += '<div class="section-title">Open</div>';
      for (const u of p.urls) {
        html += `<div class="row"><span class="key">URL</span><span class="val"><a class="detail-link" href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">${escapeHtml(u)}</a></span></div>`;
      }
    }

    if (p.known_service) {
      html += `<div class="info-box"><span class="info-name">${escapeHtml(p.known_service.name)}</span> — ${escapeHtml(p.known_service.description)}</div>`;
      if (p.known_service.is_access_port !== undefined) {
        const isAccess = p.known_service.is_access_port;
        html += `<div class="info-box access-box"><span class="info-name">${isAccess ? '🔓 Access Port' : '🔒 Internal Port'}</span>`;
        html += ` — ${isAccess ? 'Users connect to this port directly (web UI, SSH, etc.)' : 'Internal service — not accessed directly'}</div>`;
      }
    }

    if (p.conflict) {
      html += `<div class="info-box conflict-box"><span class="info-name">⚠ Port Conflict</span> — Declared in ${p.compose_configs.length} compose files.</div>`;
    }

    if (p.containers && p.containers.length > 0) {
      html += '<div class="section-title">Containers</div>';
      for (const c of p.containers) {
        const tag = c.status === 'running' ? 'running' : 'exited';
        html += `
          <div class="row"><span class="key">${escapeHtml(c.name)}</span><span class="tag ${tag}">${c.status}</span></div>
          <div class="row"><span class="key">Image</span><span class="val" style="font-size:0.75rem">${escapeHtml(c.image)}</span></div>
          ${c.compose_project ? `<div class="row"><span class="key">Project</span><span class="val">${escapeHtml(c.compose_project)}</span></div>` : ''}
          ${c.compose_service ? `<div class="row"><span class="key">Service</span><span class="val">${escapeHtml(c.compose_service)}</span></div>` : ''}
          ${c.network_mode === 'host' ? '<div class="row"><span class="key">Network</span><span class="val">host</span></div>' : ''}
        `;
      }
    }

    if (p.compose_configs && p.compose_configs.length > 0) {
      html += '<div class="section-title">Compose Configs</div>';
      for (const cc of p.compose_configs) {
        html += `
          <div class="row"><span class="key">Project</span><span class="val">${escapeHtml(cc.project_dir)}</span></div>
          <div class="row"><span class="key">Service</span><span class="val">${escapeHtml(cc.service_name)}</span></div>
          <div class="row"><span class="key">File</span><span class="val" style="font-size:0.75rem">${escapeHtml(cc.compose_file)}</span></div>
          ${cc.container_port ? `<div class="row"><span class="key">Container Port</span><span class="val">${cc.container_port}</span></div>` : ''}
        `;
      }
    }

    if (p.manual_label) {
      html += `<div class="info-box"><span class="info-name">Manual Entry</span> — ${escapeHtml(p.manual_label)}</div>`;
    }

    html += '<div class="action-row">';
    if (p.is_hidden) {
      html += `<button class="btn-unhide" onclick="window._portLightUnhide(${p.port})">Unhide</button>`;
    } else {
      html += `<button class="btn-hide" onclick="window._portLightHide(${p.port})">Hide from grid</button>`;
    }
    if (p.manual_label || p.source_type === 'manual') {
      html += `<button class="btn-delete" onclick="window._portLightDeleteManual(${p.port})">Delete</button>`;
    }
    html += '</div>';

    detailContent.innerHTML = html;
  }

  function closeDetail(skipHash) {
    detailPanel.classList.add('hidden');
    selectedPort = null;
    if (!skipHash && route.name === 'port') location.hash = '#/';
  }

  window._portLightHide = async function(port) {
    await api(`/api/hidden/${port}`, { method: 'POST' });
    tick();
  };
  window._portLightUnhide = async function(port) {
    await api(`/api/hidden/${port}`, { method: 'DELETE' });
    tick();
  };
  window._portLightDeleteManual = async function(port) {
    await api(`/api/manual-ports/${port}`, { method: 'DELETE' });
    tick();
  };

  async function addManualPort() {
    const port = parseInt(document.getElementById('add-port').value, 10);
    const label = document.getElementById('add-label').value.trim();
    if (!port || port < 1 || port > 65535) return;

    await api('/api/manual-ports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port, label, machine: 'localhost' }),
    });

    document.getElementById('add-modal').classList.add('hidden');
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
    const data = await fetchPorts();
    if (!data) return;
    if (data.summary && data.summary.hidden_locked) {
      hiddenUnlock = '';
      sessionStorage.removeItem('port-light-hidden-unlock');
      showHidden = false;
      const input = document.getElementById('unhide-password');
      input.value = '';
      input.placeholder = 'Wrong password — try again';
      input.style.borderColor = 'var(--danger)';
      return;
    }
    currentData = data;
    document.getElementById('unhide-modal').classList.add('hidden');
    document.getElementById('unhide-password').value = '';
    document.getElementById('btn-unhide').classList.add('active');
    render();
  }

  function escapeHtml(text) {
    if (text === 0) return '0';
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

})();
