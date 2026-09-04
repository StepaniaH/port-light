/* Explanations derived from the current occupancy response, including older peers. */
import { t, escapeHtml } from './text.js?v=90';

export function scanDiagnosticKeys(summary = {}) {
  if (summary.scan_complete === true && !summary.stale) return [];
  if (summary.stale) return ['stale'];
  const sources = summary.sources || {};
  const keys = [];
  for (const source of ['listen', 'docker']) {
    if (sources[source] === 'failed') keys.push(source);
  }
  if (sources.compose !== 'disabled') {
    if (summary.compose_truncated) keys.push('composeTruncated');
    if (summary.compose_incomplete) keys.push('composeIncomplete');
    if (sources.compose === 'failed' && !summary.compose_truncated && !summary.compose_incomplete) {
      keys.push('compose');
    }
  }
  return keys.length ? keys : ['unknown'];
}

export function scanWarningMarkup(summary, hostId, context) {
  const keys = scanDiagnosticKeys(summary);
  if (!keys.length) return '';
  const local = hostId === 'local';
  const needsConfig = keys.some(key => key !== 'stale' && key !== 'unknown');
  const copy = key => escapeHtml(t('scanner.diagnostics.' + key));
  const key = [context, hostId, ...keys].join(':');
  return '<details class="scan-warning" data-scan-warning="' + escapeHtml(key) + '">' +
    '<summary>' + escapeHtml(t('scanner.snapshotUnavailable')) +
    ' <span class="scan-warning-info" aria-hidden="true">ⓘ</span></summary>' +
    '<div class="scan-warning-panel"><strong>' + copy('title') + '</strong>' +
    keys.map(key => '<p>' + copy(key) + '</p>').join('') +
    (needsConfig ? '<p>' + copy('selection') + '</p><p>' + copy('upgrade') + '</p>' : '') +
    (!local ? '<p>' + copy('remote') + '</p>' : '') +
    '<div class="scan-warning-links">' +
    (local ? '<a href="#/settings/occupancy">' + copy('settings') + '</a>' : '') +
    '<a href="https://github.com/StepaniaH/port-light/blob/main/docs/troubleshooting.md#occupancy-scan-warning"' +
    ' target="_blank" rel="noopener noreferrer">' + copy('guide') + '</a></div></div></details>';
}

export function scanWarningState(root) {
  return Array.from(root.querySelectorAll('[data-scan-warning]')).filter(node => node.open).map(node => ({
    key: node.getAttribute('data-scan-warning'),
    focused: node.contains(document.activeElement),
  }));
}

export function wireScanWarnings(root, previous = []) {
  root.querySelectorAll('[data-scan-warning]').forEach(warning => {
    const trigger = warning.querySelector('summary');
    const info = warning.querySelector('.scan-warning-info');
    let automatic = false;
    let dismissing = false;
    const open = () => {
      if (dismissing) return;
      if (!warning.open) automatic = true;
      warning.open = true;
    };
    info.addEventListener('pointerenter', event => {
      if (event.pointerType === 'mouse') open();
    });
    warning.addEventListener('pointerleave', () => {
      if (!warning.contains(document.activeElement)) warning.open = false;
    });
    trigger.addEventListener('focus', open);
    trigger.addEventListener('click', event => {
      // Focus/hover already opened it: the first click keeps the disclosure open.
      if (automatic && warning.open) event.preventDefault();
      automatic = false;
    });
    warning.addEventListener('focusout', event => {
      if (!warning.contains(event.relatedTarget)) warning.open = false;
    });
    warning.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      dismissing = true;
      warning.open = false;
      trigger.focus();
      dismissing = false;
      automatic = false;
    });
    const saved = previous.find(item => item.key === warning.getAttribute('data-scan-warning'));
    if (saved) {
      warning.open = true;
      if (saved.focused) trigger.focus({ preventScroll: true });
    }
  });
}
