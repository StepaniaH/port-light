/* Guards the shared cache-bust version string across the module graph. */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const dir = new URL('../js/', import.meta.url);
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const entry = html.match(/app\.js\?v=(\d+)/);
assert.ok(entry, 'index.html must load app.js with an explicit ?v=');
const V = entry[1];

test('index.html pins classic assets and omits inline scripts', () => {
  for (const asset of ['boot.js', 'style.css', 'i18n.js', 'js/app.js']) {
    assert.ok(html.includes(asset + '?v=' + V), 'index.html must pin ' + asset + '?v=' + V);
  }
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)/i, 'index.html must not contain inline scripts');
});

test('every js import specifier pins the entry version', () => {
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.js')) continue;
    const src = readFileSync(new URL('../js/' + f, import.meta.url), 'utf8');
    const specs = [...src.matchAll(/\?v=(\d+)/g)].map((m) => m[1]);
    for (const v of specs) {
      assert.equal(v, V, f + ' pins ?v=' + v + ', entry is ?v=' + V);
    }
    const localImports = [...src.matchAll(/from\s+['"](\.\/[\w-]+\.js(?:\?v=\d+)?)['"]/g)].map((m) => m[1]);
    for (const spec of localImports) {
      assert.ok(spec.endsWith('?v=' + V), f + ' local import must pin ?v=' + V + ': ' + spec);
    }
  }
});

test('frontend imports remain acyclic and the API stays independent of views', () => {
  const graph = new Map(readdirSync(dir).filter(f => f.endsWith('.js')).map(f => {
    const source = readFileSync(new URL(f, dir), 'utf8');
    return [f, [...source.matchAll(/from\s+['"]\.\/([\w-]+\.js)\?v=\d+['"]/g)].map(m => m[1])];
  }));
  function visit(file, path = []) {
    assert.ok(!path.includes(file), 'Import cycle: ' + [...path, file].join(' → '));
    for (const dependency of graph.get(file) || []) visit(dependency, [...path, file]);
  }
  for (const file of graph.keys()) visit(file);
  assert.deepEqual(graph.get('api.js').sort(), ['hosts.js', 'state.js']);
});
