import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { scanDiagnosticKeys, scanWarningMarkup, wireScanWarnings } = await import('../js/scan-warning.js?v=' + version);

test('scan diagnostics use current source states, including v0.7.6 summaries', () => {
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: true }), []);
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: false, sources: {
    listen: 'ok', docker: 'failed', compose: 'disabled',
  } }), ['docker']);
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: false, sources: { compose: 'failed' },
    compose_truncated: true, compose_incomplete: true }), ['composeTruncated', 'composeIncomplete']);
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: false, sources: { compose: 'failed' } }), ['compose']);
});

test('stale snapshots do not present old failures as current diagnoses', () => {
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: false, stale: true,
    sources: { docker: 'failed' } }), ['stale']);
  assert.deepEqual(scanDiagnosticKeys({}), ['unknown']);
  assert.deepEqual(scanDiagnosticKeys({ scan_complete: false, sources: { compose: 'disabled' },
    compose_incomplete: true }), ['unknown']);
});

test('warning provides upgrade guidance without exposing raw diagnostic data', () => {
  const html = scanWarningMarkup({ scan_complete: false, sources: { docker: 'failed' },
    reason: '<script>secret</script>', path: '/private/config.yml' }, 'local', 'summary');
  assert.match(html, /<details/);
  assert.match(html, /<summary/);
  assert.match(html, /scanner\.diagnostics\.docker/);
  assert.match(html, /scanner\.diagnostics\.upgrade/);
  assert.match(html, /#\/settings\/occupancy/);
  assert.doesNotMatch(html, /secret|private\/config/);
  const remote = scanWarningMarkup({ scan_complete: false }, 'peer0001', 'board');
  assert.match(remote, /scanner\.diagnostics\.remote/);
  assert.doesNotMatch(remote, /#\/settings\/occupancy/);
});

test('warning opens only from the info icon on pointer hover and dismisses with Escape', () => {
  const root = document.createElement('div');
  root.innerHTML = scanWarningMarkup({ scan_complete: false }, 'local', 'summary');
  wireScanWarnings(root);
  const warning = root.querySelector('details');
  const trigger = warning.querySelector('summary');
  const info = warning.querySelector('.scan-warning-info');
  warning.open = false;
  warning.dispatchEvent({ type: 'pointerenter', pointerType: 'mouse' });
  assert.equal(warning.open, false);
  info.dispatchEvent({ type: 'pointerenter', pointerType: 'mouse' });
  assert.equal(warning.open, true);
  warning.dispatchEvent({ type: 'pointerleave' });
  assert.equal(warning.open, false);
  trigger.dispatchEvent({ type: 'focus' });
  assert.equal(warning.open, true);
  warning.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.equal(warning.open, false);
});

test('structured range diagnostics interpolate safely and disappear on stale snapshots', () => {
  const previous = window.PortLightI18n;
  window.PortLightI18n = { t: (key, vars = {}) => key + Object.values(vars).join(' ') };
  try {
    const summary = { scan_complete: false, compose_incomplete: true,
      compose_diagnostics: [{ code: 'port_range', file: '<script>alert(1)</script>.yaml',
        range: '20000-24096', limit: 4096 }] };
    const html = scanWarningMarkup(summary, 'local', 'summary');
    assert.match(html, /scanner\.diagnostics\.port_range/);
    assert.match(html, /20000-24096 4096/);
    assert.match(html, /&lt;script&gt;/);
    assert.doesNotMatch(html, /<script>/);
    assert.doesNotMatch(scanWarningMarkup({ ...summary, stale: true }, 'local', 'summary'), /20000-24096/);
  } finally {
    window.PortLightI18n = previous;
  }
});
