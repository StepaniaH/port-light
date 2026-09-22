/* Optional local modules use the same shell, appearance and navigation as core views. */
import { S } from './state.js?v=103';
import { t } from './text.js?v=103';
import { enhanceSelects } from './select.js?v=103';
import { workspaceAssets } from './extension-assets.js?v=103';

export { workspaceAssets } from './extension-assets.js?v=103';

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
    const signal = (controller = new AbortController()).signal;
    const link = (S.meta.ui_links || []).find(item => item.key === route.key);
    const assets = workspaceAssets(link);
    const intro = document.createElement('div');
    intro.className = 'page-intro';
    const title = document.createElement('h1');
    const locale = window.PortLightI18n?.locale() || 'en';
    title.textContent = link?.labels?.[locale] || link?.label || t('workspace.title');
    intro.append(title);
    const status = document.createElement('p');
    status.className = 'settings-lead';
    status.setAttribute('role', 'status');
    status.textContent = t(assets ? 'workspace.loading' : 'workspace.unavailable');
    const root = document.createElement('div');
    container.replaceChildren(intro, status, root);
    container.focus();
    if (!assets) return;
    try {
      const style = document.createElement('link');
      stylesheet = style;
      style.rel = 'stylesheet';
      style.href = assets.stylesheet;
      const ready = new Promise((resolve, reject) => {
        style.onload = resolve;
        style.onerror = reject;
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
      });
      document.head.append(style);
      const [module] = await Promise.all([import(assets.entry), ready]);
      if (signal.aborted) return;
      if (typeof module.mount !== 'function') throw new Error('Invalid workspace entry');
      const dispose = await module.mount({ root, locale, port: route.port, signal, enhanceSelects, revision: assets.revision });
      if (signal.aborted) { dispose?.(); return; }
      cleanup = typeof dispose === 'function' ? dispose : null;
      status.remove();
      enhanceSelects(root);
    } catch (_) {
      if (signal.aborted) return;
      root.replaceChildren();
      status.textContent = t('workspace.failed');
    }
  }
  return { open, close };
}
