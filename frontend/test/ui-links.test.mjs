import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { uiLinksHtml } = await import('../js/ui-links.js?v=' + version);

test('local extension links use localized labels and escape text', () => {
  const link = { path: '/tools/', label: 'Tools', labels: { 'zh-CN': '工具 <预览>' } };
  assert.match(uiLinksHtml([link], 'zh-CN'), /href="\/tools\/" title="工具 &lt;预览&gt;">工具 &lt;预览&gt;<\/a>/);
  assert.match(uiLinksHtml([link], 'fr'), />Tools<\/a>/);
});

test('empty, malformed and external navigation cannot produce links', () => {
  assert.equal(uiLinksHtml(undefined), '');
  for (const path of ['https://example.invalid/', '//example.invalid/', '/\\example.invalid', '/%2f%2fexample.invalid', 'javascript:alert(1)']) {
    assert.equal(uiLinksHtml([{ path, label: 'Test' }]), '');
  }
  assert.equal(uiLinksHtml([{ path: '/tools/', label: {} }]), '');
});
