/* Data layer: host catalog helpers, URL builders, fetchers, and the
   occupancy poll loop. */

import { S } from './state.js?v=64';
import { grid, hostBoards, markRefreshed, setSyncError } from './dom.js?v=64';
import { t, escapeHtml } from './text.js?v=64';
import { modalOpen } from './modal.js?v=64';
import { render } from './grid.js?v=64';


  export function hasPeers() {
    return !!(S.hostCatalog.peers && S.hostCatalog.peers.length);
  }
  export function listedHosts() {
    const local = S.hostCatalog.local || { id: 'local', name: '', local: true };
    return [local].concat(S.hostCatalog.peers || []);
  }
  export function hostById(id) {
    const hosts = listedHosts();
    for (let i = 0; i < hosts.length; i++) {
      if (hosts[i].id === id) return hosts[i];
    }
    return null;
  }
  export function hostName(id) {
    const row = hostById(id);
    return (row && row.name) || id || t('hosts.thisMachine');
  }
  export function occupancyUrl(hostId) {
    const q = 'range_start=' + S.rangeStart + '&range_end=' + S.rangeEnd + '&include_hidden=' + S.showHidden;
    if (!hasPeers() && hostId === 'local') return '/api/ports?' + q;
    return '/api/hosts/' + encodeURIComponent(hostId) + '/ports?' + q;
  }
  export function portApiUrl(hostId, port) {
    if (!hasPeers() && hostId === 'local') return '/api/ports/' + port;
    return '/api/hosts/' + encodeURIComponent(hostId || 'local') + '/ports/' + port;
  }
  export function gridHash(hostId) {
    hostId = hostId || S.focusHostId;
    if (!hasPeers() || hostId === 'local') return '#/';
    return '#/h/' + hostId;
  }
  export function portHash(hostId, port) {
    hostId = hostId || S.selectedHostId || S.focusHostId;
    if (!hasPeers() || hostId === 'local') return '#/port/' + port;
    return '#/h/' + hostId + '/port/' + port;
  }
  export function dataForHost(hostId) {
    if (!hasPeers() || hostId === 'local') {
      if (S.hostMaps.local && S.hostMaps.local.data) return S.hostMaps.local.data;
      return S.currentData;
    }
    return S.hostMaps[hostId] && S.hostMaps[hostId].data;
  }
  export function fpSummary(s) {
    if (!s) return s;
    const copy = Object.assign({}, s);
    delete copy.stale;
    return copy;
  }
  export function occupancyFingerprint() {
    if (!hasPeers()) {
      if (!S.currentData) return '';
      return JSON.stringify({ ports: S.currentData.ports, summary: fpSummary(S.currentData.summary) });
    }
    return JSON.stringify(listedHosts().map(function (h) {
      const m = S.hostMaps[h.id] || {};
      return {
        id: h.id,
        ports: m.data && m.data.ports,
        summary: m.data && fpSummary(m.data.summary),
        error: m.error || '',
        scanners: m.scanners || null,
      };
    }));
  }
  export function apiHeaders(extra) {
    const headers = Object.assign({}, extra || {});
    if (S.hiddenUnlock) headers['X-Hidden-Unlock'] = S.hiddenUnlock;
    return headers;
  }
  export async function api(url, opts) {
    opts = opts || {};
    const res = await fetch(url, Object.assign({ credentials: 'same-origin' }, opts, {
      headers: apiHeaders(opts.headers),
    }));
    return res;
  }
  export async function fetchMeta() {
    try {
      const res = await api('/api/meta');
      if (res.ok) S.meta = await res.json();
    } catch (err) {
      console.error('meta error:', err);
    }
    const ver = document.getElementById('app-version');
    if (ver && S.meta.version) ver.textContent = 'v' + S.meta.version;
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
  export async function fetchHealth() {
    try {
      const res = await api('/api/health');
      if (!res.ok) return;
      const body = await res.json();
      renderScanners(body.scanners || {}, document.getElementById('scanner-pills'), S.currentData);
    } catch (err) {}
  }
  export async function fetchHostHealth(hostId) {
    const url = hostId === 'local' ? '/api/health' : '/api/hosts/' + encodeURIComponent(hostId) + '/health';
    try {
      const res = await api(url);
      if (!res.ok) return;
      const body = await res.json();
      if (!S.hostMaps[hostId]) S.hostMaps[hostId] = {};
      S.hostMaps[hostId].scanners = body.scanners || {};
    } catch (err) {}
  }
  export async function fetchHosts() {
    try {
      const res = await api('/api/hosts');
      if (!res.ok) return S.hostCatalog;
      const body = await res.json();
      S.hostCatalog = {
        local: body.local || { id: 'local', name: '', local: true },
        peers: Array.isArray(body.peers) ? body.peers : [],
        readonly: !!body.readonly,
      };
    } catch (err) {}
    if (!hostById(S.focusHostId)) S.focusHostId = 'local';
    return S.hostCatalog;
  }
  export async function fetchPorts(opts) {
    const isolated = !!(opts && opts.isolated);
    if (S.portsAbort) S.portsAbort.abort();
    const ac = new AbortController();
    if (!isolated) {
      S.portsAbort = ac;
      grid.setAttribute('aria-busy', 'true');
    }
    try {
      const url = '/api/ports?range_start=' + S.rangeStart + '&range_end=' + S.rangeEnd + '&include_hidden=' + S.showHidden;
      const headers = {};
      if (!isolated && S.portsEtag && S.portsEtagUrl === url) headers['If-None-Match'] = S.portsEtag;
      const res = await api(url, { signal: ac.signal, headers: headers });
      if (res.status === 304) return { ok: true, unchanged: true };
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const etag = res.headers.get('etag');
      const data = await res.json();
      if (!isolated) {
        S.portsEtag = etag || '';
        S.portsEtagUrl = url;
      } else if (data.summary && !data.summary.hidden_locked) {
        S.portsEtag = etag || '';
        S.portsEtagUrl = url;
      }
      return { ok: true, data: data };
    } catch (err) {
      if (err && err.name === 'AbortError') return { ok: false, stale: true };
      console.error('fetch error:', err);
      return { ok: false, stale: false };
    } finally {
      if (!isolated && S.portsAbort === ac) grid.setAttribute('aria-busy', 'false');
    }
  }
  export async function fetchHostOccupancy(hostId, opts) {
    const isolated = !!(opts && opts.isolated);
    if (S.hostAborts[hostId]) S.hostAborts[hostId].abort();
    const ac = new AbortController();
    S.hostAborts[hostId] = ac;
    const root = document.getElementById('host-grid-' + hostId) || (hostId === 'local' ? grid : null);
    if (!isolated && root) root.setAttribute('aria-busy', 'true');
    try {
      const url = occupancyUrl(hostId);
      const headers = {};
      const tag = S.hostEtags[hostId];
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
        S.hostEtags[hostId] = { etag: etag || '', url: url };
      }
      return { ok: true, data: data };
    } catch (err) {
      if (err && err.name === 'AbortError') return { ok: false, stale: true };
      console.error('fetch error:', err);
      return { ok: false, stale: false };
    } finally {
      if (!isolated && S.hostAborts[hostId] === ac && root) root.setAttribute('aria-busy', 'false');
    }
  }
  export async function loadAllOccupancy(opts) {
    const ids = listedHosts().map(function (h) { return h.id; });
    const results = await Promise.all(ids.map(function (id) {
      return fetchHostOccupancy(id, opts);
    }));
    let localOk = false;
    ids.forEach(function (id, i) {
      const result = results[i];
      if (!S.hostMaps[id]) S.hostMaps[id] = {};
      if (!result || result.stale) return;
      if (result.unchanged) {
        if (id === 'local') localOk = true;
        return;
      }
      if (result.ok && result.data) {
        S.hostMaps[id].data = result.data;
        S.hostMaps[id].error = '';
        if (id === 'local') localOk = true;
      } else {
        S.hostMaps[id].error = result.auth ? 'auth' : 'down';
      }
    });
    await Promise.all(ids.map(fetchHostHealth));
    if (localOk) {
      setSyncError(false);
      markRefreshed();
    } else {
      setSyncError(true);
    }
    const focused = dataForHost(S.focusHostId);
    if (focused) S.currentData = focused;
    else if (S.hostMaps.local && S.hostMaps.local.data) S.currentData = S.hostMaps.local.data;
    return S.currentData;
  }
  export async function loadPorts(opts) {
    if (hasPeers()) return loadAllOccupancy(opts);
    const result = await fetchPorts(opts);
    if (!result || result.stale) return null;
    if (result.unchanged) {
      setSyncError(false);
      markRefreshed();
      return S.currentData;
    }
    if (!result.ok || !result.data) {
      setSyncError(true);
      return null;
    }
    setSyncError(false);
    markRefreshed();
    return result.data;
  }
  export async function retryHost(hostId) {
    if (!hostId || S.hostRetrying[hostId]) return;
    S.hostRetrying[hostId] = true;
    const btn = hostBoards && hostBoards.querySelector('[data-host-retry="' + hostId + '"]');
    if (btn) btn.disabled = true;
    let shouldRender = false;
    try {
      const result = await fetchHostOccupancy(hostId);
      if (!S.hostMaps[hostId]) S.hostMaps[hostId] = {};
      if (result && !result.stale) {
        if (result.unchanged) {
          S.hostMaps[hostId].error = '';
        } else if (result.ok && result.data) {
          S.hostMaps[hostId].data = result.data;
          S.hostMaps[hostId].error = '';
        } else {
          S.hostMaps[hostId].error = result.auth ? 'auth' : 'down';
        }
      }
      if (!result || result.stale) return;
      await fetchHostHealth(hostId);
      S.occupancyKey = occupancyFingerprint();
      if (hostId === S.focusHostId && S.hostMaps[hostId].data) {
        S.currentData = S.hostMaps[hostId].data;
      }
      if (hostId === 'local') setSyncError(!!S.hostMaps[hostId].error);
      if (!S.hostMaps[hostId].error) markRefreshed();
      shouldRender = true;
    } finally {
      S.hostRetrying[hostId] = false;
    }
    if (shouldRender) render();
  }
  export function tick() {
    if (S.route.name === 'settings') return;
    if (modalOpen()) return;
    const wantHidden = S.showHidden;
    loadPorts().then(function (data) {
      if (S.route.name === 'settings') return;
      if (S.showHidden !== wantHidden) return;
      if (!hasPeers()) {
        if (!data) return;
        if (data === S.currentData && S.occupancyKey) return;
        S.currentData = data;
      } else if (data) {
        S.currentData = dataForHost(S.focusHostId) || data;
      } else if (!S.currentData) {
        return;
      }
      const lockedNow = !!(S.currentData && S.currentData.summary && S.currentData.summary.hidden_locked);
      if (lockedNow !== S.lastHiddenLocked) {
        S.lastHiddenLocked = lockedNow;
        Object.keys(S.lockedHitCache).forEach(function (k) { delete S.lockedHitCache[k]; });
        Object.keys(S.lockedHitInflight).forEach(function (k) { delete S.lockedHitInflight[k]; });
      }
      const key = occupancyFingerprint();
      if (key !== S.occupancyKey) {
        S.occupancyKey = key;
        render();
      }
    }).then(function () {
      if (!hasPeers()) fetchHealth();
    });
  }
  let eventStream = null;

  export function startEventStream() {
    if (!window.EventSource || eventStream) return;
    eventStream = new EventSource('/api/events');
    eventStream.addEventListener('refresh', function () {
      if (S.settings.auto_refresh && S.route.name !== 'settings' && !modalOpen()) tick();
    });
  }

  export function setupRefresh() {
    if (S.refreshTimer) { clearInterval(S.refreshTimer); S.refreshTimer = null; }
    if (!eventStream) startEventStream();
    if (S.settings.auto_refresh) {
      tick();
      S.refreshTimer = setInterval(tick, S.settings.refresh_ms || 5000);
    } else {
      tick();
    }
  }
