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

const { parseHash } = await import('../js/router.js?' + V);

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
