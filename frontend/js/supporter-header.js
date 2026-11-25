/* Top-bar Supporter Beacon nameplate and settings-tab indicator. */

import { S } from './state.js?v=131';
import { t } from './text.js?v=131';
import { syncHeaderHeight } from './dom.js?v=131';

export function syncSupporterHeader() {
  const info = (S.meta && S.meta.supporter) || {};
  const active = !!info.active;
  if (active && S.supporterPreviewHeader) S.supporterPreviewHeader = false;
  const previewing = !active && !!S.supporterPreviewHeader;
  const showInHeader = info.show_in_header !== false;
  const showBadge = (active && showInHeader) || previewing;
  const tier = info.tier === 'fleet' ? 'fleet' : 'personal';

  const header = document.getElementById('app-header');
  const badge = document.getElementById('supporter-badge');
  const divider = document.getElementById('supporter-badge-divider');
  const label = document.getElementById('supporter-badge-label');
  const tabDot = document.getElementById('settings-tab-supporter-dot');
  const tabGlyph = document.getElementById('settings-tab-supporter-glyph');
  const footerLink = document.getElementById('footer-support-link');
  const footerLabel = document.getElementById('footer-support-label');

  if (header && header.classList) {
    header.classList.toggle('has-supporter-beacon', showBadge);
  }
  if (divider) divider.hidden = !showBadge;
  if (tabDot) tabDot.hidden = !active;
  if (tabGlyph) tabGlyph.hidden = active;

  if (badge) {
    badge.hidden = !showBadge;
    if (showBadge) {
      if (previewing) {
        const shortLabel = t('supporter.badge.personal');
        const title = t('supporter.preview.headerTitle');
        if (label) label.textContent = shortLabel;
        badge.setAttribute('data-tier', 'personal');
        badge.setAttribute('data-preview', 'true');
        badge.setAttribute('title', title);
        badge.setAttribute('aria-label', title);
      } else {
        const shortLabel = t(tier === 'fleet' ? 'supporter.badge.fleet' : 'supporter.badge.personal');
        const tierName = t(tier === 'fleet' ? 'supporter.tier.fleet' : 'supporter.tier.personal');
        const title = info.issued_at
          ? t('supporter.badge.title', { tier: tierName, date: info.issued_at })
          : t('supporter.badge.titleNoDate', { tier: tierName });
        if (label) label.textContent = shortLabel;
        badge.setAttribute('data-tier', tier);
        badge.removeAttribute('data-preview');
        badge.setAttribute('title', title);
        badge.setAttribute('aria-label', title);
      }
    } else {
      badge.removeAttribute('data-tier');
      badge.removeAttribute('data-preview');
      badge.removeAttribute('title');
      badge.removeAttribute('aria-label');
    }
  }

  if (footerLink && footerLink.classList) {
    footerLink.classList.toggle('is-lit', active);
  }
  if (footerLabel) {
    if (active) {
      footerLabel.textContent = info.issued_at
        ? t('footer.supportingSince', { date: info.issued_at })
        : t('footer.supporting');
    } else {
      footerLabel.textContent = t('footer.support');
    }
  }

  syncHeaderHeight();
}
