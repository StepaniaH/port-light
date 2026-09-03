/* One real-browser flow against two temporary Port-Light instances. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.PYTHON || (existsSync(join(root, '.venv/bin/python')) ? join(root, '.venv/bin/python') : 'python');
const temporary = await mkdtemp(join(tmpdir(), 'port-light-smoke-'));
const processes = [];
let browser;

async function unusedPort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function startHost(name, peers = [], scanners = 'listen,compose') {
  const data = join(temporary, name);
  const compose = join(data, 'compose');
  await mkdir(compose, { recursive: true });
  await writeFile(join(data, 'port_light.json'), JSON.stringify({
    manual_ports: [{ port: 42000, label: name + ' service', machine: 'localhost' }],
    hidden_ports: [], peers,
    settings: { port_range_start: 42000, port_range_end: 42010, locale: 'en', copy_on_click: false,
      show_bind_addresses: false, show_bind_ipv4: true, show_bind_ipv6: true },
  }));
  await writeFile(join(compose, 'compose.yaml'), `services:
  sample:
    image: example.invalid/service
    ports:
      - "42008:80"
      - "192.0.2.8:42008:80"
      - "[2001:db8:1234:5678::]:42008:80"
`);
  const port = await unusedPort();
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(PORT_LIGHT_|AUTH_|HIDDEN_|AGENT_|WEBHOOK_|COMPOSE_|DOCKER_|URL_|PORT_RANGE_|HISTORY_)/.test(key)));
  const child = spawn(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: root, env: { ...env, PORT_LIGHT_DATA_DIR: data, COMPOSE_SCAN_DIR: compose,
      PORT_LIGHT_SCANNERS: scanners, PORT_LIGHT_SETTINGS_SOURCE: 'file', PORT_LIGHT_HOST_NAME: name, PORT_LIGHT_PORT: String(port),
      DOCKER_HOST: 'unix://' + join(data, 'no-docker.sock'), HISTORY_RETENTION_DAYS: '7' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  processes.push(child);
  let logs = '';
  child.stdout.on('data', chunk => { logs = (logs + chunk).slice(-5000); });
  child.stderr.on('data', chunk => { logs = (logs + chunk).slice(-5000); });
  const url = 'http://127.0.0.1:' + port;
  for (let i = 0; i < 100; i++) {
    if (child.exitCode !== null) throw new Error('Server exited: ' + logs);
    try { if ((await fetch(url + '/api/health')).ok) return url; } catch {}
    await delay(100);
  }
  throw new Error('Server startup timed out: ' + logs);
}

try {
  const peer = await startHost('Peer', [], 'listen,comopse');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  assert.equal((await fetch(peer + '/api/ports/suggest')).status, 503);
  await page.goto(peer + '/#/settings/occupancy');
  await expect(page.locator('[data-i18n="settings.scanners.invalid"]')).toBeVisible();
  await expect(page.locator('input[name="local_scanners"]:checked')).toHaveCount(0);
  await page.locator('input[name="local_scanners"][value="listen"]').check();
  await page.locator('input[name="local_scanners"][value="compose"]').check();
  await page.locator('#settings-save').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await expect(page.locator('[data-i18n="settings.scanners.invalid"]')).toHaveCount(0);
  await expect.poll(async () => (await fetch(peer + '/api/ports/suggest')).status).toBe(200);

  const hub = await startHost('Hub', [{ id: 'peer0001', name: 'Peer', url: peer }]);
  page.on('console', message => {
    const expectedConflict = message.location().url === hub + '/api/manual-ports/batch' && message.text().includes('409');
    if (message.type() === 'error' && !expectedConflict) errors.push(message.text());
  });
  await page.goto(hub);
  const localCell = page.locator('#host-grid-local .port-cell[data-port="42000"]');
  await expect(localCell).toContainText('Hub service');
  await localCell.click();
  const detail = page.locator('#detail-panel');
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('Hub service');
  await detail.locator('[data-label-input]').fill('Updated service');
  await detail.locator('[data-label-form] button[type="submit"]').click();
  await expect(localCell).toContainText('Updated service');
  const saved = await (await fetch(hub + '/api/ports/42000')).json();
  assert.equal(saved.manual_label, 'Updated service');
  await page.keyboard.press('Escape');

  await page.goto(hub + '/#/settings/occupancy');
  await expect(page.locator('input[name="host_name"]')).toHaveValue('Hub');
  await expect(page.locator('input[name="local_scanners"]')).toHaveCount(3);
  await expect(page.locator('input[name="local_scanners"][value="listen"]')).toBeChecked();
  await expect(page.locator('input[name="local_scanners"][value="compose"]')).toBeChecked();
  await expect(page.locator('input[name="local_scanners"][value="docker"]')).not.toBeChecked();
  await expect(page.locator('.scanner-option .scanner-state.disabled')).toHaveCount(1);

  await page.goto(hub + '/#/settings/appearance');
  const showBinds = page.locator('input[name="show_bind_addresses"]');
  const showV4 = page.locator('input[name="show_bind_ipv4"]');
  const showV6 = page.locator('input[name="show_bind_ipv6"]');
  const families = page.locator('#bind-address-family-options');
  await expect(showBinds).not.toBeChecked();
  await expect(families).toBeHidden();
  await showBinds.check();
  await expect(families).toBeVisible();
  await expect(showV4).toBeChecked();
  await expect(showV6).toBeChecked();
  await expect(page.locator('[data-setting^="show_bind_"] .origin-hint')).toHaveCount(0);
  await page.locator('#settings-save').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.goto(hub);
  const boundCell = page.locator('#host-grid-local .port-cell[data-port="42008"]');
  await expect(boundCell.locator('.bind-address-row')).toHaveCount(2);
  await expect(boundCell).toContainText('2001:db8:…::');
  await expect(boundCell).toHaveAttribute('title', /2001:db8:1234:5678::/);
  await expect(localCell.locator('.bind-address-row')).toHaveCount(0);

  await page.goto(hub + '/#/settings/appearance');
  await expect(page.locator('[data-setting^="show_bind_"] .origin-hint')).toHaveCount(0);
  await showV4.uncheck();
  await showV6.uncheck();
  await expect(showBinds).toBeChecked();
  await showBinds.uncheck();
  await showBinds.check();
  await expect(showV4).not.toBeChecked();
  await expect(showV6).not.toBeChecked();
  await showV4.check();
  await page.locator('#settings-save').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.goto(hub);
  await expect(boundCell.locator('.bind-address-row')).toHaveCount(1);
  await expect(boundCell).toContainText('192.0.2.8');
  await expect(boundCell).not.toHaveAttribute('title', /IPv6/);

  await page.locator('#btn-free').click();
  await page.locator('#free-count').fill('2');
  await page.locator('#free-label').fill('Browser batch');
  await page.locator('#free-form button[type="submit"]').click();
  const reserve = page.locator('#free-results [data-reserve]').first();
  await expect(reserve).toBeVisible();
  // Claim the first planned port from another client before the browser submits.
  const [planned] = (await reserve.getAttribute('data-reserve')).split(':').map(Number);
  assert.equal((await fetch(hub + '/api/manual-ports', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port: planned, label: 'Other writer' }),
  })).status, 200);
  await reserve.click();
  await expect(page.locator('#free-error')).toContainText('no longer free');
  await expect(page.locator('#free-modal')).toBeVisible();
  let manual = await (await fetch(hub + '/api/manual-ports')).json();
  assert.equal(manual.manual_ports.filter(p => p.label === 'Browser batch').length, 0);
  await page.locator('#free-form button[type="submit"]').click();
  await expect(page.locator('#free-error')).toBeHidden();
  await reserve.click();
  await expect(page.locator('#free-modal')).toBeHidden();
  manual = await (await fetch(hub + '/api/manual-ports')).json();
  assert.equal(manual.manual_ports.filter(p => p.label === 'Browser batch').length, 2);
  assert.equal(manual.manual_ports.find(p => p.port === planned).label, 'Other writer');
  // Mobile host switching must close the local drawer and use the peer's row.
  await page.setViewportSize({ width: 800, height: 900 });
  await page.keyboard.press('Escape');
  await page.locator('[data-host-switch="peer0001"]').click();
  const peerCell = page.locator('#host-grid-peer0001 .port-cell[data-port="42000"]');
  await expect(peerCell).toBeVisible();
  await peerCell.click();
  await expect(detail).toContainText('Peer service');
  await expect(detail.locator('[data-label-form]')).toHaveCount(0);

  // Enable the intentionally unavailable Docker source to exercise real warnings.
  await page.goto(hub + '/#/settings/occupancy');
  await page.locator('input[name="local_scanners"][value="docker"]').check();
  await page.locator('#settings-save').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.goto(hub);
  await page.locator('[data-host-switch="local"]').click();
  const warning = page.locator('[data-host-error="local"] .scan-warning');
  const trigger = warning.locator('summary');
  const explanation = warning.locator('.scan-warning-panel');
  await expect(warning).toBeVisible();
  await trigger.hover();
  await expect(explanation).toBeVisible();
  await expect(explanation).toContainText('group_add');
  await expect(explanation).toContainText('docker compose restart does not apply');
  await trigger.focus();
  await trigger.press('Escape');
  await expect(explanation).toBeHidden();
  await trigger.click();
  await expect(explanation).toBeVisible();
  await trigger.click();
  await expect(explanation).toBeHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  await trigger.click();
  await expect(explanation).toBeVisible();
  const bounds = await explanation.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await explanation.locator('a[href="#/settings/occupancy"]').click();
  await expect(page.locator('input[name="host_name"]')).toBeVisible();
  assert.deepEqual(errors, []);
  console.log('Browser smoke passed: invalid scanner recovery, startup, detail, saved label, mixed bind addresses, batch conflict and retry, host switch, scan guidance and mobile layout.');
} finally {
  if (browser) await browser.close();
  await Promise.all(processes.map(async child => {
    if (child.exitCode !== null) return;
    const exited = once(child, 'exit');
    child.kill('SIGTERM');
    const timeout = setTimeout(() => child.kill('SIGKILL'), 5000);
    await exited;
    clearTimeout(timeout);
  }));
  await rm(temporary, { recursive: true, force: true });
}
