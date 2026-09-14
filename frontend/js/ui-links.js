/* Navigation for local pages registered by the hosting application. */
import { escapeHtml } from './text.js?v=100';

export function uiLinksHtml(links, locale = 'en') {
  if (!Array.isArray(links)) return '';
  return links.slice(0, 4).map(link => {
    if (!link || typeof link.path !== 'string' ||
        !/^\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]*$/.test(link.path)) return '';
    const label = link.labels?.[locale] || link.label;
    if (typeof label !== 'string' || !label.trim() || label.length > 40) return '';
    return '<a class="toolbar-btn" href="' + escapeHtml(link.path) + '" title="' +
      escapeHtml(label) + '">' + escapeHtml(label) + '</a>';
  }).join('');
}

export function renderUiLinks(links) {
  const container = document.getElementById('ui-links');
  if (!container) return;
  const locale = window.PortLightI18n?.locale() || 'en';
  container.innerHTML = uiLinksHtml(links, locale);
  container.hidden = !container.childElementCount;
}
