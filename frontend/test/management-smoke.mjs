/* Real browser coverage for grouping, rule edits and reservation ownership. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.PYTHON || (existsSync(join(root, '.venv/bin/python')) ? join(root, '.venv/bin/python') : 'python');
const socket = createServer().listen(0, '127.0.0.1');
await once(socket, 'listening');
const port = socket.address().port;
await new Promise(resolve => socket.close(resolve));
const base = 'http://127.0.0.1:' + port;
const child = spawn(python, ['scripts/dev.py', 'preview', '--port', String(port)], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
let logs = ''; child.stdout.on('data', data => { logs += data; }); child.stderr.on('data', data => { logs += data; });
let browser;
try {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    try { if ((await (await fetch(base + '/api/health')).json()).status === 'ok') { ready = true; break; } } catch {}
    if (child.exitCode !== null) throw new Error(logs);
    await delay(100);
  }
  assert.ok(ready, logs);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await expect(page.locator('.port-cell')).toHaveCount(259);
  await page.locator('#group-mode').selectOption('service');
  await expect(page.locator('.port-run summary')).toContainText(['20000–20255']);
  await page.locator('.port-run summary').click();
  await expect(page.locator('.port-cell[data-port="20128"]')).toBeVisible();
  await page.locator('.port-cell[data-port="20128"]').click();
  await expect(page).toHaveURL(/#\/port\/20128$/);
  await page.goto(base);
  await page.locator('#search').fill('20255');
  await expect(page.locator('.search-hit[data-port="20255"]')).toBeVisible();
  await page.goto(base + '/#/manage/conflicts');
  await expect(page.locator('.compose-conflict')).toHaveCount(2);
  const suggest = page.locator('[data-form="suggest"]').first();
  await suggest.locator('[name="rule"]').selectOption('development');
  await suggest.getByRole('button').click();
  await expect(page.locator('.snippet-result pre').first()).toContainText('published: "20256"');
  await page.goto(base + '/#/manage/rules');
  const form = page.locator('[data-form="rule"]');
  await form.locator('[name="name"]').fill('infra');
  await form.locator('[name="start"]').fill('28000');
  await form.locator('[name="end"]').fill('28005');
  await form.getByRole('button').click();
  await expect(page.locator('.manage-card')).toHaveCount(2);
  // Renaming preserves identity and project assignments; collisions keep both rules intact.
  const ruleSnapshot = async () => (await (await fetch(base + '/api/port-rules')).json()).rules;
  for (const [oldName, newName] of [['infra', 'infrastructure'], ['development', 'dev-range']]) {
    await page.locator('.manage-card').filter({ has: page.getByText(oldName, { exact: true }) }).locator('[data-action="editRule"]').click();
    await form.locator('[name="name"]').fill(newName);
    await form.getByRole('button').click();
    await expect.poll(async () => (await ruleSnapshot()).map(rule => rule.name)).not.toContain(oldName);
    assert.equal((await ruleSnapshot()).length, 2);
  }
  const beforeCollision = await ruleSnapshot();
  await page.locator('.manage-card').filter({ has: page.getByText('dev-range', { exact: true }) }).locator('[data-action="editRule"]').click();
  await form.locator('[name="name"]').fill('infrastructure');
  const rejected = page.waitForResponse(response => response.url().endsWith('/api/port-rules') && response.request().method() === 'PUT');
  await form.getByRole('button').click();
  assert.equal((await rejected).status(), 422);
  assert.deepEqual(await ruleSnapshot(), beforeCollision);
  await page.goto(base + '/#/manage/reservations');
  await expect(page.locator('#reservation-list .manage-card')).toHaveCount(1);
  const reserve = page.locator('[data-form="reserve"]');
  await reserve.locator('[name="label"]').fill('browser test');
  await reserve.locator('[name="rule"]').selectOption('infrastructure');
  await reserve.locator('[name="count"]').fill('2');
  // Commit on the server, then lose the response. Reload and retry the retained key.
  let dropped = false;
  await page.route('**/api/reservations', async route => {
    if (route.request().method() !== 'POST' || dropped) return route.continue();
    dropped = true; await route.fetch(); await route.abort('failed');
  });
  await reserve.getByRole('button').click();
  await expect.poll(async () => (await (await fetch(base + '/api/reservations')).json()).reservations.length).toBe(3);
  await page.reload();
  await page.locator('[data-action="retry"]').click();
  await expect(page.locator('[data-action="release"]:enabled')).toHaveCount(2);
  assert.equal((await (await fetch(base + '/api/reservations')).json()).reservations.length, 3);
  const other = await browser.newPage();
  await other.goto(base + '/#/manage/reservations');
  await expect(other.locator('[data-action="release"]')).toHaveCount(2);
  await expect(other.locator('[data-action="release"]:enabled')).toHaveCount(0);
  await other.close();
  await page.locator('[data-filter="expiry"]').selectOption('permanent');
  await expect(page.locator('#reservation-list .manage-card')).toHaveCount(1);
  await page.locator('[data-filter="expiry"]').selectOption('expiring');
  await expect(page.locator('#reservation-list .manage-card')).toHaveCount(2);
  await page.locator('[data-action="release"][data-port="28000"]').click();
  await expect(page.locator('[data-action="release"]')).toHaveCount(1);
  await page.locator('[data-action="release"][data-port="28001"]').click();
  await expect(page.locator('[data-action="release"]')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/#/manage/conflicts');
  await expect(page.locator('.compose-conflict')).toHaveCount(2);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  // A rebuild of the fleet boards must preserve the user's expanded range.
  const peerResponse = await fetch(base + '/api/hosts', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ peers: [{ name: 'Same-host test peer', url: base }] }) });
  assert.equal(peerResponse.status, 200);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  await expect(page.locator('.host-board')).toHaveCount(2);
  await page.locator('#group-mode').selectOption('service');
  const localRun = page.locator('#host-grid-local .port-run').first();
  if (!await localRun.evaluate(el => el.open)) await localRun.locator('summary').click();
  await expect(localRun.locator('[data-port="20128"]')).toBeVisible();
  await fetch(base + '/api/manual-ports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ port: 29099, label: 'changed snapshot' }) });
  await page.locator('#btn-refresh').click();
  await expect(page.locator('#host-grid-local [data-port="29099"]')).toBeVisible();
  await expect(page.locator('#host-grid-local .port-run [data-port="20128"]')).toBeVisible();
  assert.equal(await page.evaluate(async () => {
    const { escapeHtml } = await import('/static/js/text.js?v=96');
    const raw = '" onmouseover="alert(1)" <b>';
    const div = document.createElement('div');
    div.innerHTML = '<input value="' + escapeHtml(raw) + '">';
    return div.firstChild.value === raw && !div.firstChild.hasAttribute('onmouseover');
  }), true);
  // The credential field appears only for instances configured to require it.
  await page.route('**/api/meta', async route => {
    const response = await route.fetch();
    const body = await response.json(); body.automation.agent_token = true;
    await route.fulfill({ response, json: body });
  });
  await page.goto(base + '/#/manage/reservations');
  await page.reload();
  const tokenField = page.locator('[name="agentToken"]');
  await expect(tokenField).toBeVisible();
  await tokenField.fill('temporary-browser-test-token');
  let sentToken;
  await page.route('**/api/reservations', async route => {
    if (route.request().method() === 'POST') sentToken = route.request().headers()['x-agent-token'];
    await route.continue();
  });
  await page.locator('[data-form="reserve"] [name="rule"]').selectOption('infrastructure');
  await page.locator('[data-form="reserve"] button').click();
  await expect(page.locator('[data-action="release"]:enabled')).toHaveCount(1);
  assert.equal(sentToken, 'temporary-browser-test-token');
  assert.equal(await page.evaluate(() => JSON.stringify(sessionStorage).includes('temporary-browser-test-token')), false);
  await page.locator('[data-action="release"]:enabled').click();
  await expect(page.locator('[data-action="release"]:enabled')).toHaveCount(0);
  assert.deepEqual(errors, []);
  console.log('Management smoke passed: range grouping, direct search/detail, conflict snippet, rules, lost-response retry, tab ownership, expiry filters, release and mobile layout.');
} finally {
  if (browser) await browser.close();
  if (child.exitCode === null) {
    const exited = once(child, 'exit'); child.kill('SIGTERM');
    const timeout = setTimeout(() => child.kill('SIGKILL'), 10000);
    await exited; clearTimeout(timeout);
  }
}
