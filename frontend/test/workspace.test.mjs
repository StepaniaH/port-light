import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const version = readFileSync(new URL('../js/app.js', import.meta.url), 'utf8').match(/\?v=(\d+)/)[1];
const { workspaceAssets } = await import('../js/workspace.js?v=' + version);
const { portUiLinksHtml, workspaceHref } = await import('../js/ui-links.js?v=' + version);
const { parseHash } = await import('../js/router.js?v=' + version);
const link = { key: 'tools', path: '/tools/', label: 'Tools', workspace: { api: 1,
  entry: '/tools/assets/main.js', stylesheet: '/tools/assets/main.css', port_action: true, revision: 'a'.repeat(64) } };

test('workspace navigation stays in the shell and carries only a local port context', () => {
  assert.equal(workspaceHref(link), '#/workspace/tools');
  assert.match(portUiLinksHtml([link], 8080), /href="#\/workspace\/tools\/port\/8080"/);
  assert.equal(portUiLinksHtml([{ ...link, workspace: { ...link.workspace, port_action: false } }], 80), '');
  assert.deepEqual(parseHash('#/workspace/tools/port/8080'), { name: 'workspace', key: 'tools', port: 8080 });
  assert.deepEqual(parseHash('#/workspace/tools'), { name: 'workspace', key: 'tools' });
  for (const route of ['#/workspace/../port/80', '#/workspace/tools/port/65536', '#/workspace/tools/h/remote']) {
    assert.equal(parseHash(route).name, 'grid');
  }
});

test('only module-local assets load, with the verified release revision', () => {
  assert.equal(workspaceAssets(link).entry, '/tools/assets/main.js?v=' + 'a'.repeat(64));
  for (const entry of ['https://example.invalid/main.js', '//example.invalid/main.js', '/tools/../main.js', '/tools/%2e/main.js', '/other/main.js', '/tools/main.js?key=value']) {
    assert.equal(workspaceAssets({ ...link, workspace: { ...link.workspace, entry } }), null);
  }
  assert.equal(workspaceAssets({ ...link, workspace: { ...link.workspace, revision: '../invalid' } }), null);
});
