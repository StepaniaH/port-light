import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  recommendedPeerLimit,
  refreshChoices,
  refreshFleet,
  usesFocusedFleet,
} from '../js/fleet.js';

test('focused layout is an explicit choice, with waterfall as the fallback', () => {
  assert.equal(usesFocusedFleet('tabs'), true);
  for (const layout of ['waterfall', undefined, '', 'unknown', 7, 32]) {
    assert.equal(usesFocusedFleet(layout), false);
  }
});

test('refresh choices preserve stored custom values and supported endpoints', () => {
  assert.deepEqual(refreshChoices(8000), [5000, 8000, 10000, 15000, 30000, 60000, 120000, 300000]);
  assert.equal(refreshChoices(100)[0], 1000);
  assert.equal(refreshChoices(900000).at(-1), 300000);
});

test('refresh recommendations scale from the existing load to the hard limit', () => {
  assert.equal(recommendedPeerLimit(5000, 32), 6);
  assert.equal(recommendedPeerLimit(15000, 32), 18);
  assert.equal(recommendedPeerLimit(30000, 32), 32);
  assert.equal(recommendedPeerLimit(300000, 12), 12);
});

test('fleet refresh preserves result order and limits active hosts', async () => {
  let active = 0;
  let peak = 0;
  const results = await refreshFleet([1, 2, 3, 4, 5, 6, 7], async function (value) {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 5));
    active -= 1;
    return value * 2;
  });
  assert.deepEqual(results, [2, 4, 6, 8, 10, 12, 14]);
  assert.equal(peak, 3);
});
