/* Port-Light frontend */

(function () {
  'use strict';

  const REFRESH_MS = 5000;
  let currentData = null;
  let activeFilters = new Set(['all']);
  let sortMode = 'port-asc';
  let searchTerm = '';
  let searchPortNum = null;
  let selectedPort = null;
  let rangeStart = 1;
  let rangeEnd = 9999;
  let showHidden = false;
  let settings = { statusText: false, accessBadge: true, autoRefresh: true, copyOnClick: true, theme: 'system' };
  let refreshTimer = null;
  let meta = { hidden_unlock_required: false, hidden_ports_withheld: false, version: '' };
  let hiddenUnlock = sessionStorage.getItem('port-light-hidden-unlock') || '';

  // DOM
  const grid = document.getElementById('grid');
  const summary = document.getElementById('summary');
  const detailPanel = document.getElementById('detail-panel');
  const detailContent = document.getElementById('detail-content');
  const searchInput = document.getElementById('search');
  const rangeStartInput = document.getElementById('range-start');
  const rangeEndInput = document.getElementById('range-end');
  const sortSelect = document.getElementById('sort-select');

  // Load settings from localStorage
  try {
    const saved = localStorage.getItem('port-light-settings');
    if (saved) settings = { ...settings, ...JSON.parse(saved) };
  } catch (e) {}
  document.getElementById('setting-status-text').checked = settings.statusText;
  document.getElementById('setting-access-badge').checked = settings.accessBadge;
  document.getElementById('setting-autorefresh').checked = settings.autoRefresh;
  document.getElementById('setting-copy-on-click').checked = settings.copyOnClick;
  if (!settings.theme) settings.theme = 'system';
  document.getElementById('setting-theme').value = settings.theme;
  applyTheme();

  function applyTheme() {
    var t = settings.theme || 'system';
    if (t === 'system') {
      t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    document.documentElement.setAttribute('data-theme', t);
  }
  try {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      if ((settings.theme || 'system') === 'system') applyTheme();
    });
  } catch (e) {}

  // ── Event listeners ──────────────────────────

  // Filter chips (multi-select)
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
      // If nothing selected, revert to all
      if (activeFilters.size === 0) {
        activeFilters.add('all');
        document.querySelector('.chip[data-filter="all"]').classList.add('active');
      } else {
        document.querySelector('.chip[data-filter="all"]').classList.remove('active');
      }
    }
    render();
  });

  sortSelect.addEventListener('change', e => { sortMode = e.target.value; render(); });

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

  // Action buttons
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

  document.getElementById('btn-settings').addEventListener('click', () => {
    document.getElementById('settings-modal').classList.remove('hidden');
  });
  document.getElementById('settings-close').addEventListener('click', () => {
    const m = document.getElementById('settings-modal');
    m.classList.add('hidden');
    settings.statusText = document.getElementById('setting-status-text').checked;
    settings.accessBadge = document.getElementById('setting-access-badge').checked;
    settings.autoRefresh = document.getElementById('setting-autorefresh').checked;
    settings.copyOnClick = document.getElementById('setting-copy-on-click').checked;
    settings.theme = document.getElementById('setting-theme').value;
    localStorage.setItem('port-light-settings', JSON.stringify(settings));
    applyTheme();
    setupRefresh();
    render();
  });
  document.getElementById('setting-theme').addEventListener('change', function () {
    settings.theme = document.getElementById('setting-theme').value;
    localStorage.setItem('port-light-settings', JSON.stringify(settings));
    applyTheme();
  });

  // Close modals on backdrop click
  document.querySelectorAll('.modal').forEach(m => {
    m.addEventListener('click', e => { if (e.target === m) m.classList.add('hidden'); });
  });

  // Click outside detail to close
  document.addEventListener('click', e => {
    if (!detailPanel.contains(e.target) && !e.target.closest('.port-cell')) {
      closeDetail();
    }
  });

  // ── Data fetch ───────────────────────────────

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
    fetchPorts().then(data => {
      if (data) { currentData = data; render(); }
    });
  }

  function setupRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    if (settings.autoRefresh) {
      tick();
      refreshTimer = setInterval(tick, REFRESH_MS);
    }
  }

  fetchMeta().then(() => setupRefresh());

  // ── Render ───────────────────────────────────

  function render() {
    if (!currentData) return;
    renderSummary(currentData.summary);
    renderGrid(currentData.ports);
    if (selectedPort !== null) {
      const entry = currentData.ports.find(p => p.port === selectedPort);
      if (entry) renderDetail(entry);
      else closeDetail();
    }
  }

  function renderSummary(s) {
    summary.innerHTML = `
      <span class="stat"><span class="dot used"></span> In Use: ${s.used}</span>
      <span class="stat"><span class="dot configured"></span> Configured: ${s.configured}</span>
      <span class="stat"><span class="dot free"></span> Free: ${s.free}</span>
      ${s.hidden > 0 ? `<span class="stat"><span class="dot hidden"></span> Hidden: ${s.hidden}${s.hidden_locked ? ' (locked)' : ''}</span>` : ''}
      <span class="stat" style="color:var(--text-dim)">Range: ${s.range_start}-${s.range_end}</span>
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

  // ── Smart search context ─────────────────────

  function buildSearchContext(ports, hitPort) {
    // If hitPort is free (not in ports list), show it as green + nearby free ports
    const allPortNums = new Set(ports.map(p => p.port));
    const hitExists = allPortNums.has(hitPort);
    const result = [];

    if (!hitExists) {
      // Port is free — show it as a synthetic free entry
      result.push({ port: hitPort, status: 'free', _synthetic: true, known_service: getKnownForFree(hitPort) });
    }

    // Gather context: 3 free ports before and after the hit
    let beforeFree = 0, afterFree = 0;
    for (let p = hitPort - 1; p >= Math.max(1, hitPort - 50) && beforeFree < 3; p--) {
      if (!allPortNums.has(p)) {
        result.unshift({ port: p, status: 'free', _synthetic: true, known_service: getKnownForFree(p) });
        beforeFree++;
      } else {
        // Include occupied port in context
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

    // If hit exists, add it
    if (hitExists) {
      const hit = ports.find(p => p.port === hitPort);
      result.push(hit);
    }

    // Sort by port number
    result.sort((a, b) => a.port - b.port);

    // Dedupe
    const seen = new Set();
    return result.filter(p => {
      if (seen.has(p.port)) return false;
      seen.add(p.port);
      return true;
    });
  }

  function getKnownForFree(port) {
    // Check if currentData has known_service for this port (from API)
    if (currentData && currentData.ports) {
      const found = currentData.ports.find(p => p.port === port);
      if (found && found.known_service) return found.known_service;
    }
    return null;
  }

  // ── Filtering ────────────────────────────────

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
        case 'hidden':
          if (p.is_hidden) matched = true;
          break;
      }
    }
    return matched;
  }

  // ── Sorting ──────────────────────────────────

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
      default: return arr.sort((a, b) => a.port - b.port); // port-asc
    }
  }

  // ── Grid render ──────────────────────────────

  function renderGrid(ports) {
    let displayPorts;

    // Smart search: if searching for a specific port number
    if (searchPortNum !== null) {
      displayPorts = buildSearchContext(ports, searchPortNum);
    } else {
      // Normal filtering
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

      // Status text
      let statusText = '';
      if (settings.statusText) {
        const st = p.status === 'used' ? 'USE' : p.status === 'configured' ? 'CFG' : '';
        if (st) statusText = `<div class="status-text">${st}</div>`;
      }

      // Access badge
      let accessBadge = '';
      if (settings.accessBadge && p.known_service && p.known_service.is_access_port) {
        accessBadge = '<div class="access-badge">🔓</div>';
      }

      return `
        <div class="port-cell ${cls}${conflict}${selected}${searchHit}${searchNear}"
             data-port="${p.port}"
             title="${escapeHtml([p.port, p.protocol, p.bind_scope, label].filter(Boolean).join(' · '))}">
          <div class="port-num">${p.port}</div>
          ${labelText}
          <div class="indicator"></div>
          ${statusText}${accessBadge}
        </div>
      `;
    }).join('');

    grid.querySelectorAll('.port-cell').forEach(el => {
      el.addEventListener('click', () => {
        const port = parseInt(el.dataset.port, 10);
        selectedPort = port;
        const entry = displayPorts.find(p => p.port === port);
        if (entry) renderDetail(entry);
        if (settings.copyOnClick) {
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

  // ── Detail panel ─────────────────────────────

  function renderDetail(p) {
    detailPanel.classList.remove('hidden');
    const statusIcon = p.status === 'used' ? '🔵' : p.status === 'configured' ? '🟡' : '🟢';
    let html = `
      <button class="close-btn" onclick="this.closest('#detail-panel').classList.add('hidden')">✕</button>
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

    // Known service
    if (p.known_service) {
      html += `<div class="info-box"><span class="info-name">${escapeHtml(p.known_service.name)}</span> — ${escapeHtml(p.known_service.description)}</div>`;

      // Access port info
      if (p.known_service.is_access_port !== undefined) {
        const isAccess = p.known_service.is_access_port;
        html += `<div class="info-box access-box"><span class="info-name">${isAccess ? '🔓 Access Port' : '🔒 Internal Port'}</span>`;
        html += ` — ${isAccess ? 'Users connect to this port directly (web UI, SSH, etc.)' : 'Internal service — not accessed directly'}</div>`;
      }
    }

    // Conflict
    if (p.conflict) {
      html += `<div class="info-box" style="background:rgba(240,136,62,0.06);border-color:rgba(240,136,62,0.2)"><span class="info-name" style="color:var(--conflict)">⚠ Port Conflict</span> — Declared in ${p.compose_configs.length} compose files.</div>`;
    }

    // Containers
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

    // Compose configs
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

    // Manual label
    if (p.manual_label) {
      html += `<div class="info-box"><span class="info-name">Manual Entry</span> — ${escapeHtml(p.manual_label)}</div>`;
    }

    // Action buttons
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

  function closeDetail() {
    detailPanel.classList.add('hidden');
    selectedPort = null;
  }

  // ── Actions (global for inline onclick) ──────

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

  // ── Add manual port ──────────────────────────

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

  // ── Unlock hidden ────────────────────────────

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

  // ── Utils ────────────────────────────────────

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

})();
