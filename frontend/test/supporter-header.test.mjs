import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import './helpers/env.mjs';

const indexHtml = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const entry = indexHtml.match(/app\.js\?v=(\d+)/);
const V = entry ? 'v=' + entry[1] : '';

function makeClassList() {
  const set = new Set();
  return {
    add(...cls) { cls.forEach(c => set.add(c)); },
    remove(...cls) { cls.forEach(c => set.delete(c)); },
    toggle(c, force) {
      if (force === undefined) {
        if (set.has(c)) { set.delete(c); return false; }
        set.add(c); return true;
      }
      if (force) set.add(c); else set.delete(c);
      return !!force;
    },
    contains(c) { return set.has(c); },
  };
}

test('index.html places #supporter-badge as sibling of #brand-link with command search keycap', () => {
  assert.match(indexHtml, /id="brand-link"/);
  assert.match(indexHtml, /id="supporter-badge"/);
  assert.match(indexHtml, /id="supporter-badge-divider"/);
  assert.match(indexHtml, /id="supporter-badge-label"/);
  assert.match(indexHtml, /class="search-kbd"[^>]*>\/<\/kbd>/);
  // Ensure #supporter-badge is NOT nested inside <a id="brand-link">
  const brandClose = indexHtml.indexOf('</a>', indexHtml.indexOf('id="brand-link"'));
  const badgeStart = indexHtml.indexOf('id="supporter-badge"');
  assert.ok(brandClose > 0 && badgeStart > brandClose, '#supporter-badge must be a sibling after #brand-link');
});

test('syncSupporterHeader toggles header nameplate, divider, horizon, and tier copy', async () => {
  const { syncSupporterHeader } = await import('../js/supporter-header.js?' + V);
  const { S } = await import('../js/state.js?' + V);

  const header = document.getElementById('app-header');
  header.classList = makeClassList();
  const badge = document.getElementById('supporter-badge');
  const divider = document.getElementById('supporter-badge-divider');
  const label = document.getElementById('supporter-badge-label');
  const tabDot = document.getElementById('settings-tab-supporter-dot');

  const savedI18n = window.PortLightI18n;
  window.PortLightI18n = {
    t(key, vars) {
      const dict = {
        'supporter.badge.personal': 'Supporter',
        'supporter.badge.fleet': 'Fleet',
        'supporter.tier.personal': 'Personal Supporter',
        'supporter.tier.fleet': 'Team / Fleet Supporter',
        'supporter.badge.title': '{tier} · Lit since {date}',
        'supporter.badge.titleNoDate': '{tier}',
      };
      const raw = dict[key] || key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? '')) : raw;
    },
  };

  try {
    // Inactive: hidden badge, no horizon, no tab dot
    S.meta.supporter = { active: false, tier: null, issued_at: null, show_in_header: true };
    syncSupporterHeader();
    assert.equal(badge.hidden, true);
    assert.equal(divider.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);
    assert.equal(tabDot.hidden, true);

    // Active Personal with show_in_header=true
    S.meta.supporter = { active: true, tier: 'personal', issued_at: '2026-11-25', show_in_header: true };
    syncSupporterHeader();
    assert.equal(badge.hidden, false);
    assert.equal(divider.hidden, false);
    assert.equal(header.classList.contains('has-supporter-beacon'), true);
    assert.equal(label.textContent, 'Supporter');
    assert.equal(badge.getAttribute('title'), 'Personal Supporter · Lit since 2026-11-25');
    assert.equal(badge.getAttribute('data-tier'), 'personal');
    assert.equal(tabDot.hidden, false);

    // Active Fleet
    S.meta.supporter = { active: true, tier: 'fleet', issued_at: '2026-11-25', show_in_header: true };
    syncSupporterHeader();
    assert.equal(label.textContent, 'Fleet');
    assert.equal(badge.getAttribute('title'), 'Team / Fleet Supporter · Lit since 2026-11-25');
    assert.equal(badge.getAttribute('data-tier'), 'fleet');

    // Active but show_in_header=false hides header badge + horizon while keeping settings tab dot lit
    S.meta.supporter = { active: true, tier: 'fleet', issued_at: '2026-11-25', show_in_header: false };
    syncSupporterHeader();
    assert.equal(badge.hidden, true);
    assert.equal(divider.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);
    assert.equal(tabDot.hidden, false);
  } finally {
    if (savedI18n) window.PortLightI18n = savedI18n;
    else delete window.PortLightI18n;
  }
});
