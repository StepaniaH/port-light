/* Local reservation ownership, allocation rules, and Compose conflict review. */
import { S } from './state.js?v=95';
import { hostName } from './hosts.js?v=95';
import { api } from './api.js?v=95';
import { t, escapeHtml } from './text.js?v=95';
import { copyReportText } from './doctor.js?v=95';

const storageKey = 'port-light-reservation-session';
const e = escapeHtml;
const tr = key => e(t('manage.' + key));
export function releaseCommand(port, token, origin) {
  const quote = value => "'" + String(value).replaceAll("'", "'\\''") + "'";
  return 'curl --fail-with-body -X DELETE ' + quote(origin + '/api/reservations/' + port) +
    ' -H ' + quote('X-Reservation-Token: ' + token);
}
export function composeSnippet(config, port) {
  if (!Number.isInteger(config.container_port) || !Number.isInteger(port) || port < 1 || port > 65535 ||
      ['expose', 'lan'].includes(config.mapping_source) || config.network_mode === 'host' || String(config.network_mode || '').startsWith('ns:') || String(config.network_mode || '').startsWith('service:') ||
      String(config.network_mode || '').startsWith('container:')) return null;
  const deploy = config.mapping_source === 'deploy.ports';
  const indent = deploy ? '  ' : '';
  const mapping = '    ports:\n      - target: ' +
    config.container_port + '\n        published: ' + JSON.stringify(String(port)) +
    '\n        protocol: ' + JSON.stringify(config.protocol || 'tcp') +
    (config.host_ip ? '\n        host_ip: ' + JSON.stringify(config.host_ip) : '') + '\n';
  return 'services:\n  ' + JSON.stringify(config.service_name) + ':\n' + (deploy ? '    deploy:\n' : '') +
    mapping.trimEnd().split('\n').map(line => indent + line).join('\n') + '\n';
}
async function json(url, options) {
  const response = await api(url, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(typeof body.detail === 'string' ? body.detail : t('manage.failed'));
    error.status = response.status;
    throw error;
  }
  return body;
}
function session() {
  const body = JSON.parse(sessionStorage.getItem(storageKey) || '{"tokens":{}}');
  return body && typeof body.tokens === 'object' && body.tokens ? body : { tokens: {} };
}
function save(body) { sessionStorage.setItem(storageKey, JSON.stringify(body)); }
function input(name, type = 'text', value = '', extra = '') {
  return '<label>' + tr(name) + '<input name="' + name + '" type="' + type + '" value="' + e(String(value)) + '" ' + extra + '></label>';
}
function button(action, label, extra = '') {
  return '<button type="button" class="btn-secondary" data-action="' + action + '" ' + extra + '>' + tr(label) + '</button>';
}
export function mountManagementPage(root) {
  let rules = [], reservations = [], rows = [], generation = 0;
  let section = 'conflicts';
  let agentToken = '';
  let busy = false;
  let rulesReadonly = false;
  function status(message) {
    const el = root.querySelector('[role="status"]');
    if (el) el.textContent = message;
  }
  function shell(content) {
    return '<header class="page-intro manage-header"><div><h1>' + tr(section) + '</h1><p class="settings-lead">' +
      e(hostName('local')) + ' <span class="manage-badge">' + e(t('hosts.thisMachine')) + '</span></p></div>' +
      '</header>' +
      '<p class="manage-status" role="status" aria-live="polite"></p>' + content;
  }
  function card(title, help, content, extra = '') {
    return '<section class="settings-card ' + extra + '"><header class="settings-card-head"><h2>' + tr(title) + '</h2>' +
      (help ? '<p>' + tr(help) + '</p>' : '') + '</header><div class="manage-card-body">' + content + '</div></section>';
  }
  function ruleOptions() {
    return '<label>' + tr('rule') + '<select name="rule"><option value="">' + tr('defaultRange') + '</option>' +
      rules.map(r => '<option value="' + e(r.name) + '">' + e(r.name) + ' · ' + r.start + '–' + r.end + '</option>').join('') + '</select></label>';
  }
  function tokenInput() { return S.meta.automation?.agent_token ? input('agentToken', 'password', '', 'autocomplete="off"') : ''; }
  function primary(label) { return '<div class="manage-form-actions"><button class="btn-primary">' + tr(label) + '</button></div>'; }
  function render() {
    let html = '';
    if (section === 'reservations') {
      const form = '<form data-form="reserve" class="manage-form">' +
        input('label', 'text', '', 'maxlength="256"') + input('count', 'number', 1, 'min="1" max="64" required') +
        ruleOptions() + input('start', 'number', '', 'min="1" max="65535"') + input('end', 'number', '', 'min="1" max="65535"') +
        input('ttl', 'number', 3600, 'min="60" max="604800"') + tokenInput() + primary('reserve') + '</form>';
      const filters = '<div class="manage-filters"><label>' + tr('machine') + '<select data-filter="machine"><option value="">' + tr('all') + '</option>' +
        [...new Set(reservations.map(r => r.machine))].map(m => '<option>' + e(m) + '</option>').join('') + '</select></label>' +
        '<label>' + tr('expiry') + '<select data-filter="expiry">' + ['all', 'permanent', 'expiring'].map(f => '<option value="' + f + '">' + tr(f) + '</option>').join('') + '</select></label></div>';
      html = card('newReservation', '', form) + card('reservations', '', filters + '<div id="reservation-list" class="manage-list"></div>') +
        '<aside class="manage-help"><p>' + tr('ownership') + '</p>' + button('retry', 'retry') + '</aside>';
    } else if (section === 'rules') {
      const form = '<form data-form="rule" class="manage-form">' + input('name', 'text', '', 'required maxlength="64" pattern="[a-zA-Z0-9][a-zA-Z0-9_-]*"') +
        input('start', 'number', 20000, 'required min="1" max="65535"') + input('end', 'number', 29999, 'required min="1" max="65535"') +
        input('projects', 'text') + primary('saveRule') + '</form>';
      const list = rules.map((r, i) => '<article class="manage-card manage-row"><div class="manage-row-main"><h3>' + e(r.name) +
        '</h3><p class="manage-range">' + r.start + '–' + r.end + '</p><p class="manage-meta">' + e(r.projects.join(', ')) + '</p></div><div class="manage-actions">' +
        button('editRule', 'edit', 'data-index="' + i + '"') + button('deleteRule', 'delete', 'data-index="' + i + '"') + '</div></article>').join('');
      html = card('saveRule', 'rulesHelp', form) + card('rules', '', '<div class="manage-list">' + (list || '<p class="manage-empty">' + tr('empty') + '</p>') + '</div>');
    } else {
      const conflicts = rows.filter(r => r.conflict || r.rule_violations?.length);
      html = '<p class="manage-lead">' + tr('conflictsHelp') + '</p>' + (conflicts.length ? conflicts.map(row =>
        '<article class="settings-card manage-card manage-conflict"><header class="settings-card-head manage-conflict-head"><h2><a href="#/port/' + row.port + '">' + row.port +
        '</a><span class="manage-protocol">' + e(row.protocol) + '</span></h2><span class="manage-badge is-warning">' + tr(row.conflict ? 'conflicts' : 'outsideRule') + '</span></header><div class="manage-card-body">' +
        (row.rule_violations || []).map(v => '<p class="manage-rule-warning">' + e(v.project + ' / ' + v.service + ' · ' + v.rule + ' · ' + v.start + '–' + v.end) + '</p>').join('') +
        (row.compose_configs || []).map((c, i) => '<section class="compose-conflict"><div class="manage-service-head"><h3>' + e((c.project_name || c.project_dir) + ' / ' + c.service_name) +
        '</h3><code>' + e((c.host_ip || '*') + ':' + row.port + '/' + c.protocol + ' → ' + c.container_port) + '</code></div><p class="manage-path">' + e(c.compose_file) + '</p>' +
          (composeSnippet(c, row.port) ? '<form data-form="suggest" data-port="' + row.port + '" data-index="' + i + '" class="manage-form">' +
          ruleOptions() + input('start', 'number', '', 'min="1" max="65535"') + input('end', 'number', '', 'min="1" max="65535"') +
          tokenInput() + primary('suggest') + '</form><div class="snippet-result"></div>' : '<p class="manage-meta">' + tr('hostNetwork') + '</p>') + '</section>').join('') + '</div></article>').join('') : '<p class="manage-empty">' + tr('empty') + '</p>');
    }
    if (section === 'rules' && rulesReadonly) html = '<p class="manage-help">' + e(t('doctor.detail.readonly')) + '</p><fieldset class="manage-readonly" disabled>' + html + '</fieldset>';
    root.innerHTML = shell(html);
    if (section === 'reservations') renderReservations();
  }
  function renderReservations() {
    const machine = root.querySelector('[data-filter="machine"]').value;
    const expiry = root.querySelector('[data-filter="expiry"]').value;
    let owned = {};
    try { owned = session().tokens; } catch { status(t('manage.storageFailed')); }
    const filtered = reservations.filter(r => (!machine || r.machine === machine) &&
      (expiry === 'all' || (expiry === 'permanent' ? !r.expires_at : !!r.expires_at)));
    root.querySelector('#reservation-list').innerHTML = filtered.map(r => '<article class="manage-card manage-row"><div class="manage-row-main"><h3><a class="manage-port" href="#/port/' + r.port + '">' + r.port +
      '</a><span>' + e(r.label) + '</span></h3><p class="manage-meta"><span>' + e(r.machine) + '</span><span class="manage-badge">' +
      (r.expires_at ? e(new Date(r.expires_at * 1000).toLocaleString(window.PortLightI18n?.locale())) : tr('permanent')) + '</span>' + (!r.is_reservation ? '<span class="manage-badge">' + tr('manual') + '</span>' : '') + '</p></div>' +
      (r.is_reservation ? '<div class="manage-actions">' + button('copyRelease', 'copyRelease', 'data-port="' + r.port + '"' + (owned[r.port] ? '' : ' disabled')) +
       button('release', 'release', 'data-port="' + r.port + '"' + (owned[r.port] ? '' : ' disabled')) + '</div>' : '') + '</article>').join('') || '<p class="manage-empty">' + tr('empty') + '</p>';
  }
  async function load(next = section) {
    section = next;
    const current = ++generation;
    root.innerHTML = shell('');
    status(t('manage.loading'));
    try {
      const results = await Promise.all([json('/api/port-rules'), json('/api/reservations'), section === 'conflicts' ? json('/api/ports?range_start=1&range_end=65535') : null]);
      if (current !== generation) return;
      rules = results[0].rules; rulesReadonly = results[0].readonly === true; reservations = results[1].reservations; rows = results[2]?.ports || [];
      render();
      if (results[2] && (results[2].summary.scan_complete !== true || results[2].summary.stale)) status(t('manage.incomplete'));
    } catch (err) { if (current === generation) status(err.message); }
  }
  async function reserve(pending) {
    let body;
    try { body = await json('/api/reservations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pending.key, 'X-Agent-Token': agentToken }, body: JSON.stringify(pending.body) }); } catch (error) {
      if ([400, 403, 422].includes(error.status)) { const state = session(); delete state.pending; save(state); }
      throw error;
    }
    await acceptReservation(body);
  }
  async function acceptReservation(body) {
    const saved = session();
    for (const r of body.reservations) saved.tokens[r.port] = r.token;
    delete saved.pending;
    save(saved);
    await load();
    if (!body.ports.length) status(t('manage.noCapacity'));
  }
  root.addEventListener('change', event => { if (event.target.dataset.filter) renderReservations(); });
  root.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const form = event.target;
    const data = Object.fromEntries(new FormData(form));
    agentToken = data.agentToken || agentToken;
    try {
      if (form.dataset.form === 'reserve') {
        const saved = session();
        if (saved.pending) throw new Error(t('manage.pending'));
        const body = { count: Number(data.count), label: data.label, start: data.start ? Number(data.start) : null,
          end: data.end ? Number(data.end) : null, ttl: data.ttl ? Number(data.ttl) : null, scope: 'self', require_count: true,
          ...(data.rule ? { rule: data.rule } : {}) };
        const bytes = crypto.getRandomValues(new Uint8Array(32));
        const key = btoa(String.fromCharCode(...bytes)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
        saved.pending = { key, body }; save(saved);
        await reserve(saved.pending);
      } else if (form.dataset.form === 'rule') {
        const rule = { name: data.name, start: Number(data.start), end: Number(data.end), projects: data.projects.split(',').map(p => p.trim()).filter(Boolean) };
        await json('/api/port-rules', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rules: [...rules.filter(r => r.name !== rule.name), rule] }) });
        await load();
      } else if (form.dataset.form === 'suggest') {
        const params = new URLSearchParams({ count: '1', require_count: 'true' });
        for (const key of ['rule', 'start', 'end']) if (data[key]) params.set(key, data[key]);
        const suggestion = await json('/api/ports/suggest?' + params, { headers: { 'X-Agent-Token': agentToken } });
        const c = rows.find(r => r.port === Number(form.dataset.port)).compose_configs[Number(form.dataset.index)];
        const output = form.parentElement.querySelector('.snippet-result');
        const snippet = composeSnippet(c, suggestion.ports[0]);
        if (!snippet) throw new Error(t('manage.failed'));
        output.innerHTML = '<p>' + tr('snippetHelp') + '</p><pre>' + e(snippet) + '</pre>' + button('copySnippet', 'copy');
      }
    } catch (err) { status(err.message); } finally { busy = false; }
  });
  root.addEventListener('click', async event => {
    const btn = event.target.closest('[data-action]');
    if (!btn || busy) return;
    busy = true;
    try {
      const action = btn.dataset.action;
      if (action === 'retry') {
        agentToken = root.querySelector('[name="agentToken"]')?.value || agentToken;
        const pending = session().pending;
        if (pending) {
          try {
            const result = await json('/api/reservations/request', { headers: { 'Idempotency-Key': pending.key, 'X-Agent-Token': agentToken } });
            await acceptReservation(result);
          } catch (error) {
            if (error.status === 404) await reserve(pending);
            else if (error.status === 409) {
              const saved = session(); delete saved.pending; save(saved);
              status(error.message);
            } else throw error;
          }
        }
        else status(t('manage.noPending'));
      } else if (action === 'copySnippet') { await copyReportText(btn.parentElement.querySelector('pre').textContent); status(t('manage.copied')); }
      else if (action === 'copyRelease' || action === 'release') {
        const saved = session(); const port = Number(btn.dataset.port); const token = saved.tokens[port];
        if (!token) return;
        if (action === 'copyRelease') { await copyReportText(releaseCommand(port, token, location.origin)); status(t('manage.copied')); }
        else {
          await json('/api/reservations/' + port, { method: 'DELETE', headers: { 'X-Reservation-Token': token } });
          delete saved.tokens[port]; save(saved); await load();
        }
      } else if (action === 'editRule') {
        const rule = rules[Number(btn.dataset.index)]; const form = root.querySelector('form');
        for (const key of ['name', 'start', 'end']) form.elements[key].value = rule[key];
        form.elements.projects.value = rule.projects.join(', '); form.elements.name.focus();
      } else if (action === 'deleteRule') {
        await json('/api/port-rules', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rules: rules.filter((r, i) => i !== Number(btn.dataset.index)) }) });
        await load();
      }
    } catch (err) { status(err.message); } finally { busy = false; }
  });
  return { open: load };
}
