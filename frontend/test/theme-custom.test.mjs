import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const V = 'v=' + (entrySrc.match(/\?v=(\d+)/) || ['', '63'])[1];

const { S, applyTheme, CUSTOM_PREFIX, customPaletteVars } =
  await import('../js/state.js?' + V);

const THEME = {
  id: 'abcd1234', name: 'Mine', basedOn: 'gruvbox', mode: 'dark',
  colors: Object.fromEntries(['bg', 'elevated', 'card', 'cardHover', 'border', 'text',
    'textDim', 'used', 'configured', 'free', 'accent', 'conflict', 'access', 'hidden',
    'danger'].map((k) => [k, '#112233'])),
};

test('customPaletteVars maps camelCase to css names', () => {
  const vars = customPaletteVars(THEME.colors);
  const flat = Object.fromEntries(vars);
  assert.equal(flat['--bg'], '#112233');
  assert.equal(flat['--card-hover'], '#112233');
  assert.equal(flat['--text-dim'], '#112233');
  assert.equal(vars.length, 15);
});

test('applyTheme injects custom vars on match and clears on switch away', () => {
  S.settings.theme_mode = 'dark';
  S.settings.theme_palette = CUSTOM_PREFIX + THEME.id;
  S.customThemes = [THEME];
  applyTheme();
  const html = document.documentElement;
  assert.equal(html.style.getPropertyValue('--bg'), '#112233');
  assert.equal(html.getAttribute('data-palette'), null);

  S.settings.theme_palette = '';
  applyTheme();
  assert.equal(html.style.getPropertyValue('--bg'), '');
});

test('applyTheme falls back to built-in when custom mode mismatches', () => {
  S.settings.theme_mode = 'light';
  S.settings.theme_palette = CUSTOM_PREFIX + THEME.id;
  S.customThemes = [THEME];
  applyTheme();
  assert.equal(document.documentElement.style.getPropertyValue('--bg'), '');
  S.settings.theme_mode = 'system';
});
