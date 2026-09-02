/* Tests for frontend/js/hosts.js URL/hash builders and fingerprint summary.
   Imports go through the same ?v= specifier as the source modules so both
   sides share one live S instance. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { occupancyUrl, portApiUrl, portHash, occupancyFingerprint } = await import('../js/hosts.js?' + V);
const { S } = await import('../js/state.js?' + V);

function withState(patch, fn) {
  const saved = {
    rangeStart: S.rangeStart,
    rangeEnd: S.rangeEnd,
    showHidden: S.showHidden,
    focusHostId: S.focusHostId,
    selectedHostId: S.selectedHostId,
    hostCatalog: S.hostCatalog,
  };
  try {
    Object.assign(S, patch);
    fn();
  } finally {
    Object.assign(S, saved);
  }
}

const PEER = 'deadbeef00112233';
const withPeers = (fn) =>
  withState({ hostCatalog: { local: { id: 'local', name: '', local: true }, peers: [{ id: PEER, name: 'nas' }] } }, fn);

test('occupancyUrl targets the flat endpoint without peers', () => {
  assert.equal(
    occupancyUrl('local'),
    '/api/ports?range_start=1&range_end=9999&include_hidden=false',
  );
  withPeers(() => {
    assert.notEqual(
      occupancyUrl('local'),
      '/api/ports?range_start=1&range_end=9999&include_hidden=false',
    );
  });
});

test('occupancyUrl encodes state and peer hosts into the query', () => {
  withPeers(() => {
    assert.equal(
      occupancyUrl(PEER),
      '/api/hosts/' + PEER + '/ports?range_start=1&range_end=9999&include_hidden=false',
    );
  });
  withState({ rangeStart: 1000, rangeEnd: 2000, showHidden: true }, () => {
    assert.equal(
      occupancyUrl('local'),
      '/api/ports?range_start=1000&range_end=2000&include_hidden=true',
    );
  });
});

test('portApiUrl switches between flat and per-host endpoints', () => {
  assert.equal(portApiUrl('local', 8080), '/api/ports/8080');
  withPeers(() => {
    assert.equal(portApiUrl('local', 8080), '/api/hosts/local/ports/8080');
    assert.equal(portApiUrl(PEER, 53), '/api/hosts/' + PEER + '/ports/53');
  });
});

test('api endpoints URL-encode peer host ids; hashes pass them through raw', () => {
  const weird = 'a b/c';
  withState(
    { hostCatalog: { local: { id: 'local', name: '', local: true }, peers: [{ id: weird, name: 'x' }] } },
    () => {
      assert.equal(portApiUrl(weird, 80), '/api/hosts/a%20b%2Fc/ports/80');
      assert.equal(portHash(weird, 80), '#/h/a b/c/port/80');
    },
  );
});

test('freshness changes trigger a view refresh', () => {
  const saved = S.currentData;
  try {
    S.currentData = { ports: [], summary: { free: 10, scan_complete: true } };
    const fresh = occupancyFingerprint();
    S.currentData.summary.stale = true;
    assert.notEqual(occupancyFingerprint(), fresh);
  } finally { S.currentData = saved; }
});
