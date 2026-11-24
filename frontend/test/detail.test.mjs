import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { S } = await import('../js/state.js?v=' + version);
const { renderDetail, showPortDetail, syncOpenDetail, unlockHidden } = await import('../js/detail.js?v=' + version);
const { portFromList } = await import('../js/grid.js?v=' + version);
const detail = document.getElementById('detail-content');
const PEER = 'abcdef123456';
const flush = () => new Promise(resolve => setImmediate(resolve));
const row = port => ({ port, status: 'configured', source_type: 'manual', manual_label: 'demo' });

beforeEach(() => {
  Object.assign(S, { selectedPort: 45000, selectedHostId: 'local', currentData: null,
    hostMaps: {}, hostCatalog: { peers: [] }, route: { name: 'grid' }, meta: {},
    hiddenUnlock: '', pendingAfterUnlock: null, detailShownPort: null });
  detail.innerHTML = '';
  document.activeElement = null;
  window.confirm = () => true;
});

test('drawer buttons send hide, unhide, rename and delete requests', async () => {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, ...opts });
    return { ok: false, status: 500 }; // avoid polling; also exercise the error UI
  };
  for (const [selector, event, hidden] of [
    ['[data-hide-port]', 'click', false], ['[data-unhide-port]', 'click', true],
    ['[data-label-form]', 'submit', false], ['[data-delete-port]', 'click', false],
  ]) {
    renderDetail({ ...row(45000), is_hidden: hidden });
    const control = detail.querySelector(selector);
    assert.ok(control, selector);
    control.dispatchEvent({ type: event, preventDefault() {} });
    await flush();
    assert.ok(detail.querySelector('.detail-error'));
  }
  assert.deepEqual(calls.filter(c => c.method).map(c => [c.method, c.url]), [
    ['POST', '/api/hidden/45000'], ['DELETE', '/api/hidden/45000'],
    ['PATCH', '/api/manual-ports/45000'], ['DELETE', '/api/manual-ports/45000'],
  ]);
});

test('hidden write denial opens unlock dialog with a retry', async () => {
  S.meta.hidden_unlock_required = true;
  globalThis.fetch = async () => ({ ok: false, status: 403 });
  await window._portLightHide(45000);
  assert.equal(typeof S.pendingAfterUnlock, 'function');
});

test('peer without data never reuses the local row or exposes mutation controls', async () => {
  S.currentData = { ports: [row(45000)] };
  S.selectedHostId = PEER;
  assert.equal(portFromList(45000, PEER), null);
  globalThis.fetch = async () => ({ ok: false, status: 404 });
  showPortDetail(45000);
  await flush();
  assert.match(detail.innerHTML, /detail.notFound/);
  assert.equal(detail.querySelector('[data-hide-port]'), null);
  assert.equal(detail.querySelector('[data-delete-port]'), null);
});

test('same port on another host fetches fresh detail', async () => {
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(url);
    return { ok: true, json: async () => url.includes('/history') ? { events: [] } : row(45000) };
  };
  showPortDetail(45000);
  await flush();
  S.selectedHostId = PEER;
  showPortDetail(45000);
  await flush();
  assert.ok(calls.includes('/api/hosts/' + PEER + '/ports/45000?include_hidden=true'));
});

test('history uses the selected host and ignores responses for a previous drawer', async () => {
  const pending = [];
  globalThis.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
  renderDetail(row(45000));
  S.selectedHostId = PEER;
  renderDetail(row(45000));
  assert.equal(pending[1].url, '/api/hosts/' + PEER + '/ports/45000/history?hours=24');
  const body = text => ({ ok: true, json: async () => ({ events: [{ ts: 1, state: 'used', holders: [text] }] }) });
  pending[1].resolve(body('peer-holder'));
  await flush();
  const history = detail.querySelector('#detail-history');
  assert.match(history.innerHTML, /peer-holder/);
  pending[0].resolve(body('local-holder'));
  await flush();
  assert.doesNotMatch(history.innerHTML, /local-holder/);
});

test('owned reservations do not offer manual overwrite or deletion', () => {
  globalThis.fetch = async () => ({ ok: false });
  renderDetail({ ...row(45000), is_reservation: true });
  assert.equal(detail.querySelector('[data-label-form]'), null);
  assert.equal(detail.querySelector('[data-delete-port]'), null);
  assert.match(detail.innerHTML, /detail.reservationHint/);
});

test('unavailable occupancy never renders a free port or mutation controls', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 503 });
  showPortDetail(45000);
  await flush();
  assert.match(detail.innerHTML, /scanner.snapshotUnavailable/);
  assert.doesNotMatch(detail.innerHTML, /status.free/);
  assert.equal(detail.querySelector('[data-hide-port]'), null);
});

test('unlockHidden validates against local even when a remote host is focused and refreshes the fleet', async () => {
  sessionStorage.removeItem('port-light-hidden-unlock');
  S.hostCatalog = {
    local: { id: 'local', name: 'Hub', local: true },
    peers: [{ id: PEER, name: 'Peer Box' }],
  };
  S.focusHostId = PEER;
  S.showHidden = false;
  S.hiddenUnlock = '';

  const pwdInput = document.getElementById('unhide-password');
  pwdInput.value = 'bogus-password';
  const bogusCalls = [];
  globalThis.fetch = async url => {
    bogusCalls.push(String(url));
    // Even if the peer were unlocked, local rejects the password.
    const locked = String(url).includes('/api/hosts/local/');
    return new Response(JSON.stringify({ ports: [], summary: { hidden_locked: locked } }));
  };
  await unlockHidden();
  assert.ok(bogusCalls[0].startsWith('/api/hosts/local/ports?'), 'must probe local, not focused peer');
  assert.equal(sessionStorage.getItem('port-light-hidden-unlock'), null);
  assert.equal(S.hiddenUnlock, '');
  assert.equal(S.showHidden, false);

  pwdInput.value = 'valid-local-secret';
  const okCalls = [];
  globalThis.fetch = async (url, opts = {}) => {
    okCalls.push({ url: String(url), unlock: opts.headers?.['X-Hidden-Unlock'] });
    if (String(url).includes('/api/hosts/' + PEER + '/ports')) {
      return new Response(JSON.stringify({
        ports: [{ port: 9443, status: 'used', is_hidden: true, process: 'vault' }],
        summary: { hidden_locked: false, scan_complete: true, used: 1, configured: 0, free: 99, hidden: 1 },
      }));
    }
    return new Response(JSON.stringify({
      ports: [{ port: 8443, status: 'used', is_hidden: true, process: 'admin' }],
      summary: { hidden_locked: false, scan_complete: true, used: 1, configured: 0, free: 99, hidden: 1 },
    }));
  };
  await unlockHidden();
  assert.equal(sessionStorage.getItem('port-light-hidden-unlock'), 'valid-local-secret');
  assert.equal(S.showHidden, true);
  assert.ok(okCalls.some(c => c.url.includes('/api/hosts/' + PEER + '/ports') && c.unlock === 'valid-local-secret'));
  assert.equal(S.hostMaps[PEER]?.data?.ports?.[0]?.port, 9443);
});

test('detail drawer renders other machines from loaded hostMaps with zero extra network requests', async () => {
  const PEER_FREE = '112233445566';
  const PEER_UNLOADED = '665544332211';
  const PEER_STALE = '998877665544';
  S.hostCatalog = {
    local: { id: 'local', name: 'Hub', local: true },
    peers: [
      { id: PEER, name: 'Apps <Node>' },
      { id: PEER_FREE, name: 'Backup NAS' },
      { id: PEER_STALE, name: 'Edge Box' },
      { id: PEER_UNLOADED, name: 'Offline Node' },
    ],
  };
  S.hostMaps = {
    local: { data: { ports: [row(45000)], summary: { scan_complete: true } } },
    [PEER]: { data: { ports: [{ port: 45000, status: 'used', process: ' caddy <edge> ' }], summary: { scan_complete: true } } },
    [PEER_FREE]: { data: { ports: [], summary: { scan_complete: true, stale: false } } },
    [PEER_STALE]: { data: { ports: [], summary: { scan_complete: true, stale: true } } },
  };
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(String(url));
    return { ok: false, status: 404 };
  };
  renderDetail(row(45000));
  await flush();

  // Only the local port history endpoint is fetched; zero requests go to peers.
  assert.deepEqual(calls, ['/api/hosts/local/ports/45000/history?hours=24']);
  const section = detail.querySelector('[data-other-machines]');
  assert.ok(section, 'renders otherMachines section');
  assert.match(detail.innerHTML, /detail\.otherMachines/);
  assert.doesNotMatch(detail.innerHTML, /<Node>|<edge>/, 'escapes peer and holder names');
  assert.match(detail.innerHTML, /Apps &lt;Node&gt;/);
  assert.match(detail.innerHTML, /caddy &lt;edge&gt;/);

  const peerLinks = section.querySelectorAll('.detail-other-machine');
  assert.equal(peerLinks.length, 3, 'lists only other machines with loaded data');
  assert.equal(peerLinks[0].getAttribute('href'), '#/h/' + PEER + '/port/45000');
  assert.ok(peerLinks[0].querySelector('.tag.used'));
  assert.ok(peerLinks[1].querySelector('.tag.free'));
  assert.ok(peerLinks[2].querySelector('.tag.unknown'));
});

test('renderDetail escapes peer numeric fields and allowlists status classes', () => {
  globalThis.fetch = async () => ({ ok: false });
  renderDetail({
    port: '<img src=x onerror=alert(1)>',
    status: 'used" onclick="alert(1)',
    pid: '<svg onload=alert(1)>',
    source_type: 'docker',
    containers: [{
      name: 'web',
      status: 'running" onmouseover="alert(1)',
      image: 'nginx',
      container_port: '<script>1</script>',
    }, {
      name: 'proto',
      status: 'constructor',
      image: 'busybox',
    }],
    compose_configs: [{
      project_name: 'proj',
      service_name: 'svc',
      compose_file: 'compose.yaml',
      container_port: '<iframe src=x>',
    }],
  });
  assert.doesNotMatch(detail.innerHTML, /<(?:img|svg|script|iframe)\b|"[^"]*\s+on(?:click|mouseover)=/i);
  assert.doesNotMatch(detail.innerHTML, /onclick=|onmouseover=/i);
  assert.match(detail.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(detail.innerHTML, /&lt;svg onload=alert\(1\)&gt;/);
  assert.match(detail.innerHTML, /&lt;script&gt;1&lt;\/script&gt;/);
  assert.match(detail.innerHTML, /&lt;iframe src=x&gt;/);
  assert.ok(detail.querySelector('.tag.unknown'), 'unknown status maps to allowlisted unknown class');
  assert.equal(detail.querySelectorAll('.tag.exited').length, 2, 'invalid/prototype container statuses map to exited');
});

test('port history resolves status.* keys from en.json instead of raw summary.* keys', async () => {
  const prevI18n = window.PortLightI18n;
  const english = JSON.parse(readFileSync(new URL('../locales/en.json', import.meta.url), 'utf8'));
  window.PortLightI18n = {
    locale: () => 'en',
    t(key, vars = {}) {
      const raw = key.split('.').reduce((acc, part) => acc?.[part], english);
      const text = typeof raw === 'string' ? raw : key;
      return text.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? '{' + name + '}'));
    },
  };
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        events: [
          { ts: 1700000000, state: 'used', holders: ['nginx'] },
          { ts: 1700000060, state: 'configured', holders: ['app'] },
          { ts: 1700000120, state: 'free', holders: [] },
        ],
      }),
    });
    renderDetail(row(45000));
    await flush();
    const history = detail.querySelector('#detail-history');
    assert.match(history.innerHTML, />in use</);
    assert.match(history.innerHTML, />configured</);
    assert.match(history.innerHTML, />free</);
    assert.doesNotMatch(history.innerHTML, /summary\.|status\./);
  } finally {
    window.PortLightI18n = prevI18n;
  }
});

test('otherMachines shows peer error state instead of stale cached occupancy or free', async () => {
  const PEER_DOWN = '112233445566';
  const PEER_AUTH = '665544332211';
  S.hostCatalog = {
    local: { id: 'local', name: 'Hub', local: true },
    peers: [
      { id: PEER_DOWN, name: 'Down Box' },
      { id: PEER_AUTH, name: 'Auth Box' },
    ],
  };
  S.hostMaps = {
    local: { data: { ports: [row(45000)], summary: { scan_complete: true } } },
    [PEER_DOWN]: {
      data: { ports: [{ port: 45000, status: 'used', process: 'stale-daemon' }], summary: { scan_complete: true } },
      error: 'down',
    },
    [PEER_AUTH]: { error: 'auth' },
  };
  globalThis.fetch = async () => ({ ok: false, status: 404 });
  renderDetail(row(45000));
  await flush();
  const downLink = detail.querySelector('[data-other-host="' + PEER_DOWN + '"]');
  const authLink = detail.querySelector('[data-other-host="' + PEER_AUTH + '"]');
  assert.ok(downLink);
  assert.ok(authLink);
  assert.equal(downLink.getAttribute('data-other-error'), 'down');
  assert.equal(authLink.getAttribute('data-other-error'), 'auth');
  assert.doesNotMatch(detail.innerHTML, /stale-daemon/);
  assert.match(detail.innerHTML, new RegExp('data-other-host="' + PEER_DOWN + '"[^>]*>[\\s\\S]*?hosts\\.unreachable[\\s\\S]*?<\\/a>'));
  assert.match(detail.innerHTML, new RegExp('data-other-host="' + PEER_AUTH + '"[^>]*>[\\s\\S]*?hosts\\.authFailed[\\s\\S]*?<\\/a>'));
  assert.ok(downLink.querySelector('.tag.unknown'));
  assert.ok(authLink.querySelector('.tag.unknown'));
  assert.equal(downLink.querySelector('.tag.used'), null);
  assert.equal(downLink.querySelector('.tag.free'), null);
});

test('syncOpenDetail avoids refetching an already-loaded free port while updating peer changes', async () => {
  S.route = { name: 'port', port: 45000, hostId: 'local' };
  S.selectedPort = 45000;
  S.selectedHostId = 'local';
  S.hostCatalog = {
    local: { id: 'local', name: 'Hub', local: true },
    peers: [{ id: PEER, name: 'Peer Box' }],
  };
  S.hostMaps = {
    local: { data: { ports: [], summary: { scan_complete: true, stale: false, hidden_locked: false } } },
  };
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(String(url));
    if (String(url).includes('/history')) return { ok: true, json: async () => ({ events: [] }) };
    return { ok: true, json: async () => ({ port: 45000, status: 'free', source_type: 'unknown' }) };
  };
  showPortDetail(45000);
  await flush();
  assert.equal(calls.filter(u => !u.includes('/history')).length, 1);
  assert.match(detail.innerHTML, /status\.free/);

  syncOpenDetail();
  syncOpenDetail();
  await flush();
  assert.equal(calls.filter(u => !u.includes('/history')).length, 1, 'must not refetch free port on repeated renders');
  assert.doesNotMatch(detail.innerHTML, /detail\.loading/);

  S.hostMaps[PEER] = {
    data: { ports: [{ port: 45000, status: 'used', process: 'peer-api' }], summary: { scan_complete: true } },
  };
  syncOpenDetail();
  await flush();
  assert.equal(calls.filter(u => !u.includes('/history')).length, 1, 'peer update refreshes drawer without refetching port');
  assert.match(detail.innerHTML, /peer-api/);
});

test('renderDetail distinguishes intra-project conflicts and renders rule_violations', () => {
  globalThis.fetch = async () => ({ ok: false });
  renderDetail({
    port: 8080,
    status: 'configured',
    source_type: 'docker',
    conflict: true,
    compose_configs: [
      { project_dir: 'apps/stack', project_name: 'stack', service_name: 'web', compose_file: 'compose.yaml' },
      { project_dir: 'apps/stack', project_name: 'stack', service_name: 'api', compose_file: 'compose.yaml' },
    ],
    rule_violations: [
      { rule: 'apps<1>', project: 'stack', service: 'web', start: 30000, end: 30999 },
    ],
  });
  const conflictBox = detail.querySelector('[data-compose-conflict]');
  assert.ok(conflictBox);
  assert.match(detail.innerHTML, /data-compose-conflict[^>]*>[\s\S]*?detail\.conflictServicesHint/);
  assert.doesNotMatch(detail.innerHTML, /detail\.conflictHint/);
  const ruleBox = detail.querySelector('[data-rule-violation]');
  assert.ok(ruleBox);
  assert.match(detail.innerHTML, /data-rule-violation[^>]*>[\s\S]*?manage\.outsideRule/);
  assert.match(detail.innerHTML, /stack \/ web/);
  assert.match(detail.innerHTML, /apps&lt;1&gt;/);
  assert.match(detail.innerHTML, /30000–30999/);

  renderDetail({
    port: 8080,
    status: 'configured',
    source_type: 'docker',
    conflict: true,
    compose_configs: [
      { project_dir: 'apps/a', project_name: 'a', service_name: 'web', compose_file: 'a/compose.yaml' },
      { project_dir: 'apps/b', project_name: 'b', service_name: 'web', compose_file: 'b/compose.yaml' },
    ],
  });
  assert.ok(detail.querySelector('[data-compose-conflict]'));
  assert.match(detail.innerHTML, /data-compose-conflict[^>]*>[\s\S]*?detail\.conflictHint/);
});

test('locked and unlocked 404 responses render unknown/locked status instead of free or unhide', async () => {
  S.currentData = { ports: [], summary: { scan_complete: true, stale: false, hidden_locked: true } };
  globalThis.fetch = async () => ({ ok: false, status: 404 });
  showPortDetail(45000);
  await flush();
  assert.ok(detail.querySelector('.tag.unknown'));
  assert.equal(detail.querySelector('.tag.free'), null);
  assert.match(detail.innerHTML, /legend\.locked/);
  assert.doesNotMatch(detail.innerHTML, /status\.free/);
  assert.match(detail.innerHTML, /detail\.hiddenLocked/);
  assert.equal(detail.querySelector('[data-unhide-port]'), null);
  assert.equal(detail.querySelector('[data-hide-port]'), null);

  S.currentData = { ports: [], summary: { scan_complete: true, stale: false, hidden_locked: false } };
  S.selectedPort = 45001;
  showPortDetail(45001);
  await flush();
  assert.ok(detail.querySelector('.tag.unknown'));
  assert.equal(detail.querySelector('.tag.free'), null);
  assert.match(detail.innerHTML, /status\.unknown/);
  assert.doesNotMatch(detail.innerHTML, /status\.free/);
  assert.match(detail.innerHTML, /detail\.notFound/);
  assert.equal(detail.querySelector('[data-unhide-port]'), null);
  assert.equal(detail.querySelector('[data-hide-port]'), null);
});

test('port history ignores mismatched port payloads and prototype state keys', async () => {
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ port: 9999, events: [{ ts: 1700000000, state: 'used', holders: ['wrong'] }] }),
  });
  renderDetail(row(45000));
  await flush();
  const wrongHistory = detail.querySelector('#detail-history');
  assert.equal(wrongHistory.hasAttribute('hidden'), true);
  assert.equal(wrongHistory.innerHTML, '');

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ port: 45000, events: [{ ts: 1700000000, state: 'constructor', holders: ['app'] }] }),
  });
  renderDetail(row(45000));
  await flush();
  const protoHistory = detail.querySelector('#detail-history');
  assert.equal(protoHistory.hidden, false);
  assert.match(protoHistory.innerHTML, /constructor/);
  assert.doesNotMatch(protoHistory.innerHTML, /function Object/);
});
