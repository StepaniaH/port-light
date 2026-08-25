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

export const CUSTOM_PREFIX = '@custom:';

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
  customThemes: [],
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

const CARD_ANCHORS = { minW: [164, 112], gap: [10, 6], padX: [14, 10], padT: [12, 8], padB: [16, 8], minH: [76, 52] };

export function applyDisplayScale(cardScale, textScale) {
  const html = document.documentElement;
  const cRaw = Number(cardScale);
  const tRaw = Number(textScale);
  const c = Math.max(0, Math.min(100, Number.isFinite(cRaw) ? cRaw : 50));
  const t = Math.max(0, Math.min(100, Number.isFinite(tRaw) ? tRaw : 50));
  const k = c / 100;
  Object.keys(CARD_ANCHORS).forEach(function (key) {
    const pair = CARD_ANCHORS[key];
    const value = Math.round(pair[0] + (pair[1] - pair[0]) * k);
    const cssName = '--cell-' + key.toLowerCase().replace('minw', 'min-w').replace('padx', 'pad-x').replace('padt', 'pad-t').replace('padb', 'pad-b').replace('minh', 'min-h');
    html.style.setProperty(key === 'gap' ? '--cell-gap' : cssName, value + 'px');
  });
  let basePx = 16;
  try {
    basePx = parseFloat(getComputedStyle(html).fontSize) || 16;
  } catch (e) {}
  const fontPx = Math.max(12, Math.min(18, Math.round(basePx + (t - 50) * 0.08)));
  html.style.setProperty('--port-font', fontPx + 'px');
}

export function applyAppearance() {
  applyTheme();
  applyDisplayScale(S.settings.card_scale, S.settings.text_scale);
  try {
    localStorage.setItem('port-light-settings', JSON.stringify({
      theme_mode: S.settings.theme_mode,
      theme_palette: S.settings.theme_palette || '',
      grid_density: S.settings.grid_density,
      card_scale: S.settings.card_scale,
      text_scale: S.settings.text_scale,
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
