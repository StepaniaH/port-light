import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { S } = await import('../js/state.js?v=' + version);
const { fetchPorts } = await import('../js/api.js?v=' + version);

test('304 retains its body when the app discarded the earlier poll', async () => {
  S.rangeStart = 42000;
  S.rangeEnd = 42010;
  const data = { ports: [{ port: 42000, status: 'used' }] };
  globalThis.fetch = async () => new Response(JSON.stringify(data), { headers: { etag: '"new"' } });
  await fetchPorts(); // A superseded app refresh can discard this result.
  globalThis.fetch = async (_url, opts) => {
    assert.equal(opts.headers['If-None-Match'], '"new"');
    return new Response(null, { status: 304 });
  };
  assert.deepEqual((await fetchPorts()).data, data);
  assert.deepEqual((await fetchPorts()).data, data);
});

test('unlock aborts a pending poll and does not reuse its conditional cache', async () => {
  S.rangeStart = 42100;
  globalThis.fetch = async (_url, opts) => new Promise((_resolve, reject) => {
    opts.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  });
  const pending = fetchPorts();
  globalThis.fetch = async (_url, opts) => {
    assert.equal(opts.headers['If-None-Match'], undefined);
    return new Response('{"ports":[]}', { headers: { etag: '"unlocked"' } });
  };
  await fetchPorts({ isolated: true });
  assert.equal((await pending).stale, true);
  assert.equal((await fetchPorts()).ok, true);
});
