/* Local AI connection settings; model calls require separate confirmation. */
export async function mountSettings({ root, locale, signal, enhanceSelects, revision }) {
  const asset = name => '/analysis/assets/' + name + (revision ? '?v=' + revision : '');
  const isAlive = () => !signal?.aborted;
  const state = {
    document: null, busy: null, status: null, disposed: false, draft: null,
    editingId: null, editorProfile: null, deletingId: null,
  };

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

  function clearSecret() {
    for (const input of root.querySelectorAll('input[data-ai-secret]')) input.value = '';
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
      (value.base_url === undefined || value.base_url === null || typeof value.base_url === 'string') &&
      (value.token_parameter === undefined || ['max_tokens', 'max_completion_tokens'].includes(value.token_parameter)) &&
      (value.json_mode === undefined || typeof value.json_mode === 'boolean') &&
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
      isProfile(value.ai) && Array.isArray(value.connections) && value.connections.every(profile =>
        isProfile(profile) && profile.configured && /^[A-Za-z0-9_-]{32,64}$/.test(profile.id)) &&
      new Set(value.connections.map(profile => profile.id)).size === value.connections.length &&
      Number.isInteger(value.connections_limit) && value.connections_limit > 0 &&
      (value.active_connection_id === null ? !value.ai.configured : value.connections.some(profile =>
        profile.id === value.active_connection_id && profile.revision === value.ai.revision &&
        profile.provider === value.ai.provider && profile.model === value.ai.model));
  }

  function requestError(item) {
    if (item?.code === 'settings_readonly') return t('error_readonly');
    if (item?.code === 'configuration_changed') return t('error_changed');
    if (['key_required', 'invalid_key', 'provider_auth'].includes(item?.code)) return t('error_key');
    if (item?.code === 'unsupported_provider') return t('error_provider');
    if (item?.code === 'invalid_input') return t('error_input');
    if (item?.code === 'invalid_model') return t('error_model_key');
    if (item?.code === 'connection_limit') return t('error_connections_limit', { count: state.document.connections_limit });
    if (item?.code === 'invalid_base_url') return t('error_base_url');
    if ([401, 403].includes(item?.status)) return t('error_access');
    return t('error_request');
  }

  function testError(item) {
    if (item?.code === 'provider_auth') return t('test_error_auth');
    if (item?.code === 'provider_limit') return t('test_error_limit');
    if (item?.code === 'provider_error') return t('test_error_provider');
    if (item?.code === 'provider_timeout') return t('test_error_timeout');
    if (item?.code === 'provider_connection') return t('test_error_connection');
    if (item?.code === 'invalid_output') return t('test_error_output');
    if (item?.code === 'busy') return t('test_error_busy');
    return requestError(item);
  }

  function statusNode() {
    if (!state.status) return null;
    return element('p', {
      className: 'analysis-settings-status is-' + state.status.kind,
      role: state.status.kind === 'error' ? 'alert' : 'status',
      'aria-live': 'polite', text: state.status.message,
    });
  }

  function providerFor(identifier) {
    return state.document?.providers.find(provider => provider.id === identifier) || null;
  }

  function editorProfile() {
    return state.editorProfile || { configured: false, key_saved: false, provider: null, model: null };
  }

  function connectionLabel(profile) {
    const provider = profile.provider === 'custom' ? t('custom_provider') : providerFor(profile.provider)?.name || profile.provider;
    return provider + ' / ' + (profile.model || '—');
  }

  function connectionsPanel() {
    const data = state.document;
    const panel = element('section', { id: 'analysis-ai-connections', className: 'analysis-connections',
      'aria-labelledby': 'analysis-ai-connections-title' });
    const heading = element('div', { className: 'analysis-connections-head' }, [
      element('h3', { id: 'analysis-ai-connections-title', text: t('saved_connections') }),
    ]);
    const writable = data.analysis_mode === 'byok' && !data.readonly && data.enabled;
    if (writable) {
      const add = element('button', { id: 'analysis-ai-add', type: 'button', className: 'btn-secondary',
        disabled: state.busy || !data.providers.length || data.connections.length >= data.connections_limit,
        text: t('add_connection') });
      add.addEventListener('click', () => editConnection(null), { signal });
      heading.append(add);
    }
    panel.append(heading, element('p', { className: 'analysis-settings-current',
      text: data.ai.configured ? t('active_connection', { connection: connectionLabel(data.ai) }) : t('choose_connection') }));
    if (!data.connections.length) {
      panel.append(element('p', { className: 'analysis-settings-hint', text: t('connections_empty') }));
      return panel;
    }
    const list = element('ul', { className: 'analysis-connections-list' });
    for (const profile of data.connections) {
      const active = profile.id === data.active_connection_id;
      const row = element('li', { className: 'analysis-connection' + (active ? ' is-active' : ''),
        'data-connection-id': profile.id });
      const radioId = 'analysis-ai-connection-' + profile.id;
      const radio = element('input', { id: radioId, type: 'radio', name: 'port-light-ai-active-connection',
        value: profile.id, checked: active, disabled: state.busy || !writable || !usableProfile(profile),
        'aria-label': t('use_connection', { connection: connectionLabel(profile) }) });
      radio.addEventListener('change', () => { if (radio.checked) activateConnection(profile); }, { signal });
      const details = element('span', { className: 'analysis-connection-details' }, [
        element('strong', { text: connectionLabel(profile) }),
      ]);
      const address = profile.base_url || providerFor(profile.provider)?.base_url;
      if (address) details.append(element('span', { className: 'analysis-connection-address', text: address }));
      details.append(element('span', { className: 'analysis-connection-key-state', text: t('key_is_saved') }));
      row.append(element('label', { className: 'analysis-connection-select', for: radioId }, [radio, details]));
      if (active) row.append(element('span', { className: 'analysis-connection-active', text: t('currently_used') }));
      const actions = element('div', { className: 'analysis-connection-actions' });
      if (state.deletingId === profile.id) {
        actions.append(element('span', { className: 'analysis-connection-delete-note', text: t('delete_prompt') }));
        const confirm = element('button', { type: 'button', className: 'btn-secondary is-danger',
          'data-connection-action': 'confirm-delete', disabled: state.busy, text: t('confirm_delete') });
        confirm.addEventListener('click', () => deleteConnection(profile), { signal });
        const cancel = element('button', { type: 'button', className: 'btn-secondary',
          'data-connection-action': 'cancel-delete', disabled: state.busy, text: t('cancel_delete') });
        cancel.addEventListener('click', () => { state.deletingId = null; renderConnections(); }, { signal });
        actions.append(confirm, cancel);
      } else {
        if (writable) {
          const edit = element('button', { type: 'button', className: 'btn-secondary',
            'data-connection-action': 'edit', 'aria-pressed': String(profile.id === state.editingId),
            disabled: state.busy, text: t('edit_connection') });
          edit.addEventListener('click', () => editConnection(profile.id), { signal });
          actions.append(edit);
        }
        if (data.analysis_mode === 'byok' && data.enabled) {
          const test = element('button', { type: 'button', className: 'btn-secondary',
            'data-connection-action': 'test', disabled: state.busy || !usableProfile(profile), text: t('test') });
          test.addEventListener('click', () => testConnection(profile), { signal });
          actions.append(test);
        }
        if (writable) {
          const remove = element('button', { type: 'button', className: 'btn-secondary',
            'data-connection-action': 'delete', disabled: state.busy, text: t('delete_connection') });
          remove.addEventListener('click', () => { state.deletingId = profile.id; renderConnections(); }, { signal });
          actions.append(remove);
        }
      }
      row.append(actions);
      list.append(row);
    }
    panel.append(list);
    return panel;
  }

  function renderConnections() {
    root.querySelector('#analysis-ai-connections')?.replaceWith(connectionsPanel());
  }

  function editorPanel() {
    const panel = element('section', { id: 'analysis-ai-editor', className: 'analysis-settings-editor',
      'aria-labelledby': 'analysis-ai-editor-title' });
    panel.append(element('h3', { id: 'analysis-ai-editor-title', text: t(state.editingId ? 'edit_connection' : 'add_connection') }));
    panel.append(element('p', { id: 'analysis-ai-editor-target', className: 'analysis-settings-hint',
      text: state.editingId ? t('editing_connection', { connection: connectionLabel(editorProfile()) }) : t('new_connection_hint') }));
    panel.append(editableForm());
    return panel;
  }

  function updateEditorHeading() {
    const title = root.querySelector('#analysis-ai-editor-title');
    const target = root.querySelector('#analysis-ai-editor-target');
    if (title) title.textContent = t(state.editingId ? 'edit_connection' : 'add_connection');
    if (target) target.textContent = state.editingId
      ? t('editing_connection', { connection: connectionLabel(editorProfile()) }) : t('new_connection_hint');
  }

  function editConnection(identifier) {
    if (state.busy || state.document.readonly) return;
    clearSecret();
    state.editingId = identifier;
    state.editorProfile = state.document.connections.find(profile => profile.id === identifier) || null;
    state.draft = null;
    state.status = null;
    state.deletingId = null;
    root.querySelector('#analysis-ai-editor')?.replaceWith(editorPanel());
    enhanceSelects?.(root);
    renderConnections();
    updateProviderHints();
    showTestStatus();
    root.querySelector('#analysis-ai-editor-title')?.scrollIntoView({ block: 'nearest' });
  }

  function usableProfile(profile) {
    return profile.configured && Boolean(profile.model) && providerFor(profile.provider) &&
      (profile.provider !== 'custom' || Boolean(profile.base_url));
  }

  function badgeText() {
    const data = state.document;
    return t(data.analysis_mode === 'demo' ? 'status_demo'
      : data.ai.configured && !usableProfile(data.ai) ? 'status_provider_missing'
        : data.ai.configured ? 'status_configured' : 'status_unconfigured');
  }

  function selectedProvider() {
    const profile = editorProfile();
    if (state.draft && state.document.providers.some(provider => provider.id === state.draft.provider)) {
      return state.draft.provider;
    }
    if (state.document.providers.some(provider => provider.id === profile.provider)) return profile.provider;
    return state.document.providers[0]?.id || '';
  }

  function normalizedBaseURL(value) {
    try {
      const text = value.trim().replace(/\/+$/, '');
      if (/[\s\\?#]/.test(text)) return null;
      const url = new URL(text);
      if (!['https:', 'http:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
      url.pathname = url.pathname.replace(/\/chat\/completions$/, '');
      return url.href.replace(/\/+$/, '');
    } catch (_) { return null; }
  }

  function retainsSavedKey(provider, baseURL) {
    const profile = editorProfile();
    return profile.configured && profile.key_saved && provider === profile.provider &&
      (provider !== 'custom' || Boolean(baseURL) && baseURL === profile.base_url);
  }

  function protectModelInput(model, key) {
    if (!model) return true;
    const secret = key?.value || '';
    if (secret && (model.value === secret || secret.length >= 16 && model.value.includes(secret))) {
      model.value = '';
      model.dataset.keyMixed = 'true';
      model.setCustomValidity(t('error_model_key'));
      root.querySelector('#analysis-ai-model-help').textContent = t('error_model_key');
      return false;
    }
    if (model.value && model.dataset.keyMixed) {
      delete model.dataset.keyMixed;
      model.setCustomValidity('');
      root.querySelector('#analysis-ai-model-help').textContent = t('model_help');
    }
    return true;
  }

  function updateProviderHints() {
    if (!state.document) return;
    const profile = editorProfile();
    const selected = root.querySelector('#analysis-ai-provider')?.value || '';
    const keyHint = root.querySelector('#analysis-ai-key-help');
    const key = root.querySelector('#analysis-ai-key');
    const model = root.querySelector('#analysis-ai-model');
    const save = root.querySelector('#analysis-ai-save');
    const test = root.querySelector('#analysis-ai-test');
    const base = root.querySelector('#analysis-ai-base-url');
    const custom = selected === 'custom';
    protectModelInput(model, key);
    for (const node of root.querySelectorAll('[data-ai-custom]')) node.hidden = !custom;
    if (base) base.required = custom;
    const baseURL = custom ? normalizedBaseURL(base?.value || '') : null;
    const customChanged = custom && (
      baseURL !== profile?.base_url ||
      root.querySelector('#analysis-ai-token-parameter')?.value !== profile?.token_parameter ||
      root.querySelector('#analysis-ai-json-mode')?.checked !== profile?.json_mode
    );
    const keepsSavedKey = profile && retainsSavedKey(selected, baseURL);
    if (keyHint) keyHint.textContent = keepsSavedKey ? t('key_keep') : t('key_required');
    if (key) {
      key.required = !keepsSavedKey;
      key.setAttribute('aria-describedby', 'analysis-ai-key-help analysis-ai-key-storage');
    }
    if (save && model && key) {
      save.disabled = state.busy || !selected || !model.validity.valid || !(
        !profile.configured || selected !== profile.provider ||
        model.value.trim() !== profile.model || customChanged || !!key.value
      );
    }
    if (test && model && key) {
      test.disabled = !!state.busy || !selected || !model.value.trim() || !model.validity.valid ||
        (custom && !baseURL) || !(key.value || keepsSavedKey);
    }
  }

  function editableForm() {
    const document = state.document;
    const profile = editorProfile();
    const form = element('div', {
      className: 'analysis-settings-form', id: 'analysis-ai-form', role: 'group',
      'aria-labelledby': 'analysis-ai-editor-title',
    });
    const providerLabel = element('label', { for: 'analysis-ai-provider', text: t('provider') });
    const provider = element('select', { id: 'analysis-ai-provider', name: 'provider', required: true, disabled: state.busy });
    for (const item of document.providers) {
      provider.append(element('option', {
        value: item.id, selected: item.id === selectedProvider(), text: item.id === 'custom' ? t('custom_provider') : item.name,
      }));
    }
    const modelLabel = element('label', { for: 'analysis-ai-model', text: t('model') });
    const model = element('input', {
      id: 'analysis-ai-model', name: 'port-light-model-id', type: 'text', maxlength: 120,
      pattern: '[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}', autocomplete: 'off', spellcheck: 'false',
      'data-1p-ignore': 'true', 'data-lpignore': 'true',
      required: true, disabled: state.busy, 'aria-describedby': 'analysis-ai-model-help',
      value: state.draft?.model ?? (profile.configured && providerFor(profile.provider) ? profile.model || '' : ''),
    });
    const modelHelp = element('p', { id: 'analysis-ai-model-help', className: 'analysis-settings-hint', text: t('model_help') });
    const base = element('input', {
      id: 'analysis-ai-base-url', name: 'base_url', type: 'url', maxlength: 2048,
      autocomplete: 'off', spellcheck: 'false', disabled: state.busy,
      placeholder: 'https://api.example.com/v1', 'aria-describedby': 'analysis-ai-base-url-help',
      value: state.draft?.base_url ?? profile.base_url ?? '',
    });
    const endpoint = element('div', { className: 'analysis-settings-field analysis-settings-wide-field', 'data-ai-custom': true }, [
      element('label', { for: 'analysis-ai-base-url', text: t('base_url') }), base,
      element('p', { id: 'analysis-ai-base-url-help', className: 'analysis-settings-hint', text: t('base_url_help') }),
    ]);
    const token = element('select', { id: 'analysis-ai-token-parameter', disabled: state.busy });
    for (const value of ['max_tokens', 'max_completion_tokens']) token.append(element('option', {
      value, text: value, selected: value === (state.draft?.token_parameter ?? profile.token_parameter ?? 'max_tokens'),
    }));
    const jsonMode = element('input', { id: 'analysis-ai-json-mode', type: 'checkbox', disabled: state.busy,
      checked: state.draft?.json_mode ?? profile.json_mode ?? false });
    const options = element('details', { className: 'analysis-settings-options analysis-settings-wide-field', 'data-ai-custom': true }, [
      element('summary', { text: t('custom_options') }),
      element('div', { className: 'analysis-settings-field' }, [
        element('label', { for: 'analysis-ai-token-parameter', text: t('token_parameter') }), token,
      ]),
      element('label', { className: 'analysis-settings-check', for: 'analysis-ai-json-mode' }, [jsonMode, t('json_mode')]),
      element('p', { className: 'analysis-settings-hint', text: t('custom_options_help') }),
    ]);
    const keyLabel = element('label', { for: 'analysis-ai-key', text: t('key') });
    const key = element('input', {
      id: 'analysis-ai-key', name: 'port-light-api-key', type: 'password', maxlength: 2048,
      autocomplete: 'section-portlight-ai new-password', spellcheck: 'false',
      'data-1p-ignore': 'true', 'data-lpignore': 'true', disabled: state.busy, 'data-ai-secret': true,
    });
    const reveal = element('button', {
      className: 'analysis-settings-reveal', type: 'button', disabled: state.busy,
      'aria-controls': 'analysis-ai-key', 'aria-pressed': 'false', text: t('key_show'),
    });
    reveal.addEventListener('click', () => {
      const visible = key.type === 'password';
      key.type = visible ? 'text' : 'password';
      reveal.setAttribute('aria-pressed', String(visible));
      reveal.textContent = t(visible ? 'key_hide' : 'key_show');
      key.focus();
    }, { signal });
    const keyHelp = element('p', { id: 'analysis-ai-key-help', className: 'analysis-settings-hint' });
    const storage = element('p', { id: 'analysis-ai-key-storage', className: 'analysis-settings-hint', text: t('key_storage') });
    const actions = element('div', { className: 'analysis-settings-actions' });
    const save = element('button', {
      className: 'btn-primary', id: 'analysis-ai-save', type: 'button', disabled: state.busy,
      text: t(state.busy === 'save' ? 'saving' : 'save'),
    });
    save.addEventListener('click', saveConnection, { signal });
    actions.append(save);
    actions.append(testButton());
    const cancel = element('button', { className: 'btn-secondary', type: 'button',
      id: 'analysis-ai-cancel', disabled: state.busy, text: t('cancel_edit') });
    cancel.addEventListener('click', () => editConnection(state.editingId), { signal });
    actions.append(cancel);
    form.append(
      element('div', { className: 'analysis-settings-field' }, [providerLabel, provider]),
      element('div', { className: 'analysis-settings-field' }, [modelLabel, model, modelHelp]),
      endpoint,
      element('div', { className: 'analysis-settings-field analysis-settings-key-field' }, [
        keyLabel, element('div', { className: 'analysis-settings-key' }, [key, reveal]), keyHelp, storage,
      ]), options, actions);
    form.append(element('p', { className: 'analysis-settings-hint', text: t('test_note') }));
    form.addEventListener('input', clearTestStatus, { signal });
    form.addEventListener('change', clearTestStatus, { signal });
    provider.addEventListener('change', () => {
      const current = editorProfile();
      model.value = provider.value === current.provider ? current.model || '' : '';
      base.value = provider.value === current.provider ? current.base_url || '' : '';
      token.value = provider.value === current.provider ? current.token_parameter || 'max_tokens' : 'max_tokens';
      jsonMode.checked = provider.value === current.provider && current.json_mode === true;
      delete model.dataset.keyMixed;
      model.setCustomValidity('');
      modelHelp.textContent = t('model_help');
      key.value = '';
      key.type = 'password';
      reveal.setAttribute('aria-pressed', 'false');
      reveal.textContent = t('key_show');
      updateProviderHints();
    }, { signal });
    model.addEventListener('input', updateProviderHints, { signal });
    base.addEventListener('input', () => { base.setCustomValidity(''); updateProviderHints(); }, { signal });
    token.addEventListener('change', updateProviderHints, { signal });
    jsonMode.addEventListener('change', updateProviderHints, { signal });
    key.addEventListener('input', () => { key.setCustomValidity(''); updateProviderHints(); }, { signal });
    form.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target instanceof HTMLInputElement) {
        event.preventDefault();
        saveConnection();
      }
    }, { signal });
    return form;
  }

  function testButton() {
    const button = element('button', {
      className: 'btn-secondary analysis-settings-test', id: 'analysis-ai-test',
      type: 'button', disabled: state.busy,
      text: t(state.busy === 'test' ? 'testing' : 'test'),
    });
    button.addEventListener('click', testConnection, { signal });
    return button;
  }

  function render() {
    if (!isAlive() || state.disposed) return;
    const data = state.document;
    const panel = element('section', { id: 'analysis-settings-ai', className: 'analysis-settings' });
    const card = element('div', { className: 'analysis-settings-card' });
    const header = element('div', { className: 'analysis-settings-card-head' });
    header.append(element('div', {}, [
      element('h2', { id: 'analysis-settings-ai-title', text: t('ai_title') }),
      element('p', { text: t('ai_lead') }),
    ]));
    if (data) header.append(element('span', {
      className: 'analysis-settings-badge',
      text: badgeText(),
    }));
    const body = element('div', { className: 'analysis-settings-card-body' });
    const status = statusNode();
    if (status) body.append(status);
    if (data?.analysis_mode === 'byok' && data.enabled && data.capabilities.byok_port_analysis) {
      body.append(connectionsPanel());
    }
    if (!data) {
      body.append(element('p', { className: 'analysis-settings-notice', text: t('settings_unavailable') }));
      const retry = element('button', { className: 'btn-secondary', type: 'button', text: t('retry') });
      retry.addEventListener('click', () => refresh(), { signal });
      body.append(retry);
    } else if (data.analysis_mode === 'demo') {
      body.append(element('p', { className: 'analysis-settings-notice', text: t('ai_demo') }));
    } else if (!data.enabled || !data.capabilities.byok_port_analysis) {
      body.append(element('p', { className: 'analysis-settings-notice', text: t('ai_disabled') }));
    } else if (data.readonly) {
      body.append(element('p', { className: 'analysis-settings-notice', text: t('ai_readonly') }));
      if (data.connections.length) body.append(element('p', { className: 'analysis-settings-hint', text: t('test_note') }));
    } else if (!data.providers.length) {
      body.append(element('p', { className: 'analysis-settings-notice', text: t('provider_unavailable') }));
    } else {
      if (data.ai.configured && !providerFor(data.ai.provider)) {
        body.append(element('p', { className: 'analysis-settings-notice', text: t('provider_saved_unavailable') }));
      }
      if (data.ai.configured && data.ai.provider === 'custom' && !data.ai.base_url) {
        body.append(element('p', { className: 'analysis-settings-notice', text: t('custom_saved_needs_address') }));
      }
      body.append(editorPanel());
    }
    if (data?.enabled) {
      const next = element('div', { className: 'analysis-settings-next' });
      next.append(element('a', { href: '#/workspace/port-analysis', text: t('open_workbench') }));
      body.append(next);
    }
    card.append(header, body);
    panel.append(card);
    root.replaceChildren(panel);
    enhanceSelects?.(root);
    updateProviderHints();
  }

  async function refresh() {
    try {
      const document = await request('/analysis/api/settings');
      if (!isAlive() || state.disposed) return false;
      if (!isSettingsDocument(document)) throw failure('settings_unavailable', 503);
      state.document = document;
      state.editingId = document.active_connection_id || document.connections[0]?.id || null;
      state.editorProfile = document.connections.find(profile => profile.id === state.editingId) || null;
      state.status = document.ai.configured && !document.ai.model
        ? { kind: 'error', message: t('error_model_key') } : null;
      render();
      return true;
    } catch (item) {
      if (!isAlive() || state.disposed) return false;
      state.document = null;
      state.status = null;
      render();
      return false;
    }
  }

  async function saveConnection() {
    if (!state.document || state.busy || state.document.readonly || !state.document.enabled) return;
    const providerInput = root.querySelector('#analysis-ai-provider');
    const modelInput = root.querySelector('#analysis-ai-model');
    const input = root.querySelector('#analysis-ai-key');
    const save = root.querySelector('#analysis-ai-save');
    if (!providerInput || !modelInput || !input || save?.disabled) return;
    if (!protectModelInput(modelInput, input)) {
      modelInput.reportValidity();
      return;
    }
    const provider = providerInput.value;
    modelInput.value = modelInput.value.trim();
    const model = modelInput.value;
    let key = input.value;
    const baseInput = root.querySelector('#analysis-ai-base-url');
    const connection = provider === 'custom' ? {
      base_url: normalizedBaseURL(baseInput?.value || ''),
      token_parameter: root.querySelector('#analysis-ai-token-parameter').value,
      json_mode: root.querySelector('#analysis-ai-json-mode').checked,
    } : {};
    if (provider === 'custom' && (!connection.base_url || !baseInput.reportValidity())) {
      baseInput.setCustomValidity(t('error_base_url'));
      baseInput.reportValidity();
      return;
    }
    const keepsSavedKey = retainsSavedKey(provider, connection.base_url);
    if (!providerInput.reportValidity() || !modelInput.reportValidity()) return;
    if (!keepsSavedKey && !key) {
      input.reportValidity();
      return;
    }
    if (key && !/^[\x21-\x7e]{1,2048}$/.test(key)) {
      input.setCustomValidity(t('error_key'));
      input.reportValidity();
      return;
    }
    // Keep the same inputs during save as well as test. Replacing a password
    // field can make a password manager refill its neighbouring model field.
    state.draft = { provider, model, ...connection };
    const headers = { 'Content-Type': 'application/json', 'X-Port-Light-Analysis': '1' };
    if (key) headers['X-Port-Light-Model-Key'] = key;
    const controls = [...root.querySelectorAll('input, select, button')];
    const disabled = controls.map(control => control.disabled);
    for (const control of controls) control.disabled = true;
    save.textContent = t('saving');
    state.busy = 'save';
    state.status = null;
    showTestStatus();
    try {
      const path = '/analysis/api/settings/ai/connections' + (state.editingId ? '/' + encodeURIComponent(state.editingId) : '');
      const document = await request(path, {
        method: state.editingId ? 'PUT' : 'POST', headers,
        body: JSON.stringify({ provider, model, ...connection,
          ...(state.editingId ? { config_revision: editorProfile().revision } : {}) }),
      });
      if (!isAlive() || state.disposed) return;
      if (!isSettingsDocument(document)) throw failure('settings_unavailable', 503);
      const saved = document.connections.find(profile => profile.id === document.saved_connection_id);
      if (!saved || saved.provider !== provider || saved.model !== model ||
          (state.editingId && saved.id !== state.editingId)) {
        throw failure('settings_unavailable', 503);
      }
      state.document = document;
      state.editingId = saved.id;
      state.editorProfile = saved;
      state.draft = null;
      input.value = '';
      input.type = 'password';
      const reveal = root.querySelector('.analysis-settings-reveal');
      reveal.setAttribute('aria-pressed', 'false');
      reveal.textContent = t('key_show');
      modelInput.value = saved.model;
      if (baseInput) baseInput.value = saved.base_url || '';
      root.querySelector('.analysis-settings-badge').textContent = badgeText();
      updateEditorHeading();
      state.status = { kind: 'ok', message: t('saved') };
    } catch (item) {
      if (!isAlive() || state.disposed) return;
      state.status = { kind: 'error', message: requestError(item) };
    } finally {
      key = '';
      delete headers['X-Port-Light-Model-Key'];
      if (isAlive() && !state.disposed) {
        state.busy = null;
        controls.forEach((control, index) => { control.disabled = disabled[index]; });
        save.textContent = t('save');
        renderConnections();
        updateProviderHints();
        showTestStatus();
      }
    }
  }

  async function testConnection(savedProfile = null) {
    const data = state.document;
    // Event listeners for the editor pass an event rather than a profile.
    if (!savedProfile?.id) savedProfile = null;
    const button = savedProfile
      ? root.querySelector('[data-connection-id="' + savedProfile.id + '"] [data-connection-action="test"]')
      : root.querySelector('#analysis-ai-test');
    if (!data || state.busy || data.analysis_mode !== 'byok' ||
        !button || button.disabled) return;
    const form = savedProfile ? null : root.querySelector('#analysis-ai-form');
    const headers = { 'Content-Type': 'application/json', 'X-Port-Light-Analysis': '1' };
    const body = { confirmed: true };
    let key = '';
    if (form) {
      const provider = root.querySelector('#analysis-ai-provider');
      const model = root.querySelector('#analysis-ai-model');
      const input = root.querySelector('#analysis-ai-key');
      const base = root.querySelector('#analysis-ai-base-url');
      if (!protectModelInput(model, input)) {
        model.reportValidity();
        return;
      }
      model.value = model.value.trim();
      if (!provider.reportValidity() || !model.reportValidity() || !input.reportValidity() ||
          (provider.value === 'custom' && !base.reportValidity())) return;
      key = input.value;
      if (key && !/^[\x21-\x7e]{1,2048}$/.test(key)) {
        input.setCustomValidity(t('error_key'));
        input.reportValidity();
        return;
      }
      body.draft = { provider: provider.value, model: model.value.trim() };
      if (provider.value === 'custom') Object.assign(body.draft, {
        base_url: normalizedBaseURL(base.value),
        token_parameter: root.querySelector('#analysis-ai-token-parameter').value,
        json_mode: root.querySelector('#analysis-ai-json-mode').checked,
      });
      if (key) headers['X-Port-Light-Model-Key'] = key;
      if (state.editingId) {
        body.profile_id = state.editingId;
        body.config_revision = editorProfile().revision;
      }
    } else {
      if (!savedProfile || !usableProfile(savedProfile)) return;
      body.profile_id = savedProfile.id;
      body.config_revision = savedProfile.revision;
    }
    // Keep the form in place so a successful draft probe can be saved without
    // retyping the key. The secret stays only in the existing password input.
    const controls = [...root.querySelectorAll('input, select, button')];
    const disabled = controls.map(control => control.disabled);
    for (const control of controls) control.disabled = true;
    button.textContent = t('testing');
    state.busy = 'test';
    state.status = null;
    showTestStatus();
    try {
      const result = await request('/analysis/api/settings/ai/test', {
        method: 'POST', headers, body: JSON.stringify(body),
      });
      if (!isAlive() || state.disposed) return;
      if (result.status !== 'connected' || result.config_revision !== (form ? null : savedProfile.revision)) {
        throw failure('invalid_output', 502);
      }
      state.status = { kind: 'ok', message: t(form ? 'test_connected_draft' : 'test_connected') };
    } catch (item) {
      if (!isAlive() || state.disposed) return;
      state.status = { kind: 'error', message: testError(item) };
    } finally {
      key = '';
      delete headers['X-Port-Light-Model-Key'];
      if (isAlive() && !state.disposed) {
        state.busy = null;
        controls.forEach((control, index) => { control.disabled = disabled[index]; });
        button.textContent = t('test');
        if (savedProfile && state.status) state.status.message = t('connection_result', {
          connection: connectionLabel(savedProfile), result: state.status.message,
        });
        updateProviderHints();
        showTestStatus();
      }
    }
  }

  function showTestStatus() {
    root.querySelector('.analysis-settings-status')?.remove();
    const node = statusNode();
    if (node) root.querySelector('.analysis-settings-card-body').prepend(node);
  }

  function clearTestStatus() {
    state.status = null;
    showTestStatus();
  }

  async function mutateSavedConnection(profile, kind) {
    if (!state.document || state.busy || state.document.readonly) return;
    if (kind === 'select' && (state.document.active_connection_id === profile.id || !usableProfile(profile))) return;
    if (kind === 'delete' && state.deletingId !== profile.id) return;
    const controls = [...root.querySelectorAll('input, select, button')];
    const disabled = controls.map(control => control.disabled);
    for (const control of controls) control.disabled = true;
    state.busy = kind;
    state.status = null;
    showTestStatus();
    let resetEditor = false;
    try {
      const selecting = kind === 'select';
      const path = '/analysis/api/settings/ai/' + (selecting ? 'active' : 'connections/' + encodeURIComponent(profile.id));
      const document = await request(path, {
        method: selecting ? 'POST' : 'DELETE',
        headers: { 'Content-Type': 'application/json', 'X-Port-Light-Analysis': '1' },
        body: JSON.stringify({ config_revision: profile.revision, ...(selecting ? { profile_id: profile.id } : {}) }),
      });
      if (!isAlive() || state.disposed) return;
      if (!isSettingsDocument(document) || (selecting ? document.active_connection_id !== profile.id
        : document.connections.some(item => item.id === profile.id))) throw failure('settings_unavailable', 503);
      state.document = document;
      state.deletingId = null;
      if (selecting) {
        if (state.editingId === profile.id && state.editorProfile?.revision === profile.revision) {
          state.editorProfile = document.connections.find(item => item.id === profile.id);
        }
        state.status = { kind: 'ok', message: t('connection_selected', { connection: connectionLabel(document.ai) }) };
      } else {
        if (state.editingId === profile.id) {
          clearSecret();
          state.editingId = document.active_connection_id || document.connections[0]?.id || null;
          state.editorProfile = document.connections.find(item => item.id === state.editingId) || null;
          state.draft = null;
          resetEditor = true;
        }
        state.status = { kind: 'ok', message: t('connection_deleted') };
      }
    } catch (item) {
      if (!isAlive() || state.disposed) return;
      state.status = { kind: 'error', message: requestError(item) };
    } finally {
      if (isAlive() && !state.disposed) {
        state.busy = null;
        controls.forEach((control, index) => { control.disabled = disabled[index]; });
        renderConnections();
        if (resetEditor) {
          root.querySelector('#analysis-ai-editor')?.replaceWith(editorPanel());
          enhanceSelects?.(root);
        }
        root.querySelector('.analysis-settings-badge').textContent = badgeText();
        updateProviderHints();
        showTestStatus();
      }
    }
  }

  function activateConnection(profile) { return mutateSavedConnection(profile, 'select'); }
  function deleteConnection(profile) { return mutateSavedConnection(profile, 'delete'); }

  await refresh();
  return () => {
    state.disposed = true;
    state.draft = null;
    clearSecret();
  };
}
