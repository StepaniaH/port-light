import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { copyReportText, renderDoctor } = await import('../js/doctor.js?v=' + version);

test('doctor renders every sanitized check and its aggregate state', () => {
  const host = document.getElementById('doctor-results');
  renderDoctor({
    overall: 'attention',
    counts: { pass: 2, warning: 1, fail: 1 },
    report: '{"schema_version":1}',
    checks: [
      { id: 'settings_store', status: 'pass', detail: 'writable', evidence: { source: 'auto' } },
      { id: 'snapshot', status: 'warning', detail: 'stale', evidence: { age_seconds: 12 } },
      { id: 'docker', status: 'fail', detail: 'failed', evidence: { transport: 'socket_denied' } },
    ],
  });
  assert.match(host.innerHTML, /doctor\.overall\.attention/);
  assert.match(host.innerHTML, /doctor\.check\.settings_store/);
  assert.match(host.innerHTML, /doctor\.check\.snapshot/);
  assert.match(host.innerHTML, /doctor\.check\.docker/);
  assert.match(host.innerHTML, /schema_version/);
});

test('doctor report preview escapes markup', () => {
  const host = document.getElementById('doctor-results');
  renderDoctor({ overall: 'healthy', counts: {}, checks: [], report: '<script>secret()</script>' });
  assert.doesNotMatch(host.innerHTML, /<script>/);
  assert.match(host.innerHTML, /&lt;script&gt;/);
});

test('doctor copy falls back when the Clipboard API is unavailable', async () => {
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const savedExec = document.execCommand;
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  let command = '';
  document.execCommand = value => { command = value; return true; };
  try {
    await copyReportText('{"schema_version":1}');
    assert.equal(command, 'copy');
  } finally {
    document.execCommand = savedExec;
    if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator);
    else delete globalThis.navigator;
  }
});

test('doctor copy rejects when neither copy mechanism is available', async () => {
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const savedExec = document.execCommand;
  Object.defineProperty(globalThis, 'navigator', { value: {}, configurable: true });
  delete document.execCommand;
  try {
    await assert.rejects(copyReportText('{}'), /Copy is unavailable/);
  } finally {
    document.execCommand = savedExec;
    if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator);
    else delete globalThis.navigator;
  }
});
