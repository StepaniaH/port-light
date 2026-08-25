/* Tests for frontend/js/leases.js — lease detection and duration formatting.
   All formatters are locale-neutral numeric strings; words come from i18n. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { isLease, remainingSeconds, fmtRemaining } from '../js/leases.js';

const NOW = Math.floor(Date.now() / 1000);

test('isLease true only for future finite expires_at', () => {
  assert.equal(isLease({ expires_at: NOW + 60 }), true);
  assert.equal(isLease({ expires_at: NOW - 1 }), false);
  assert.equal(isLease({ expires_at: NaN }), false);
  assert.equal(isLease({}), false);
  assert.equal(isLease(null), false);
});

test('remainingSeconds clamps expired leases to zero', () => {
  assert.equal(remainingSeconds(NOW + 90, NOW), 90);
  assert.equal(remainingSeconds(NOW - 90, NOW), 0);
});

test('fmtRemaining picks the widest unit', () => {
  assert.equal(fmtRemaining(30), '<1m');
  assert.equal(fmtRemaining(59), '<1m');
  assert.equal(fmtRemaining(60), '1m');
  assert.equal(fmtRemaining(58 * 60), '58m');
  assert.equal(fmtRemaining(3 * 3600), '3h');
  assert.equal(fmtRemaining(47 * 3600), '2d');
});

test('badge text derivation matches drawer countdown', () => {
  // Same inputs both surfaces use — pins the contract between them.
  const exp = NOW + 58 * 60;
  assert.equal(fmtRemaining(remainingSeconds(exp, NOW)), '58m');
  const expOld = NOW + 25 * 3600;
  assert.equal(fmtRemaining(remainingSeconds(expOld, NOW)), '1d');
});
