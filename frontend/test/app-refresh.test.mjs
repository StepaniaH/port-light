import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { refreshFleet } from '../js/fleet.js';

// Exercise the actual app coordinator and its SSE callback without booting the UI.
const source = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const start = source.indexOf('  let loadGeneration = 0;');
const end = source.indexOf('  configureDetail(');
assert.ok(start > 0 && end > start);
const coordinator = source.slice(start, end);
const settle = () => new Promise(resolve => setImmediate(resolve));
const data = { ports: [], summary: { hidden_locked: false } };

function createApp(fetchOccupancy) {
  const listeners = {};
  class EventSource {
    addEventListener(name, callback) { listeners[name] = callback; }
  }
  const S = {
    settings: { auto_refresh: true, refresh_ms: 300000 }, route: { name: 'grid' },
    focusHostId: 'local', showHidden: false, hostMaps: {}, currentData: data,
    lockedHitCache: {}, lockedHitInflight: {}, hostRetrying: {},
  };
  const app = runInNewContext(coordinator + '\n({loadPorts, tick, startEventStream, inFlight: () => refreshInFlight})', {
    S, refreshFleet, hasPeers: () => true, usesFocusedHostView: () => false,
    listedHosts: () => ['local', 'peer1', 'peer2', 'peer3'].map(id => ({ id })),
    fetchHostOccupancy: fetchOccupancy,
    fetchHostHealth: async () => ({ scanners: {} }),
    dataForHost: id => S.hostMaps[id]?.data || null,
    hostBoards: { querySelectorAll: () => [] }, modalOpen: () => false,
    occupancyFingerprint: () => 'snapshot', render() {}, markRefreshed() {}, setSyncError() {},
    EventSource, window: { EventSource }, setInterval() {}, clearInterval() {},
  });
  return { S, app, listeners };
}

test('local SSE events do not poll peers before the configured interval', async () => {
  const fetched = [];
  const { app, listeners } = createApp(async id => {
    fetched.push(id);
    return { ok: true, data };
  });
  app.startEventStream();
  for (let i = 0; i < 2; i++) {
    listeners.refresh();
    await app.inFlight();
  }
  assert.deepEqual(fetched, ['local', 'local']);
  fetched.length = 0;
  await app.tick();
  assert.deepEqual(fetched, ['local', 'peer1', 'peer2', 'peer3'], 'manual/timer refresh still covers the fleet');
});

test('an obsolete queued sweep does not start a request for a newer isolated unlock', async () => {
  const pending = [];
  const { S, app } = createApp((id, opts) => new Promise(resolve => {
    pending.push({ id, isolated: !!opts?.isolated, resolve });
  }));
  const sweep = app.loadPorts();
  S.focusHostId = 'peer3';
  const unlock = app.loadPorts({ isolated: true });
  pending[0].resolve({ ok: true, data });
  await settle();
  const peer3Requests = pending.filter(request => request.id === 'peer3');
  for (const request of pending) request.resolve({ ok: true, data });
  await Promise.all([sweep, unlock]);
  assert.equal(peer3Requests.length, 1, 'stale work must not abort the isolated request');
  assert.equal(peer3Requests[0].isolated, true);
});

test('queued local events cannot downgrade or duplicate a pending fleet refresh', async () => {
  const fetched = [];
  let release;
  const first = new Promise(resolve => { release = resolve; });
  const { app, listeners } = createApp(async id => {
    fetched.push(id);
    if (fetched.length === 1) await first;
    return { ok: true, data };
  });
  app.startEventStream();
  listeners.refresh();
  listeners.refresh();
  app.tick();
  listeners.refresh();
  assert.deepEqual(fetched, ['local']);
  release();
  await app.inFlight();
  await app.inFlight();
  assert.deepEqual(fetched, ['local', 'local', 'peer1', 'peer2', 'peer3']);
});

test('local events remain local when a remote machine is focused and respect auto-refresh', async () => {
  const fetched = [];
  const { S, app, listeners } = createApp(async id => {
    fetched.push(id);
    return { ok: true, data };
  });
  S.focusHostId = 'peer3';
  app.startEventStream();
  listeners.refresh();
  await app.inFlight();
  assert.deepEqual(fetched, ['local']);
  S.settings.auto_refresh = false;
  listeners.refresh();
  assert.deepEqual(fetched, ['local']);
  S.settings.auto_refresh = true;
  S.route.name = 'settings';
  listeners.refresh();
  assert.deepEqual(fetched, ['local']);
});
