/* Guards the shared cache-bust version string across the module graph. */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const dir = new URL('../js/', import.meta.url);
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const entry = html.match(/app\.js\?v=(\d+)/);
assert.ok(entry, 'index.html must load app.js with an explicit ?v=');
const V = entry[1];

test('every js import specifier pins the entry version', () => {
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.js')) continue;
    const src = readFileSync(new URL('../js/' + f, import.meta.url), 'utf8');
    const specs = [...src.matchAll(/\?v=(\d+)/g)].map((m) => m[1]);
    for (const v of specs) {
      assert.equal(v, V, f + ' pins ?v=' + v + ', entry is ?v=' + V);
    }
  }
});
