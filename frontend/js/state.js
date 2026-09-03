/* Shared mutable application state. One store, owned here; every view
   reads and writes through `S` so modules stay free of import cycles. */

export const SETTINGS_PANELS = ['appearance', 'occupancy', 'automation', 'advanced'];

export const CARD_FIELD_KEYS = {
  show_status_text: true,
  show_access_badge: true,
  show_protocol_badge: true,
  show_bind_addresses: true,
  show_bind_ipv4: true,
  show_bind_ipv6: true,
};

export const CORE_THEMES = ['system', 'dark', 'light'];

export const PALETTE_VARIANTS = {
  gruvbox: ['dark', 'light'],
  catppuccin: ['dark', 'light'],
  solarized: ['dark', 'light'],
  nord: ['dark', 'light'],
  dracula: ['dark', 'light'],
  'tokyo-night': ['dark', 'light'],
  'one-dark': ['dark', 'light'],
  everforest: ['dark', 'light'],
  'rose-pine': ['dark', 'light'],
  kanagawa: ['dark', 'light'],
};

export const CUSTOM_PREFIX = '@custom:';

export const LIVE_APPLY_KEYS = ['theme_mode', 'theme_palette', 'grid_density', 'locale'];

const CUSTOM_VAR_NAMES = {
  bg: '--bg', elevated: '--elevated', card: '--card', cardHover: '--card-hover',
  border: '--border', text: '--text', textDim: '--text-dim', used: '--used',
  configured: '--configured', free: '--free', accent: '--accent',
  conflict: '--conflict', access: '--access', hidden: '--hidden', danger: '--danger',
};

export function customPaletteVars(colors) {
  return Object.keys(CUSTOM_VAR_NAMES).map(function (key) {
    return [CUSTOM_VAR_NAMES[key], colors[key]];
  });
}

function clearCustomVars(html) {
  Object.keys(CUSTOM_VAR_NAMES).forEach(function (key) {
    html.style.removeProperty(CUSTOM_VAR_NAMES[key]);
  });
}

function findCustom(id) {
  return (S.customThemes || []).find(function (t) { return t.id === id; }) || null;
}

export const S = {
  currentData: null,
  hostCatalog: { local: { id: 'local', name: '', local: true }, peers: [], readonly: false, max_peers: 32 },
  hostMaps: {},
  focusHostId: 'local',
  selectedHostId: 'local',
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
    grid_density: 'standard',
    show_status_text: false,
    show_access_badge: true,
    show_protocol_badge: true,
    show_bind_addresses: false,
    show_bind_ipv4: true,
    show_bind_ipv6: true,
    copy_on_click: true,
    auto_refresh: true,
    refresh_ms: 5000,
    host_layout: 'waterfall',
    port_range_start: 1,
    port_range_end: 9999,
    guess_urls: true,
    url_host: '',
    url_scheme: 'auto',
  },
  customThemes: [],
  settingsDoc: null,
  settingsDirty: false,
  settingsRevision: 0,
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
  portDetailGen: 0,
  detailShownPort: null,
  knownCache: {},
  knownInflight: {},
  knownRenderFrame: 0,
  lockedHitCache: {},
  lockedHitInflight: {},
  lastHiddenLocked: null,
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
  clearCustomVars(html);
  var pal = S.settings.theme_palette || '';
  var customId = pal.indexOf(CUSTOM_PREFIX) === 0 ? pal.slice(CUSTOM_PREFIX.length) : '';
  var custom = customId ? findCustom(customId) : null;
  if (custom && custom.mode === mode) {
    customPaletteVars(custom.colors).forEach(function (pair) {
      html.style.setProperty(pair[0], pair[1]);
    });
    html.removeAttribute('data-palette');
    return;
  }
  if (pal && !customId && paletteAvailable(pal, mode)) {
    html.setAttribute('data-palette', pal);
  } else {
    html.removeAttribute('data-palette');
  }
}

export const DENSITY_PRESETS = {
  loose: { minW: 164, gap: 10, padX: 14, padT: 12, padB: 16, minH: 76 },
  standard: { minW: 138, gap: 8, padX: 12, padT: 10, padB: 12, minH: 64 },
  compact: { minW: 112, gap: 6, padX: 10, padT: 8, padB: 8, minH: 52 },
};

const CELL_VAR_NAMES = {
  minW: '--cell-min-w', gap: '--cell-gap', padX: '--cell-pad-x',
  padT: '--cell-pad-t', padB: '--cell-pad-b', minH: '--cell-min-h',
};

export function applyDensity(name) {
  const preset = DENSITY_PRESETS[name] || DENSITY_PRESETS.standard;
  const html = document.documentElement;
  Object.keys(CELL_VAR_NAMES).forEach(function (key) {
    html.style.setProperty(CELL_VAR_NAMES[key], preset[key] + 'px');
  });
}

export function hydrateCachedAppearance() {
  try {
    const cached = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    if (cached.theme_mode) S.settings.theme_mode = cached.theme_mode;
    if (typeof cached.theme_palette === 'string') S.settings.theme_palette = cached.theme_palette;
    if (cached.grid_density) S.settings.grid_density = cached.grid_density;
    if (cached.locale) S.settings.locale = cached.locale;
  } catch (e) {}
}

export function applyAppearance() {
  applyTheme();
  applyDensity(S.settings.grid_density);
}

/* Sole writer of the `port-light-settings` key: called on boot (once the
   server document lands), on save, and on revert — never for previews. */
export function persistAppearance() {
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
