/* Pure-function tests for the theme model in state.js. No DOM. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { S, PALETTE_VARIANTS, resolveMode, paletteAvailable, applyTheme } = await import('../js/state.js?' + V);

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const css = readFileSync(new URL('../style.css', import.meta.url), 'utf8');
const newLightFamilies = ['nord', 'dracula', 'tokyo-night', 'one-dark', 'everforest', 'rose-pine', 'kanagawa'];

function tokens(selector) {
  const start = css.indexOf(selector + ' {');
  assert.ok(start >= 0, `Missing CSS: ${selector}`);
  const block = css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start));
  return Object.fromEntries([...block.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(match => [match[1], match[2].trim()]));
}

function rgb(hex) {
  assert.match(hex, /^#[\da-f]{6}$/i);
  return [1, 3, 5].map(start => parseInt(hex.slice(start, start + 2), 16) / 255);
}

function contrast(first, second) {
  function luminance(color) {
    const [r, g, b] = color.map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  const [low, high] = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (high + 0.05) / (low + 0.05);
}

test('resolveMode honors explicit modes', () => {
  assert.equal(resolveMode('dark', true), 'dark');
  assert.equal(resolveMode('light', false), 'light');
});

test('resolveMode falls back to prefersLight for system', () => {
  assert.equal(resolveMode('system', true), 'light');
  assert.equal(resolveMode('system', false), 'dark');
  assert.equal(resolveMode('', false), 'dark');
});

test('all built-in palette families are available in both modes', () => {
  for (const f of Object.keys(PALETTE_VARIANTS)) {
    assert.ok(paletteAvailable(f, 'dark'));
    assert.ok(paletteAvailable(f, 'light'));
  }
});

test('unknown family is never available', () => {
  assert.ok(!paletteAvailable('nope', 'dark'));
});

test('variant map covers exactly ten families', () => {
  assert.equal(Object.keys(PALETTE_VARIANTS).length, 10);
});

test('each registered variant has complete colors and matching preview swatches', () => {
  const required = ['bg', 'elevated', 'card', 'card-hover', 'border', 'text', 'text-dim',
    'used', 'configured', 'free', 'accent', 'conflict', 'access', 'hidden', 'danger',
    'overlay', 'toast-bg', 'shadow', 'btn-on-accent'];
  for (const [family, modes] of Object.entries(PALETTE_VARIANTS)) {
    for (const mode of modes) {
      const colors = tokens(`[data-palette="${family}"][data-mode="${mode}"]`);
      for (const name of required) assert.ok(colors['--' + name], `${family}/${mode}: ${name}`);
      const preview = tokens(`.theme-swatch[data-theme-preview="${family}${mode === 'light' ? '-light' : ''}"]`);
      for (const [name, key] of [['bg', 'bg'], ['used', 'used'], ['cfg', 'configured'], ['free', 'free']]) {
        assert.equal(preview['--preview-' + name], colors['--' + key], `${family}/${mode}: ${name}`);
      }
    }
  }
});

test('new light palettes keep text, tinted status labels, and primary buttons readable', () => {
  for (const family of newLightFamilies) {
    const colors = tokens(`[data-palette="${family}"][data-mode="light"]`);
    for (const background of ['bg', 'elevated', 'card', 'card-hover']) {
      for (const foreground of ['text', 'text-dim']) {
        assert.ok(contrast(rgb(colors['--' + foreground]), rgb(colors['--' + background])) >= 4.5,
          `${family}: ${foreground} on ${background}`);
      }
    }
    for (const key of ['used', 'configured', 'free', 'accent', 'conflict', 'access', 'danger']) {
      const foreground = rgb(colors['--' + key]);
      for (const background of ['bg', 'elevated', 'card']) {
        const tinted = rgb(colors['--' + background]).map((value, i) => value * 0.84 + foreground[i] * 0.16);
        assert.ok(contrast(foreground, tinted) >= 4.5, `${family}: ${key} on tinted ${background}`);
      }
    }
    assert.ok(contrast(rgb(colors['--btn-on-accent']), rgb(colors['--accent'])) >= 4.5,
      `${family}: primary button text`);
  }
});

test('cached appearance bootstrap and runtime preserve every palette across system modes', () => {
  const bootstrap = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const previous = { ...S.settings };
  const media = window.matchMedia;
  try {
    for (const family of Object.keys(PALETTE_VARIANTS)) {
      for (const prefersLight of [true, false]) {
        const attrs = new Map();
        runInNewContext(bootstrap, {
          localStorage: { getItem: () => JSON.stringify({ theme_mode: 'system', theme_palette: family }) },
          matchMedia: () => ({ matches: prefersLight }),
          document: { documentElement: { setAttribute: (key, value) => attrs.set(key, value), style: { setProperty() {} } } },
        });
        window.matchMedia = () => ({ matches: prefersLight });
        Object.assign(S.settings, { theme_mode: 'system', theme_palette: family });
        applyTheme();
        assert.equal(attrs.get('data-palette'), family);
        assert.equal(document.documentElement.getAttribute('data-palette'), family);
        assert.equal(attrs.get('data-mode'), prefersLight ? 'light' : 'dark');
        assert.equal(document.documentElement.getAttribute('data-mode'), attrs.get('data-mode'));
      }
    }
  } finally {
    S.settings = previous;
    window.matchMedia = media;
    applyTheme();
  }
});
