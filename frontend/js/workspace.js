/* Optional local modules use the same shell, appearance and navigation as core views. */
import { S } from './state.js?v=121';
import { t } from './text.js?v=121';
import { enhanceSelects } from './select.js?v=121';
import { workspaceAssets } from './extension-assets.js?v=121';

export { workspaceAssets } from './extension-assets.js?v=121';

export function mountWorkspacePage(container) {
  let controller;
  let cleanup;
  let stylesheet;

  function close() {
    controller?.abort();
    controller = null;
    cleanup?.();
    cleanup = null;
    stylesheet?.remove();
    stylesheet = null;
    container?.replaceChildren();
  }

  async function open(route) {
    close();
    const current = controller = new AbortController();
    const { signal } = current;
    const link = (S.meta.ui_links || []).find(item => item.key === route.key);
    const assets = workspaceAssets(link);
    const intro = document.createElement('div');
    intro.className = 'page-intro workspace-intro';
    const title = document.createElement('h1');
    const locale = window.PortLightI18n?.locale() || 'en';
    title.textContent = link?.labels?.[locale] || link?.label || t('workspace.title');
    intro.append(title);
    if (assets && route.key === 'port-analysis') {
      const settingsLink = document.createElement('a');
      settingsLink.className = 'btn-secondary workspace-settings-link';
      settingsLink.href = '#/settings/analysis/ai';
      settingsLink.textContent = t('workspace.aiSettings');
      intro.append(settingsLink);
    }
    const status = document.createElement('p');
    status.className = 'settings-lead';
    status.setAttribute('role', 'status');
    status.textContent = t(assets ? 'workspace.loading' : 'workspace.unavailable');
    const root = document.createElement('div');
    container.replaceChildren(intro, status, root);
    container.focus();
    if (!assets) return;
    const style = document.createElement('link');
    stylesheet = style;
    style.rel = 'stylesheet';
    style.href = assets.stylesheet;
    let timeout;
    const stopped = new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      timeout = setTimeout(() => current.abort(new Error('Workspace load timed out')), 15000);
    });
    async function load() {
      const ready = new Promise((resolve, reject) => {
        style.onload = resolve;
        style.onerror = reject;
      });
      document.head.append(style);
      const [module] = await Promise.all([import(assets.entry), ready]);
      if (signal.aborted) return;
      if (typeof module.mount !== 'function') throw new Error('Invalid workspace entry');
      const dispose = await module.mount({ root, locale, port: route.port, signal, enhanceSelects, revision: assets.revision });
      if (signal.aborted) { dispose?.(); return; }
      return typeof dispose === 'function' ? dispose : null;
    }
    try {
      const dispose = await Promise.race([load(), stopped]);
      if (signal.aborted) return;
      cleanup = dispose;
      status.remove();
      enhanceSelects(root);
    } catch (_) {
      if (controller !== current) return;
      current.abort();
      style.remove();
      stylesheet = null;
      root.replaceChildren();
      status.setAttribute('role', 'alert');
      status.textContent = t('workspace.failed');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'btn-secondary';
      retry.textContent = t('workspace.retry');
      // Reload also clears a failed dynamic import from the browser's module map.
      retry.addEventListener('click', () => window.location.reload());
      root.append(retry);
    } finally {
      clearTimeout(timeout);
      style.onload = style.onerror = null;
    }
  }
  return { open, close };
}
