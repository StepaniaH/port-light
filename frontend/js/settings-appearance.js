/* Locale menu and theme selection controls. */
import { S, CORE_THEMES, PALETTE_VARIANTS, CUSTOM_PREFIX, resolveMode, paletteAvailable } from './state.js?v=93';
import { t, escapeHtml } from './text.js?v=93';
import { choiceLabel } from './settings-format.js?v=93';

  export function localeCopyHtml(c) {
    var native;
    var nativeAttr;
    var localKey;
    if (c === 'auto') {
      native = t('choice.auto');
      nativeAttr = ' data-i18n="choice.auto"';
      localKey = 'localeName.auto';
    } else {
      native = t('localeNative.' + c);
      nativeAttr = '';
      localKey = 'localeName.' + c;
    }
    return '<span class="locale-copy"><span class="locale-endonym"' + nativeAttr + '>' +
      escapeHtml(native) + '</span><span class="locale-exonym" data-i18n="' + localKey + '">' +
      escapeHtml(t(localKey)) + '</span></span>';
  }

  export function closeLocaleMenu(opts) {
    const drop = document.querySelector('.locale-dropdown.is-open');
    if (!drop) return false;
    drop.classList.remove('is-open');
    const btn = drop.querySelector('.locale-trigger');
    if (btn) btn.setAttribute('aria-expanded', 'false');
    if (opts && opts.focusTrigger && btn) btn.focus();
    return true;
  }

  export function moveLocaleHighlight(delta) {
    const drop = document.querySelector('.locale-dropdown.is-open');
    if (!drop) return;
    const rows = Array.prototype.slice.call(drop.querySelectorAll('.locale-row'));
    if (!rows.length) return;
    let i = rows.indexOf(document.activeElement);
    if (delta === 'start') i = 0;
    else if (delta === 'end') i = rows.length - 1;
    else if (i < 0) i = 0;
    else i = (i + delta + rows.length) % rows.length;
    rows[i].focus();
  }

  export function syncLocaleTrigger() {
    const drop = document.querySelector('.locale-dropdown');
    if (!drop) return;
    const input = drop.querySelector('input[name="locale"]');
    const dest = drop.querySelector('.locale-trigger .locale-copy');
    if (!input || !dest) return;
    const row = drop.querySelector('.locale-row[data-value="' + input.value + '"] .locale-copy');
    if (row) dest.innerHTML = row.innerHTML;
  }

  export function renderLocaleList(choices, value, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : 'auto';
    const label = escapeHtml(t('settings.fields.locale.label'));
    const rows = choices.map(function (c) {
      const on = c === current;
      const id = 'locale-opt-' + c;
      return '<button type="button" class="locale-row' + (on ? ' is-selected' : '') +
        '" id="' + escapeHtml(id) + '" data-value="' + escapeHtml(c) + '" role="option" aria-selected="' + (on ? 'true' : 'false') + '"' +
        disabled + '>' + localeCopyHtml(c) + '<span class="locale-check" aria-hidden="true"></span></button>';
    }).join('');
    return '<div class="locale-dropdown">' +
      '<input type="hidden" name="locale" value="' + escapeHtml(current) + '"' + disabled + '>' +
      '<button type="button" class="locale-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="locale-menu" aria-label="' +
      label + '"' + disabled + '>' +
      localeCopyHtml(current) + '<span class="locale-caret" aria-hidden="true"></span></button>' +
      '<div class="locale-menu" id="locale-menu" role="listbox" aria-label="' + label + '">' + rows + '</div></div>';
  }

  function modeSwatch(c, current, disabled) {
    const on = c === current;
    const preview = c === 'system'
      ? '<span class="theme-swatch-preview is-system" aria-hidden="true">' +
        '<span class="theme-swatch-half dark"></span><span class="theme-swatch-half light"></span></span>'
      : '<span class="theme-swatch-preview" aria-hidden="true"><i class="used"></i><i class="configured"></i><i class="free"></i></span>';
    return '<label class="theme-swatch" data-theme-preview="' + escapeHtml(c) + '">' +
      '<input type="radio" name="theme_mode" value="' + escapeHtml(c) + '"' +
      (on ? ' checked' : '') + disabled + '>' + preview +
      '<span class="theme-swatch-name" data-i18n="choice.' + c + '">' +
      escapeHtml(choiceLabel(c)) + '</span></label>';
  }

  export function renderModePicker(choices, value, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : 'system';
    const label = escapeHtml(t('settings.fields.theme_mode.label'));
    const core = CORE_THEMES.filter(function (c) { return choices.indexOf(c) >= 0; });
    return '<div class="theme-picker" role="radiogroup" aria-label="' + label + '">' +
      '<div class="theme-picker-core">' + core.map(function (c) {
        return modeSwatch(c, current, disabled);
      }).join('') + '</div></div>';
  }

  export function currentMode() {
    let prefersLight = false;
    try {
      prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    } catch (e) {}
    return resolveMode(S.settings.theme_mode || 'system', prefersLight);
  }

  export function renderPalettePicker(choices, value, resolvedModeValue, disabled) {
    const current = choices.indexOf(value) >= 0 ? value : '';
    const mode = resolvedModeValue || currentMode();
    const label = escapeHtml(t('settings.fields.theme_palette.label'));

    function previewId(family) {
      if (mode === 'light' && PALETTE_VARIANTS[family].indexOf('light') >= 0) {
        return family + '-light';
      }
      return family;
    }

    function entry(family) {
      const on = family === current;
      const available = family === '' || paletteAvailable(family, mode);
      const cls = available ? 'theme-swatch' : 'theme-swatch is-unavailable';
      const dis = available ? disabled : ' disabled';
      const previewIdResolved = family === '' ? mode : previewId(family);
      const preview = '<span class="theme-swatch-preview" aria-hidden="true">' +
        '<i class="used"></i><i class="configured"></i><i class="free"></i></span>';
      const nameKey = family === '' ? 'settings.theme.builtin' : 'choice.' + family;
      const nameText = family === '' ? escapeHtml(t('settings.theme.builtin')) : escapeHtml(choiceLabel(family));
      return '<label class="' + cls + '" data-theme-preview="' + escapeHtml(previewIdResolved) + '">' +
        '<input type="radio" name="theme_palette" value="' + escapeHtml(family) + '"' +
        (on ? ' checked' : '') + dis + '>' + preview +
        '<span class="theme-swatch-name" data-i18n="' + nameKey + '">' + nameText + '</span></label>';
    }

    const families = choices.filter(function (c) { return c !== ''; });

    function customEntry(theme) {
      const sel = CUSTOM_PREFIX + theme.id;
      const on = sel === current;
      const available = theme.mode === mode;
      const cls = available ? 'theme-swatch is-custom' : 'theme-swatch is-custom is-unavailable';
      const dis = available ? disabled : ' disabled';
      const dots = ['used', 'configured', 'free'].map(function (kind) {
        return '<i class="' + kind + '" style="background:' + escapeHtml(theme.colors[kind]) + '"></i>';
      }).join('');
      return '<span class="' + cls + '" data-theme-preview="">' +
        '<label><input type="radio" name="theme_palette" value="' + escapeHtml(sel) + '"' +
        (on ? ' checked' : '') + dis + '>' +
        '<span class="theme-swatch-preview" aria-hidden="true">' + dots + '</span>' +
        '<span class="theme-swatch-name"><span class="custom-name">' + escapeHtml(theme.name) +
        '</span><em class="theme-badge">' + escapeHtml(t('settings.theme.customBadge')) + '</em></span></label>' +
        '<button type="button" class="btn-delete" data-delete-theme="' + escapeHtml(theme.id) + '"' +
        disabled + '>' + escapeHtml(t('hosts.remove')) + '</button></span>';
    }

    const customs = S.customThemes || [];

    return '<div class="theme-picker" role="radiogroup" aria-label="' + label + '">' +
      '<p class="theme-picker-label" data-i18n="settings.theme.palettes">' +
      escapeHtml(t('settings.theme.palettes')) + '</p>' +
      '<div class="theme-picker-palettes">' + entry('').concat(families.map(entry).join(''), customs.map(customEntry).join('')) + '</div></div>';
  }

  export function syncPaletteAvailability() {
    const mode = currentMode();
    const readonly = !!(S.settingsDoc && S.settingsDoc.readonly);
    document.querySelectorAll('.theme-swatch[data-theme-preview]').forEach(function (labelEl) {
      const input = labelEl.querySelector('input[name="theme_palette"]');
      if (!input) return;
      const family = input.value;
      if (family.indexOf(CUSTOM_PREFIX) === 0) {
        const id = family.slice(CUSTOM_PREFIX.length);
        const themeRow = (S.customThemes || []).find(function (x) { return x.id === id; });
        const ok = !!themeRow && themeRow.mode === mode;
        input.disabled = readonly || !ok;
        labelEl.classList.toggle('is-unavailable', !ok);
        return;
      }
      const previewId = family === '' ? mode
        : (PALETTE_VARIANTS[family].indexOf('light') >= 0 && mode === 'light' ? family + '-light' : family);
      labelEl.setAttribute('data-theme-preview', previewId);
      const available = family === '' || paletteAvailable(family, mode);
      input.disabled = readonly || !available;
      labelEl.classList.toggle('is-unavailable', !available);
    });
  }
