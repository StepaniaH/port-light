/* The local AI connection panel owns its requests and clears keys on exit. */
import { S } from './state.js?v=121';
import { t } from './text.js?v=121';
import { enhanceSelects } from './select.js?v=121';
import { settingsAssets } from './extension-assets.js?v=121';

let active;
export function closeAnalysisSettings() {
  const session = active;
  active = null;
  if (!session) return;
  session.controller.abort();
  session.cleanup?.();
  session.style.remove();
  session.root.replaceChildren();
}
export function analysisCardsHtml() {
  return '<div data-analysis-settings-root></div>';
}
export async function syncAnalysisSettings() {
  if (S.settingsPanel !== 'analysis') return;
  const root = document.querySelector('[data-analysis-settings-root]');
  const link = (S.meta.ui_links || []).find(item => item.key === 'port-analysis');
  const assets = settingsAssets(link);
  if (!root) return;
  const locale = window.PortLightI18n?.locale() || 'en';
  if (active?.root === root && active.locale === locale) return;
  closeAnalysisSettings();
  if (!assets) { root.textContent = t('workspace.unavailable'); return; }
  const controller = new AbortController();
  const style = document.createElement('link');
  style.rel = 'stylesheet';
  style.href = assets.stylesheet;
  const session = { root, controller, style, locale };
  active = session;
  try {
    const ready = new Promise((resolve, reject) => {
      style.onload = resolve;
      style.onerror = reject;
      controller.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
    document.head.append(style);
    const [module] = await Promise.all([import(assets.entry), ready]);
    if (controller.signal.aborted) return;
    const cleanup = await module.mountSettings({ root, locale, section: 'ai', signal: controller.signal, enhanceSelects, revision: assets.revision });
    if (controller.signal.aborted) { cleanup?.(); return; }
    session.cleanup = cleanup;
  } catch (_) {
    if (!controller.signal.aborted) root.textContent = t('workspace.failed');
  }
}
export function rerenderAnalysis() {
  closeAnalysisSettings();
  return syncAnalysisSettings();
}
