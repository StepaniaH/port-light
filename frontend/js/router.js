/* Hash router: #/, #/settings/:panel, #/port/:n, #/h/:host(/port/:n). */

import { S, SETTINGS_PANELS } from './state.js?v=58';
import { settingsBtn, appEl, syncHeaderHeight } from './dom.js?v=58';
import { hostById, tick, hasPeers } from './api.js?v=58';
import { t } from './text.js?v=58';
import { render, applyPendingGridFocus } from './grid.js?v=58';
import { closeDetail, showPortDetail } from './detail.js?v=58';
import { loadSettingsPage, showSettingsPanel, revertUnsavedSettings } from './settings.js?v=58';


  export function parseHash(hash) {
    const raw = String(hash || '#/').replace(/^#\/?/, '');
    const parts = raw.split('/').filter(Boolean);
    if (parts[0] === 'settings') {
      let section = parts[1];
      if (SETTINGS_PANELS.indexOf(section) < 0) {
        section = S.route.name === 'settings' && S.settingsPanel ? S.settingsPanel : 'appearance';
      }
      return { name: 'settings', section: section };
    }
    let hostId = 'local';
    let rest = parts;
    if (parts[0] === 'h' && parts[1]) {
      hostId = parts[1];
      rest = parts.slice(2);
      if (hostId !== 'local' && !/^[a-z0-9]{8,16}$/.test(hostId)) hostId = 'local';
    }
    if (rest[0] === 'port' && /^\d+$/.test(rest[1] || '')) {
      const n = parseInt(rest[1], 10);
      if (n >= 1 && n <= 65535) return { name: 'port', port: n, hostId: hostId };
    }
    return { name: 'grid', hostId: hostId };
  }

  export function parseRoute() {
    return parseHash(location.hash);
  }

  export function leaveSettingsOrStay() {
    if (!S.settingsDirty || S.route.name !== 'settings') return true;
    if (!window.confirm(t('settings.discard'))) return false;
    revertUnsavedSettings();
    return true;
  }

  export function applyRoute() {
    const next = parseRoute();
    if (S.settingsDirty && S.route.name === 'settings' && next.name !== 'settings') {
      if (!leaveSettingsOrStay()) {
        const stay = '#/settings/' + S.settingsPanel;
        if ((location.hash || '') !== stay) location.hash = stay;
        return;
      }
    }
    const prev = S.route.name;
    S.route = next;
    const onSettings = S.route.name === 'settings';
    document.getElementById('view-grid').classList.toggle('hidden', onSettings);
    document.getElementById('view-settings').classList.toggle('hidden', !onSettings);
    appEl.classList.toggle('page-settings', onSettings);
    settingsBtn.classList.toggle('active', onSettings);
    settingsBtn.setAttribute('aria-current', onSettings ? 'page' : 'false');
    syncHeaderHeight();
    if (onSettings) {
      S.pendingGridFocus = null;
      closeDetail(true);
      S.settingsPanel = S.route.section || 'appearance';
      const want = '#/settings/' + S.settingsPanel;
      if ((location.hash || '') !== want) history.replaceState(null, '', want);
      if (prev === 'settings') {
        showSettingsPanel(S.settingsPanel);
        return;
      }
      loadSettingsPage();
      return;
    }
    if (prev === 'settings') tick();
    if (S.route.hostId && hostById(S.route.hostId)) S.focusHostId = S.route.hostId;
    else if (S.route.name !== 'settings') S.focusHostId = 'local';
    if (S.route.name === 'port') {
      S.selectedPort = S.route.port;
      S.selectedHostId = S.route.hostId || 'local';
      S.focusHostId = S.selectedHostId;
      if (S.currentData || hasPeers()) render();
      else showPortDetail(S.route.port);
      return;
    }
    closeDetail(true);
    if (S.currentData || hasPeers()) render();
    applyPendingGridFocus();
  }
