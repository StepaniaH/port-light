/* Pre-paint appearance bootstrap: applies cached mode, palette, and density before CSS loads. */
(function () {
  'use strict';
  try {
    var s = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
    var m = s.theme_mode === 'light' || s.theme_mode === 'dark'
      ? s.theme_mode
      : (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-mode', m);
    var PAL = {
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
    if (
      typeof s.theme_palette === 'string' &&
      s.theme_palette &&
      Object.prototype.hasOwnProperty.call(PAL, s.theme_palette) &&
      PAL[s.theme_palette].indexOf(m) >= 0
    ) {
      document.documentElement.setAttribute('data-palette', s.theme_palette);
    }
    var DENSITY = {
      loose: { minW: 164, gap: 10, padX: 14, padT: 12, padB: 16, minH: 76 },
      standard: { minW: 138, gap: 8, padX: 12, padT: 10, padB: 12, minH: 64 },
      compact: { minW: 112, gap: 6, padX: 10, padT: 8, padB: 8, minH: 52 },
    };
    var d = Object.prototype.hasOwnProperty.call(DENSITY, s.grid_density)
      ? DENSITY[s.grid_density]
      : DENSITY.standard;
    for (var key in d) {
      if (!Object.prototype.hasOwnProperty.call(d, key)) continue;
      var name = '--cell-' + key
        .toLowerCase()
        .replace('minw', 'min-w')
        .replace('padx', 'pad-x')
        .replace('padt', 'pad-t')
        .replace('padb', 'pad-b')
        .replace('minh', 'min-h');
      document.documentElement.style.setProperty(name, d[key] + 'px');
    }
  } catch (e) {}
})();
