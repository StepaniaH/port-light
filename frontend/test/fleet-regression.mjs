/* Exercise the real multi-machine preview and grouped grids at desktop/mobile sizes. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const socket = createServer().listen(0, '127.0.0.1');
await once(socket, 'listening');
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const child = spawn(process.env.PYTHON || (existsSync(join(root, '.venv/bin/python')) ? join(root, '.venv/bin/python') : 'python'), ['scripts/dev.py', 'preview', '--fleet', '--port', String(port)], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
let logs = ''; child.stdout.on('data', data => { logs += data; }); child.stderr.on('data', data => { logs += data; });
let browser;
async function put(path, body) {
  const response = await fetch(base + path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  assert.equal(response.status, 200); return response.json();
}
try {
  for (let i = 0; i < 150 && !logs.includes('four simulated machines'); i++) {
    if (child.exitCode !== null) throw new Error(logs);
    await delay(100);
  }
  assert.ok(logs.includes('four simulated machines'), logs);
  const catalog = await (await fetch(base + '/api/hosts')).json();
  assert.equal(catalog.peers.length, 3);
  for (const peer of catalog.peers) assert.match(peer.id, /^[a-z0-9]{8,16}$/);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1000 } });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await expect(page.locator('.host-board')).toHaveCount(4);
  const peers = catalog.peers;
  await expect(page.locator('.app-actions > .toolbar-btn, #btn-more')).toHaveCount(3);
  for (const id of ['btn-refresh', 'btn-manage', 'btn-more']) assert.ok((await page.locator('#' + id).innerText()).trim());
  await page.locator('#btn-more').click();
  await expect(page.locator('#toolbar-menu')).toBeVisible();
  await expect(page.locator('.action-menu-item')).toHaveCount(5);
  await page.locator('#btn-add').click();
  await expect(page.locator('#add-modal')).toBeVisible();
  await expect(page.locator('#toolbar-menu')).toBeHidden();
  await expect(page.locator('#add-port')).toBeFocused();
  await page.locator('#add-cancel').click();
  await page.locator('#btn-more').focus();
  await page.locator('#btn-more').press('ArrowDown');
  await expect(page.locator('#btn-add')).toBeFocused();
  await page.locator('#btn-add').press('End');
  await expect(page.locator('#btn-settings')).toBeFocused();
  await page.locator('#btn-settings').press('Escape');
  await expect(page.locator('#toolbar-menu')).toBeHidden();
  await expect(page.locator('#btn-more')).toBeFocused();
  await page.locator('#btn-more').click();
  await page.locator('#search').click();
  await expect(page.locator('#toolbar-menu')).toBeHidden();
  await page.locator('#btn-more').click();
  await page.locator('#btn-settings').click();
  await expect(page).toHaveURL(/#\/settings\/appearance$/);
  await expect(page.locator('#toolbar-menu')).toBeHidden();
  await page.goto(base);

  for (const width of [1920, 2560, 1280, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const mode of ['none', 'project', 'service']) {
      await page.locator('#group-mode').selectOption(mode);
      const grid = page.locator('#host-grid-local');
      if (mode !== 'none') {
        const run = grid.locator('.port-run').first();
        if (!await run.evaluate(el => el.open)) await run.locator('summary').click();
        const columns = await grid.evaluate(el => [getComputedStyle(el).gridTemplateColumns.split(' ').length,
          getComputedStyle(el.querySelector('.port-run .group-ports')).gridTemplateColumns.split(' ').length]);
        assert.equal(columns[1], columns[0], `${width}px ${mode}: expanded range must retain parent columns`);
      }
      await expect.poll(async () => Math.max(...await page.locator('.host-board').evaluateAll(els => els.map(el => el.getBoundingClientRect().right)))).toBeGreaterThan(width - 32);

      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await expect.poll(() => page.locator('.host-board').evaluateAll(els => {
        const boxes = els.map(el => el.getBoundingClientRect());
        return boxes.every((a, i) => boxes.every((b, j) => i === j || a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1));
      })).toBe(true);
    }
  }
  // A two-machine fleet must expand instead of reserving empty columns.
  await put('/api/hosts', { peers: [peers[0]] });
  await page.setViewportSize({ width: 2560, height: 1000 });
  await page.reload();
  await expect(page.locator('.host-board')).toHaveCount(2);
  const widths = await page.locator('.host-board').evaluateAll(els => els.map(el => el.clientWidth));
  assert.ok(widths.every(width => width > 1200), JSON.stringify(widths));
  const columnsByDensity = [];
  for (const density of ['loose', 'standard', 'compact']) {
    await put('/api/settings', { grid_density: density });
    await page.reload();
    await expect(page.locator('.host-board')).toHaveCount(2);
    const wide = await page.locator('#host-grid-local').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
    await page.setViewportSize({ width: 1200, height: 1000 });
    const narrow = await page.locator('#host-grid-local').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
    assert.ok(wide > narrow, density + ': card columns must adapt to available width');
    columnsByDensity.push(wide);
    await page.setViewportSize({ width: 2560, height: 1000 });
  }
  assert.ok(columnsByDensity[2] > columnsByDensity[0], 'compact density must fit more cards than loose');
  if (process.env.PORT_LIGHT_TEST_SCREENSHOTS) await page.screenshot({ path: join(process.env.PORT_LIGHT_TEST_SCREENSHOTS, 'two-host-adaptive.png'), fullPage: true });
  await put('/api/settings', { grid_density: 'standard' });
  await put('/api/hosts', { peers });
  await put('/api/settings', { host_layout: 'tabs' });
  await page.reload();
  await expect(page.locator('[data-host-switch]')).toHaveCount(4);
  // Each first click must select its destination, including peer-to-peer transitions.
  for (const id of [peers[0].id, peers[1].id, peers[2].id, peers[1].id, 'local', peers[0].id]) {
    await page.locator(`[data-host-switch="${id}"]`).click();
    await expect(page.locator('[data-host-switch][aria-selected="true"]')).toHaveAttribute('data-host-switch', id);
    await expect(page.locator('.host-board')).toHaveAttribute('data-host', id);
    await page.locator('#btn-refresh').click();
    await expect(page.locator('.host-board')).toHaveAttribute('data-host', id);
  }
  await page.locator('[data-host-switch][aria-selected="true"]').press('ArrowRight');
  await expect(page.locator('.host-board')).toHaveAttribute('data-host', peers[1].id);
  await page.locator('#group-mode').selectOption('none');
  await page.locator('.port-cell[data-port="3000"]').click();
  await expect(page).toHaveURL(new RegExp('/h/' + peers[1].id + '/port/3000$'));
  await page.locator(`[data-host-switch="${peers[2].id}"]`).click();
  await expect(page.locator('.host-board')).toHaveAttribute('data-host', peers[2].id);
  for (const mode of ['light', 'dark']) {
    await put('/api/settings', { theme_mode: mode });
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-mode', mode);
    for (const section of ['conflicts', 'reservations', 'rules']) {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(base + '/#/manage/' + section);
      await expect(page.locator('.manage-tabs [aria-current="page"]')).toHaveAttribute('href', '#/manage/' + section);
      await expect(page.locator('.settings-card').first()).toBeVisible();
      const colors = await page.locator('.settings-card').first().evaluate(el => ({ background: getComputedStyle(el).backgroundColor, color: getComputedStyle(el).color }));
      assert.notEqual(colors.background, 'rgba(0, 0, 0, 0)');
      if (process.env.PORT_LIGHT_TEST_SCREENSHOTS) await page.screenshot({ path: join(process.env.PORT_LIGHT_TEST_SCREENSHOTS, `${section}-${mode}.png`), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), section + ' overflow');
      if (process.env.PORT_LIGHT_TEST_SCREENSHOTS) await page.screenshot({ path: join(process.env.PORT_LIGHT_TEST_SCREENSHOTS, `${section}-${mode}-mobile.png`), fullPage: true });
      await page.locator('#btn-more').click();
      await expect(page.locator('#toolbar-menu')).toBeVisible();
      const menu = await page.locator('#toolbar-menu').boundingBox();
      assert.ok(menu.x >= 0 && menu.x + menu.width <= 390);
      await page.locator('#btn-more').press('Escape');
    }
  }
  assert.deepEqual(errors, []);
  console.log('Fleet regression passed: full-width 2/4-host layouts, grouped range columns, mobile density, first-click tabs, keyboard, refresh, detail switching, management light/dark/mobile.');
} finally {
  await browser?.close();
  child.kill('SIGTERM');
  await Promise.race([once(child, 'exit'), delay(12000)]);
  if (child.exitCode === null) child.kill('SIGKILL');
}
