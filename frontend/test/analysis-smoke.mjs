/* Exercise the bundled workbench through the application shell and its opt-in synthetic host. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { chromium, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const project = fileURLToPath(new URL('../../', import.meta.url));
const python = process.env.PYTHON || (existsSync(join(project, '.venv/bin/python')) ? join(project, '.venv/bin/python') : 'python');
const temporary = await mkdtemp(join(tmpdir(), 'port-light-workbench-smoke-'));
const screenshots = process.env.PORT_LIGHT_SCREENSHOT_DIR || join(temporary, 'screenshots');
const children = [];
let browser;

async function freePort() {
  const socket = createServer().listen(0, '127.0.0.1');
  await once(socket, 'listening');
  const port = socket.address().port;
  await new Promise(done => socket.close(done));
  return port;
}

async function startHost({ name = 'demo', demo = true } = {}) {
  const port = await freePort();
  const data = join(temporary, name + '-host-data');
  await mkdir(data, { recursive: true });
  await writeFile(join(data, 'port_light.json'), JSON.stringify({
    settings: { locale: 'zh-CN', theme_mode: 'dark', theme_palette: '', port_range_start: 1, port_range_end: 65535 },
  }));
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(PORT_LIGHT_|AUTH_|HIDDEN_|AGENT_|WEBHOOK_|COMPOSE_|DOCKER_|URL_|PORT_RANGE_|HISTORY_|SETTINGS_)/.test(key)));
  Object.assign(env, {
    PYTHONPATH: project,
    PYTHONDONTWRITEBYTECODE: '1',
    PORT_LIGHT_DATA_DIR: data,
    PORT_LIGHT_SETTINGS_SOURCE: 'file',
    PORT_LIGHT_PORT: String(port),
    PORT_LIGHT_ANALYSIS_DEMO: demo ? '1' : '0',
    PORT_LIGHT_PREVIEW_FIXTURE: 'workbench',
  });
  const child = spawn(python, ['-m', 'uvicorn', 'tests.analysis.browser_host:create_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--no-access-log'], {
    cwd: data, env, stdio: ['ignore', 'pipe', 'pipe'],
  });
  children.push(child);
  let logs = '';
  child.stdout.on('data', chunk => { logs = (logs + chunk).slice(-8000); });
  child.stderr.on('data', chunk => { logs = (logs + chunk).slice(-8000); });
  const base = 'http://127.0.0.1:' + port;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) throw new Error('Workbench test host exited:\n' + logs);
    try {
      const response = await fetch(base + '/__preview/workbench/state');
      if (response.ok) return base;
    } catch (_) { /* The listener is still starting. */ }
    await delay(100);
  }
  throw new Error('Workbench test host did not start:\n' + logs);
}

async function responseJson(response, expected = 200) {
  assert.equal(response.status(), expected, await response.text());
  return response.json();
}

async function advance(page, base, phase) {
  const body = await responseJson(await page.request.post(base + '/__preview/workbench/advance', { data: { phase } }));
  assert.equal(body.synthetic, true);
  assert.equal(body.phase, phase);
  return body;
}

async function capture(page) {
  await page.locator('#analysis-capture-button').click();
  await expect(page.locator('#analysis-results')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('#analysis-result-state')).not.toHaveText(/Working|采集中/);
}

async function workbenchReports(page, base) {
  return responseJson(await page.request.get(base + '/analysis/api/workbench/reports?limit=20'));
}

async function rowWithBothSides(page, selector) {
  const rows = page.locator(selector + ' li');
  const count = await rows.count();
  for (let index = 0; index < count; index += 1) {
    const row = rows.nth(index);
    if (await row.locator('.comparison-facts[data-capture-side="saved"]').count() &&
        await row.locator('.comparison-facts[data-capture-side="new"]').count()) return row;
  }
  throw new Error('No comparison row exposes both saved and new frozen facts in ' + selector);
}

async function assertTheme(page) {
  const colors = await page.evaluate(() => {
    const panelElement = document.querySelector('.analysis-panel');
    const probe = document.createElement('span');
    probe.style.backgroundColor = 'var(--card)'; probe.style.color = 'var(--text)';
    panelElement.append(probe);
    const expected = getComputedStyle(probe);
    const panel = getComputedStyle(panelElement);
    const trigger = getComputedStyle(document.querySelector('#analysis-protocol + .pl-select-trigger'));
    const task = getComputedStyle(document.querySelector('.task-choice[aria-pressed="true"]'));
    const result = {
      panel: panel.backgroundColor,
      card: expected.backgroundColor,
      trigger: trigger.color,
      text: expected.color,
      task: task.backgroundColor,
      triggerBackground: trigger.backgroundColor,
    };
    probe.remove();
    return result;
  });
  assert.notEqual(colors.panel, '');
  assert.notEqual(colors.card, '');
  assert.notEqual(colors.trigger, '');
  assert.notEqual(colors.text, '');
  assert.notEqual(colors.task, '');
  assert.notEqual(colors.triggerBackground, '');
  assert.equal(colors.panel, colors.card);
  assert.equal(colors.trigger, colors.text);
  assert.equal(colors.panel, colors.task);
}

async function scrollToHeading(page, selector) {
  await page.locator(selector).evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -64));
}

try {
  await mkdir(screenshots, { recursive: true });
  const base = await startHost();
  const fixture = await (await fetch(base + '/__preview/workbench/state')).json();
  assert.equal(fixture.synthetic, true, 'The browser fixture must explicitly identify its synthetic sources');
  assert.equal(fixture.phase, 'baseline');

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let capturePosts = 0;
  let aiPosts = 0;
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    if (/\/analysis\/api\/workbench\/captures(?:\?|$)/.test(request.url())) capturePosts += 1;
    if (/\/analysis\/api\/workbench\/captures\/[^/]+\/ai$/.test(request.url())) aiPosts += 1;
  });

  await page.goto(base + '/#/port/8081', { waitUntil: 'networkidle' });
  const workspaceLink = page.locator('#detail-content a[href="#/workspace/port-analysis/port/8081"]');
  await expect(workspaceLink).toBeVisible();
  await workspaceLink.click();
  await expect(page.locator('#workspace-page h1')).toContainText('排障工作台');
  await expect(page.locator('#analysis-scope-kind')).toHaveValue('single_port');
  await expect(page.locator('#analysis-single-port')).toHaveValue('8081');
  assert.equal(capturePosts, 0, 'A one-port deep link only presets scope; it never collects automatically');
  await expect(page.locator('#ui-links a')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('header')).toHaveCount(1);
  await expect(page.locator('h1:visible')).toHaveCount(1);
  assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.analysis-workspace [id]')]
    .map(node => node.id).filter(id => document.querySelectorAll('[id="' + id + '"]').length !== 1)), []);

  // A normal multi-port operation can paste individual ports and ranges without repeated clicking.
  await page.locator('#analysis-scope-kind').selectOption('selected_ports');
  await expect(page.locator('#analysis-selected-ports-fields')).toBeVisible();
  await page.locator('#analysis-bulk-ports').fill('8080, 8081, 65535');
  await page.locator('#analysis-add-bulk-ports').click();
  await expect(page.locator('#analysis-selected-ports .port-chip')).toHaveCount(3);
  await capture(page);
  const queueCount = await page.locator('#analysis-priority-queue .problem-card').count();
  assert.ok(queueCount >= 1 && queueCount <= 5, 'The priority queue stays bounded to five cards');
  await expect(page.locator('#analysis-priority-queue')).toContainText('端口 8081 · TCP');
  assert.doesNotMatch(await page.locator('#analysis-priority-queue .problem-card').first().innerText(), /\b(?:current|event):/,
    'Visible problem evidence uses readable facts, not opaque evidence IDs');
  await expect(page.locator('#analysis-ai-payload-details')).toBeVisible();
  await page.locator('#analysis-consent').check();
  await expect(page.locator('#analysis-start-ai')).toBeEnabled();
  await page.locator('#analysis-protocol').selectOption('tcp');
  await expect(page.locator('#analysis-consent')).not.toBeChecked();
  await expect(page.locator('#analysis-ai-payload-details')).toBeHidden();

  // Save a complete all-known baseline. The screenshot uses only the local synthetic test host.
  await page.locator('#analysis-scope-kind').selectOption('all_known');
  await page.locator('#analysis-protocol').selectOption('all');
  await capture(page);
  await expect(page.locator('#analysis-priority-queue')).toContainText('端口 65535 · TCP');
  await page.locator('#analysis-save-report').click();
  await expect.poll(async () => (await workbenchReports(page, base)).reports.some(report => report.result_revision === 'rules-v1'),
    { timeout: 5000 }).toBe(true);
  const baselineReports = await workbenchReports(page, base);
  const baseline = baselineReports.reports.find(report => report.result_revision === 'rules-v1');
  assert.ok(baseline, 'The deterministic baseline is saved before optional AI');
  const baselineExport = await responseJson(await page.request.get(base + '/analysis/api/workbench/reports/' + encodeURIComponent(baseline.id) + '/export'));
  const baselineFrozen = JSON.stringify(baselineExport);
  assert.equal(baselineExport.capture.ai.status, 'not_started');
  await scrollToHeading(page, '#analysis-results-heading');
  await page.screenshot({ path: join(screenshots, 'desktop-queue-zh-CN.png'), fullPage: false });

  // The fixture resolves one Compose conflict while retaining a declaration mismatch: both comparison states must be factual.
  await advance(page, base, 'resolved');
  await page.locator('#analysis-recheck').click();
  await expect(page.locator('#analysis-comparison-section')).toBeVisible();
  assert.ok(await page.locator('#analysis-comparison-persisting li').count() > 0, 'The resolved recheck retains at least one independently observed condition');
  const notObserved = await rowWithBothSides(page, '#analysis-comparison-not-observed');
  await expect(notObserved.locator('.comparison-change')).toContainText('Compose 冲突 是 → 否');
  const savedBlock = notObserved.locator('.comparison-facts[data-capture-side="saved"]');
  const newBlock = notObserved.locator('.comparison-facts[data-capture-side="new"]');
  await expect(savedBlock).toHaveJSProperty('open', false);
  await expect(newBlock).toHaveJSProperty('open', false);
  await savedBlock.locator(':scope > summary').click();
  await newBlock.locator(':scope > summary').click();
  const savedFact = savedBlock.locator('.comparison-fact').first();
  const newFact = newBlock.locator('.comparison-fact').first();
  await expect(savedFact).toContainText('Compose 冲突：是');
  await expect(newFact).toContainText('Compose 冲突：否');
  assert.notEqual(await savedFact.innerText(), await newFact.innerText(), 'A resolved comparison shows changed frozen values, not only different timestamps');
  const currentEvidenceId = await newFact.getAttribute('data-evidence-id');
  assert.ok(currentEvidenceId);
  await notObserved.locator('.evidence-jump').click();
  await expect(page.locator('.facts-details')).toHaveJSProperty('open', true);
  await expect(page.locator('.facts-details [data-evidence-id="' + currentEvidenceId + '"]')).toBeVisible();
  await savedBlock.locator(':scope > summary').click();
  await newBlock.locator(':scope > summary').click();
  await scrollToHeading(page, '#analysis-comparison-heading');
  await page.screenshot({ path: join(screenshots, 'desktop-recheck-zh-CN.png'), fullPage: false });

  // Mobile starts with task, scope and the main action visible; the shared custom select stays in bounds and is keyboard-dismissable.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.analysis-task-panel').scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const protocolCombo = page.locator('#analysis-protocol + .pl-select-trigger');
  await protocolCombo.click();
  const listbox = page.getByRole('listbox', { name: '协议', exact: true });
  await expect(listbox).toBeVisible();
  const box = await listbox.boundingBox();
  assert.ok(box && box.x >= 0 && box.x + box.width <= 391 && box.y >= 0 && box.y + box.height <= 845);
  await protocolCombo.press('Escape');
  await page.screenshot({ path: join(screenshots, 'mobile-zh-CN.png'), fullPage: false });
  await page.setViewportSize({ width: 1440, height: 1100 });

  // Saving the recheck rules, then the completed AI selection, creates two immutable revisions for the same capture.
  await page.locator('#analysis-save-report').click();
  await expect(page.locator('#analysis-ai-payload-details')).toBeVisible();
  await page.locator('#analysis-consent').check();
  await expect(page.locator('#analysis-start-ai')).toBeEnabled();
  const beforeAiPosts = aiPosts;
  await page.locator('#analysis-start-ai').click();
  await expect(page.locator('#analysis-ai-recommendations')).toBeVisible({ timeout: 15000 });
  assert.equal(aiPosts, beforeAiPosts + 1, 'One consent produces one demo AI request');
  await expect(page.locator('#analysis-save-report')).toBeEnabled();
  await page.locator('#analysis-save-report').click();
  await expect(page.locator('#analysis-save-report')).toBeDisabled();
  await expect.poll(async () => (await workbenchReports(page, base)).reports.some(report => String(report.result_revision).startsWith('ai-')),
    { timeout: 5000 }).toBe(true);
  const originalAgain = await responseJson(await page.request.get(base + '/analysis/api/workbench/reports/' + encodeURIComponent(baseline.id) + '/export'));
  assert.equal(JSON.stringify(originalAgain), baselineFrozen, 'The original baseline report remains immutable after later AI results');

  // The recent-change task honors a selected history window and retains before/after evidence as facts.
  await advance(page, base, 'changed');
  await page.locator('#analysis-task-changes').click();
  await expect(page.locator('#analysis-window-fields')).toBeVisible();
  await page.locator('#analysis-history-hours').selectOption('6');
  await page.locator('#analysis-scope-kind').selectOption('single_port');
  await page.locator('#analysis-single-port').fill('8080');
  await capture(page);
  await expect(page.locator('#analysis-capture-meta')).toContainText('最近变更');
  const beforeAfter = page.locator('#analysis-priority-queue .fact-card').first();
  await expect(beforeAfter).toBeVisible();
  await beforeAfter.locator(':scope > summary').click();
  await expect(beforeAfter.locator('.comparison-fact').first()).toContainText('状态');
  await expect(beforeAfter.locator('.fact-raw').first()).toHaveJSProperty('open', false);

  // A 1,024-port range produces more than a thousand frozen facts but only renders the first page until asked.
  await page.locator('#analysis-task-triage').click();
  await page.locator('#analysis-scope-kind').selectOption('port_range');
  await page.locator('#analysis-range-start').fill('10000');
  await page.locator('#analysis-range-end').fill('11023');
  await page.locator('#analysis-protocol').selectOption('tcp');
  await capture(page);
  await page.locator('.facts-details').evaluate(node => { node.open = true; });
  await expect(page.locator('#analysis-facts .fact-card')).toHaveCount(20);
  assert.match(await page.locator('#analysis-facts-note').innerText(), /1025/);
  await page.locator('#analysis-show-more-facts').click();
  await expect(page.locator('#analysis-facts .fact-card')).toHaveCount(40);

  // A failed start response is recovered by one read; it is never retried as another model POST.
  await page.locator('#analysis-scope-kind').selectOption('all_known');
  await page.locator('#analysis-protocol').selectOption('all');
  await capture(page);
  let rejectedStarts = 0;
  await page.route('**/analysis/api/workbench/captures/*/ai', async route => {
    rejectedStarts += 1;
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'core_unavailable', message: 'synthetic response uncertainty' } }) });
  });
  await page.locator('#analysis-consent').check();
  await page.locator('#analysis-start-ai').click();
  await expect(page.locator('#analysis-results')).toBeVisible();
  await delay(450);
  assert.equal(rejectedStarts, 1, 'The client does not resend an uncertain AI POST');
  await page.unroute('**/analysis/api/workbench/captures/*/ai');

  // An access-loss receipt stops polling, removes disclosure-sensitive capture content, and enables a fresh capture.
  await capture(page);
  let unavailableReads = 0;
  await page.route('**/analysis/api/workbench/captures/*', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    unavailableReads += 1;
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: { code: 'capture_expired', message: 'synthetic unavailable receipt' } }) });
  });
  const beforeUnavailableAi = aiPosts;
  await page.locator('#analysis-consent').check();
  await page.locator('#analysis-start-ai').click();
  await expect(page.locator('#analysis-empty-result')).toBeVisible({ timeout: 9000 });
  await expect(page.locator('#analysis-results')).toBeHidden();
  await expect(page.locator('#analysis-capture-button')).toBeEnabled();
  assert.equal(aiPosts, beforeUnavailableAi + 1);
  assert.equal(unavailableReads, 1, 'A 404 receipt stops polling instead of creating an infinite retry loop');
  await page.unroute('**/analysis/api/workbench/captures/*');

  // Older single-port reports still use the local structured renderer and never echo free-form model text.
  const legacyPreview = await responseJson(await page.request.post(base + '/analysis/api/analysis/previews', {
    headers: { 'X-Port-Light-Analysis': '1' },
    data: { port: 8080, include_history: true, protocol: 'tcp', question_type: 'recorded_changes', max_records: 8 },
  }), 201);
  const legacyPath = base + '/analysis/api/analysis/' + encodeURIComponent(legacyPreview.id);
  await responseJson(await page.request.post(legacyPath + '/start', {
    headers: { 'X-Port-Light-Analysis': '1' }, data: { provider: 'demo', model: 'synthetic-demo', confirmed: true },
  }), 202);
  await expect.poll(async () => (await responseJson(await page.request.get(legacyPath))).status, { timeout: 10000 }).toBe('completed');
  await responseJson(await page.request.post(base + '/analysis/api/reports', {
    headers: { 'X-Port-Light-Analysis': '1' }, data: { completed_analysis_id: legacyPreview.id },
  }), 201);
  await page.goto(base + '/#/workspace/port-analysis', { waitUntil: 'networkidle' });
  await expect(page.locator('#analysis-legacy-reports')).toBeVisible();
  await page.locator('#analysis-legacy-reports > summary').click();
  await expect(page.locator('#analysis-legacy-report-list button')).toHaveCount(1);
  await page.locator('#analysis-legacy-report-list button').click();
  await expect(page.locator('#analysis-results')).toBeVisible();
  assert.ok(!(await page.locator('#workspace-page').innerText()).includes('UNTRUSTED'));

  // Every supported locale and palette mounts the same task-oriented controls and inherited theme.
  const catalogs = JSON.parse(await readFile(join(project, 'backend/analysis/static/messages.json'), 'utf8'));
  for (const [locale, messages] of Object.entries(catalogs)) {
    await responseJson(await page.request.put(base + '/api/settings', {
      data: { locale, theme_mode: locale === 'en' ? 'dark' : 'light', theme_palette: locale === 'ja' ? 'nord' : '' },
    }));
    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.locator('#analysis-task-triage')).toContainText(messages.workbench.task_triage);
    await assertTheme(page);
  }

  // Leaving a delayed request cannot repopulate an unmounted workspace.
  await responseJson(await page.request.put(base + '/api/settings', { data: { locale: 'zh-CN', theme_mode: 'dark', theme_palette: '' } }));
  await page.goto(base + '/#/workspace/port-analysis', { waitUntil: 'networkidle' });
  await page.route('**/analysis/api/workbench/captures', async route => { await delay(350); await route.continue().catch(() => {}); });
  await page.locator('#analysis-capture-button').click();
  await page.evaluate(() => { location.hash = '#/doctor'; });
  await expect(page.locator('#workspace-page')).toBeEmpty();
  await delay(500);
  await expect(page.locator('#workspace-page')).toBeEmpty();
  await page.unroute('**/analysis/api/workbench/captures');

  // A second local host runs in BYOK mode. The browser exercises the AI
  // settings route and credential UX, but the only AI start is intercepted
  // before it can reach any provider.
  const byokBase = await startHost({ name: 'byok', demo: false });
  const byokPage = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  byokPage.on('pageerror', error => errors.push(error.message));
  await byokPage.goto(byokBase + '/#/settings/analysis', { waitUntil: 'networkidle' });
  await expect(byokPage.locator('#analysis-ai-form')).toBeVisible();

  let rejectedSettingsSaves = 0;
  await byokPage.route('**/analysis/api/settings/ai', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    rejectedSettingsSaves += 1;
    await delay(180);
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'settings_unavailable', message: 'synthetic settings storage unavailable' } }),
    });
  });
  await byokPage.locator('#analysis-ai-provider').selectOption('openai');
  await byokPage.locator('#analysis-ai-model').fill('gpt-4.1-mini');
  await byokPage.locator('#analysis-ai-key').fill('fixture-browser-key-not-a-secret');
  await byokPage.locator('#analysis-ai-form button[type="submit"]').click();
  await expect(byokPage.locator('#analysis-ai-form button[type="submit"]')).toBeDisabled();
  await expect(byokPage.locator('.analysis-settings-status[role="alert"]')).toBeVisible();
  await expect(byokPage.locator('#analysis-ai-provider')).toHaveValue('openai');
  await expect(byokPage.locator('#analysis-ai-model')).toHaveValue('gpt-4.1-mini');
  await expect(byokPage.locator('#analysis-ai-key')).toHaveValue('');
  assert.equal(rejectedSettingsSaves, 1, 'A failed save retains only the non-secret draft');
  await byokPage.unroute('**/analysis/api/settings/ai');

  await byokPage.locator('#analysis-ai-key').fill('fixture-browser-key-not-a-secret');
  await byokPage.locator('#analysis-ai-form button[type="submit"]').click();
  await expect(byokPage.locator('.analysis-settings-status.is-ok')).toBeVisible();
  const firstProfile = await responseJson(await byokPage.request.get(byokBase + '/analysis/api/settings'));
  assert.equal(firstProfile.ai.configured, true);
  assert.equal(firstProfile.ai.provider, 'openai');
  assert.equal(firstProfile.ai.model, 'gpt-4.1-mini');
  assert.ok(firstProfile.ai.revision);
  assert.doesNotMatch(await byokPage.locator('body').innerText(), /fixture-browser-key-not-a-secret/);

  // A same-provider edit may retain the stored key while changing its revision.
  await byokPage.locator('#analysis-ai-model').fill('gpt-4.1-nano');
  await expect(byokPage.locator('#analysis-ai-key')).toHaveValue('');
  await byokPage.locator('#analysis-ai-form button[type="submit"]').click();
  await expect(byokPage.locator('.analysis-settings-status.is-ok')).toBeVisible();
  const changedProfile = await responseJson(await byokPage.request.get(byokBase + '/analysis/api/settings'));
  assert.equal(changedProfile.ai.model, 'gpt-4.1-nano');
  assert.notEqual(changedProfile.ai.revision, firstProfile.ai.revision);
  await byokPage.screenshot({ path: join(screenshots, 'ai-settings-zh-CN.png'), fullPage: false });

  // Saving rules preserves a live one-port capture. Changing the connection in
  // settings must reset consent on return without downgrading to the report.
  await byokPage.goto(byokBase + '/#/workspace/port-analysis/port/8081', { waitUntil: 'networkidle' });
  await expect(byokPage.locator('#analysis-scope-kind')).toHaveValue('single_port');
  await expect(byokPage.locator('#analysis-single-port')).toHaveValue('8081');
  await capture(byokPage);
  await expect(byokPage.locator('#analysis-ai-payload-summary')).toBeVisible();
  await byokPage.locator('#analysis-save-report').click();
  await expect(byokPage.locator('#analysis-recheck')).toBeEnabled();
  const savedVisit = await byokPage.evaluate(() => JSON.parse(sessionStorage.getItem('port-light-workbench-visit-v1') || 'null'));
  assert.equal(savedVisit.routePort, 8081);
  assert.equal(savedVisit.liveCapture, true);
  assert.ok(savedVisit.captureId && savedVisit.reportId);
  await byokPage.locator('#analysis-consent').check();
  await expect(byokPage.locator('#analysis-start-ai')).toBeEnabled();
  await byokPage.locator('#analysis-manage-ai-settings').click();
  await expect(byokPage.locator('#analysis-ai-form')).toBeVisible();
  await byokPage.locator('#analysis-ai-model').fill('gpt-4.1');
  await byokPage.locator('#analysis-ai-form button[type="submit"]').click();
  await expect(byokPage.locator('.analysis-settings-status.is-ok')).toBeVisible();

  await byokPage.goto(byokBase + '/#/workspace/port-analysis/port/8081', { waitUntil: 'networkidle' });
  await expect(byokPage.locator('#analysis-scope-kind')).toHaveValue('single_port');
  await expect(byokPage.locator('#analysis-single-port')).toHaveValue('8081');
  await expect(byokPage.locator('#analysis-results')).toBeVisible();
  await expect(byokPage.locator('#analysis-recheck')).toBeEnabled();
  await expect(byokPage.locator('#analysis-consent-field')).toBeVisible();
  await expect(byokPage.locator('#analysis-consent')).not.toBeChecked();
  await byokPage.screenshot({ path: join(screenshots, 'byok-analysis-summary-zh-CN.png'), fullPage: false });

  let revisionMismatchStarts = 0;
  await byokPage.route('**/analysis/api/workbench/captures/*/ai', async route => {
    revisionMismatchStarts += 1;
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'configuration_changed', message: 'synthetic revision mismatch' } }),
    });
  });
  await byokPage.locator('#analysis-consent').check();
  await expect(byokPage.locator('#analysis-start-ai')).toBeEnabled();
  await byokPage.locator('#analysis-start-ai').click();
  await expect(byokPage.locator('#analysis-consent')).not.toBeChecked();
  await expect(byokPage.locator('#analysis-start-ai')).toBeDisabled();
  await expect(byokPage.locator('#analysis-error')).not.toBeEmpty();
  assert.equal(revisionMismatchStarts, 1, 'A revision mismatch does not call or retry a real model');
  await byokPage.unroute('**/analysis/api/workbench/captures/*/ai');

  // If the temporary capture has expired, its saved report remains the
  // recovery fallback and stays read-only on the return route.
  await byokPage.goto(byokBase + '/#/settings/analysis/ai', { waitUntil: 'networkidle' });
  let expiredCaptureReads = 0;
  await byokPage.route('**/analysis/api/workbench/captures/*', async route => {
    if (route.request().method() !== 'GET') return route.continue();
    expiredCaptureReads += 1;
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'capture_expired', message: 'synthetic expired capture' } }),
    });
  });
  await byokPage.goto(byokBase + '/#/workspace/port-analysis/port/8081', { waitUntil: 'networkidle' });
  await expect(byokPage.locator('#analysis-results')).toBeVisible();
  await expect(byokPage.locator('#analysis-consent-field')).toBeHidden();
  assert.equal(expiredCaptureReads, 1, 'An expired live capture falls back to its saved report once');
  await byokPage.unroute('**/analysis/api/workbench/captures/*');

  await byokPage.goto(byokBase + '/#/settings/analysis/ai', { waitUntil: 'networkidle' });
  await expect(byokPage.locator('.analysis-settings-clear button')).toBeVisible();
  await byokPage.locator('.analysis-settings-clear button').click();
  await expect(byokPage.locator('#analysis-settings-ai')).toContainText('尚未保存');
  const clearedProfile = await responseJson(await byokPage.request.get(byokBase + '/analysis/api/settings'));
  assert.equal(clearedProfile.ai.configured, false);

  assert.deepEqual(errors, []);
  console.log('Workbench smoke passed: bundled workbench, synthetic runtime fixture, task/scope controls, consent invalidation, immutable reports, BYOK settings save/change/clear, live-capture settings return, revision mismatch recovery, cross-capture evidence, mobile bounds, lazy facts, retry recovery, legacy reports, seven locales, and abort handling.');
  if (process.env.PORT_LIGHT_SCREENSHOT_DIR) console.log('Screenshots: ' + screenshots);
} finally {
  if (browser) await browser.close();
  for (const child of children) if (child.exitCode === null) child.kill('SIGTERM');
  await Promise.all(children.map(child => child.exitCode !== null ? Promise.resolve() : once(child, 'exit')));
  await rm(temporary, { recursive: true, force: true });
}
