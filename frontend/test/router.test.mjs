/* Tests for frontend/js/router.js parseHash — hash-to-route parsing. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

test('entry pins a shared cache-bust version', () => {
  assert.ok(version, 'app.js should carry a ?v=N cache-bust marker');
});

const { applyRoute, parseHash } = await import('../js/router.js?' + V);
const { goSettingsPanel, mountSettingsPage } = await import('../js/settings.js?' + V);
const { S } = await import('../js/state.js?' + V);

const grid = (hostId) => ({ name: 'grid', hostId: hostId || 'local' });
const port = (n, hostId) => ({ name: 'port', port: n, hostId: hostId || 'local' });

test('empty and root hashes resolve to the local grid', () => {
  assert.deepEqual(parseHash(undefined), grid());
  assert.deepEqual(parseHash(''), grid());
  assert.deepEqual(parseHash('#'), grid());
  assert.deepEqual(parseHash('#/'), grid());
  assert.deepEqual(parseHash('#///'), grid());
});

test('port routes carry the parsed numeric port within range', () => {
  assert.deepEqual(parseHash('#/port/1'), port(1));
  assert.deepEqual(parseHash('#/port/8080'), port(8080));
  assert.deepEqual(parseHash('#/port/65535'), port(65535));
});

test('out-of-range or non-numeric ports fall back to the grid', () => {
  assert.deepEqual(parseHash('#/port/0'), grid());
  assert.deepEqual(parseHash('#/port/65536'), grid());
  assert.deepEqual(parseHash('#/port/abc'), grid());
  assert.deepEqual(parseHash('#/port/'), grid());
});

test('known settings panels pass through, unknown ones reset to appearance', () => {
  assert.deepEqual(parseHash('#/settings/appearance'), { name: 'settings', section: 'appearance' });
  assert.deepEqual(parseHash('#/settings/occupancy'), { name: 'settings', section: 'occupancy' });
  assert.deepEqual(parseHash('#/settings/advanced'), { name: 'settings', section: 'advanced' });
  assert.deepEqual(parseHash('#/settings/nope'), { name: 'settings', section: 'appearance' });
  assert.deepEqual(parseHash('#/settings'), { name: 'settings', section: 'appearance' });
});

test('doctor has a standalone route', () => {
  assert.deepEqual(parseHash('#/doctor'), { name: 'doctor' });
});

const PEER = 'abcdef123456';

test('peer host routes keep validated host ids', () => {
  assert.deepEqual(parseHash('#/h/' + PEER), grid(PEER));
  assert.deepEqual(parseHash('#/h/' + PEER + '/port/443'), port(443, PEER));
});

test('malformed host ids are forced back to local', () => {
  assert.deepEqual(parseHash('#/h/BADHOST/port/80'), port(80));
  assert.deepEqual(parseHash('#/h/ab/port/80'), port(80));
  assert.deepEqual(parseHash('#/h/abcdeg123456789xyz/port/80'), port(80));
  assert.deepEqual(parseHash('#/h/../port/80'), port(80));
});

test('applyRoute falls back to local when a port URL references a deleted or unknown peer', () => {
  const prevHash = location.hash;
  const prevState = {
    route: S.route,
    focusHostId: S.focusHostId,
    selectedHostId: S.selectedHostId,
    selectedPort: S.selectedPort,
    hostCatalog: S.hostCatalog,
    currentData: S.currentData,
  };
  try {
    S.hostCatalog = { local: { id: 'local', name: 'Hub', local: true }, peers: [{ id: PEER, name: 'Peer' }] };
    S.currentData = { ports: [], summary: { scan_complete: true } };
    S.route = { name: 'grid', hostId: 'local' };
    S.focusHostId = 'local';
    S.selectedHostId = 'local';
    S.selectedPort = null;

    let renders = 0;
    location.hash = '#/h/deadbeef1234/port/8080';
    applyRoute({ render: () => { renders += 1; }, refresh: () => {} });
    assert.equal(S.selectedPort, 8080);
    assert.equal(S.selectedHostId, 'local');
    assert.equal(S.focusHostId, 'local');
    assert.equal(renders, 1);

    location.hash = '#/h/' + PEER + '/port/443';
    applyRoute({ render: () => { renders += 1; }, refresh: () => {} });
    assert.equal(S.selectedPort, 443);
    assert.equal(S.selectedHostId, PEER);
    assert.equal(S.focusHostId, PEER);
    assert.equal(renders, 2);
  } finally {
    location.hash = prevHash;
    Object.assign(S, prevState);
  }
});

test('hashchange after goSettingsPanel does not reset scroll a second time, while direct hash changes still do', () => {
  const prevHash = location.hash;
  const prevRoute = S.route;
  const prevPanel = S.settingsPanel;
  const savedScroll = window.scrollTo;
  const calls = [];
  window.scrollTo = (...args) => calls.push(args);
  try {
    const settingsPage = mountSettingsPage(document.getElementById('settings-form'));
    S.route = { name: 'settings', section: 'appearance' };
    S.settingsPanel = 'appearance';
    location.hash = '#/settings/appearance';

    // Tab click synchronously switches panel and resets scroll once, then sets location.hash.
    goSettingsPanel('automation');
    assert.equal(S.settingsPanel, 'automation');
    assert.equal(location.hash, '#/settings/automation');
    assert.deepEqual(calls, [[0, 0]]);

    // Queued hashchange arriving after a subsequent user scroll must not scrollTo(0, 0) again.
    applyRoute({ render: () => {}, refresh: () => {}, settingsPage });
    assert.equal(S.settingsPanel, 'automation');
    assert.deepEqual(calls, [[0, 0]]);

    // Direct hash navigation to a different settings panel still resets scroll.
    location.hash = '#/settings/advanced';
    applyRoute({ render: () => {}, refresh: () => {}, settingsPage });
    assert.equal(S.settingsPanel, 'advanced');
    assert.deepEqual(calls, [[0, 0], [0, 0]]);
  } finally {
    window.scrollTo = savedScroll;
    location.hash = prevHash;
    S.route = prevRoute;
    S.settingsPanel = prevPanel;
  }
});
