/* HTTP requests and conditional occupancy fetches. */

import { S } from './state.js?v=77';
import { occupancyUrl } from './hosts.js?v=77';

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
  async function fetchJson(url) {
    try {
      const res = await api(url);
      return res.ok ? await res.json() : null;
    } catch (err) {
      return null;
    }
  }
  export function fetchMeta() { return fetchJson('/api/meta'); }
  export function fetchSettings() { return fetchJson('/api/settings'); }
  export function fetchHostHealth(hostId) {
    return fetchJson(hostId === 'local' ? '/api/health' : '/api/hosts/' + encodeURIComponent(hostId) + '/health');
  }
  export async function fetchHosts() {
    const body = await fetchJson('/api/hosts');
    if (!body) return null;
    return {
      local: body.local || { id: 'local', name: '', local: true },
      peers: Array.isArray(body.peers) ? body.peers : [],
      readonly: !!body.readonly,
    };
  }
  const occupancyRequests = new Map();

  async function fetchOccupancy(url, key, opts) {
    const isolated = !!(opts && opts.isolated);
    const previous = occupancyRequests.get(key);
    if (previous) previous.controller.abort();
    const request = { controller: new AbortController(), url, etag: '', unlock: S.hiddenUnlock };
    occupancyRequests.set(key, request);
    try {
      const headers = {};
      if (!isolated && previous && previous.url === url && previous.unlock === request.unlock && previous.etag) {
        headers['If-None-Match'] = previous.etag;
      }
      const res = await api(url, { signal: request.controller.signal, headers });
      if (res.status === 304) {
        request.etag = previous.etag;
        request.data = previous.data;
        return { ok: true, unchanged: true, data: request.data };
      }
      if (res.status === 502) {
        const body = await res.json().catch(function () { return {}; });
        return { ok: false, auth: String(body.detail || '').toLowerCase().includes('auth') };
      }
      if (!res.ok) return { ok: false };
      const data = await res.json();
      request.data = data;
      if (!isolated) request.etag = res.headers.get('etag') || '';
      return { ok: true, data };
    } catch (err) {
      return { ok: false, stale: !!(err && err.name === 'AbortError') };
    }
  }

  export function fetchPorts(opts) {
    return fetchOccupancy(occupancyUrl('local'), 'local', opts);
  }

  export function fetchHostOccupancy(hostId, opts) {
    return fetchOccupancy(occupancyUrl(hostId), hostId, opts);
  }
