/* Shared mutable application state. One store, owned here; every view
   reads and writes through `S` so modules stay free of import cycles. */

export const SETTINGS_PANELS = ['appearance', 'occupancy', 'automation', 'advanced'];

export const CARD_FIELD_KEYS = {
  show_status_text: true,
  show_access_badge: true,
  show_protocol_badge: true,
};

export const CORE_THEMES = ['system', 'dark', 'light'];

export const PALETTE_VARIANTS = {
  gruvbox: ['dark', 'light'],
  catppuccin: ['dark', 'light'],
  solarized: ['dark', 'light'],
  nord: ['dark'], dracula: ['dark'], 'tokyo-night': ['dark'],
  'one-dark': ['dark'], everforest: ['dark'], 'rose-pine': ['dark'],
  kanagawa: ['dark'],
};

export const S = {
  currentData: null,
  hostCatalog: { local: { id: 'local', name: '', local: true }, peers: [], readonly: false },
  hostMaps: {},
  focusHostId: 'local',
  selectedHostId: 'local',
  hostAborts: {},
  hostEtags: {},
  hostRetrying: {},
  peersDraft: [],
  statusFilter: 'all',
  kindFilters: new Set(),
  sortMode: 'port-asc',
  searchTerm: '',
  searchPortNum: null,
  selectedPort: null,
  rangeStart: 1,
  rangeEnd: 9999,
  rangeFromView: false,
  showHidden: false,
  settings: {
    locale: 'auto',
    theme_mode: 'system',
    theme_palette: '',
    grid_density: 'comfortable',
    show_status_text: false,
    show_access_badge: true,
    show_protocol_badge: true,
    copy_on_click: true,
    auto_refresh: true,
    refresh_ms: 5000,
    port_range_start: 1,
    port_range_end: 9999,
    guess_urls: true,
    url_host: '',
    url_scheme: 'auto',
  },
  settingsDoc: null,
  settingsDirty: false,
  settingsPanel: 'appearance',
  refreshTimer: null,
  meta: { hidden_unlock_required: false, hidden_ports_withheld: false, version: '', settings_readonly: false },
  hiddenUnlock: sessionStorage.getItem('port-light-hidden-unlock') || '',
  route: { name: 'grid' },
  pendingAfterUnlock: null,
  pendingUnlockFocus: 'eye',
  focusBack: null,
  pendingGridFocus: null,
  occupancyKey: '',
  portsEtag: '',
  portsEtagUrl: '',
  portDetailGen: 0,
  detailShownPort: null,
  knownCache: {},
  knownInflight: {},
  knownRenderFrame: 0,
  lockedHitCache: {},
  lockedHitInflight: {},
  lastHiddenLocked: null,
  portsAbort: null,
};

export function resolveMode(requested, prefersLight) {
  if (requested === 'light' || requested === 'dark') return requested;
  return prefersLight ? 'light' : 'dark';
}

export function paletteAvailable(family, resolved) {
  var variants = PALETTE_VARIANTS[family];
  return !!variants && variants.indexOf(resolved) >= 0;
}

export function applyTheme() {
  var prefersLight = false;
  try {
    prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  } catch (e) {}
  var mode = resolveMode(S.settings.theme_mode || 'system', prefersLight);
  var html = document.documentElement;
  html.setAttribute('data-mode', mode);
  html.removeAttribute('data-theme');
  var pal = S.settings.theme_palette || '';
  if (pal && paletteAvailable(pal, mode)) {
    html.setAttribute('data-palette', pal);
  } else {
    html.removeAttribute('data-palette');
  }
}

export function applyAppearance() {
  applyTheme();
  document.documentElement.setAttribute('data-density', S.settings.grid_density || 'comfortable');
  try {
    localStorage.setItem('port-light-settings', JSON.stringify({
      theme_mode: S.settings.theme_mode,
      theme_palette: S.settings.theme_palette || '',
      grid_density: S.settings.grid_density,
      locale: S.settings.locale || 'auto',
    }));
  } catch (e) {}
}

export function saveView() {
  try {
    localStorage.setItem('port-light-view', JSON.stringify({
      sort: S.sortMode,
      status: S.statusFilter,
      kinds: Array.from(S.kindFilters),
      showHidden: S.showHidden,
      rangeStart: S.rangeStart,
      rangeEnd: S.rangeEnd,
    }));
  } catch (e) {}
}
