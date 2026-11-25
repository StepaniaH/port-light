/* Host catalog, occupancy selectors, and route URLs. */

import { S } from './state.js?v=131';
import { t } from './text.js?v=131';
import { usesFocusedFleet } from './fleet.js?v=131';

  export function hasPeers() {
    return !!(S.hostCatalog.peers && S.hostCatalog.peers.length);
  }
  export function listedHosts() {
    const local = S.hostCatalog.local || { id: 'local', name: '', local: true };
    return [local].concat(S.hostCatalog.peers || []);
  }
  export function usesFocusedHostView() {
    return usesFocusedFleet(S.settings.host_layout);
  }
  export function displayedHosts() {
    const hosts = listedHosts();
    if (!usesFocusedHostView()) return hosts;
    return [hostById(S.focusHostId) || hosts[0]];
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
    const name = (row && row.name) || id || t('hosts.thisMachine');
    return window.PortLightI18n?.sampleText ? window.PortLightI18n.sampleText(name) : name;
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
    if (!hostId || hostId === 'local') {
      if (S.hostMaps.local && S.hostMaps.local.data) return S.hostMaps.local.data;
      return S.currentData;
    }
    return S.hostMaps[hostId] && S.hostMaps[hostId].data;
  }
  function detailPeerSlice(hostId, portNum) {
    const m = S.hostMaps[hostId];
    if (!m) return null;
    const data = m.data;
    const summary = data && data.summary;
    const match = data && Array.isArray(data.ports)
      ? data.ports.find(function (r) { return r && r.port === portNum; }) || null
      : null;
    const hiddenOcc = summary && Array.isArray(summary.hidden_occupancy)
      ? summary.hidden_occupancy.find(function (r) { return r && r.port === portNum; }) || null
      : null;
    const hiddenListed = !!(summary && Array.isArray(summary.hidden_ports) && summary.hidden_ports.indexOf(portNum) >= 0);
    return {
      id: hostId,
      error: m.error || '',
      loaded: !!data,
      scan_complete: summary ? summary.scan_complete : null,
      stale: !!(summary && summary.stale),
      hidden_locked: !!(summary && summary.hidden_locked),
      hidden_listed: hiddenListed,
      hidden_occ: hiddenOcc,
      locked_hit: S.lockedHitCache[hostId + ':' + portNum] || '',
      match: match,
    };
  }
  export function occupancyFingerprint() {
    if (!hasPeers()) {
      if (!S.currentData) return '';
      return JSON.stringify({ ports: S.currentData.ports, summary: S.currentData.summary });
    }
    const visible = displayedHosts();
    const boards = visible.map(function (h) {
      const m = S.hostMaps[h.id] || {};
      return {
        id: h.id,
        ports: m.data && m.data.ports,
        summary: m.data && m.data.summary,
        error: m.error || '',
        scanners: m.scanners || null,
      };
    });
    if (usesFocusedHostView() && S.selectedPort !== null) {
      const shownIds = new Set(visible.map(function (h) { return h.id; }));
      const detailPeers = listedHosts()
        .filter(function (h) { return !shownIds.has(h.id); })
        .map(function (h) { return detailPeerSlice(h.id, S.selectedPort); });
      return JSON.stringify({ boards: boards, detailPort: S.selectedPort, detailPeers: detailPeers });
    }
    return JSON.stringify(boards);
  }
