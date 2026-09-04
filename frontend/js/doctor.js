/* Setup diagnostics page. Rendering is fed only the sanitized Doctor API. */

import { fetchDoctor } from './api.js?v=90';
import { escapeHtml, t } from './text.js?v=90';

function statusLabel(status) {
  return t('doctor.status.' + status);
}

function evidenceText(check) {
  const evidence = check.evidence || {};
  if (check.id === 'snapshot') {
    return evidence.age_seconds == null ? '' : t('doctor.evidence.age', { value: evidence.age_seconds });
  }
  if (check.id === 'listen') {
    return t('doctor.evidence.listen', { source: evidence.source || 'none' });
  }
  if (check.id === 'docker') {
    return t('doctor.evidence.docker', { transport: t('doctor.transport.' + (evidence.transport || 'socket_missing')) });
  }
  if (check.id === 'compose') {
    return t('doctor.evidence.compose', { count: evidence.files_scanned || 0 });
  }
  if (check.id === 'degradations') {
    return t('doctor.evidence.events', { count: evidence.count || 0 });
  }
  if (check.id === 'settings_store') {
    return t('doctor.evidence.settings', { source: evidence.source || 'auto' });
  }
  return '';
}

export function renderDoctor(document) {
  const host = window.document.getElementById('doctor-results');
  if (!host || !document) return;
  const counts = document.counts || {};
  const checks = (document.checks || []).map(function (check) {
    const evidence = evidenceText(check);
    return '<article class="doctor-check is-' + escapeHtml(check.status) + '">' +
      '<div class="doctor-check-main"><span class="doctor-dot" aria-hidden="true"></span>' +
      '<div><h2>' + escapeHtml(t('doctor.check.' + check.id)) + '</h2>' +
      '<p>' + escapeHtml(t('doctor.detail.' + check.detail)) + '</p>' +
      (evidence ? '<p class="field-help">' + escapeHtml(evidence) + '</p>' : '') +
      (check.remediation ? '<p class="doctor-remediation">' + escapeHtml(t('doctor.remediation.' + check.remediation)) + '</p>' : '') +
      '</div></div><span class="doctor-badge">' + escapeHtml(statusLabel(check.status)) + '</span></article>';
  }).join('');
  host.innerHTML = '<section class="doctor-summary is-' + escapeHtml(document.overall) + '">' +
    '<div><h2>' + escapeHtml(t('doctor.overall.' + document.overall)) + '</h2>' +
    '<p>' + escapeHtml(t('doctor.summary', {
      pass: counts.pass || 0, warning: counts.warning || 0, fail: counts.fail || 0,
    })) + '</p></div></section><div class="doctor-checks">' + checks + '</div>' +
    '<details class="doctor-report-preview"><summary>' + escapeHtml(t('doctor.preview')) +
    '</summary><pre>' + escapeHtml(document.report || '') + '</pre></details>';
}

export async function copyReportText(report) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(report);
      return;
    } catch {}
  }
  const area = window.document.createElement('textarea');
  area.value = report;
  area.setAttribute('readonly', '');
  area.setAttribute('aria-hidden', 'true');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  window.document.body.appendChild(area);
  if (typeof area.select === 'function') area.select();
  let copied = false;
  try {
    copied = typeof window.document.execCommand === 'function' && window.document.execCommand('copy');
  } finally {
    area.remove();
  }
  if (!copied) throw new Error('Copy is unavailable');
}

export function mountDoctorPage(root) {
  if (!root) throw new Error('doctor root is required');
  let report = '';
  let generation = 0;
  const status = window.document.getElementById('doctor-status');
  const copy = window.document.getElementById('doctor-copy');

  async function load() {
    const current = ++generation;
    root.setAttribute('aria-busy', 'true');
    if (status) { status.className = 'action-status'; status.textContent = t('doctor.running'); }
    const document = await fetchDoctor();
    if (current !== generation) return null;
    root.removeAttribute('aria-busy');
    if (!document) {
      report = '';
      if (copy) copy.disabled = true;
      if (status) { status.className = 'action-status is-error'; status.textContent = t('doctor.failed'); }
      return null;
    }
    report = document.report || '';
    if (copy) copy.disabled = !report;
    if (status) { status.className = 'action-status'; status.textContent = ''; }
    renderDoctor(document);
    return document;
  }

  window.document.getElementById('doctor-refresh').addEventListener('click', load);
  copy.addEventListener('click', async function () {
    if (!report) return;
    try {
      await copyReportText(report);
      if (status) { status.className = 'action-status is-ok'; status.textContent = t('doctor.copied'); }
    } catch (err) {
      if (status) { status.className = 'action-status is-error'; status.textContent = t('doctor.copyFailed'); }
    }
  });

  return { open: load };
}
