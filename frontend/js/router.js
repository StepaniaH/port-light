/* Hash router: #/, #/settings/:panel, #/port/:n, #/h/:host(/port/:n). */

import { S, SETTINGS_PANELS } from './state.js?v=89';
import { doctorBtn, settingsBtn, appEl, syncHeaderHeight } from './dom.js?v=89';
import { hostById, hasPeers, usesFocusedHostView } from './hosts.js?v=89';
import { applyPendingGridFocus } from './grid.js?v=89';
import { closeDetail, showPortDetail } from './detail.js?v=89';


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
    if (parts[0] === 'doctor') return { name: 'doctor' };
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

  export function applyRoute({ render, refresh, settingsPage, doctorPage }) {
    const next = parseRoute();
    const prev = S.route.name;
    const previousHostId = S.focusHostId;
    S.route = next;
    const onSettings = S.route.name === 'settings';
    const onDoctor = S.route.name === 'doctor';
    const onWorkspace = onSettings || onDoctor;
    document.getElementById('view-grid').classList.toggle('hidden', onWorkspace);
    document.getElementById('view-settings').classList.toggle('hidden', !onSettings);
    document.getElementById('view-doctor').classList.toggle('hidden', !onDoctor);
    appEl.classList.toggle('page-settings', onWorkspace);
    settingsBtn.classList.toggle('active', onSettings);
    settingsBtn.setAttribute('aria-current', onSettings ? 'page' : 'false');
    doctorBtn.classList.toggle('active', onDoctor);
    doctorBtn.setAttribute('aria-current', onDoctor ? 'page' : 'false');
    syncHeaderHeight();
    if (onSettings) {
      S.pendingGridFocus = null;
      closeDetail(true);
      S.settingsPanel = S.route.section || 'appearance';
      const want = '#/settings/' + S.settingsPanel;
      if ((location.hash || '') !== want) history.replaceState(null, '', want);
      if (prev === 'settings') {
        settingsPage.show(S.settingsPanel);
        return;
      }
      settingsPage.open(S.settingsPanel);
      return;
    }
    if (onDoctor) {
      S.pendingGridFocus = null;
      closeDetail(true);
      if (prev !== 'doctor') doctorPage.open();
      return;
    }
    if (prev === 'settings' || prev === 'doctor') refresh();
    if (S.route.hostId && hostById(S.route.hostId)) S.focusHostId = S.route.hostId;
    else if (S.route.name !== 'settings') S.focusHostId = 'local';
    if (usesFocusedHostView() && S.focusHostId !== previousHostId) refresh();
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
