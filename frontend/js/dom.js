/* Shared DOM references and sticky-header / sync-banner utilities. */

import { t } from './text.js?v=63';

function byId(id) {
  return document.getElementById(id);
}

export const appEl = byId('app');
export const grid = byId('grid');
export const hostBoards = byId('host-boards');
export const hostSwitcher = byId('host-switcher');
export const summary = byId('summary');
export const detailPanel = byId('detail-panel');
export const detailBackdrop = byId('detail-backdrop');
export const detailContent = byId('detail-content');
export const searchInput = byId('search');
export const rangeStartInput = byId('range-start');
export const rangeEndInput = byId('range-end');
export const sortSelect = byId('sort-select');
export const unhideBtn = byId('btn-unhide');
export const settingsBtn = byId('btn-settings');

export function syncHeaderHeight() {
  const header = document.getElementById('app-header');
  if (!header) return;
  document.documentElement.style.setProperty('--header-h', header.offsetHeight + 'px');
}

export function markRefreshed() {
  const el = document.getElementById('sync-age');
  if (!el) return;
  const loc = window.PortLightI18n ? PortLightI18n.locale() : undefined;
  const time = new Date().toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  el.hidden = false;
  el.dateTime = new Date().toISOString();
  el.textContent = t('grid.updated', { time: time });
  syncHeaderHeight();
}

export function setSyncError(on) {
  const el = document.getElementById('sync-error');
  if (!el) return;
  el.hidden = !on;
  el.classList.toggle('hidden', !on);
  if (on) el.textContent = t('grid.refreshFailed');
  syncHeaderHeight();
}
