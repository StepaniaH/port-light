/* Settings → Supporter Key panel: Harbor Beacon Plaque and offline key activation. */

import { S } from './state.js?v=127';
import { t, escapeHtml } from './text.js?v=127';
import { api } from './api.js?v=127';
import { kvRow } from './settings-format.js?v=127';
import { syncSupporterHeader } from './supporter-header.js?v=127';

const SUPPORT_URL = 'https://portlight.stepaniah.me/#support';

function plaqueLogoHtml() {
  const brandSvg = document.querySelector('#brand-link .brand-icon');
  if (brandSvg && brandSvg.innerHTML) {
    return '<svg class="plaque-logo" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="30" height="30" aria-hidden="true">' +
      brandSvg.innerHTML + '</svg>';
  }
  return '<svg class="plaque-logo" viewBox="0 0 16 16" width="24" height="24" fill="none" aria-hidden="true">' +
    '<path d="M8 1.5v2.4M2.8 4.1l1.9 1.3M13.2 4.1l-1.9 1.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>' +
    '<path d="M5.6 7.5h4.8l.95 6.5h-6.7l.95-6.5Z" fill="currentColor" fill-opacity="0.16" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/>' +
    '<circle cx="8" cy="5.7" r="1.35" fill="currentColor"/></svg>';
}

function showHeaderRowHtml(field, value, readonly) {
  if (!field) return '';
  const disabled = readonly ? ' disabled' : '';
  const checked = value ? ' checked' : '';
  return '<label class="setting-row" data-setting="supporter_show_header">' +
    '<span class="setting-copy">' +
    '<span class="setting-label" id="setting-label-supporter_show_header" data-i18n="settings.fields.supporter_show_header.label">' +
    escapeHtml(t('settings.fields.supporter_show_header.label')) + '</span>' +
    '<span class="field-help" data-i18n="settings.fields.supporter_show_header.help">' +
    escapeHtml(t('settings.fields.supporter_show_header.help')) + '</span>' +
    '</span>' +
    '<span class="setting-control"><span class="switch">' +
    '<input type="checkbox" name="supporter_show_header"' + checked + disabled + '>' +
    '<span class="track"></span></span></span></label>';
}

function keyFormHtml(canClear) {
  const removeBtn = canClear
    ? '<button type="button" class="btn-secondary supporter-btn-remove" data-supporter-clear data-i18n="supporter.action.remove">' +
      escapeHtml(t('supporter.action.remove')) + '</button>'
    : '';
  return '<div class="supporter-key-form">' +
    '<input type="text" id="supporter-key-input" data-supporter-input class="supporter-key-input" ' +
    'autocomplete="off" spellcheck="false" ' +
    'placeholder="' + escapeHtml(t('supporter.input.placeholder')) + '" ' +
    'data-i18n-placeholder="supporter.input.placeholder" ' +
    'aria-label="' + escapeHtml(t('supporter.input.label')) + '" ' +
    'data-i18n-aria="supporter.input.label">' +
    '<button type="button" class="btn-primary" data-supporter-activate data-i18n="supporter.action.activate">' +
    escapeHtml(t('supporter.action.activate')) + '</button>' +
    removeBtn + '</div>';
}

function composeHintHtml() {
  const text = escapeHtml(t('supporter.inactive.composeHint', { env: 'PORT_LIGHT_SUPPORTER_KEY' }))
    .replace('PORT_LIGHT_SUPPORTER_KEY', '<code>PORT_LIGHT_SUPPORTER_KEY</code>');
  return '<div class="supporter-inactive-foot">' +
    '<span>' + text + '</span>' +
    '<a class="supporter-link" href="' + SUPPORT_URL + '" target="_blank" rel="noopener noreferrer">' +
    '<span data-i18n="supporter.inactive.getKey">' + escapeHtml(t('supporter.inactive.getKey')) + '</span>' +
    '<span aria-hidden="true"> ↗</span></a></div>';
}

export function supporterCardsHtml(supporterDoc, showHeaderField, showHeaderValue, readonly) {
  const s = supporterDoc || {};
  const canEdit = s.can_edit !== undefined ? !!s.can_edit : !readonly;
  const canClear = !!s.can_clear && canEdit;

  if (s.active) {
    const tier = s.tier === 'fleet' ? 'fleet' : 'personal';
    const tierKey = tier === 'fleet' ? 'supporter.tier.fleet' : 'supporter.tier.personal';
    const thanksKey = tier === 'fleet' ? 'supporter.plaque.thanksFleet' : 'supporter.plaque.thanksPersonal';
    const originKey = s.origin === 'env' ? 'supporter.origin.env' : 'supporter.origin.file';
    const sinceHtml = s.issued_at
      ? '<div class="plaque-since">' + escapeHtml(t('supporter.plaque.since', { date: s.issued_at })) + '</div>'
      : '';
    const rows = [
      kvRow('supporter.plaque.tierLabel', t(tierKey), tierKey),
      kvRow('supporter.plaque.orderLabel', s.order_preview || '—'),
      kvRow('supporter.plaque.originLabel', t(originKey), originKey),
    ].join('');

    const plaqueCard =
      '<section class="settings-card supporter-plaque">' +
      '<div class="plaque-top">' +
      '<div class="plaque-emblem">' + plaqueLogoHtml() + '</div>' +
      '<div class="plaque-meta">' +
      '<div class="plaque-title-row">' +
      '<h2 class="plaque-title" data-i18n="' + tierKey + '">' + escapeHtml(t(tierKey)) + '</h2>' +
      '<span class="plaque-tier-tag"><i class="supporter-beacon-dot" aria-hidden="true"></i>' +
      '<span data-i18n="supporter.plaque.lit">' + escapeHtml(t('supporter.plaque.lit')) + '</span></span>' +
      '</div>' +
      sinceHtml +
      '<p class="plaque-thanks" data-i18n="' + thanksKey + '">' + escapeHtml(t(thanksKey)) + '</p>' +
      '</div></div>' +
      '<div class="settings-card-body">' +
      '<div class="supporter-receipt">' +
      '<span class="rk">' + escapeHtml(s.key_preview || 'PL1.…') + '</span>' +
      '<span class="rv">✓ ' + escapeHtml(t('supporter.plaque.verified')) + '</span>' +
      '</div>' +
      rows +
      '</div></section>';

    const envHint = canEdit && s.origin === 'env'
      ? '<p class="field-help supporter-env-hint" data-i18n="supporter.manage.envOverrideHint">' +
        escapeHtml(t('supporter.manage.envOverrideHint')) + '</p>'
      : '';
    const manageInner = canEdit
      ? envHint + keyFormHtml(canClear)
      : '<p class="field-help" data-i18n="supporter.manage.readonly">' +
        escapeHtml(t('supporter.manage.readonly')) + '</p>';

    const manageCard =
      '<section class="settings-card">' +
      '<div class="settings-card-body supporter-manage-card">' +
      showHeaderRowHtml(showHeaderField, showHeaderValue, readonly) +
      '<details class="supporter-manage">' +
      '<summary data-i18n="supporter.manage.summary">' + escapeHtml(t('supporter.manage.summary')) + '</summary>' +
      '<div class="supporter-manage-body">' + manageInner + '</div>' +
      '</details>' +
      '<p id="supporter-action-status" class="action-status" role="status" aria-live="polite"></p>' +
      '</div></section>';

    return plaqueCard + manageCard;
  }

  let alertHtml = '';
  if (s.status === 'invalid') {
    const alertKey = s.origin === 'env' ? 'supporter.invalid.env' : 'supporter.invalid.file';
    const clearBtn = s.origin === 'file' && canClear
      ? '<button type="button" class="btn-secondary supporter-btn-remove" data-supporter-clear data-i18n="supporter.action.remove">' +
        escapeHtml(t('supporter.action.remove')) + '</button>'
      : '';
    alertHtml = '<div class="supporter-alert" role="alert">' +
      '<p data-i18n="' + alertKey + '">' + escapeHtml(t(alertKey)) + '</p>' +
      clearBtn + '</div>';
  }

  const formHtml = canEdit
    ? keyFormHtml(false)
    : '<p class="field-help" data-i18n="supporter.manage.readonly">' +
      escapeHtml(t('supporter.manage.readonly')) + '</p>';

  return '<section class="settings-card supporter-inactive">' +
    '<header class="settings-card-head">' +
    '<h2 data-i18n="supporter.inactive.title">' + escapeHtml(t('supporter.inactive.title')) + '</h2>' +
    '<p data-i18n="supporter.inactive.lead">' + escapeHtml(t('supporter.inactive.lead')) + '</p>' +
    '</header>' +
    '<div class="settings-card-body">' +
    alertHtml +
    formHtml +
    '<p id="supporter-action-status" class="action-status" role="status" aria-live="polite"></p>' +
    composeHintHtml() +
    '</div></section>';
}

function setSupporterStatus(key, className) {
  const el = document.getElementById('supporter-action-status');
  if (!el) return;
  el.className = className || 'action-status';
  el.textContent = key ? t(key) : '';
}

function supporterErrorKey(body, status) {
  const detail = body && typeof body.detail === 'string' ? body.detail : '';
  if (detail === 'supporter_key_empty') return 'supporter.error.empty';
  if (detail === 'supporter_key_format') return 'supporter.error.format';
  if (detail === 'supporter_key_invalid') return 'supporter.error.invalid';
  if (status === 403 || detail === 'readonly') return 'supporter.error.readonly';
  return 'supporter.error.failed';
}

export function syncSupporterMeta(supporterDoc) {
  if (!supporterDoc || typeof supporterDoc !== 'object') return;
  if (!S.meta) S.meta = {};
  S.meta.supporter = {
    active: !!supporterDoc.active,
    tier: supporterDoc.tier || null,
    issued_at: supporterDoc.issued_at || null,
    show_in_header: supporterDoc.show_in_header !== false,
  };
  syncSupporterHeader();
}

export function rerenderSupporter(statusKey, statusClass) {
  const panel = document.getElementById('settings-panel-supporter');
  if (!panel || !S.settingsDoc) return;
  const field = (S.settingsDoc.fields || []).find(function (f) { return f.key === 'supporter_show_header'; }) || null;
  const showValue = S.settingsDraft && Object.prototype.hasOwnProperty.call(S.settingsDraft, 'supporter_show_header')
    ? !!S.settingsDraft.supporter_show_header
    : (S.settings.supporter_show_header !== false);
  panel.innerHTML = supporterCardsHtml(S.settingsDoc.supporter || {}, field, showValue, !!S.settingsDoc.readonly);
  if (statusKey) setSupporterStatus(statusKey, statusClass || 'action-status');
}

export async function submitSupporterKey(rawKey) {
  const trimmed = String(rawKey || '').trim();
  if (!trimmed) {
    setSupporterStatus('supporter.error.empty', 'action-status is-error');
    return false;
  }
  const btn = document.querySelector('[data-supporter-activate]');
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('supporter.action.activating');
  }
  setSupporterStatus('', 'action-status');
  try {
    const res = await api('/api/supporter-key', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: trimmed }),
    });
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = t('supporter.action.activate');
      }
      setSupporterStatus(supporterErrorKey(body, res.status), 'action-status is-error');
      return false;
    }
    const next = body && body.supporter ? body.supporter : body;
    if (S.settingsDoc) S.settingsDoc.supporter = next;
    syncSupporterMeta(next);
    rerenderSupporter('supporter.status.activated', 'action-status is-ok');
    return true;
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('supporter.action.activate');
    }
    setSupporterStatus('supporter.error.failed', 'action-status is-error');
    return false;
  }
}

export async function clearSupporterKey() {
  const btn = document.querySelector('[data-supporter-clear]');
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('supporter.action.removing');
  }
  setSupporterStatus('', 'action-status');
  try {
    const res = await api('/api/supporter-key', { method: 'DELETE' });
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (btn) {
        btn.disabled = false;
        btn.textContent = t('supporter.action.remove');
      }
      setSupporterStatus(supporterErrorKey(body, res.status), 'action-status is-error');
      return false;
    }
    const next = body && body.supporter ? body.supporter : body;
    if (S.settingsDoc) S.settingsDoc.supporter = next;
    syncSupporterMeta(next);
    rerenderSupporter('supporter.status.removed', 'action-status is-ok');
    return true;
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('supporter.action.remove');
    }
    setSupporterStatus('supporter.error.failed', 'action-status is-error');
    return false;
  }
}

let _delegated = false;
export function ensureSupporterDelegates() {
  if (_delegated) return;
  _delegated = true;
  document.addEventListener('click', function (e) {
    const activateBtn = e.target.closest('[data-supporter-activate]');
    if (activateBtn) {
      e.preventDefault();
      const input = document.getElementById('supporter-key-input');
      submitSupporterKey(input ? input.value : '');
      return;
    }
    const clearBtn = e.target.closest('[data-supporter-clear]');
    if (clearBtn) {
      e.preventDefault();
      clearSupporterKey();
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    const input = e.target && e.target.closest ? e.target.closest('[data-supporter-input]') : null;
    if (!input) return;
    e.preventDefault();
    e.stopPropagation();
    submitSupporterKey(input.value);
  });
}
