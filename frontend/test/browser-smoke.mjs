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

async function startHost(name, peers = []) {
  const data = join(temporary, name);
  const compose = join(data, 'compose');
  await mkdir(compose, { recursive: true });
  await writeFile(join(data, 'port_light.json'), JSON.stringify({
    manual_ports: [{ port: 42000, label: name + ' service', machine: 'localhost' }],
    hidden_ports: [], peers,
    settings: { port_range_start: 42000, port_range_end: 42010, locale: 'en', copy_on_click: false },
  }));
  const port = await unusedPort();
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(PORT_LIGHT_|AUTH_|HIDDEN_|AGENT_|WEBHOOK_|COMPOSE_|DOCKER_|URL_|PORT_RANGE_|HISTORY_)/.test(key)));
  const child = spawn(python, ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: root, env: { ...env, PORT_LIGHT_DATA_DIR: data, COMPOSE_SCAN_DIR: compose,
      PORT_LIGHT_SETTINGS_SOURCE: 'file', PORT_LIGHT_HOST_NAME: name, PORT_LIGHT_PORT: String(port),
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
  const peer = await startHost('Peer');
  const hub = await startHost('Hub', [{ id: 'peer0001', name: 'Peer', url: peer }]);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
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
  // Mobile host switching must close the local drawer and use the peer's row.
  await page.setViewportSize({ width: 800, height: 900 });
  await page.keyboard.press('Escape');
  await page.locator('[data-host-switch="peer0001"]').click();
  const peerCell = page.locator('#host-grid-peer0001 .port-cell[data-port="42000"]');
  await expect(peerCell).toBeVisible();
  await peerCell.click();
  await expect(detail).toContainText('Peer service');
  await expect(detail.locator('[data-label-form]')).toHaveCount(0);
  assert.deepEqual(errors, []);
  console.log('Browser smoke passed: startup, detail, saved label, host switch.');
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
