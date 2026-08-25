/* Pure-function tests for the theme model in state.js. No DOM. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const entrySrc = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8');
const version = entrySrc.match(/\?v=(\d+)/);
const V = version ? 'v=' + version[1] : '';

const { PALETTE_VARIANTS, resolveMode, paletteAvailable } = await import('../js/state.js?' + V);

test('resolveMode honors explicit modes', () => {
  assert.equal(resolveMode('dark', true), 'dark');
  assert.equal(resolveMode('light', false), 'light');
});

test('resolveMode falls back to prefersLight for system', () => {
  assert.equal(resolveMode('system', true), 'light');
  assert.equal(resolveMode('system', false), 'dark');
  assert.equal(resolveMode('', false), 'dark');
});

test('dual-variant families are available in both modes', () => {
  for (const f of ['gruvbox', 'catppuccin', 'solarized']) {
    assert.ok(paletteAvailable(f, 'dark'));
    assert.ok(paletteAvailable(f, 'light'));
  }
});

test('single-variant families grey out on mismatch', () => {
  assert.ok(!paletteAvailable('dracula', 'light'));
  assert.ok(paletteAvailable('dracula', 'dark'));
  assert.ok(!paletteAvailable('nord', 'light'));
});

test('unknown family is never available', () => {
  assert.ok(!paletteAvailable('nope', 'dark'));
});

test('variant map covers exactly ten families', () => {
  assert.equal(Object.keys(PALETTE_VARIANTS).length, 10);
});
