/* Navigation for local pages registered by the hosting application. */
import { escapeHtml } from './text.js?v=103';

export function workspaceHref(link, port) {
  if (link?.workspace?.api !== 1 || !/^[a-z][a-z0-9-]{0,39}$/.test(link.key || '')) return null;
  const base = '#/workspace/' + link.key;
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? base + '/port/' + port : base;
}

export function portUiLinksHtml(links, port, locale = 'en') {
  return uiLinksHtml((links || []).filter(link => link?.workspace?.port_action === true), locale, port);
}

export function uiLinksHtml(links, locale = 'en', port) {
  if (!Array.isArray(links)) return '';
  return links.slice(0, 4).map(link => {
    if (!link || typeof link.path !== 'string' ||
        !/^\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]*$/.test(link.path)) return '';
    const label = link.labels?.[locale] || link.label;
    if (typeof label !== 'string' || !label.trim() || label.length > 40) return '';
    return '<a class="toolbar-btn" href="' + escapeHtml(workspaceHref(link, port) || link.path) + '" title="' +
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
