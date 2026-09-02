import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { S } = await import('../js/state.js?v=' + version);
const { renderDetail, showPortDetail } = await import('../js/detail.js?v=' + version);
const { portFromList } = await import('../js/grid.js?v=' + version);
const detail = document.getElementById('detail-content');
const PEER = 'abcdef123456';
const flush = () => new Promise(resolve => setImmediate(resolve));
const row = port => ({ port, status: 'configured', source_type: 'manual', manual_label: 'demo' });

beforeEach(() => {
  Object.assign(S, { selectedPort: 45000, selectedHostId: 'local', currentData: null,
    hostMaps: {}, hostCatalog: { peers: [] }, route: { name: 'grid' }, meta: {},
    hiddenUnlock: '', pendingAfterUnlock: null, detailShownPort: null });
  detail.innerHTML = '';
  document.activeElement = null;
  window.confirm = () => true;
});

test('drawer buttons send hide, unhide, rename and delete requests', async () => {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, ...opts });
    return { ok: false, status: 500 }; // avoid polling; also exercise the error UI
  };
  for (const [selector, event, hidden] of [
    ['[data-hide-port]', 'click', false], ['[data-unhide-port]', 'click', true],
    ['[data-label-form]', 'submit', false], ['[data-delete-port]', 'click', false],
  ]) {
    renderDetail({ ...row(45000), is_hidden: hidden });
    const control = detail.querySelector(selector);
    assert.ok(control, selector);
    control.dispatchEvent({ type: event, preventDefault() {} });
    await flush();
    assert.ok(detail.querySelector('.detail-error'));
  }
  assert.deepEqual(calls.filter(c => c.method).map(c => [c.method, c.url]), [
    ['POST', '/api/hidden/45000'], ['DELETE', '/api/hidden/45000'],
    ['PATCH', '/api/manual-ports/45000'], ['DELETE', '/api/manual-ports/45000'],
  ]);
});

test('hidden write denial opens unlock dialog with a retry', async () => {
  S.meta.hidden_unlock_required = true;
  globalThis.fetch = async () => ({ ok: false, status: 403 });
  await window._portLightHide(45000);
  assert.equal(typeof S.pendingAfterUnlock, 'function');
});

test('peer without data never reuses the local row or exposes mutation controls', async () => {
  S.currentData = { ports: [row(45000)] };
  S.selectedHostId = PEER;
  assert.equal(portFromList(45000, PEER), null);
  globalThis.fetch = async () => ({ ok: false, status: 404 });
  showPortDetail(45000);
  await flush();
  assert.match(detail.innerHTML, /detail.notFound/);
  assert.equal(detail.querySelector('[data-hide-port]'), null);
  assert.equal(detail.querySelector('[data-delete-port]'), null);
});

test('same port on another host fetches fresh detail', async () => {
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(url);
    return { ok: true, json: async () => url.includes('/history') ? { events: [] } : row(45000) };
  };
  showPortDetail(45000);
  await flush();
  S.selectedHostId = PEER;
  showPortDetail(45000);
  await flush();
  assert.ok(calls.includes('/api/hosts/' + PEER + '/ports/45000?include_hidden=true'));
});

test('history uses the selected host and ignores responses for a previous drawer', async () => {
  const pending = [];
  globalThis.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
  renderDetail(row(45000));
  S.selectedHostId = PEER;
  renderDetail(row(45000));
  assert.equal(pending[1].url, '/api/hosts/' + PEER + '/ports/45000/history?hours=24');
  const body = text => ({ ok: true, json: async () => ({ events: [{ ts: 1, state: 'used', holders: [text] }] }) });
  pending[1].resolve(body('peer-holder'));
  await flush();
  const history = detail.querySelector('#detail-history');
  assert.match(history.innerHTML, /peer-holder/);
  pending[0].resolve(body('local-holder'));
  await flush();
  assert.doesNotMatch(history.innerHTML, /local-holder/);
});

test('owned reservations do not offer manual overwrite or deletion', () => {
  globalThis.fetch = async () => ({ ok: false });
  renderDetail({ ...row(45000), is_reservation: true });
  assert.equal(detail.querySelector('[data-label-form]'), null);
  assert.equal(detail.querySelector('[data-delete-port]'), null);
  assert.match(detail.innerHTML, /detail.reservationHint/);
});

test('unavailable occupancy never renders a free port or mutation controls', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 503 });
  showPortDetail(45000);
  await flush();
  assert.match(detail.innerHTML, /scanner.snapshotUnavailable/);
  assert.doesNotMatch(detail.innerHTML, /status.free/);
  assert.equal(detail.querySelector('[data-hide-port]'), null);
});
