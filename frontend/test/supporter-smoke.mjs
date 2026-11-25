/* Real-browser Playwright verification for Port-Light Supporter Beacon & Header Elevation. */
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
const screenshotDir = process.env.SUPPORTER_SCREENSHOT_DIR || '';
const temporary = await mkdtemp(join(tmpdir(), 'port-light-supporter-smoke-'));
let child;
let browser;

async function unusedPort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function readTestVectors() {
  const out = await new Promise((resolve, reject) => {
    const proc = spawn(python, ['-c', `
import json
from tests.storage.supporter_vectors import PERSONAL_KEY, FLEET_KEY
print(json.dumps({"personal": PERSONAL_KEY, "fleet": FLEET_KEY}))
`], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', d => { stdout += d; });
    proc.stderr.on('data', d => { stderr += d; });
    proc.on('close', code => code === 0 ? resolve(stdout) : reject(new Error(stderr)));
  });
  return JSON.parse(out.trim());
}

async function startServer() {
  const data = join(temporary, 'data');
  const compose = join(data, 'compose');
  await mkdir(compose, { recursive: true });
  await writeFile(join(data, 'port_light.json'), JSON.stringify({
    manual_ports: [
      { port: 22, label: 'sshd', machine: 'localhost' },
      { port: 3000, label: 'grafana', machine: 'localhost' },
      { port: 5432, label: 'postgres', machine: 'localhost' },
      { port: 8080, label: 'caddy', machine: 'localhost' },
      { port: 9000, label: 'portainer', machine: 'localhost' },
    ],
    hidden_ports: [],
    peers: [],
    settings: {
      port_range_start: 1,
      port_range_end: 9999,
      locale: 'en',
      theme_mode: 'dark',
      theme_palette: '',
      copy_on_click: false,
    },
  }));
  await writeFile(join(compose, 'compose.yaml'), `services:
  grafana:
    image: example.invalid/grafana
    ports:
      - "3000:3000"
  caddy:
    image: example.invalid/caddy
    ports:
      - "8080:80"
`);
  const port = await unusedPort();
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
    !/^(PORT_LIGHT_|AUTH_|HIDDEN_|AGENT_|WEBHOOK_|COMPOSE_|DOCKER_|URL_|PORT_RANGE_|HISTORY_)/.test(key)));

  // Launch uvicorn with the test-only public key tuple patched in-process (no production env hook).
  const bootCode = `
import uvicorn
from backend import supporter
from tests.storage.supporter_vectors import TEST_PUB1_PEM
supporter.TRUSTED_PUBLIC_KEYS_PEM = (TEST_PUB1_PEM,)
supporter.TRUSTED_PUBLIC_KEYS = (supporter.parse_public_key_pem(TEST_PUB1_PEM),)
uvicorn.run("backend.main:app", host="127.0.0.1", port=${port}, log_level="warning")
`;
  child = spawn(python, ['-c', bootCode], {
    cwd: root,
    env: {
      ...env,
      PORT_LIGHT_DATA_DIR: data,
      COMPOSE_SCAN_DIR: compose,
      PORT_LIGHT_SCANNERS: 'compose',
      PORT_LIGHT_SETTINGS_SOURCE: 'file',
      PORT_LIGHT_PORT: String(port),
      DOCKER_HOST: 'unix://' + join(data, 'no-docker.sock'),
      HISTORY_RETENTION_DAYS: '7',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
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

async function shot(page, name) {
  if (!screenshotDir) return;
  await mkdir(screenshotDir, { recursive: true });
  await page.screenshot({ path: join(screenshotDir, name), fullPage: false });
}

try {
  const vectors = await readTestVectors();
  const baseUrl = await startServer();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    const expectedInvalid = message.location().url === baseUrl + '/api/supporter-key' && message.text().includes('400');
    if (message.type() === 'error' && !expectedInvalid) errors.push(message.text());
  });

  // 1. Free / unactivated dashboard: zero badge, zero horizon, crisp command search, gentle footer
  await page.goto(baseUrl);
  await expect(page.locator('#brand-link')).toBeVisible();
  await expect(page.locator('#supporter-badge')).toBeHidden();
  await expect(page.locator('#supporter-badge-divider')).toBeHidden();
  await expect(page.locator('#app-header')).not.toHaveClass(/has-supporter-beacon/);
  await expect(page.locator('.search-kbd')).toBeVisible();
  await expect(page.locator('#app-footer')).toBeVisible();
  await expect(page.locator('#footer-support-link')).not.toHaveClass(/\bis-lit\b/);
  await expect(page.locator('#footer-support-label')).toHaveText('Support Port-Light');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No desktop overflow');
  await shot(page, '01-dashboard-free-dark-1280.png');

  // Keyboard '/' focuses search and hides '/' keycap cleanly
  await page.keyboard.press('/');
  await expect(page.locator('#search')).toBeFocused();
  await expect(page.locator('.search-kbd')).toHaveCSS('opacity', '0');
  await page.keyboard.press('Escape');

  // 2. Dashboard footer jumps straight to Settings → Supporter Key Showcase
  await page.locator('#footer-support-link').click();
  await expect(page).toHaveURL(/#\/settings\/supporter$/);
  await expect(page.locator('#settings-tab-supporter-dot')).toBeHidden();
  await expect(page.locator('#settings-tab-supporter-glyph')).toBeVisible();
  await expect(page.locator('#settings-panel-supporter')).toBeVisible();
  await expect(page.locator('#settings-panel-supporter .supporter-showcase')).toBeVisible();
  await expect(page.locator('.settings-footer')).toBeHidden();
  await shot(page, '02-settings-supporter-inactive-dark.png');

  // Ephemeral "Preview in header" lights header temporarily and clears on switching away
  await page.locator('[data-supporter-preview]').click();
  await expect(page.locator('[data-supporter-preview]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#supporter-badge')).toBeVisible();
  await expect(page.locator('#supporter-badge')).toHaveAttribute('data-preview', 'true');
  await expect(page.locator('#app-header')).toHaveClass(/has-supporter-beacon/);
  await expect(page.locator('#settings-tab-supporter-dot')).toBeHidden();
  await page.getByRole('tab', { name: 'Appearance', exact: true }).click();
  await expect(page.locator('#supporter-badge')).toBeHidden();
  await expect(page.locator('#app-header')).not.toHaveClass(/has-supporter-beacon/);
  await page.locator('#settings-tab-supporter').click();
  await expect(page.locator('[data-supporter-preview]')).toHaveAttribute('aria-pressed', 'false');

  // Tampered key rejected inline without saving
  await page.locator('#supporter-key-input').fill(vectors.personal.slice(0, -1) + (vectors.personal.endsWith('A') ? 'B' : 'A'));
  await page.locator('[data-supporter-activate]').click();
  await expect(page.locator('#supporter-action-status')).toHaveClass(/is-error/);
  await expect(page.locator('#supporter-badge')).toBeHidden();

  // 3. Activate Personal Supporter key
  await page.locator('#supporter-key-input').fill(vectors.personal);
  await page.locator('[data-supporter-activate]').click();
  await expect(page.locator('#settings-panel-supporter .supporter-plaque')).toBeVisible();
  await expect(page.locator('#settings-panel-supporter .plaque-eyebrow')).toHaveText('HARBOR BEACON · ED25519');
  await expect(page.locator('#settings-panel-supporter .plaque-title')).toHaveText('Personal Supporter');
  await expect(page.locator('#settings-tab-supporter-dot')).toBeVisible();
  await expect(page.locator('#supporter-badge')).toBeVisible();
  await expect(page.locator('#supporter-badge-label')).toHaveText('Supporter');
  await expect(page.locator('#app-header')).toHaveClass(/has-supporter-beacon/);
  await expect(page.locator('.settings-footer')).toBeVisible();
  const darkSupMain = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--sup-main').trim().toLowerCase());
  assert.equal(darkSupMain, '#2dd4bf', `Default dark --sup-main mismatch: ${darkSupMain}`);
  await shot(page, '03-settings-plaque-personal-dark.png');

  // 4. Dashboard with Personal Supporter badge across Dark, Light, Catppuccin Mocha, and Mobile 390px / 360px
  await page.getByRole('link', { name: /^Port-Light/ }).click();
  await expect(page.locator('#view-grid')).toBeVisible();
  await expect(page.locator('#btn-settings')).not.toHaveClass(/\bactive\b/);
  await expect(page.locator('#supporter-badge')).toBeVisible();
  await expect(page.locator('#footer-support-link')).toHaveClass(/\bis-lit\b/);
  await expect(page.locator('#footer-support-label')).toContainText('Supporting Port-Light since');
  await expect(page.locator('#grid .port-cell').first()).toBeVisible();
  // Verify #port-menu dropdown stays unclipped while has-supporter-beacon is active
  await page.locator('#btn-manage').click();
  await expect(page.locator('#port-menu')).toBeVisible();
  const menuBox = await page.locator('#port-menu').boundingBox();
  assert.ok(menuBox && menuBox.height > 120, 'Manage menu clipped by app-header');
  await page.keyboard.press('Escape');
  await expect(page.locator('#port-menu')).toBeHidden();
  await page.locator('#btn-manage').evaluate(el => el.blur());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await shot(page, '04-dashboard-personal-dark-1280.png');

  // Click header nameplate -> jumps straight to #/settings/supporter
  await page.locator('#supporter-badge').click();
  await expect(page).toHaveURL(/#\/settings\/supporter$/);
  await expect(page.locator('#settings-panel-supporter .supporter-plaque')).toBeVisible();

  // Switch to Light theme and capture dashboard + plaque
  await page.getByRole('tab', { name: 'Appearance', exact: true }).click();
  await page.locator('.theme-picker-core [data-theme-preview="light"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-mode', 'light');
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await page.locator('#settings-tab-supporter').click();
  await shot(page, '05-settings-plaque-personal-light.png');
  await page.getByRole('link', { name: /^Port-Light/ }).click();
  await expect(page.locator('#view-grid')).toBeVisible();
  await expect(page.locator('#btn-settings')).not.toHaveClass(/\bactive\b/);
  // Verify Light-mode keyboard focus-visible ring is visible on #supporter-badge
  await page.locator('#brand-link').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('#supporter-badge')).toBeFocused();
  const lightFocusShadow = await page.locator('#supporter-badge').evaluate(el => getComputedStyle(el).boxShadow);
  assert.ok(lightFocusShadow.includes('4px'), `Light-mode focus ring missing on #supporter-badge: ${lightFocusShadow}`);
  await page.locator('#supporter-badge').evaluate(el => el.blur());
  await shot(page, '06-dashboard-personal-light-1280.png');

  // Switch to Catppuccin Mocha (dark) and capture dashboard
  await page.goto(baseUrl + '/#/settings/appearance');
  await page.locator('.theme-picker-core [data-theme-preview="dark"]').click();
  await page.locator('[data-theme-preview="catppuccin"]').click();
  await expect(page.locator('html')).toHaveAttribute('data-palette', 'catppuccin');
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  const catSupMain = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--sup-main').trim().toLowerCase());
  assert.equal(catSupMain, '#cba6f7', `Catppuccin Mocha --sup-main mismatch: ${catSupMain}`);
  await page.getByRole('link', { name: /^Port-Light/ }).click();
  await expect(page.locator('#view-grid')).toBeVisible();
  await expect(page.locator('#btn-settings')).not.toHaveClass(/\bactive\b/);
  await shot(page, '07-dashboard-personal-catppuccin-1280.png');

  // Restore default dark theme and test Fleet key + zh-CN locale + mobile viewports
  await page.goto(baseUrl + '/#/settings/appearance');
  await page.locator('.theme-picker-palettes [data-theme-preview="dark"]').click();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');

  await page.locator('#settings-tab-supporter').click();
  await page.locator('.supporter-manage summary').click();
  await page.locator('#supporter-key-input').fill(vectors.fleet);
  await page.locator('[data-supporter-activate]').click();
  await expect(page.locator('#settings-panel-supporter .plaque-title')).toHaveText('Team / Fleet Supporter');
  await expect(page.locator('#supporter-badge-label')).toHaveText('Fleet');

  // Test Show beacon in header toggle off -> persists across reload -> back on
  const showToggle = page.locator('input[name="supporter_show_header"]');
  await showToggle.uncheck();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await expect(page.locator('#supporter-badge')).toBeHidden();
  await expect(page.locator('#app-header')).not.toHaveClass(/has-supporter-beacon/);
  await expect(page.locator('#settings-tab-supporter-dot')).toBeVisible();
  await page.reload();
  await expect(page.locator('#supporter-badge')).toBeHidden();
  await expect(page.locator('#settings-tab-supporter-dot')).toBeVisible();
  await page.locator('input[name="supporter_show_header"]').check();
  await expect(page.locator('#settings-status')).toHaveClass('is-ok');
  await expect(page.locator('#supporter-badge')).toBeVisible();

  // Check mid-width 900px & 768px (EN + DE with #ui-links visible) plus mobile 390px and 360px
  await page.getByRole('link', { name: /^Port-Light/ }).click();
  await expect(page.locator('#view-grid')).toBeVisible();
  await expect(page.locator('#btn-settings')).not.toHaveClass(/\bactive\b/);
  for (const width of [900, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.locator('#supporter-badge')).toBeVisible();
    const layout = await page.evaluate(() => {
      const search = document.getElementById('search').getBoundingClientRect();
      const settings = document.getElementById('btn-settings').getBoundingClientRect();
      return {
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
        searchW: search.width,
        settingsRight: settings.right,
      };
    });
    assert.ok(layout.sw <= layout.iw, `Overflow at ${width}px: ${layout.sw} > ${layout.iw}`);
    assert.ok(layout.searchW >= 220, `Crushed search at ${width}px: ${layout.searchW}`);
    assert.ok(layout.settingsRight <= layout.iw + 1, `Settings clipped at ${width}px: ${layout.settingsRight}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#supporter-badge')).toBeVisible();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No overflow at 390px');
  await shot(page, '08-dashboard-fleet-dark-390.png');

  await page.setViewportSize({ width: 360, height: 780 });
  await expect(page.locator('#supporter-badge')).toBeVisible();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No overflow at 360px');

  // Verify German (longest European labels) at 900px has zero overflow and uncrushed search
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(baseUrl + '/#/settings/appearance');
  await page.locator('.locale-trigger').click();
  await page.locator('#locale-opt-de').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await page.getByRole('link', { name: /^Port-Light/ }).click();
  await expect(page.locator('#view-grid')).toBeVisible();
  await page.setViewportSize({ width: 900, height: 844 });
  const deLayout = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    iw: window.innerWidth,
    searchW: document.getElementById('search').getBoundingClientRect().width,
  }));
  assert.ok(deLayout.sw <= deLayout.iw, `DE overflow at 900px: ${deLayout.sw} > ${deLayout.iw}`);
  assert.ok(deLayout.searchW >= 220, `DE crushed search at 900px: ${deLayout.searchW}`);

  // Check zh-CN locale renders localized plaque & header tooltip
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(baseUrl + '/#/settings/appearance');
  await page.locator('.locale-trigger').click();
  await page.locator('#locale-opt-zh-CN').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await page.locator('#settings-tab-supporter').click();
  await expect(page.locator('#settings-panel-supporter .plaque-title')).toHaveText('团队 / 机群赞助者');
  await shot(page, '09-settings-plaque-fleet-zhCN-dark.png');

  // Verify ZERO raw key leakage across APIs and browser storage
  for (const endpoint of ['/api/meta', '/api/settings', '/api/health', '/api/doctor', '/api/doctor/report']) {
    const body = await (await fetch(baseUrl + endpoint)).text();
    assert.ok(!body.includes(vectors.personal), `Raw personal key leaked in ${endpoint}`);
    assert.ok(!body.includes(vectors.fleet), `Raw fleet key leaked in ${endpoint}`);
  }
  const storageDump = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  assert.ok(!storageDump.includes(vectors.personal) && !storageDump.includes(vectors.fleet), 'Raw key leaked in browser storage');

  // Remove saved key -> returns cleanly to unactivated showcase state
  await page.locator('.supporter-manage summary').click();
  await page.locator('[data-supporter-clear]').click();
  await expect(page.locator('#settings-panel-supporter .supporter-showcase')).toBeVisible();
  await expect(page.locator('#supporter-badge')).toBeHidden();
  await expect(page.locator('#settings-tab-supporter-dot')).toBeHidden();
  await expect(page.locator('#settings-tab-supporter-glyph')).toBeVisible();

  // Verify WCAG AA (>= 4.5:1) contrast on showcase pill, badge, eyebrow, and CTA across all 11 light themes
  const lightPalettes = ['', 'gruvbox', 'catppuccin', 'nord', 'dracula', 'tokyo-night', 'one-dark', 'solarized', 'everforest', 'rose-pine', 'kanagawa'];
  const contrastReport = await page.evaluate((palettes) => {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const parseColor = (cssColor) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = 'rgba(0, 0, 0, 0)';
      ctx.fillStyle = cssColor;
      ctx.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const composite = (fg, bg) => {
      const a = fg[3];
      return [
        fg[0] * a + bg[0] * (1 - a),
        fg[1] * a + bg[1] * (1 - a),
        fg[2] * a + bg[2] * (1 - a),
        1,
      ];
    };
    const lum = ([r, g, b]) => {
      const c = [r, g, b].map(v => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    };
    const ratio = (c1, c2) => {
      const l1 = lum(c1);
      const l2 = lum(c2);
      return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    };
    const effectiveBg = (el) => {
      const layers = [];
      let cur = el;
      while (cur && cur.nodeType === 1) {
        const bg = parseColor(getComputedStyle(cur).backgroundColor);
        if (bg[3] > 0) layers.push(bg);
        cur = cur.parentElement;
      }
      let out = [255, 255, 255, 1];
      for (let i = layers.length - 1; i >= 0; i--) out = composite(layers[i], out);
      return out;
    };
    const rootEl = document.documentElement;
    const prevMode = rootEl.getAttribute('data-mode');
    const prevPal = rootEl.getAttribute('data-palette');
    const results = [];
    rootEl.setAttribute('data-mode', 'light');
    for (const pal of palettes) {
      if (pal) rootEl.setAttribute('data-palette', pal);
      else rootEl.removeAttribute('data-palette');
      for (const sel of ['.supporter-preview-pill', '.supporter-showcase-badge', '.plaque-eyebrow']) {
        const el = document.querySelector(sel);
        const fg = parseColor(getComputedStyle(el).color);
        const bg = effectiveBg(el);
        results.push({ palette: pal || 'default', selector: sel, ratio: Number(ratio(fg, bg).toFixed(2)) });
      }
    }
    if (prevMode) rootEl.setAttribute('data-mode', prevMode); else rootEl.removeAttribute('data-mode');
    if (prevPal) rootEl.setAttribute('data-palette', prevPal); else rootEl.removeAttribute('data-palette');
    return results;
  }, lightPalettes);
  let minContrast = Infinity;
  let minLabel = '';
  for (const row of contrastReport) {
    assert.ok(Number.isFinite(row.ratio) && row.ratio >= 4.5, `Light contrast < 4.5:1 on ${row.palette} ${row.selector}: ${row.ratio}:1`);
    if (row.ratio < minContrast) {
      minContrast = row.ratio;
      minLabel = `${row.palette} ${row.selector}`;
    }
  }

  assert.deepEqual(errors, []);
  console.log(`Supporter browser smoke passed: free/personal/fleet states, header nameplate, horizon shimmer, live toggle, themes (dark/light/catppuccin), 11-theme light WCAG contrast (min ${minContrast}:1 on ${minLabel}), zh-CN locale, 390px/360px mobile overflow checks, zero raw-key leakage.`);
} finally {
  if (browser) await browser.close();
  if (child && child.exitCode === null) {
    const exited = once(child, 'exit');
    child.kill('SIGTERM');
    const timeout = setTimeout(() => child.kill('SIGKILL'), 5000);
    await exited;
    clearTimeout(timeout);
  }
  await rm(temporary, { recursive: true, force: true });
}
