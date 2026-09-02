import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { S } = await import('../js/state.js?v=' + version);
const { buildSearchContext, renderSummary } = await import('../js/grid.js?v=' + version);

test('search and summary do not claim free ports from incomplete or stale data', () => {
  for (let port = 41950; port < 42051; port++) S.knownCache[port] = null;
  for (const summary of [{ scan_complete: false }, { scan_complete: true, stale: true }, {}]) {
    const rows = buildSearchContext([], 42000, { summary });
    assert.ok(rows.length > 0);
    assert.ok(rows.every(row => row.status === 'unknown' && row._unavailable));
    renderSummary({ ...summary, used: 0, configured: 0, free: 123 });
    const html = document.getElementById('summary').innerHTML;
    assert.match(html, /scanner.snapshotUnavailable/);
    assert.doesNotMatch(html, /123/);
  }
  const rows = buildSearchContext([], 42000, { summary: { scan_complete: true } });
  assert.ok(rows.every(row => row.status === 'free'));
});
