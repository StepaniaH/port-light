import './helpers/env.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
import { groupPorts, portRuns } from '../js/port-groups.js';
const { composeSnippet, mountManagementPage, releaseCommand } = await import('../js/management.js?v=' + version);

const config = { project_name: 'wiki', project_dir: 'apps/wiki', service_name: 'web', container_port: 80, protocol: 'tcp', host_ip: '127.0.0.1' };
const rows = Array.from({ length: 256 }, (_, i) => ({ port: 20000 + i, status: 'configured', protocol: 'tcp', compose_configs: [config] }));
test('a service range folds without losing port records, gaps or status boundaries', () => {
  const [group] = groupPorts(rows);
  assert.equal(group.label, 'wiki / web');
  assert.equal(portRuns(group.rows)[0].length, 256);
  const changed = rows.map(r => ({ ...r }));
  changed[4].status = 'used';
  changed.splice(8, 1);
  assert.deepEqual(portRuns(changed).map(r => [r[0].port, r.at(-1).port]), [[20000, 20003], [20004, 20004], [20005, 20007], [20009, 20255]]);
});
test('projects with equal display names remain distinct and all conflict owners are grouped', () => {
  const other = { ...config, project_dir: 'other/wiki', service_name: 'api' };
  const groups = groupPorts([{ ...rows[0], compose_configs: [config, other] }], 'project');
  assert.equal(groups.length, 2);
  assert.notEqual(groups[0].key, groups[1].key);
  assert.notEqual(groups[0].label, groups[1].label);
  assert.equal(groups[0].label, 'wiki (apps/wiki)');
  assert.equal(groups[1].label, 'wiki (other/wiki)');
  assert.equal(groups[0].rows[0], groups[1].rows[0]);

  const serviceGroups = groupPorts([{ ...rows[0], compose_configs: [config, { ...other, service_name: 'web' }] }], 'service');
  assert.deepEqual(serviceGroups.map(g => g.label), ['wiki (apps/wiki) / web', 'wiki (other/wiki) / web']);
});

test('portRuns never merges rule-violating ports into compliant contiguous runs', () => {
  const sample = Array.from({ length: 8 }, (_, i) => ({
    port: 21000 + i,
    status: 'configured',
    protocol: 'tcp',
    rule_violations: i === 3 || i === 4 ? [{ rule: 'apps' }] : [],
  }));
  assert.deepEqual(
    portRuns(sample).map(r => [r[0].port, r.at(-1).port]),
    [[21000, 21002], [21003, 21004], [21005, 21007]],
  );
});
test('Compose replacement preserves target, bind and protocol; Swarm uses deploy.ports', () => {
  const snippet = composeSnippet({ ...config, protocol: 'udp', host_ip: '::1' }, 28000);
  assert.match(snippet, /target: 80/);
  assert.match(snippet, /published: "28000"/);
  assert.match(snippet, /protocol: "udp"/);
  assert.match(snippet, /host_ip: "::1"/);
  assert.match(composeSnippet({ ...config, mapping_source: 'deploy.ports' }, 28001), /    deploy:\n      ports:/);
  assert.equal(composeSnippet({ ...config, mapping_source: 'lan' }, 28001), null);
  assert.equal(composeSnippet({ ...config, network_mode: 'host' }, 28001), null);
  assert.equal(composeSnippet(config, undefined), null);
});
test('Compose names cannot escape YAML strings and release commands quote secrets', () => {
  assert.match(composeSnippet({ ...config, service_name: 'web:\n evil' }, 28000), /"web:\\n evil"/);
  assert.equal(releaseCommand(20000, 'secret', 'http://localhost:2100'), "curl --fail-with-body -X DELETE 'http://localhost:2100/api/reservations/20000' -H 'X-Reservation-Token: secret'");
  assert.match(releaseCommand(20000, "x'y", 'http://localhost'), /'\\''/);
});

test('quoted names remain text in attributes and grouped range identifiers', async () => {
  const { escapeHtml } = await import('../js/text.js');
  const raw = '\" onmouseover=\"alert(1)\" <b> & \'quoted\'';
  const el = document.createElement('div');
  el.innerHTML = '<input value="' + escapeHtml(raw) + '">';
  const input = el.querySelector('input');
  assert.equal(input.getAttribute('value'), raw);
  assert.equal(input.getAttribute('onmouseover'), null);
});

test('status sorting puts used ports first, then configured and free', async () => {
  const { S } = await import('../js/state.js?v=' + version);
  const { sortPorts } = await import('../js/grid.js?v=' + version);
  const original = S.sortMode;
  try {
    S.sortMode = 'status';
    assert.deepEqual(sortPorts([{ port: 1, status: 'free' }, { port: 2, status: 'configured' }, { port: 3, status: 'used' }]).map(r => r.status), ['used', 'configured', 'free']);
  } finally { S.sortMode = original; }
});

test('grouped runs respect name and status sorting across noncontiguous ports', async () => {
  const { S } = await import('../js/state.js?v=' + version);
  const { sortPortRuns } = await import('../js/grid.js?v=' + version);
  const original = S.sortMode;
  const entries = [
    { port: 8080, status: 'configured', manual_label: 'Zulu', source_type: 'manual' },
    { port: 9000, status: 'used', manual_label: 'Alpha', source_type: 'manual' },
    { port: 7000, status: 'configured', manual_label: 'Beta', source_type: 'manual' },
  ];
  try {
    for (const [mode, expected] of [['name-asc', [9000, 7000, 8080]], ['name-desc', [8080, 7000, 9000]], ['status', [9000, 7000, 8080]], ['port-desc', [9000, 8080, 7000]]]) {
      S.sortMode = mode;
      assert.deepEqual(sortPortRuns(entries).map(run => run[0].port), expected);
    }
  } finally { S.sortMode = original; }
});

test('discardPending clears stuck reservation state and conflicts include unlocked hidden ports', async () => {
  const { S } = await import('../js/state.js?v=' + version);
  const root = document.createElement('main');
  const savedFetch = globalThis.fetch;
  const savedUnlock = S.hiddenUnlock;
  try {
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'stuck-idempotency-key', body: { count: 1 } },
    }));
    S.hiddenUnlock = 'local-secret';
    const seenRequests = [];
    globalThis.fetch = async (url, opts = {}) => {
      seenRequests.push({ url: String(url), unlock: opts.headers?.['X-Hidden-Unlock'] });
      if (String(url).startsWith('/api/port-rules')) return new Response(JSON.stringify({ rules: [], readonly: false }));
      if (String(url).startsWith('/api/reservations')) return new Response(JSON.stringify({ reservations: [] }));
      return new Response(JSON.stringify({
        ports: [{
          port: 24500, status: 'configured', protocol: 'tcp', is_hidden: true, conflict: true,
          compose_configs: [config],
        }],
        summary: { scan_complete: true, stale: false },
      }));
    };

    const page = mountManagementPage(root);
    await page.open('reservations');
    const retryBtn = root.querySelector('[data-action="retry"]');
    const discardBtn = root.querySelector('[data-action="discardPending"]');
    assert.ok(retryBtn && !retryBtn.hidden);
    assert.ok(discardBtn && !discardBtn.hidden);

    root.dispatchEvent({
      type: 'click',
      target: { closest: sel => (sel === '[data-action]' ? { dataset: { action: 'discardPending' } } : null) },
    });
    await new Promise(resolve => setImmediate(resolve));

    const stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending, undefined);
    assert.equal(stored.tokens[24001], 'keep-token');
    assert.equal(retryBtn.hidden, true);
    assert.equal(discardBtn.hidden, true);

    await page.open('conflicts');
    const portsCall = seenRequests.find(r => r.url.startsWith('/api/ports?'));
    assert.ok(portsCall, 'conflicts view fetches /api/ports');
    assert.match(portsCall.url, /include_hidden=true/);
    assert.equal(portsCall.unlock, 'local-secret');
    assert.match(root.innerHTML, /24500/);
  } finally {
    globalThis.fetch = savedFetch;
    S.hiddenUnlock = savedUnlock;
    sessionStorage.removeItem('port-light-reservation-session');
  }
});
