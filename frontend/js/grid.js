/* Grid view: summary bar, host columns, occupancy cells, filters/sort/search. */

import { S, CARD_FIELD_KEYS } from './state.js?v=77';
import { t, tx, collate, escapeHtml, safeHref } from './text.js?v=77';
import { KIND_MATCHERS } from './kinds.js?v=77';
import { isLease } from './leases.js?v=77';
import { appEl, grid, hostBoards, hostSwitcher, summary, detailPanel, searchInput, unhideBtn, syncHeaderHeight } from './dom.js?v=77';
import { hasPeers, listedHosts, hostById, hostName, dataForHost, portApiUrl } from './hosts.js?v=77';
import { api } from './api.js?v=77';


  export function syncFilterUI() {
    document.querySelectorAll('#filters .chip').forEach(function (c) {
      const on = S.kindFilters.has(c.dataset.filter);
      c.classList.toggle('active', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  export function syncHiddenButton() {
    unhideBtn.classList.toggle('active', S.showHidden);
    unhideBtn.setAttribute('aria-pressed', S.showHidden ? 'true' : 'false');
    const title = t(S.showHidden ? 'action.hiddenVisible' : 'action.showHidden');
    unhideBtn.title = title;
    unhideBtn.setAttribute('aria-label', title);
  }

  export function snapshotGridFocus() {
    const el = document.activeElement;
    if (!el || !el.classList || !el.classList.contains('port-cell')) return null;
    return { port: el.getAttribute('data-port'), host: el.getAttribute('data-host') || 'local' };
  }

  export function applyPendingGridFocus() {
    if (S.route.name === 'settings') {
      S.pendingGridFocus = null;
      return;
    }
    const pending = S.pendingGridFocus;
    S.pendingGridFocus = null;
    if (!pending) return;
    const port = pending.port || pending;
    const hostId = pending.hostId || 'local';
    const root = document.getElementById('host-grid-' + hostId) || grid;
    const cell = root.querySelector('.port-cell[data-port="' + port + '"]');
    if (cell) cell.focus({ preventScroll: true });
  }

  export function gridRootFrom(el) {
    if (!el || !el.closest) return grid;
    return el.closest('.host-grid, #grid') || grid;
  }

  export function gridCells(root) {
    root = root || gridRootFrom(document.activeElement);
    return Array.prototype.slice.call(root.querySelectorAll('.port-cell'));
  }

  export function cellsByRow(cells) {
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

  export function moveGridFocus(key, root) {
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

  export function freeStub(port) {
    return {
      port: port,
      status: 'free',
      source_type: 'unknown',
      known_service: S.knownCache[port] || null,
      containers: [],
      compose_configs: [],
      urls: [],
      conflict: false,
      is_hidden: false,
    };
  }

  export function pendingStub(port) {
    return {
      port: port,
      status: 'free',
      source_type: 'unknown',
      known_service: S.knownCache[port] || null,
      containers: [],
      compose_configs: [],
      urls: [],
      conflict: false,
      is_hidden: false,
      _pending: true,
    };
  }

  export function getKnownForFree(port) {
    if (Object.prototype.hasOwnProperty.call(S.knownCache, port)) return S.knownCache[port];
    const found = portFromList(port);
    if (found && found.known_service) return found.known_service;
    return null;
  }

  export function prefetchKnown(port) {
    if (S.knownCache[port] !== undefined || S.knownInflight[port]) return;
    S.knownInflight[port] = true;
    api('/api/known-ports/' + port).then(function (res) {
      if (res.status === 404) {
        S.knownCache[port] = null;
        return null;
      }
      if (!res.ok) {
        S.knownCache[port] = null;
        return null;
      }
      return res.json();
    }).then(function (body) {
      delete S.knownInflight[port];
      if (!body) return;
      S.knownCache[port] = {
        name: body.name,
        description: body.description,
        category: body.category,
        is_access_port: body.is_access_port,
      };
      if (S.currentData && S.searchPortNum !== null && !S.knownRenderFrame) {
        S.knownRenderFrame = requestAnimationFrame(function () {
          S.knownRenderFrame = 0;
          if (S.currentData && S.searchPortNum !== null) render();
        });
      }
    }).catch(function () {
      S.knownCache[port] = null;
      delete S.knownInflight[port];
    });
  }

  export function hiddenOccupancy(port, dataCtx) {
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

  export function probeLockedHit(port, hostId) {
    const key = (hostId || 'local') + ':' + port;
    if (S.lockedHitCache[key] || S.lockedHitInflight[key]) return;
    S.lockedHitInflight[key] = true;
    api(portApiUrl(hostId || 'local', port)).then(function (res) {
      S.lockedHitCache[key] = res.status === 404 ? 'locked' : 'free';
      delete S.lockedHitInflight[key];
      if (S.searchPortNum === port) render();
    }).catch(function () {
      delete S.lockedHitInflight[key];
    });
  }

  export function getCellLabel(p) {
    if (p.containers && p.containers.length > 0) return p.containers[0].name;
    if (p.process) return p.process;
    if (p.manual_label) return p.manual_label;
    if (p.compose_configs && p.compose_configs.length > 0) return p.compose_configs[0].service_name;
    if (p.known_service) return p.known_service.name;
    return '';
  }

  export function buildSearchContext(ports, hitPort, dataCtx, hostId) {
    dataCtx = dataCtx || S.currentData;
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
        const kind = S.lockedHitCache[(hostId || 'local') + ':' + hitPort];
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

  export function matchesFilter(p) {
    if (S.statusFilter === 'used' && p.status !== 'used') return false;
    if (S.statusFilter === 'configured' && p.status !== 'configured') return false;
    const kinds = Array.from(S.kindFilters);
    for (let i = 0; i < kinds.length; i++) {
      const match = KIND_MATCHERS[kinds[i]];
      if (match && !match(p)) return false;
    }
    return true;
  }

  export function sortPorts(arr) {
    switch (S.sortMode) {
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

  export function portFromList(port, hostId) {
    const data = dataForHost(hostId || S.selectedHostId || 'local');
    if (!data || !data.ports) return null;
    return data.ports.find(function (p) { return p.port === port; }) || null;
  }

  export function renderSummary(s) {
    function toggle(active, attrs, dot, n, label) {
      return '<button type="button" class="stat' + (active ? ' active' : '') + '" ' + attrs +
        ' aria-pressed="' + (active ? 'true' : 'false') + '">' +
        '<span class="dot ' + dot + '"></span><span class="num">' + n + '</span> ' + label + '</button>';
    }
    let html = '';
    if (hasPeers()) {
      html += '<span class="legend-host">' + escapeHtml(t('hosts.legendHost', { name: hostName(S.focusHostId) })) + '</span>';
    }
    html += toggle(S.statusFilter === 'used', 'data-status="used"', 'used', s.used, t('legend.inUse')) +
      toggle(S.statusFilter === 'configured', 'data-status="configured"', 'configured', s.configured, t('legend.configured')) +
      '<span class="stat is-static"><span class="dot free"></span><span class="num">' + s.free + '</span> ' + t('legend.free') + '</span>';
    if (s.hidden > 0) {
      html += toggle(S.kindFilters.has('hidden'), 'data-kind="hidden"', 'hidden', s.hidden,
        t('legend.hidden') + (s.hidden_locked ? ' (' + t('legend.locked') + ')' : ''));
    }
    summary.innerHTML = html;
  }

  export function renderHostSwitcher() {
    if (!hostSwitcher) return;
    hostSwitcher.innerHTML = listedHosts().map(function (h) {
      const on = h.id === S.focusHostId;
      return '<button type="button" class="host-chip' + (on ? ' active' : '') +
        '" role="tab" aria-selected="' + (on ? 'true' : 'false') +
        '" data-host-switch="' + escapeHtml(h.id) + '">' +
        escapeHtml(h.name || h.id) + '</button>';
    }).join('');
  }

  export function renderHostBoards() {
    if (!hostBoards) return;
    const restore = snapshotGridFocus();
    const hosts = listedHosts();
    hostBoards.innerHTML = hosts.map(function (h) {
      return '<article class="host-board' + (h.id === S.focusHostId ? ' is-active' : '') +
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
      const map = S.hostMaps[h.id] || {};
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
        if (retry) retry.disabled = !!S.hostRetrying[h.id];
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

  export function showCopyToast(cell) {
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

  export function syncAddButton() {
    const btn = document.getElementById('btn-add');
    if (!btn) return;
    const local = !hasPeers() || S.focusHostId === 'local';
    btn.disabled = !local;
    const title = local ? t('action.add') : t('hosts.localOnly');
    btn.title = title;
    btn.setAttribute('aria-label', title);
  }

  export function render() {
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
      const focused = dataForHost(S.focusHostId);
      if (focused && focused.summary) {
        S.currentData = focused;
        renderSummary(focused.summary);
      } else if (S.currentData && S.currentData.summary) {
        renderSummary(S.currentData.summary);
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
      if (!S.currentData) return;
      renderSummary(S.currentData.summary);
      renderGrid(S.currentData.ports, grid, S.currentData, 'local');
    }

  }

  export function renderGrid(ports, rootEl, dataCtx, hostId, restore) {
    rootEl = rootEl || grid;
    dataCtx = dataCtx || S.currentData;
    hostId = hostId || 'local';
    ports = ports || [];
    let displayPorts;

    if (S.searchPortNum !== null) {
      displayPorts = buildSearchContext(ports, S.searchPortNum, dataCtx, hostId).filter(function (p) {
        if (p.port === S.searchPortNum) return true;
        return matchesFilter(p);
      });
    } else {
      displayPorts = ports.filter(function (p) {
        if (!S.searchTerm && (p.port < S.rangeStart || p.port > S.rangeEnd)) return false;
        if (!S.showHidden && p.is_hidden) return false;
        if (!matchesFilter(p)) return false;
        if (S.searchTerm) {
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
          if (!haystack.includes(S.searchTerm)) return false;
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
      return p.port === S.selectedPort && (S.selectedHostId || 'local') === hostId;
    };

    if (displayPorts.length === 0) {
      const inRange = (dataCtx.ports || []).filter(function (p) {
        return p.port >= S.rangeStart && p.port <= S.rangeEnd && (S.showHidden || !p.is_hidden);
      });
      const noFacet = !S.searchTerm && S.searchPortNum === null && S.kindFilters.size === 0 && S.statusFilter === 'all';
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
      const isSearchHit = S.searchPortNum !== null && p.port === S.searchPortNum;
      const searchHit = isSearchHit ? ' search-hit' : '';
      const searchNear = S.searchPortNum !== null && !isSearchHit ? ' search-near' : '';
      const label = getCellLabel(p);
      const labelText = label ? '<div class="port-label">' + escapeHtml(label) + '</div>' : '';
      const statusLabel = lockedHit ? t('legend.locked') : t('status.' + p.status);
      const statusText = S.settings.show_status_text && !lockedHit && p.status !== 'free'
        ? '<span class="status-text">' + escapeHtml(t('status.' + p.status)) + '</span>' : '';
      const accessBadge = S.settings.show_access_badge && p.known_service && p.known_service.is_access_port
        ? '<span class="access-badge">' + escapeHtml(t('grid.web')) + '</span>' : '';
      const protoBadge = S.settings.show_protocol_badge && p.protocol && p.protocol !== 'tcp'
        ? '<span class="proto-badge">' + escapeHtml(p.protocol) + '</span>' : '';
      const leaseBadge = isLease(p)
        ? '<span class="lease-badge" role="img" aria-label="' + escapeHtml(t('grid.leaseBadge')) +
          '" title="' + escapeHtml(t('grid.leaseBadge')) + '"></span>'
        : '';

      const ariaParts = [String(p.port), statusLabel, label, p.protocol].filter(Boolean);
      return '<button type="button" class="port-cell ' + cls + conflict + selected + searchHit + searchNear + '"' +
        ' data-port="' + p.port + '" data-host="' + escapeHtml(hostId) + '"' +
        ' aria-label="' + escapeHtml(ariaParts.join(', ')) + '"' +
        ' aria-selected="' + (cellSelected(p) ? 'true' : 'false') + '"' +
        ' title="' + escapeHtml([p.port, p.protocol, p.bind_scope, label].filter(Boolean).join(' · ')) + '">' +
        '<div class="port-num">' + p.port + '</div>' +
        labelText +
        '<div class="cell-meta"><span class="indicator"></span>' + protoBadge + accessBadge + leaseBadge + statusText + '</div>' +
        '</button>';
    }).join('');

    if (restorePort && (!restoreHost || restoreHost === hostId)) {
      const again = rootEl.querySelector('.port-cell[data-port="' + restorePort + '"]');
      if (again) again.focus({ preventScroll: true });
    }
  }

  export function renderScanners(scanners, hostEl, data) {
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
