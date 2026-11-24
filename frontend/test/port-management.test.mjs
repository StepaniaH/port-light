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
test('Compose replacement preserves target, bind, protocol and publish mode under service ports', () => {
  const snippet = composeSnippet({ ...config, protocol: 'udp', host_ip: '::1' }, 28000);
  assert.match(snippet, /target: 80/);
  assert.match(snippet, /published: "28000"/);
  assert.match(snippet, /protocol: "udp"/);
  assert.match(snippet, /host_ip: "::1"/);
  const hostModeSnippet = composeSnippet({ ...config, publish_mode: 'host' }, 28001);
  assert.match(hostModeSnippet, /mode: "host"/);
  assert.doesNotMatch(hostModeSnippet, /^\s*deploy:/m);
  const legacyDeploySnippet = composeSnippet({ ...config, mapping_source: 'deploy.ports' }, 28002);
  assert.match(legacyDeploySnippet, /^  "web":\n    ports:/m);
  assert.doesNotMatch(legacyDeploySnippet, /^\s*deploy:/m);
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

test('discardPending probes server before discarding, recovers committed claims, and confirms offline discards', async () => {
  const { S } = await import('../js/state.js?v=' + version);
  const root = document.createElement('main');
  const savedFetch = globalThis.fetch;
  const savedUnlock = S.hiddenUnlock;
  const savedConfirm = window.confirm;
  const savedMeta = S.meta;
  const clickDiscard = () => root.dispatchEvent({
    type: 'click',
    target: { closest: sel => (sel === '[data-action]' ? { dataset: { action: 'discardPending' } } : null) },
  });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const drain = async () => { for (let i = 0; i < 8; i++) await flush(); };
  try {
    S.hiddenUnlock = 'local-secret';
    S.meta = { automation: { agent_token: true } };

    // 1. Probe returns 404 (not found) -> asks confirm with discardNotFoundConfirm; cancel preserves, accept clears.
    const confirmPrompts = [];
    window.confirm = msg => { confirmPrompts.push(msg); return false; };
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'stuck-idempotency-key', body: { count: 1 } },
    }));
    const seenRequests = [];
    let probeBehavior = '404';
    let serverReservations = [];
    globalThis.fetch = async (url, opts = {}) => {
      const target = String(url);
      seenRequests.push({
        url: target,
        unlock: opts.headers?.['X-Hidden-Unlock'],
        agentToken: opts.headers?.['X-Agent-Token'],
        idempotencyKey: opts.headers?.['Idempotency-Key'],
      });
      if (target === '/api/reservations/request') {
        if (probeBehavior === '404') return new Response(JSON.stringify({ detail: 'not found' }), { status: 404 });
        if (probeBehavior === '409') return new Response(JSON.stringify({ detail: 'conflict' }), { status: 409 });
        if (probeBehavior === '500') return new Response(JSON.stringify({ detail: 'server error' }), { status: 500 });
        if (probeBehavior === '503') return new Response(JSON.stringify({ detail: 'in progress' }), { status: 503 });
        if (probeBehavior === '200') {
          return new Response(JSON.stringify({
            ports: [28000],
            reservations: [{ port: 28000, token: 'recovered-tok', expires_at: null }],
          }), { status: 200 });
        }
      }
      if (target.startsWith('/api/port-rules')) return new Response(JSON.stringify({ rules: [], readonly: false }));
      if (target.startsWith('/api/reservations')) return new Response(JSON.stringify({ reservations: serverReservations }));
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

    const tokenField = root.querySelector('[name="agentToken"]');
    tokenField.value = 'typed-agent-secret';
    clickDiscard();
    await drain();

    let stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending.key, 'stuck-idempotency-key');
    assert.equal(stored.tokens[24001], 'keep-token');
    assert.deepEqual(confirmPrompts, ['manage.discardNotFoundConfirm']);
    const probeReq = seenRequests.find(r => r.url === '/api/reservations/request');
    assert.equal(probeReq.agentToken, 'typed-agent-secret');
    assert.equal(probeReq.idempotencyKey, 'stuck-idempotency-key');

    window.confirm = msg => { confirmPrompts.push(msg); return true; };
    clickDiscard();
    await drain();
    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending, undefined);
    assert.equal(stored.tokens[24001], 'keep-token');
    assert.equal(confirmPrompts.length, 2);
    assert.equal(retryBtn.hidden, true);
    assert.equal(discardBtn.hidden, true);

    // 2. Probe returns 200 (server had committed) -> recovers tokens without confirming.
    confirmPrompts.length = 0;
    probeBehavior = '200';
    serverReservations = [{ port: 28000, label: 'perm', machine: 'localhost', is_reservation: true, expires_at: null }];
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'committed-key', body: { count: 1 } },
    }));
    await page.open('reservations');
    clickDiscard();
    await drain();

    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending, undefined);
    assert.equal(stored.tokens[28000], 'recovered-tok');
    assert.equal(stored.tokens[24001], 'keep-token');
    assert.equal(confirmPrompts.length, 0);
    assert.match(root.querySelector('[role="status"]').textContent, /manage\.recoveredPending/);
    const expiryFilter = root.querySelector('[data-filter="expiry"]');
    expiryFilter.value = 'permanent';
    root.dispatchEvent({ type: 'change', target: { dataset: { filter: 'expiry' } } });
    const releaseBtn = root.querySelector('[data-action="release"][data-port="28000"]');
    assert.ok(releaseBtn && !releaseBtn.hasAttribute('disabled'));

    // 3. Probe returns 409 (already released/expired) -> clears without prompt.
    probeBehavior = '409';
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'stale-key', body: { count: 1 } },
    }));
    await page.open('reservations');
    clickDiscard();
    await drain();
    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending, undefined);
    assert.equal(confirmPrompts.length, 0);

    // 4. Probe returns 503 (in flight) -> prompts with discardConfirm; cancel preserves pending.
    probeBehavior = '503';
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'inflight-key', body: { count: 1 } },
    }));
    await page.open('reservations');
    window.confirm = msg => { confirmPrompts.push(msg); return false; };
    clickDiscard();
    await drain();
    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending.key, 'inflight-key');
    assert.deepEqual(confirmPrompts, ['manage.discardConfirm']);

    // 5. Probe fails with 500 -> prompts with discardConfirm; cancel preserves key, confirm discards key.
    confirmPrompts.length = 0;
    probeBehavior = '500';
    sessionStorage.setItem('port-light-reservation-session', JSON.stringify({
      tokens: { 24001: 'keep-token' },
      pending: { key: 'uncertain-key', body: { count: 1 } },
    }));
    await page.open('reservations');
    window.confirm = msg => { confirmPrompts.push(msg); return false; };
    clickDiscard();
    await drain();
    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending.key, 'uncertain-key');
    assert.deepEqual(confirmPrompts, ['manage.discardConfirm']);

    window.confirm = msg => { confirmPrompts.push(msg); return true; };
    clickDiscard();
    await drain();
    stored = JSON.parse(sessionStorage.getItem('port-light-reservation-session'));
    assert.equal(stored.pending, undefined);
    assert.equal(stored.tokens[24001], 'keep-token');
    assert.equal(confirmPrompts.length, 2);

    await page.open('conflicts');
    const portsCall = seenRequests.find(r => r.url.startsWith('/api/ports?'));
    assert.ok(portsCall, 'conflicts view fetches /api/ports');
    assert.match(portsCall.url, /include_hidden=true/);
    assert.equal(portsCall.unlock, 'local-secret');
    assert.match(root.innerHTML, /24500/);
  } finally {
    globalThis.fetch = savedFetch;
    S.hiddenUnlock = savedUnlock;
    S.meta = savedMeta;
    window.confirm = savedConfirm;
    sessionStorage.removeItem('port-light-reservation-session');
  }
});
