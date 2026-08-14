/* Port-Light locales: frontend/locales/{en,zh-CN,zh-TW,ja}.json */

(function (global) {
  'use strict';

  var SUPPORTED = ['en', 'zh-CN', 'zh-TW', 'ja'];
  var CACHE_BUST = '16';
  var cache = {};
  var dict = {};
  var locale = 'en';
  var fallback = {};

  function lookup(tree, key) {
    if (!tree) return undefined;
    var parts = key.split('.');
    var cur = tree;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return undefined;
      cur = cur[parts[i]];
    }
    return typeof cur === 'string' ? cur : undefined;
  }

  function interpolate(text, vars) {
    if (!vars) return text;
    return text.replace(/\{(\w+)\}/g, function (_, name) {
      return vars[name] == null ? '{' + name + '}' : String(vars[name]);
    });
  }

  function matchLocale(raw) {
    if (!raw) return null;
    var tag = String(raw).replace(/_/g, '-');
    var lower = tag.toLowerCase();
    if (lower === 'zh-cn' || lower === 'zh-hans' || lower.indexOf('zh-hans') === 0 || lower === 'zh-sg' || lower === 'zh-my') {
      return 'zh-CN';
    }
    if (lower === 'zh-tw' || lower === 'zh-hk' || lower === 'zh-mo' || lower === 'zh-hant' || lower.indexOf('zh-hant') === 0) {
      return 'zh-TW';
    }
    if (lower === 'zh') return 'zh-CN';
    if (lower === 'ja' || lower.indexOf('ja-') === 0) return 'ja';
    if (lower === 'en' || lower.indexOf('en-') === 0) return 'en';
    for (var i = 0; i < SUPPORTED.length; i++) {
      if (SUPPORTED[i].toLowerCase() === lower) return SUPPORTED[i];
    }
    return null;
  }

  function detectBrowser() {
    var list = [];
    try {
      if (navigator.languages && navigator.languages.length) {
        list = navigator.languages;
      } else if (navigator.language) {
        list = [navigator.language];
      }
    } catch (e) {}
    for (var i = 0; i < list.length; i++) {
      var hit = matchLocale(list[i]);
      if (hit) return hit;
    }
    return 'en';
  }

  function storedPreference() {
    try {
      var s = JSON.parse(localStorage.getItem('port-light-settings') || '{}');
      if (s.locale) return s.locale;
    } catch (e) {}
    return 'auto';
  }

  function resolve(pref) {
    var wanted = pref == null || pref === '' ? storedPreference() : pref;
    if (!wanted || wanted === 'auto') return detectBrowser();
    return matchLocale(wanted) || detectBrowser();
  }

  function applyLang() {
    var htmlLang = locale === 'zh-CN' ? 'zh-CN' : locale === 'zh-TW' ? 'zh-TW' : locale === 'ja' ? 'ja' : 'en';
    document.documentElement.lang = htmlLang;
    document.documentElement.setAttribute('data-locale', locale);
  }

  function applyDom() {
    applyLang();
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var text = t(el.getAttribute('data-i18n-title'));
      el.setAttribute('title', text);
      el.setAttribute('aria-label', text);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
    });
    document.documentElement.setAttribute('data-i18n-ready', '');
  }

  function t(key, vars) {
    var raw = lookup(dict, key);
    if (raw == null) raw = lookup(fallback, key);
    if (raw == null) raw = key;
    return interpolate(raw, vars);
  }

  function fetchLocale(code) {
    if (cache[code]) return Promise.resolve(cache[code]);
    return fetch('/static/locales/' + code + '.json?v=' + CACHE_BUST, { credentials: 'same-origin' })
      .then(function (res) {
        if (!res.ok) throw new Error('locale ' + code);
        return res.json();
      })
      .then(function (data) {
        cache[code] = data;
        return data;
      });
  }

  function load(pref) {
    var next = resolve(pref);
    var jobs = [fetchLocale(next)];
    if (next !== 'en' && !cache.en) jobs.push(fetchLocale('en'));
    return Promise.all(jobs).then(function () {
      locale = next;
      dict = cache[next] || cache.en || {};
      fallback = cache.en || dict;
      applyDom();
      return locale;
    }).catch(function () {
      locale = 'en';
      dict = cache.en || {};
      fallback = dict;
      applyDom();
      return locale;
    });
  }

  global.PortLightI18n = {
    supported: SUPPORTED,
    matchLocale: matchLocale,
    resolve: resolve,
    load: load,
    t: t,
    applyDom: applyDom,
    locale: function () { return locale; },
  };
})(window);
