/* One real-browser flow against a temporary multi-host Port-Light fleet. */
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
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await expect(page.locator('[data-i18n="settings.scanners.invalid"]')).toHaveCount(0);
  await expect.poll(async () => (await fetch(peer + '/api/ports/suggest')).status).toBe(200);

  const extraPeers = await Promise.all(Array.from({ length: 6 }, (_, i) => startHost('Peer ' + (i + 2))));
  const peerRows = [peer, ...extraPeers].map((url, i) => ({
    id: 'peer000' + (i + 1), name: 'Peer ' + (i + 1), url,
  }));
  const hub = await startHost('Hub', peerRows);
  page.on('console', message => {
    const expectedConflict = message.location().url === hub + '/api/manual-ports/batch' && message.text().includes('409');
    if (message.type() === 'error' && !expectedConflict) errors.push(message.text());
  });
  await page.goto(hub);
  await expect(page.locator('.host-board')).toHaveCount(8);
  await expect(page.locator('#host-switcher')).toBeHidden();
  await expect(page.locator('.host-board-description')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const id of ['local', 'peer0001', 'peer0007']) {
    await expect(page.locator('.host-board[data-host="' + id + '"]')).toBeVisible();
  }
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.setViewportSize({ width: 1280, height: 900 });
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
  let releaseSave;
  let saveHeld = false;
  const saveGate = new Promise(resolve => { releaseSave = resolve; });
  const holdSettingsSave = async route => {
    if (route.request().method() === 'PUT') {
      saveHeld = true;
      await saveGate;
    }
    await route.continue();
  };
  await page.route(hub + '/api/settings', holdSettingsSave);
  try {
    await page.locator('input[name="host_name"]').fill('Pending hub name');
    await expect.poll(() => saveHeld).toBe(true);
    await page.getByRole('link', { name: /^Port-Light/ }).click();
    await page.getByRole('link', { name: 'Settings', exact: true }).click();
    await page.getByRole('tab', { name: 'Occupancy', exact: true }).click();
    await expect(page.locator('input[name="host_name"]')).toHaveValue('Pending hub name');
  } finally {
    releaseSave();
    await page.unrouteAll({ behavior: 'wait' });
  }
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  assert.equal((await (await fetch(hub + '/api/settings')).json()).values.host_name, 'Pending hub name');
  await page.locator('input[name="host_name"]').fill('Hub');
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  const peerRow = page.locator('details.peer-row').first();
  await peerRow.locator('summary').click();
  await peerRow.locator('[data-peer-field="username"]').fill('smoke-user');
  const peerPassword = peerRow.locator('[data-peer-field="password"]');
  await peerPassword.fill('smoke-password');
  await expect(page.locator('#peers-status')).toHaveClass(/is-ok/);
  await expect(peerPassword).toHaveValue('smoke-password');
  await expect(peerRow.locator('[data-peer-clear-auth]')).toBeVisible();
  await peerRow.locator('[data-peer-field="name"]').focus();
  await expect(peerPassword).toHaveValue('');
  await peerRow.locator('[data-peer-clear-auth]').click();
  await expect(page.locator('#peers-status')).toHaveClass(/is-ok/);
  assert.equal((await (await fetch(hub + '/api/hosts')).json()).peers[0].has_auth, false);
  await expect(page.locator('details.peer-row').first().locator('[data-peer-clear-auth]')).toHaveCount(0);
  await expect(page.locator('[data-settings-panel="occupancy"] [data-setting="host_layout"]')).toHaveCount(0);
  await page.locator('input[name="host_description"]').fill('Local · 100.64.0.10');
  await page.locator('details.peer-row').first().locator('summary').click();
  await page.locator('details.peer-row').first().locator('[data-peer-field="description"]').fill('Tailscale · 100.64.0.12');
  await page.locator('details.peer-row').first().locator('summary').click();
  await expect(page.locator('input[name="local_scanners"]')).toHaveCount(3);
  await expect(page.locator('input[name="local_scanners"][value="listen"]')).toBeChecked();
  await expect(page.locator('input[name="local_scanners"][value="compose"]')).toBeChecked();
  await expect(page.locator('input[name="local_scanners"][value="docker"]')).not.toBeChecked();
  await expect(page.locator('.scanner-option .scanner-state.disabled')).toHaveCount(1);
  await expect(page.locator('details.peer-row')).toHaveCount(7);
  await expect(page.locator('details.peer-row[open]')).toHaveCount(0);
  const refreshSlider = page.locator('[data-refresh-slider]');
  await expect(refreshSlider).toBeVisible();
  await refreshSlider.click();
  assert.equal(await refreshSlider.evaluate(element => getComputedStyle(element).boxShadow), 'none');
  assert.equal(await refreshSlider.evaluate(element => getComputedStyle(element).outlineStyle), 'none');
  await refreshSlider.focus();
  await page.keyboard.press('End');
  await expect(page.locator('[data-refresh-hidden]')).toHaveValue('300000');
  await expect(page.locator('#refresh-capacity')).toContainText('slower polling reduces traffic');
  await page.keyboard.press('Home');
  await expect(page.locator('[data-refresh-hidden]')).toHaveValue('5000');
  await refreshSlider.evaluate(element => {
    element.value = '2';
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('[data-refresh-hidden]')).toHaveValue('15000');
  await expect(page.locator('#refresh-capacity')).toContainText('Recommended up to 18');
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.getByRole('tab', { name: 'Automation', exact: true }).click();
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight));
  await page.evaluate(() => { document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight; });
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await page.getByRole('tab', { name: 'Appearance', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.getByRole('tab', { name: 'Automation', exact: true }).click();
  await page.evaluate(() => { document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight; });
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
  await page.evaluate(() => { location.hash = '#/settings/advanced'; });
  await expect(page.getByRole('tab', { name: 'Advanced', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole('tab', { name: 'Appearance', exact: true }).click();
  const layoutChoice = page.locator('[data-setting="host_layout"]');
  await expect(layoutChoice).toBeVisible();
  await expect(layoutChoice.locator('input[value="waterfall"]')).toBeChecked();
  await expect(page.locator('[data-settings-panel="appearance"] .settings-card').filter({ has: layoutChoice }).locator('h2')).toHaveText('Cards');
  await expect(layoutChoice.locator('.layout-option-selected:visible')).toHaveCount(1);
  await layoutChoice.locator('[data-i18n="choice.tabs"]').click();
  const tabsOption = layoutChoice.locator('.layout-option').filter({ has: page.locator('input[value="tabs"]') });
  await expect(tabsOption.locator('input')).toBeChecked();
  await expect(tabsOption.locator('.layout-option-selected')).toBeVisible();
  await expect(layoutChoice.locator('.layout-option-selected:visible')).toHaveCount(1);
  assert.equal(await tabsOption.evaluate(element => getComputedStyle(element).outlineStyle), 'none');
  assert.equal(await tabsOption.locator('input').evaluate(element => getComputedStyle(element).boxShadow), 'none');
  await tabsOption.locator('input').press('ArrowLeft');
  await expect(layoutChoice.locator('input[value="waterfall"]')).toBeChecked();
  assert.equal(await layoutChoice.locator('.layout-option:has(input:checked)').evaluate(element => getComputedStyle(element).outlineStyle), 'solid');
  await layoutChoice.locator('input[value="waterfall"]').press('ArrowRight');
  await expect(tabsOption.locator('input')).toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(layoutChoice.locator('.layout-option')).toHaveCount(2);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  assert.equal((await (await fetch(hub + '/api/settings')).json()).values.refresh_ms, 15000);
  await page.goto(hub);
  await expect(page.locator('.host-chip')).toHaveCount(8);
  await expect(page.locator('.host-board')).toHaveCount(1);
  await expect(page.locator('.host-board-description')).toHaveText('Local · 100.64.0.10');
  await page.locator('#host-tab-local').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('.host-board')).toHaveAttribute('data-host', 'peer0001');
  await expect(page.locator('#host-tab-peer0001')).toBeFocused();
  await expect(page.locator('.host-board-description')).toHaveText('Tailscale · 100.64.0.12');
  await page.reload();
  await expect(page.locator('.host-board-description')).toHaveText('Tailscale · 100.64.0.12');
  await page.locator('#host-tab-local').click();

  await page.goto(hub + '/#/settings/appearance');
  await expect(page.locator('#theme-editor')).toBeVisible();
  let editorSettingsWrites = 0;
  const countEditorWrites = request => {
    if (request.url() === hub + '/api/settings' && request.method() === 'PUT') editorSettingsWrites++;
  };
  page.on('request', countEditorWrites);
  await page.locator('#theme-editor summary').click();
  await page.locator('[data-editor-save]').click();
  await expect(page.locator('#theme-editor-status')).toHaveClass(/is-error/);
  await page.locator('#editor-name').fill('Draft palette');
  await page.locator('[data-editor-hex="bg"]').fill('#223344');
  const exportDownload = page.waitForEvent('download');
  await page.locator('[data-editor-export]').click();
  await exportDownload;
  await expect(page.locator('#theme-editor-status')).toHaveClass(/is-ok/);
  await delay(900); // Longer than the general settings debounce.
  assert.equal(editorSettingsWrites, 0);
  assert.equal(await page.locator('html').evaluate(el => el.style.getPropertyValue('--bg')), '#223344');
  page.off('request', countEditorWrites);
  await page.locator('#theme-editor summary').click();
  const palettes = ['gruvbox', 'catppuccin', 'solarized', 'nord', 'dracula',
    'tokyo-night', 'one-dark', 'everforest', 'rose-pine', 'kanagawa'];
  const rootElement = page.locator('html');
  await page.locator('.theme-picker-core [data-theme-preview="light"]').click();
  for (const family of palettes) {
    const swatch = page.locator('[data-theme-preview="' + family + '-light"]');
    await expect(swatch).not.toHaveClass(/is-unavailable/);
    await swatch.click();
    await expect(rootElement).toHaveAttribute('data-mode', 'light');
    await expect(rootElement).toHaveAttribute('data-palette', family);
    const colors = await swatch.evaluate(element => {
      const pageStyle = getComputedStyle(document.documentElement);
      const previewStyle = getComputedStyle(element);
      return ['bg', 'used', 'configured', 'free'].map(key => [
        pageStyle.getPropertyValue('--' + key).trim(),
        previewStyle.getPropertyValue('--preview-' + (key === 'configured' ? 'cfg' : key)).trim(),
      ]);
    });
    assert.ok(colors.every(([actual, preview]) => actual === preview), family + ' swatch colors');
  }
  await page.locator('.theme-swatch[data-theme-preview="nord-light"]').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.reload();
  await expect(rootElement).toHaveAttribute('data-palette', 'nord');
  await expect(rootElement).toHaveAttribute('data-mode', 'light');
  await page.locator('.theme-picker-core [data-theme-preview="system"]').click();
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(rootElement).toHaveAttribute('data-mode', 'dark');
  await expect(rootElement).toHaveAttribute('data-palette', 'nord');
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(rootElement).toHaveAttribute('data-mode', 'light');
  await expect(rootElement).toHaveAttribute('data-palette', 'nord');

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
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.goto(hub);
  await page.locator('[data-host-switch="local"]').click();
  const warning = page.locator('[data-host-error="local"] .scan-warning');
  const trigger = warning.locator('summary');
  const warningInfo = warning.locator('.scan-warning-info');
  const explanation = warning.locator('.scan-warning-panel');
  await expect(warning).toBeVisible();
  await trigger.hover();
  await expect(explanation).toBeHidden();
  await warningInfo.hover();
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

  await page.goto(hub + '/#/doctor');
  await expect(page.locator('#doctor-page h1')).toHaveText('Setup / Doctor');
  await expect(page.locator('.doctor-check')).toHaveCount(6);
  await expect(page.locator('#doctor-copy')).toBeEnabled();
  await expect(page.locator('#doctor-download')).toHaveAttribute('href', '/api/doctor/report');
  await expect(page.locator('.doctor-report-preview')).toContainText('"schema_version": 1');
  const diagnosticReport = await (await fetch(hub + '/api/doctor/report')).text();
  assert.doesNotMatch(diagnosticReport, /smoke-user|smoke-password|Local ·|Tailscale ·/);
  assert.ok(!diagnosticReport.includes(peer));
  assert.ok(!diagnosticReport.includes(hub));

  await page.getByRole('link', { name: 'Settings', exact: true }).click();
  await page.getByRole('tab', { name: 'Occupancy', exact: true }).click();
  await expect(page.locator('input[name="host_name"]')).toBeVisible();
  assert.deepEqual(errors, []);
  console.log('Browser smoke passed: adaptive waterfall, independent settings and peer saves, draft theme feedback, mobile waterfall, persisted machine descriptions, slider focus and capacity guidance, keyboard host switch, invalid scanner recovery, detail, saved label, all light palettes, saved/system appearance, mixed bind addresses, batch conflict and retry, scan guidance, Doctor checks and sanitized report.');
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
