/* HTTP requests and conditional occupancy fetches. */

import { S } from './state.js?v=129';
import { occupancyUrl } from './hosts.js?v=129';

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
  export function fetchDoctor() { return fetchJson('/api/doctor'); }
  export function fetchHostHealth(hostId) {
    return fetchJson(hostId === 'local' ? '/api/health' : '/api/hosts/' + encodeURIComponent(hostId) + '/health');
  }
  export async function fetchHosts() {
    const body = await fetchJson('/api/hosts');
    if (!body) return null;
    return {
      local: body.local || { id: 'local', name: '', description: '', local: true },
      peers: Array.isArray(body.peers) ? body.peers : [],
      readonly: !!body.readonly,
      max_peers: Number(body.max_peers) || 32,
    };
  }
  const occupancyRequests = new Map();
  const isolatedControllers = new Map();

  async function fetchOccupancy(url, key, opts) {
    const isolated = !!(opts && opts.isolated);
    const previous = occupancyRequests.get(key);
    if (previous) previous.controller.abort();
    const prevIsolated = isolatedControllers.get(key);
    if (prevIsolated) prevIsolated.abort();
    const controller = new AbortController();
    if (isolated) isolatedControllers.set(key, controller);
    const sameContext = !isolated && previous && previous.url === url && previous.unlock === S.hiddenUnlock;
    const request = {
      controller,
      url,
      etag: sameContext ? previous.etag : '',
      data: sameContext ? previous.data : undefined,
      unlock: S.hiddenUnlock,
    };
    if (!isolated) occupancyRequests.set(key, request);
    try {
      const headers = {};
      if (sameContext && previous.etag) {
        headers['If-None-Match'] = previous.etag;
      }
      const res = await api(url, { signal: controller.signal, headers });
      if (res.status === 304) {
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
    } finally {
      if (isolated && isolatedControllers.get(key) === controller) {
        isolatedControllers.delete(key);
      }
    }
  }

  export function fetchPorts(opts) {
    return fetchOccupancy(occupancyUrl('local'), 'local', opts);
  }

  export function fetchHostOccupancy(hostId, opts) {
    return fetchOccupancy(occupancyUrl(hostId), hostId, opts);
  }
