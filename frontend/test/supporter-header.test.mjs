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

test('index.html places #supporter-badge as sibling of #brand-link with command search keycap and dashboard footer', () => {
  assert.match(indexHtml, /id="brand-link"/);
  assert.match(indexHtml, /id="supporter-badge"/);
  assert.match(indexHtml, /id="supporter-badge-divider"/);
  assert.match(indexHtml, /id="supporter-badge-label"/);
  assert.match(indexHtml, /class="search-kbd"[^>]*>\/<\/kbd>/);
  assert.match(indexHtml, /id="settings-tab-supporter-glyph"/);
  assert.match(indexHtml, /id="app-footer"/);
  assert.match(indexHtml, /id="footer-version"/);
  assert.match(indexHtml, /id="footer-support-link"[^>]*href="#\/settings\/supporter"/);
  assert.match(indexHtml, /id="footer-support-label"/);
  // Ensure #supporter-badge is NOT nested inside <a id="brand-link">
  const brandClose = indexHtml.indexOf('</a>', indexHtml.indexOf('id="brand-link"'));
  const badgeStart = indexHtml.indexOf('id="supporter-badge"');
  assert.ok(brandClose > 0 && badgeStart > brandClose, '#supporter-badge must be a sibling after #brand-link');
});

test('syncSupporterHeader toggles header nameplate, ephemeral preview, sidebar glyph, and dashboard footer', async () => {
  const { syncSupporterHeader } = await import('../js/supporter-header.js?' + V);
  const { S } = await import('../js/state.js?' + V);

  const header = document.getElementById('app-header');
  header.classList = makeClassList();
  const badge = document.getElementById('supporter-badge');
  const divider = document.getElementById('supporter-badge-divider');
  const label = document.getElementById('supporter-badge-label');
  const tabDot = document.getElementById('settings-tab-supporter-dot');
  const tabGlyph = document.getElementById('settings-tab-supporter-glyph');
  const footerLink = document.getElementById('footer-support-link');
  footerLink.classList = makeClassList();
  const footerLabel = document.getElementById('footer-support-label');

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
        'supporter.preview.headerTitle': 'Supporter Beacon preview',
        'footer.support': 'Support Port-Light',
        'footer.supporting': 'Thank you for supporting Port-Light',
        'footer.supportingSince': 'Supporting Port-Light since {date}',
      };
      const raw = dict[key] || key;
      return vars ? raw.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? '')) : raw;
    },
  };

  try {
    // Inactive: hidden badge, no horizon, no tab dot, wireframe glyph visible, quiet footer CTA
    S.supporterPreviewHeader = false;
    S.meta.supporter = { active: false, tier: null, issued_at: null, show_in_header: true };
    syncSupporterHeader();
    assert.equal(badge.hidden, true);
    assert.equal(divider.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);
    assert.equal(tabDot.hidden, true);
    assert.equal(tabGlyph.hidden, false);
    assert.equal(footerLink.classList.contains('is-lit'), false);
    assert.equal(footerLabel.textContent, 'Support Port-Light');

    // Inactive + ephemeral preview enabled: header lights up with data-preview="true", tab dot stays hidden
    S.supporterPreviewHeader = true;
    syncSupporterHeader();
    assert.equal(badge.hidden, false);
    assert.equal(divider.hidden, false);
    assert.equal(header.classList.contains('has-supporter-beacon'), true);
    assert.equal(badge.getAttribute('data-preview'), 'true');
    assert.equal(badge.getAttribute('title'), 'Supporter Beacon preview');
    assert.equal(tabDot.hidden, true);
    assert.equal(tabGlyph.hidden, false);
    assert.equal(footerLink.classList.contains('is-lit'), false);

    // Active Personal with show_in_header=true clears preview marker, lights tab dot, hides glyph, updates footer
    S.meta.supporter = { active: true, tier: 'personal', issued_at: '2026-11-25', show_in_header: true };
    syncSupporterHeader();
    assert.equal(S.supporterPreviewHeader, false);
    assert.equal(badge.hidden, false);
    assert.equal(badge.getAttribute('data-preview'), null);
    assert.equal(divider.hidden, false);
    assert.equal(header.classList.contains('has-supporter-beacon'), true);
    assert.equal(label.textContent, 'Supporter');
    assert.equal(badge.getAttribute('title'), 'Personal Supporter · Lit since 2026-11-25');
    assert.equal(badge.getAttribute('data-tier'), 'personal');
    assert.equal(tabDot.hidden, false);
    assert.equal(tabGlyph.hidden, true);
    assert.equal(footerLink.classList.contains('is-lit'), true);
    assert.equal(footerLabel.textContent, 'Supporting Port-Light since 2026-11-25');

    // Active Fleet
    S.meta.supporter = { active: true, tier: 'fleet', issued_at: '2026-11-25', show_in_header: true };
    syncSupporterHeader();
    assert.equal(label.textContent, 'Fleet');
    assert.equal(badge.getAttribute('title'), 'Team / Fleet Supporter · Lit since 2026-11-25');
    assert.equal(badge.getAttribute('data-tier'), 'fleet');

    // Active but show_in_header=false hides header badge + horizon while keeping settings tab dot lit and footer lit
    S.meta.supporter = { active: true, tier: 'fleet', issued_at: '2026-11-25', show_in_header: false };
    syncSupporterHeader();
    assert.equal(badge.hidden, true);
    assert.equal(divider.hidden, true);
    assert.equal(header.classList.contains('has-supporter-beacon'), false);
    assert.equal(tabDot.hidden, false);
    assert.equal(tabGlyph.hidden, true);
    assert.equal(footerLink.classList.contains('is-lit'), true);
  } finally {
    S.supporterPreviewHeader = false;
    if (savedI18n) window.PortLightI18n = savedI18n;
    else delete window.PortLightI18n;
  }
});
