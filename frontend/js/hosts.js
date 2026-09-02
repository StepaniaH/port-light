/* Host catalog, occupancy selectors, and route URLs. */

import { S } from './state.js?v=77';
import { t } from './text.js?v=77';

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
    if (!hostId || hostId === 'local') {
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
