/* Tests for frontend/js/text.js — pure helpers, no DOM state needed. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import './helpers/env.mjs';
import { escapeHtml, errorText, safeHref, t, tx } from '../js/text.js';

test('safeHref keeps absolute http(s) URLs only', () => {
  assert.equal(safeHref('https://example.com/path'), 'https://example.com/path');
  assert.equal(safeHref('http://localhost:9000'), 'http://localhost:9000');
  assert.equal(safeHref('HTTPS://EXAMPLE.COM'), 'HTTPS://EXAMPLE.COM');
  assert.equal(safeHref('  https://padded.example  '), 'https://padded.example');
});

test('safeHref rejects non-http schemes and unsafe characters', () => {
  assert.equal(safeHref('javascript:alert(1)'), '');
  assert.equal(safeHref('data:text/html,<b>hi</b>'), '');
  assert.equal(safeHref('ftp://files.example.com'), '');
  assert.equal(safeHref('https://exa mple.com'), '');
  assert.equal(safeHref('https://a<b'), '');
});

test('safeHref treats falsy input as no link', () => {
  assert.equal(safeHref(''), '');
  assert.equal(safeHref(null), '');
  assert.equal(safeHref(undefined), '');
  assert.equal(safeHref(0), '');
});

test('escapeHtml escapes markup but keeps plain text and zero', () => {
  assert.equal(escapeHtml('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.equal(escapeHtml('a&b'), 'a&amp;b');
  assert.equal(escapeHtml('plain'), 'plain');
  assert.equal(escapeHtml(0), '0');
  assert.equal(escapeHtml(''), '');
  assert.equal(escapeHtml(null), '');
});

test('t falls back to the key when i18n is not loaded', () => {
  delete globalThis.window.PortLightI18n;
  assert.equal(t('grid.updated'), 'grid.updated');
});

test('tx returns the value when no translation exists, empty for falsy', () => {
  delete globalThis.window.PortLightI18n;
  assert.equal(tx('kind', 'udp'), 'udp');
  assert.equal(tx('kind', ''), '');
  assert.equal(tx('kind', null), '');
});

test('errorText prefers string details and joins validation lists', () => {
  assert.equal(errorText({ detail: 'range too wide' }, 400), 'range too wide');
  assert.equal(errorText({ detail: [{ msg: 'must be a number' }, 'second'] }, 422), 'must be a number; second');
});

test('errorText falls back to the localized http status line', () => {
  const saved = globalThis.window.PortLightI18n;
  globalThis.window.PortLightI18n = { t: (key, vars) => 'HTTP ' + vars.status };
  try {
    assert.equal(errorText({}, 404), 'HTTP 404');
    assert.equal(errorText({ detail: [{ not_a_msg: true }, ''] }, 422), 'HTTP 422');
  } finally {
    if (saved) globalThis.window.PortLightI18n = saved;
    else delete globalThis.window.PortLightI18n;
  }
});
