/* Local AI connection settings; model calls require separate confirmation. */
export async function mountSettings({ root, locale, section, signal, enhanceSelects, revision }) {
  const asset = name => '/analysis/assets/' + name + (revision ? '?v=' + revision : '');
  const isAlive = () => !signal?.aborted;
  const state = { document: null, busy: false, status: null, disposed: false, draft: null };

  function element(name, attributes = {}, children = []) {
    const node = document.createElement(name);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'className') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'tabIndex') node.tabIndex = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of Array.isArray(children) ? children : [children]) {
      if (child !== undefined && child !== null) node.append(child);
    }
    return node;
  }

  function safeSection() {
    return 'ai';
  }

  function clearSecret() {
    for (const input of root.querySelectorAll('input[type="password"]')) input.value = '';
  }

  if (signal) signal.addEventListener('abort', () => {
    state.disposed = true;
    state.draft = null;
    clearSecret();
  }, { once: true });

  const [catalogs, display] = await Promise.all([
    fetch(asset('messages.json'), { credentials: 'same-origin', signal }).then(response => {
      if (!response.ok) throw new Error('Settings messages unavailable');
      return response.json();
    }),
    import(asset('display.js')),
  ]);
  if (!isAlive()) return () => {};
  const t = display.translator(catalogs, locale, 'settings');

  function failure(code, status) {
    const item = new Error('AI settings request failed');
    item.code = code;
    item.status = status;
    return item;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin', cache: 'no-store', signal, ...options,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok || !body) throw failure(body?.error?.code, response.status);
    return body;
  }

  function isProfile(value) {
    return value && typeof value === 'object' &&
      typeof value.configured === 'boolean' && typeof value.key_saved === 'boolean' &&
      (value.provider === null || typeof value.provider === 'string') &&
      (value.model === null || typeof value.model === 'string') &&
      (value.revision === null || typeof value.revision === 'string');
  }

  function isSettingsDocument(value) {
    return value && typeof value === 'object' && value.schema_version === 1 &&
      (value.analysis_mode === 'demo' || value.analysis_mode === 'byok') &&
      typeof value.readonly === 'boolean' && typeof value.enabled === 'boolean' &&
      value.capabilities && typeof value.capabilities === 'object' &&
      typeof value.capabilities.byok_port_analysis === 'boolean' &&
      Array.isArray(value.providers) && value.providers.every(provider => provider &&
        typeof provider.id === 'string' && typeof provider.name === 'string' &&
        (provider.notice === undefined || typeof provider.notice === 'string')) &&
      isProfile(value.ai);
  }

  function requestError(item) {
    if (item?.code === 'settings_readonly') return t('error_readonly');
    if (item?.code === 'configuration_changed') return t('error_changed');
    if (['key_required', 'invalid_key', 'provider_auth'].includes(item?.code)) return t('error_key');
    if (item?.code === 'unsupported_provider') return t('error_provider');
    if (item?.code === 'invalid_input') return t('error_input');
    if ([401, 403].includes(item?.status)) return t('error_access');
    return t('error_request');
  }

  function statusNode() {
    if (!state.status) return null;
    return element('p', {
      className: 'analysis-settings-status is-' + state.status.kind,
      role: state.status.kind === 'error' ? 'alert' : 'status',
      'aria-live': 'polite', text: state.status.message,
    });
  }

  function navigation(view) {
    const nav = element('nav', {
      className: 'analysis-settings-nav', 'aria-label': t('settings_navigation'),
    });
    const links = [['ai', t('nav_ai')]];
    for (const [name, label] of links) {
      nav.append(element('a', {
        href: '#/settings/analysis/' + name,
        'aria-current': view === name ? 'page' : undefined,
        text: label,
      }));
    }
    return nav;
  }

  function heading(view, key) {
    return element('h2', {
      id: 'analysis-settings-' + view + '-title',
      className: 'analysis-settings-heading', tabIndex: -1, text: t(key),
    });
  }

  function summaryRow(label, value) {
    const term = element('dt', { text: label });
    const detail = element('dd', { text: value });
    return [term, detail];
  }

  function summaryList(rows) {
    const list = element('dl', { className: 'analysis-settings-summary' });
    for (const [label, value] of rows) list.append(...summaryRow(label, value));
    return list;
  }

  function providerFor(identifier) {
    return state.document?.providers.find(provider => provider.id === identifier) || null;
  }

  function connectionText(profile) {
    if (!profile.configured) return t('connection_missing');
    const provider = providerFor(profile.provider);
    return t('connection_saved', {
      provider: provider?.name || profile.provider || t('unknown_provider'),
      model: profile.model || '—',
    });
  }

  function selectedProvider() {
    const profile = state.document.ai;
    if (state.draft && state.document.providers.some(provider => provider.id === state.draft.provider)) {
      return state.draft.provider;
    }
    if (state.document.providers.some(provider => provider.id === profile.provider)) return profile.provider;
    return state.document.providers[0]?.id || '';
  }

  function selectedProviderNotice() {
    const select = root.querySelector('#analysis-ai-provider');
    return providerFor(select?.value || '');
  }

  function updateProviderHints() {
    const profile = state.document?.ai;
    const selected = root.querySelector('#analysis-ai-provider')?.value || '';
    const notice = selectedProviderNotice();
    const noticeNode = root.querySelector('#analysis-ai-provider-notice');
    const keyHint = root.querySelector('#analysis-ai-key-help');
    const key = root.querySelector('#analysis-ai-key');
    if (noticeNode) {
      noticeNode.hidden = !notice?.notice;
      noticeNode.textContent = notice?.notice || '';
    }
    const keepsSavedKey = profile?.configured && profile?.key_saved && selected === profile.provider;
    if (keyHint) keyHint.textContent = keepsSavedKey ? t('key_keep') : t('key_required');
    if (key) {
      key.required = !keepsSavedKey;
      key.setAttribute('aria-describedby', 'analysis-ai-key-help analysis-ai-key-storage');
    }
    enhanceSelects?.(root);
  }

  function editableForm() {
    const document = state.document;
    const profile = document.ai;
    const form = element('form', { className: 'analysis-settings-form', id: 'analysis-ai-form', novalidate: true });
    const providerLabel = element('label', { for: 'analysis-ai-provider', text: t('provider') });
    const provider = element('select', { id: 'analysis-ai-provider', name: 'provider', required: true, disabled: state.busy });
    for (const item of document.providers) {
      provider.append(element('option', {
        value: item.id, selected: item.id === selectedProvider(), text: item.name,
      }));
    }
    const providerNotice = element('p', { id: 'analysis-ai-provider-notice', className: 'analysis-settings-hint', hidden: true });
    const modelLabel = element('label', { for: 'analysis-ai-model', text: t('model') });
    const model = element('input', {
      id: 'analysis-ai-model', name: 'model', type: 'text', maxlength: 120,
      pattern: '[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}', autocomplete: 'off', spellcheck: 'false',
      required: true, disabled: state.busy,
      value: state.draft?.model ?? (profile.configured ? profile.model || '' : ''),
    });
    const keyLabel = element('label', { for: 'analysis-ai-key', text: t('key') });
    const key = element('input', {
      id: 'analysis-ai-key', name: 'key', type: 'password', maxlength: 2048,
      autocomplete: 'off', spellcheck: 'false', disabled: state.busy,
    });
    const keyHelp = element('p', { id: 'analysis-ai-key-help', className: 'analysis-settings-hint' });
    const storage = element('p', { id: 'analysis-ai-key-storage', className: 'analysis-settings-hint', text: t('key_storage') });
    const actions = element('div', { className: 'analysis-settings-actions' });
    actions.append(element('button', {
      className: 'btn-primary', type: 'submit', disabled: state.busy, text: state.busy ? t('saving') : t('save'),
    }));
    form.append(providerLabel, provider, providerNotice, modelLabel, model, keyLabel, key, keyHelp, storage, actions);
    form.addEventListener('submit', saveConnection, { signal });
    provider.addEventListener('input', updateProviderHints, { signal });
    provider.addEventListener('change', updateProviderHints, { signal });
    return form;
  }

  function clearButton() {
    const document = state.document;
    if (!document.ai.configured || document.readonly || document.analysis_mode === 'demo') return null;
    const actions = element('div', { className: 'analysis-settings-actions analysis-settings-clear' });
    const button = element('button', {
      className: 'btn-secondary', type: 'button', disabled: state.busy,
      text: state.busy ? t('clearing') : t('clear'),
    });
    button.addEventListener('click', clearConnection, { signal });
    actions.append(button);
    return actions;
  }

  function renderUnavailable(view) {
    const panel = element('section', { id: 'analysis-settings-' + view, className: 'analysis-settings' });
    panel.append(heading(view, 'ai_title'), navigation(view),
      element('p', { className: 'analysis-settings-status is-error', role: 'alert', text: t('settings_unavailable') }));
    root.replaceChildren(panel);
  }

  function renderAI() {
    const document = state.document;
    const panel = element('section', { id: 'analysis-settings-ai', className: 'analysis-settings' });
    panel.append(heading('ai', 'ai_title'), navigation('ai'), element('p', { className: 'analysis-settings-lead', text: t('ai_lead') }));
    const status = statusNode();
    if (status) panel.append(status);
    if (document.analysis_mode === 'demo') {
      panel.append(element('p', { className: 'analysis-settings-notice', text: t('ai_demo') }));
      root.replaceChildren(panel);
      return;
    }
    if (document.readonly) panel.append(element('p', { className: 'analysis-settings-notice', text: t('ai_readonly') }));
    const rows = [
      [t('connection'), connectionText(document.ai)],
      [t('key_saved'), document.ai.key_saved ? t('yes') : t('no')],
    ];
    panel.append(summaryList(rows));
    if (document.ai.configured) panel.append(element('p', { className: 'analysis-settings-hint', text: t('save_note') }));
    const form = editableForm();
    if (form) panel.append(form);
    else if (!document.readonly && document.enabled && !document.providers.length) panel.append(element('p', { className: 'analysis-settings-notice', text: t('provider_unavailable') }));
    const clear = clearButton();
    if (clear) panel.append(clear);
    root.replaceChildren(panel);
    if (form) updateProviderHints();
  }

  function render({ focus = false } = {}) {
    if (!isAlive() || state.disposed) return;
    const view = safeSection(section);
    if (!state.document) renderUnavailable(view);
    else renderAI();
    enhanceSelects?.(root);
    if (focus && view !== 'overview') root.querySelector('.analysis-settings-heading')?.focus({ preventScroll: true });
  }

  async function refresh({ focus = false } = {}) {
    state.busy = false;
    try {
      const document = await request('/analysis/api/settings');
      if (!isAlive() || state.disposed) return false;
      if (!isSettingsDocument(document)) throw failure('settings_unavailable', 503);
      state.document = document;
      render({ focus });
      return true;
    } catch (item) {
      if (!isAlive() || state.disposed) return false;
      state.document = null;
      state.status = { kind: 'error', message: t('settings_unavailable') };
      render({ focus });
      return false;
    }
  }

  async function saveConnection(event) {
    event.preventDefault();
    if (!state.document || state.busy) return;
    const form = event.currentTarget;
    const provider = form.elements.provider.value;
    const model = form.elements.model.value.trim();
    const input = form.elements.key;
    let key = input.value;
    const retainsSavedKey = state.document.ai.configured && state.document.ai.key_saved && provider === state.document.ai.provider;
    if (!form.reportValidity()) return;
    if (!retainsSavedKey && !key) {
      state.status = { kind: 'error', message: t('error_key') };
      render();
      return;
    }
    // Re-rendering while a request is in flight disables the editable fields.
    // Keep only non-secret input so a failed request does not make the user
    // retype provider and model; the password is cleared before rendering.
    state.draft = { provider, model };
    const headers = { 'Content-Type': 'application/json', 'X-Port-Light-Analysis': '1' };
    if (key) headers['X-Port-Light-Model-Key'] = key;
    input.value = '';
    state.busy = true;
    state.status = null;
    render();
    try {
      const document = await request('/analysis/api/settings/ai', {
        method: 'PUT', headers, body: JSON.stringify({ provider, model }),
      });
      if (!isAlive() || state.disposed) return;
      if (!isSettingsDocument(document)) throw failure('settings_unavailable', 503);
      state.document = document;
      state.draft = null;
      state.status = { kind: 'ok', message: t('saved') };
    } catch (item) {
      if (!isAlive() || state.disposed) return;
      state.status = { kind: 'error', message: requestError(item) };
    } finally {
      key = '';
      delete headers['X-Port-Light-Model-Key'];
      if (isAlive() && !state.disposed) {
        state.busy = false;
        render();
      }
    }
  }

  async function clearConnection() {
    if (!state.document || state.busy || state.document.readonly || !state.document.ai.configured) return;
    state.draft = null;
    state.busy = true;
    state.status = null;
    render();
    try {
      const document = await request('/analysis/api/settings/ai', {
        method: 'DELETE', headers: { 'X-Port-Light-Analysis': '1' },
      });
      if (!isAlive() || state.disposed) return;
      if (!isSettingsDocument(document)) throw failure('settings_unavailable', 503);
      state.document = document;
      state.status = { kind: 'ok', message: t('cleared') };
    } catch (item) {
      if (!isAlive() || state.disposed) return;
      state.status = { kind: 'error', message: requestError(item) };
    } finally {
      if (isAlive() && !state.disposed) {
        state.busy = false;
        render();
      }
    }
  }

  await refresh({ focus: safeSection(section) !== 'overview' });
  return () => {
    state.disposed = true;
    state.draft = null;
    clearSecret();
  };
}
