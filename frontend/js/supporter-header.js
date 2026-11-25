/* Top-bar Supporter Beacon nameplate and settings-tab indicator. */

import { S } from './state.js?v=128';
import { t } from './text.js?v=128';
import { syncHeaderHeight } from './dom.js?v=128';

export function syncSupporterHeader() {
  const info = (S.meta && S.meta.supporter) || {};
  const active = !!info.active;
  const showInHeader = info.show_in_header !== false;
  const showBadge = active && showInHeader;
  const tier = info.tier === 'fleet' ? 'fleet' : 'personal';

  const header = document.getElementById('app-header');
  const badge = document.getElementById('supporter-badge');
  const divider = document.getElementById('supporter-badge-divider');
  const label = document.getElementById('supporter-badge-label');
  const tabDot = document.getElementById('settings-tab-supporter-dot');

  if (header && header.classList) {
    header.classList.toggle('has-supporter-beacon', showBadge);
  }
  if (divider) divider.hidden = !showBadge;
  if (tabDot) tabDot.hidden = !active;

  if (badge) {
    badge.hidden = !showBadge;
    if (showBadge) {
      const shortLabel = t(tier === 'fleet' ? 'supporter.badge.fleet' : 'supporter.badge.personal');
      const tierName = t(tier === 'fleet' ? 'supporter.tier.fleet' : 'supporter.tier.personal');
      const title = info.issued_at
        ? t('supporter.badge.title', { tier: tierName, date: info.issued_at })
        : t('supporter.badge.titleNoDate', { tier: tierName });
      if (label) label.textContent = shortLabel;
      badge.setAttribute('data-tier', tier);
      badge.setAttribute('title', title);
      badge.setAttribute('aria-label', title);
    } else {
      badge.removeAttribute('data-tier');
      badge.removeAttribute('title');
      badge.removeAttribute('aria-label');
    }
  }
  syncHeaderHeight();
}
