/* Mounted inside the application shell. A visit owns its requests and recovery IDs. */
export async function mount({ root, locale, port, signal, enhanceSelects, revision }) {
  const asset = name => '/analysis/assets/' + name + (revision ? '?v=' + revision : '');
  const resource = async (name, format) => {
    const response = await fetch(asset(name), { credentials: 'same-origin', signal });
    if (!response.ok) throw new Error('Workspace asset unavailable');
    return response[format]();
  };
  const [html, catalogs, display] = await Promise.all([
    resource('index.html', 'text'), resource('messages.json', 'json'), import(asset('display.js')),
  ]);
  if (signal.aborted) return;
  const t = display.translator(catalogs, locale);
  root.innerHTML = html;
  for (const element of root.querySelectorAll('[data-analysis-i18n]')) element.textContent = t(element.dataset.analysisI18n);
  for (const element of root.querySelectorAll('[data-analysis-i18n-placeholder]')) element.placeholder = t(element.dataset.analysisI18nPlaceholder);
  for (const element of root.querySelectorAll('[data-analysis-i18n-aria]')) element.setAttribute('aria-label', t(element.dataset.analysisI18nAria));

  const byId = id => root.querySelector('#analysis-' + id);
  const on = (id, event, handler) => byId(id).addEventListener(event, handler, { signal });
  const VISIT_KEY = 'port-light-workbench-visit-v1';
  const LEGACY_ANALYSIS_KEY = 'port-light-analysis-id';
  const LEGACY_REPORT_KEY = 'port-light-analysis-report-id';
  const state = {
    capture: null, report: null, sourceReport: null, legacy: null, options: null,
    task: 'triage', selectedPorts: [], busy: false, initializing: true, capturing: false, enabled: false, reportsAvailable: false,
    demo: false, settings: null, settingsError: null, consentedRevision: null, dirty: false, generation: 0, timer: null,
    visibleSelected: 40, visibleProblems: 12, visibleFacts: 20, remainingOpen: false,
    reports: [], reportsCursor: null, legacyReports: [], restoring: null, pollFailures: 0, aiUncertain: false, liveCapture: false, payloadKey: null, aiResultKey: null,
  };

  signal.addEventListener('abort', () => {
    clearTimeout(state.timer);
    state.generation += 1;
  }, { once: true });

  function date(value) { return display.formatDate(value, locale) || t('unknown_time'); }
  function setText(id, text) { byId(id).textContent = text; }
  function error(message = '') {
    if (signal.aborted) return;
    setText('error', message);
    byId('error').hidden = !message;
  }
  function status(message = '') {
    if (signal.aborted) return;
    setText('status', message);
    byId('status').hidden = !message;
  }
  function failure(code, statusCode) {
    const item = new Error('Workbench request failed');
    item.code = code;
    item.status = statusCode;
    return item;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', ...options, signal });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data) throw failure(data?.error?.code, response.status);
    return data;
  }
  function action(body, extra = {}) {
    return {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Port-Light-Analysis': '1', 'Accept-Language': locale, ...extra },
      body: JSON.stringify(body),
    };
  }
  function isPort(value) { return Number.isInteger(value) && value >= 1 && value <= 65535; }
  function portNumber(id) {
    const value = Number(byId(id).value);
    if (!isPort(value)) throw failure('invalid_scope', 422);
    return value;
  }
  function currentScope() {
    const kind = byId('scope-kind').value;
    if (kind === 'all_known') return { kind };
    if (kind === 'single_port') return { kind, port: portNumber('single-port') };
    if (kind === 'selected_ports') {
      if (!state.selectedPorts.length) throw failure('invalid_scope', 422);
      return { kind, ports: [...state.selectedPorts] };
    }
    if (kind === 'port_range') {
      const start = portNumber('range-start');
      const end = portNumber('range-end');
      if (end < start || end - start + 1 > 1024) throw failure(end - start + 1 > 1024 ? 'scope_too_large' : 'invalid_scope', 422);
      return { kind, start, end };
    }
    throw failure('invalid_scope', 422);
  }
  function scopeSummary(requested) {
    const scope = requested?.scope && typeof requested.scope === 'object' ? requested.scope : requested;
    if (!scope || typeof scope !== 'object') return t('scope_invalid');
    if (scope.kind === 'single_port' && isPort(scope.port)) return t('scope_summary_single', { port: scope.port });
    if (scope.kind === 'selected_ports' && Array.isArray(scope.ports)) return t('scope_summary_selected', { count: scope.ports.filter(isPort).length });
    if (scope.kind === 'port_range' && isPort(scope.start) && isPort(scope.end)) return t('scope_summary_range', { start: scope.start, end: scope.end });
    return scope.kind === 'all_known' ? t('scope_all_known') : t('scope_invalid');
  }
  function selectedScopeSummary() {
    try { return scopeSummary(currentScope()); } catch (_) { return t('scope_invalid'); }
  }
  function storedVisit() {
    try {
      const value = JSON.parse(sessionStorage.getItem(VISIT_KEY) || 'null');
      if (!value || value.version !== 1 || typeof value !== 'object') return null;
      return value;
    } catch (_) { return null; }
  }
  function saveVisit() {
    if (signal.aborted) return;
    try {
      const value = {
        version: 1,
        captureId: state.capture?.id || null,
        reportId: state.report?.id || null,
        legacyReportId: state.legacy?.reportId || null,
        // A report can coexist with its still-live capture after Save. Keep
        // that distinction so a trip through settings does not turn a live
        // capture into a read-only historical report.
        liveCapture: state.liveCapture === true,
        // A port deep link should restore only the visit that created it.  A
        // different port page still starts as a preset-only new visit.
        routePort: isPort(port) ? port : null,
        task: state.task,
        scopeKind: byId('scope-kind').value,
        selectedPorts: state.selectedPorts.slice(0, 1024),
        singlePort: byId('single-port').value,
        rangeStart: byId('range-start').value,
        rangeEnd: byId('range-end').value,
        protocol: byId('protocol').value,
        historyHours: byId('history-hours').value,
      };
      sessionStorage.setItem(VISIT_KEY, JSON.stringify(value));
    } catch (_) { /* Recovery is optional when browser storage is unavailable. */ }
  }
  function clearLegacyKeys() {
    try { sessionStorage.removeItem(LEGACY_ANALYSIS_KEY); sessionStorage.removeItem(LEGACY_REPORT_KEY); } catch (_) { /* Optional. */ }
  }
  function safeSet(select, value, fallback) {
    if ([...select.options].some(option => option.value === value)) select.value = value;
    else if (fallback !== undefined) select.value = fallback;
  }
  function restoreForm(saved) {
    if (saved) {
      state.task = saved.task === 'changes' ? 'changes' : 'triage';
      if (['all_known', 'selected_ports', 'port_range', 'single_port'].includes(saved.scopeKind)) byId('scope-kind').value = saved.scopeKind;
      state.selectedPorts = Array.isArray(saved.selectedPorts) ? [...new Set(saved.selectedPorts.filter(isPort))].sort((a, b) => a - b).slice(0, 1024) : [];
      byId('single-port').value = isPort(Number(saved.singlePort)) ? saved.singlePort : '';
      byId('range-start').value = isPort(Number(saved.rangeStart)) ? saved.rangeStart : '';
      byId('range-end').value = isPort(Number(saved.rangeEnd)) ? saved.rangeEnd : '';
      safeSet(byId('protocol'), saved.protocol, 'all');
      safeSet(byId('history-hours'), saved.historyHours, '24');
    }
    if (isPort(port) && saved?.routePort !== port) {
      state.task = 'triage';
      byId('scope-kind').value = 'single_port';
      byId('single-port').value = String(port);
    }
  }
  function applyScopeFields() {
    const kind = byId('scope-kind').value;
    byId('single-port-fields').hidden = kind !== 'single_port';
    byId('selected-ports-fields').hidden = kind !== 'selected_ports';
    byId('range-fields').hidden = kind !== 'port_range';
    byId('window-fields').hidden = state.task !== 'changes';
    byId('task-triage').setAttribute('aria-pressed', String(state.task === 'triage'));
    byId('task-changes').setAttribute('aria-pressed', String(state.task === 'changes'));
    setText('task-hint', t(state.task === 'changes' ? 'task_changes_hint' : 'task_triage_hint'));
    byId('capture-button').textContent = t(state.dirty && (state.capture || state.legacy) ? 'recapture' : 'capture');
    const note = state.options?.known_ports_complete === false ? t('known_ports_truncated') : t('known_ports_complete');
    setText('known-ports-note', note);
    byId('known-ports-note').hidden = kind !== 'selected_ports';
    renderSelectedPorts();
    enhanceSelects(root);
  }
  function renderSelectedPorts() {
    const list = byId('selected-ports');
    const values = state.selectedPorts.slice(0, state.visibleSelected);
    list.replaceChildren(...values.map(value => {
      const item = document.createElement('li'); item.className = 'port-chip';
      const label = document.createElement('span'); label.textContent = t('port_number', { port: value });
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
      remove.setAttribute('aria-label', t('remove_port', { port: value }));
      remove.addEventListener('click', () => {
        state.selectedPorts = state.selectedPorts.filter(item => item !== value);
        state.visibleSelected = Math.min(Math.max(40, state.visibleSelected), state.selectedPorts.length || 40);
        markScopeChanged();
      }, { signal });
      item.append(label, remove); return item;
    }));
    byId('selected-ports-empty').hidden = state.selectedPorts.length > 0;
    const more = byId('show-more-selected');
    more.hidden = state.selectedPorts.length <= values.length;
    more.textContent = t('show_more_count', { count: Math.min(40, state.selectedPorts.length - values.length) });
  }
  function parsedBulkPorts(value) {
    const items = String(value || '').trim().split(/[\s,]+/).filter(Boolean);
    if (!items.length) return [];
    const output = new Set();
    for (const item of items) {
      const match = /^(\d+)(?:-(\d+))?$/.exec(item);
      if (!match) return null;
      const start = Number(match[1]); const end = match[2] === undefined ? start : Number(match[2]);
      if (!isPort(start) || !isPort(end) || end < start || end - start + 1 > 1024) return null;
      for (let current = start; current <= end; current += 1) {
        output.add(current);
        if (output.size > 1024) return null;
      }
    }
    return [...output];
  }
  function addPorts(values) {
    const combined = new Set(state.selectedPorts);
    for (const value of values) combined.add(value);
    if (combined.size > 1024) { error(t('error_port_list')); return false; }
    state.selectedPorts = [...combined].sort((a, b) => a - b);
    state.visibleSelected = Math.max(40, Math.min(state.visibleSelected, state.selectedPorts.length));
    error(); markScopeChanged(); return true;
  }
  function markScopeChanged() {
    if (signal.aborted || state.busy || state.capture?.status === 'running') return;
    state.generation += 1;
    byId('consent').checked = false;
    state.consentedRevision = null;
    updateDirtyState();
    applyScopeFields(); renderScopeNotice(); renderAI(); controls(); saveVisit();
  }
  function updateDirtyState() {
    try {
      state.dirty = Boolean(state.legacy || state.capture && !display.matchesCaptureRequest(state.capture, {
        kind: state.task, scope: currentScope(), protocol: byId('protocol').value,
        history_hours: Number(byId('history-hours').value),
      }));
    } catch (_) { state.dirty = Boolean(state.capture || state.legacy); }
  }
  function renderScopeNotice() {
    byId('scope-changed').hidden = !state.dirty;
    setText('scope-changed-text', t('scope_changed', { scope: selectedScopeSummary() }));
    byId('restore-scope').hidden = !state.capture;
  }
  function setTask(task) {
    if (task === state.task || state.busy || state.capture?.status === 'running') return;
    state.task = task; markScopeChanged();
  }
  function configureOptions(options) {
    state.options = options;
    const known = Array.isArray(options.known_ports) ? options.known_ports.filter(isPort) : [];
    const select = byId('known-port');
    select.replaceChildren(...known.map(value => {
      const option = document.createElement('option'); option.value = String(value); option.textContent = t('port_number', { port: value }); return option;
    }));
    select.disabled = known.length === 0;
    const range = options.range || {};
    const minimum = isPort(range.min) ? range.min : 1;
    const maximum = isPort(range.max) ? range.max : 65535;
    for (const id of ['single-port', 'manual-port', 'range-start', 'range-end']) {
      byId(id).min = String(minimum); byId(id).max = String(maximum);
    }
    if (!byId('range-start').value) byId('range-start').value = String(minimum);
    if (!byId('range-end').value) byId('range-end').value = String(Math.min(maximum, minimum + 99));
    for (const hour of [...byId('history-hours').options]) hour.hidden = !options.limits?.history_hours?.includes(Number(hour.value));
    if (byId('history-hours').selectedOptions[0]?.hidden) byId('history-hours').value = String(options.limits?.history_hours?.at(-1) || 24);
    applyScopeFields();
  }
  function syncCaptureScope(capture, report, sourceReport) {
    const requested = capture?.scope_requested || {};
    const scope = requested.scope && typeof requested.scope === 'object' ? requested.scope : requested;
    const taskKind = capture?.kind === 'changes' || capture?.source_kind === 'changes' || report?.kind === 'changes' || sourceReport?.kind === 'changes' ? 'changes' : 'triage';
    state.task = taskKind;
    if (['all_known', 'selected_ports', 'port_range', 'single_port'].includes(scope.kind)) {
      byId('scope-kind').value = scope.kind;
      if (scope.kind === 'single_port' && isPort(scope.port)) byId('single-port').value = String(scope.port);
      if (scope.kind === 'selected_ports' && Array.isArray(scope.ports)) state.selectedPorts = [...new Set(scope.ports.filter(isPort))].sort((a, b) => a - b).slice(0, 1024);
      if (scope.kind === 'port_range' && isPort(scope.start) && isPort(scope.end)) {
        byId('range-start').value = String(scope.start); byId('range-end').value = String(scope.end);
      }
    }
    const protocol = capture?.protocol ?? requested.protocol;
    if (['tcp', 'udp', 'all'].includes(protocol)) safeSet(byId('protocol'), protocol, 'all');
    const hours = capture?.history_hours ?? requested.history_hours;
    if ([1, 6, 24].includes(Number(hours))) safeSet(byId('history-hours'), String(hours), '24');
    applyScopeFields();
  }

  function makeCoverageCard(label, value) {
    const card = document.createElement('div'); card.className = 'coverage-card';
    const title = document.createElement('small'); title.textContent = label;
    const detail = document.createElement('strong'); detail.textContent = value;
    card.append(title, detail); return card;
  }
  function limitText(code) { return display.limitationText?.(code, t) || t('coverage_limit_unknown'); }
  function renderCoverage(capture) {
    const coverage = capture.coverage || {};
    const requested = Number.isInteger(coverage.requested_count) ? coverage.requested_count : 0;
    const observed = Number.isInteger(coverage.observed_count) ? coverage.observed_count : 0;
    const omitted = Math.max(0, requested - observed);
    const statusKey = ['complete', 'partial', 'empty'].includes(capture.data_status) ? capture.data_status : 'partial';
    const cards = [
      makeCoverageCard(t('coverage_requested'), String(requested)),
      makeCoverageCard(t('coverage_observed'), String(observed)),
      makeCoverageCard(t('coverage_omitted'), String(omitted)),
      makeCoverageCard(t('coverage_status'), t('data_' + statusKey)),
    ];
    const limitations = Array.isArray(coverage.limitations) ? coverage.limitations : [];
    if (limitations.length) {
      const list = document.createElement('ul'); list.className = 'coverage-limitations';
      list.replaceChildren(...limitations.slice(0, 8).map(code => {
        const item = document.createElement('li'); item.textContent = limitText(code); return item;
      }));
      cards.push(list);
    }
    byId('coverage').replaceChildren(...cards);
  }
  function priorityValue(value) { return [1, 2, 3, 4].includes(Number(value)) ? String(value) : '4'; }
  function appendDetailRow(list, label, content) {
    const term = document.createElement('dt'); term.textContent = label;
    const value = document.createElement('dd');
    if (typeof content === 'string') value.textContent = content;
    else if (content) value.append(content);
    list.append(term, value);
  }
  function fieldList(fields, className) {
    const list = document.createElement('dl'); list.className = className;
    for (const field of fields) {
      const row = document.createElement('div');
      const value = document.createElement('span');
      for (const part of Array.isArray(field.value) ? field.value : [field.value]) {
        const segment = document.createElement('span'); segment.textContent = String(part); value.append(segment);
      }
      appendDetailRow(row, field.label, value); list.append(row);
    }
    return list;
  }
  function resourceLinks(resources) {
    const list = document.createElement('div'); list.className = 'resource-links';
    const label = (ports, protocol) => {
      const fragment = document.createDocumentFragment();
      const number = document.createElement('span'); number.className = 'resource-port';
      number.textContent = ports.length === 1 ? String(ports[0]) : ports[0] + '–' + ports.at(-1);
      const transport = document.createElement('span'); transport.className = 'resource-protocol';
      transport.textContent = display.protocolText(protocol, t);
      fragment.append(number, document.createTextNode(' '), transport); return fragment;
    };
    const portLink = (port, protocol) => {
      const link = document.createElement('a'); link.className = 'port-resource-link'; link.href = '#/port/' + port;
      link.setAttribute('aria-label', t('port_detail', { port }) + ', ' + display.protocolText(protocol, t));
      link.append(label([port], protocol)); return link;
    };
    const entries = [];
    for (const { protocol, ports } of display.resourceGroups(resources)) {
      if (ports.length < 4) {
        entries.push(...ports.map(port => ({ node: portLink(port, protocol), count: 1 })));
        continue;
      }
      const group = document.createElement('details'); group.className = 'resource-range';
      const summary = document.createElement('summary'); summary.append(label(ports, protocol));
      const count = document.createElement('span'); count.className = 'resource-count'; count.textContent = t('port_count', { count: ports.length });
      summary.append(document.createTextNode(' '), count);
      const members = document.createElement('div'); members.className = 'resource-links';
      members.replaceChildren(...ports.map(port => portLink(port, protocol)));
      group.append(summary, members); entries.push({ node: group, count: ports.length });
    }
    list.append(...entries.slice(0, 8).map(item => item.node));
    if (entries.length > 8) {
      const more = document.createElement('details'); more.className = 'resource-more';
      const summary = document.createElement('summary');
      summary.textContent = t('show_more_count', { count: entries.slice(8).reduce((sum, item) => sum + item.count, 0) });
      const rest = document.createElement('div'); rest.className = 'resource-links'; rest.append(...entries.slice(8).map(item => item.node));
      more.append(summary, rest); list.append(more);
    }
    return list;
  }
  function portsText(values) {
    const ports = [...new Set((Array.isArray(values) ? values : []).filter(isPort))];
    if (!ports.length) return t('no_related_ports');
    const first = ports.slice(0, 12).join(', ');
    return ports.length > 12 ? t('ports_truncated', { ports: first, count: ports.length - 12 }) : first;
  }
  function resourcesText(resources) {
    const all = Array.isArray(resources) ? resources : [];
    const values = all.filter(item => isPort(item?.port)).slice(0, 12);
    if (!values.length) return t('no_resources');
    const text = values.map(item => display.resourceText(item, t)).join(', ');
    return all.length > values.length ? t('ports_truncated', { ports: text, count: all.length - values.length }) : text;
  }
  function evidenceList(values, capture = state.capture) {
    const facts = factsById(capture);
    const list = document.createElement('ul'); list.className = 'evidence-list';
    const items = (Array.isArray(values) ? values : []).slice(0, 6).map(item => {
      const fact = facts.get(item?.id);
      const row = document.createElement('li');
      if (fact) row.append(factHeading(fact));
      else row.textContent = t('fact_observed', { time: item?.observed_at ? date(item.observed_at) : t('unknown_time') });
      return row;
    });
    if (!items.length) return t('no_evidence');
    list.append(...items); return list;
  }
  function factsList(capture) {
    if (Array.isArray(capture?.facts)) return capture.facts;
    if (!capture?.facts || typeof capture.facts !== 'object') return [];
    return Object.entries(capture.facts).map(([id, value]) => ({
      ...(value && typeof value === 'object' ? value : { data: value }), id: value?.id || id,
    }));
  }
  function factsById(capture) {
    return new Map(factsList(capture).map(item => [item.id, item]));
  }
  function evidenceRefsText(ids, capture) {
    const facts = factsById(capture);
    const values = [...new Set((Array.isArray(ids) ? ids : []).slice(0, 6).map(id => {
      const fact = facts.get(id); return fact ? t('fact_observed', { time: factTime(fact) }) : t('unknown_time');
    }))];
    return values.length ? values.join(', ') : t('no_evidence');
  }
  function beforeAfterCard(evidence) {
    const facts = factsById(state.capture);
    const entries = (Array.isArray(evidence) ? evidence : []).map(item => facts.get(item?.id)).filter(item => item?.data?.before || item?.data?.after || item?.before || item?.after).slice(0, 4);
    if (!entries.length) return null;
    const details = document.createElement('details'); details.className = 'fact-card';
    const summary = document.createElement('summary'); summary.textContent = t('before_after'); details.append(summary);
    for (const fact of entries) details.append(frozenFactNode(fact, 'current'));
    return details;
  }
  function actionNode(check) {
    const fragment = document.createDocumentFragment();
    const main = document.createElement('span'); main.textContent = display.actionText(check?.action, t); fragment.append(main);
    const purpose = document.createElement('span'); purpose.className = 'purpose'; purpose.textContent = display.purposeText(check?.purpose, t); fragment.append(purpose);
    return fragment;
  }
  function problemCard(problem) {
    const card = document.createElement('article'); card.className = 'problem-card';
    card.dataset.problemId = problem?.id || '';
    card.dataset.priority = priorityValue(problem?.priority);
    const header = document.createElement('div'); header.className = 'problem-card-header';
    const title = document.createElement('h4'); title.textContent = display.problemText(problem, t);
    const priority = document.createElement('span'); priority.className = 'priority-tag'; priority.textContent = t('priority_' + priorityValue(problem?.priority));
    header.append(title, priority);
    const checks = document.createElement('ul'); checks.className = 'problem-checks';
    const seen = new Set();
    for (const check of [problem?.first_check, problem?.confirm]) {
      if (!check?.action || seen.has(check.action + ':' + check.purpose)) continue;
      seen.add(check.action + ':' + check.purpose);
      const item = document.createElement('li'); item.append(actionNode(check)); checks.append(item);
    }
    const { affected, related } = display.problemResources(problem, state.capture);
    const resourceLine = document.createElement('div'); resourceLine.className = 'problem-resources';
    if (affected.length) resourceLine.append(resourceLinks(affected));
    else if (related.length || problem?.related_ports?.some(isPort)) resourceLine.textContent = related.length ? t('no_resources') : portsText(problem.related_ports);
    else resourceLine.hidden = true;
    const basis = document.createElement('details'); basis.className = 'problem-basis';
    const basisTitle = document.createElement('summary'); basisTitle.textContent = t('problem_details');
    const basisRows = document.createElement('dl');
    const grouped = [display.relationText(problem?.relation, t), display.groupText(problem?.relation?.group, t)].filter(Boolean).join(' ');
    appendDetailRow(basisRows, t('why_grouped'), grouped);
    if (related.length) appendDetailRow(basisRows, t('related_ports'), resourceLinks(related));
    appendDetailRow(basisRows, t('evidence'), evidenceList(problem?.evidence));
    basis.append(basisTitle, basisRows);
    const evidence = document.createElement('button'); evidence.type = 'button'; evidence.className = 'evidence-jump'; evidence.textContent = t('view_evidence');
    evidence.addEventListener('click', () => openCurrentFacts(problem?.evidence?.map(item => item.id)), { signal });
    if (problem?.evidence?.length) basis.append(evidence);
    card.append(header, resourceLine, checks);
    const beforeAfter = beforeAfterCard(problem?.evidence);
    if (beforeAfter) basis.append(beforeAfter);
    card.append(basis);
    return card;
  }
  function renderProblems(capture) {
    const all = Array.isArray(capture.problems) ? capture.problems : [];
    const queued = (Array.isArray(capture.priority_queue) ? capture.priority_queue : []).slice(0, 5);
    const queueIds = new Set(queued.map(problem => problem?.id).filter(Boolean));
    const priority = queued.length ? queued : all.slice(0, 5);
    const remaining = all.filter(problem => !queueIds.has(problem?.id) && !priority.includes(problem));
    setText('priority-intro', priority.length ? t('priority_intro', { count: priority.length }) : t('priority_empty'));
    byId('priority-intro').hidden = priority.length > 0;
    setText('queue-meta', priority.length === all.length ? t('problem_count', { count: all.length }) : t('queue_meta', { shown: priority.length, total: all.length }));
    byId('priority-queue').replaceChildren(...priority.map(problemCard));
    const section = byId('remaining-section');
    section.hidden = remaining.length === 0;
    if (!remaining.length) return;
    const target = byId('remaining-problems');
    target.hidden = !state.remainingOpen;
    const shown = state.remainingOpen ? remaining.slice(0, state.visibleProblems) : [];
    target.replaceChildren(...shown.map(problemCard));
    const button = byId('show-more-problems');
    button.setAttribute('aria-expanded', String(state.remainingOpen));
    if (!state.remainingOpen) button.textContent = t('show_remaining', { count: remaining.length });
    else if (shown.length < remaining.length) button.textContent = t('show_more_count', { count: Math.min(12, remaining.length - shown.length) });
    else button.textContent = t('hide_remaining');
  }
  function factTime(fact) {
    return fact?.observed_at ? date(fact.observed_at) : t('unknown_time');
  }
  function factHeading(fact) {
    const heading = document.createElement('span'); heading.className = 'fact-heading';
    const summary = display.factSummary(fact, t);
    const name = document.createElement('span'); name.className = 'fact-name'; name.textContent = summary.title;
    const time = document.createElement('time'); time.className = 'fact-time'; time.textContent = factTime(fact);
    time.setAttribute('aria-label', t('fact_observed', { time: factTime(fact) }));
    heading.append(name, time); return heading;
  }
  function factContent(fact) {
    const body = document.createElement('div'); body.className = 'fact-summary';
    for (const section of display.factSummary(fact, t).sections) {
      const group = document.createElement('section'); group.className = 'fact-section';
      if (section.title) {
        const title = document.createElement('h5'); title.textContent = section.title; group.append(title);
      }
      if (section.fields.length) group.append(fieldList(section.fields, 'fact-fields'));
      else { const empty = document.createElement('p'); empty.textContent = t('fact_no_value'); group.append(empty); }
      body.append(group);
    }
    return body;
  }
  function appendRawFact(container, fact) {
    const raw = document.createElement('details'); raw.className = 'fact-raw';
    const summary = document.createElement('summary'); summary.textContent = t('fact_raw');
    const pre = document.createElement('pre'); pre.textContent = JSON.stringify(fact, null, 2);
    raw.append(summary, pre); container.append(raw);
  }
  function frozenFactNode(fact, side) {
    const node = document.createElement('article'); node.className = 'comparison-fact';
    node.dataset.captureSide = side;
    if (fact?.id) node.dataset.evidenceId = fact.id;
    const title = document.createElement('strong');
    const detail = document.createElement('p');
    if (!fact) {
      title.textContent = t('comparison_missing_fact'); detail.textContent = t('comparison_missing_fact');
      node.append(title, detail); return node;
    }
    title.append(factHeading(fact));
    node.append(title, factContent(fact)); appendRawFact(node, fact);
    return node;
  }
  function comparisonFactBlock(labelKey, ids, capture, side) {
    const requested = Array.isArray(ids) ? [...new Set(ids.filter(id => typeof id === 'string'))].slice(0, 8) : [];
    if (!requested.length) return null;
    const block = document.createElement('details'); block.className = 'comparison-facts';
    block.dataset.captureSide = side;
    const title = document.createElement('summary'); title.textContent = labelKey; block.append(title);
    const facts = factsById(capture);
    for (const identifier of requested) block.append(frozenFactNode(facts.get(identifier), side));
    return block;
  }
  function renderFacts(capture) {
    const all = factsList(capture);
    const shown = all.slice(0, state.visibleFacts);
    byId('facts-note').textContent = all.length ? t('facts_note', { shown: shown.length, total: all.length }) : t('facts_empty');
    byId('facts').replaceChildren(...shown.map(fact => {
      const item = document.createElement('details'); item.className = 'fact-card';
      if (typeof fact?.id === 'string') item.dataset.evidenceId = fact.id;
      const title = document.createElement('summary'); title.append(factHeading(fact));
      item.append(title, factContent(fact)); appendRawFact(item, fact); return item;
    }));
    const more = byId('show-more-facts');
    more.hidden = shown.length >= all.length;
    more.textContent = t('show_more_count', { count: Math.min(20, all.length - shown.length) });
  }
  function comparisonProblem(id, current, previous) {
    return (current || []).find(item => item?.id === id) || (previous || []).find(item => item?.id === id) || null;
  }
  function currentFactsFor(capture, ids) {
    const facts = factsById(capture);
    return (Array.isArray(ids) ? ids : []).map(identifier => facts.get(identifier)).filter(fact => fact?.kind === 'current');
  }
  function currentFactKey(fact) {
    const resource = fact?.resource;
    return isPort(resource?.port) && ['tcp', 'udp', 'all'].includes(resource?.protocol) ? resource.port + ':' + resource.protocol : '';
  }
  function comparisonChangeSummary(beforeIds, afterIds, sourceCapture, currentCapture) {
    const afterByResource = new Map(currentFactsFor(currentCapture, afterIds).map(fact => [currentFactKey(fact), fact]).filter(([key]) => key));
    const summaries = [];
    for (const before of currentFactsFor(sourceCapture, beforeIds)) {
      const after = afterByResource.get(currentFactKey(before));
      if (!after) continue;
      const changes = display.currentFactChanges(before, after, t);
      summaries.push(t('comparison_change_resource', {
        resource: display.resourceText(before.resource, t),
        changes: changes.length ? changes.join('; ') : t('comparison_no_field_change'),
      }));
    }
    return summaries.length ? t('comparison_change_summary', { changes: summaries.join('\n') }) : '';
  }
  function openCurrentFacts(ids = []) {
    const details = root.querySelector('.facts-details');
    if (!details) return;
    details.open = true;
    const requested = Array.isArray(ids) ? ids.filter(id => typeof id === 'string') : [];
    const facts = factsList(state.capture);
    const index = facts.findIndex(fact => requested.includes(fact?.id));
    if (index >= state.visibleFacts) {
      state.visibleFacts = Math.ceil((index + 1) / 20) * 20;
      renderFacts(state.capture);
    }
    const target = [...details.querySelectorAll('[data-evidence-id]')].find(item => requested.includes(item.dataset.evidenceId));
    if (target) {
      target.open = true;
      target.querySelector('summary').focus({ preventScroll: true });
      target.scrollIntoView({ block: 'nearest' });
    } else { details.querySelector('summary').focus({ preventScroll: true }); details.scrollIntoView({ block: 'nearest' }); }
  }
  function comparisonItem(value, current, previous, stateName) {
    const id = typeof value === 'string' ? value : value?.problem_id;
    const item = document.createElement('li');
    const currentProblem = (current || []).find(value => value?.id === id) || null;
    const previousProblem = (previous || []).find(value => value?.id === id) || null;
    const problem = currentProblem || previousProblem;
    const title = document.createElement('strong'); title.textContent = problem ? display.problemText(problem, t) : t('comparison_unknown_problem'); item.append(title);
    const resources = value?.resources || problem?.resources;
    if (Array.isArray(resources) && resources.length) {
      const resourceLine = document.createElement('span'); resourceLine.className = 'evidence-line';
      resourceLine.textContent = t('comparison_resources', { resources: resourcesText(resources) }); item.append(resourceLine);
    }
    const beforeIds = Array.isArray(value?.before_evidence_ids) ? value.before_evidence_ids : previousProblem?.evidence?.map(entry => entry?.id) || [];
    const afterIds = Array.isArray(value?.after_evidence_ids) ? value.after_evidence_ids : currentProblem?.evidence?.map(entry => entry?.id) || [];
    const sourceCapture = state.sourceReport?.capture || null;
    const currentTime = afterIds.length ? evidenceRefsText(afterIds, state.capture) : t('comparison_not_seen');
    const previousTime = beforeIds.length ? evidenceRefsText(beforeIds, sourceCapture) : t('comparison_not_seen');
    const times = document.createElement('span'); times.className = 'evidence-line';
    times.textContent = stateName === 'added' ? t('comparison_current', { evidence: currentTime }) : stateName === 'not_observed' ? t('comparison_previous', { evidence: previousTime }) : t('comparison_times', { previous: previousTime, current: currentTime });
    item.append(times);
    const change = comparisonChangeSummary(beforeIds, afterIds, sourceCapture, state.capture);
    if (change) { const line = document.createElement('span'); line.className = 'comparison-change'; line.textContent = change; item.append(line); }
    const group = display.groupText(currentProblem?.relation?.group || previousProblem?.relation?.group, t);
    if (group) { const groupLine = document.createElement('span'); groupLine.className = 'evidence-line'; groupLine.textContent = group; item.append(groupLine); }
    const savedFacts = comparisonFactBlock(t('comparison_saved_facts'), beforeIds, sourceCapture, 'saved');
    const newFacts = comparisonFactBlock(t('comparison_new_facts'), afterIds, state.capture, 'new');
    if (savedFacts) item.append(savedFacts);
    if (newFacts) item.append(newFacts);
    if (afterIds.length) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'evidence-jump'; button.textContent = t('view_new_evidence');
      button.addEventListener('click', () => openCurrentFacts(afterIds), { signal }); item.append(button);
    }
    return item;
  }
  function renderComparison(capture) {
    const comparison = capture.comparison;
    const section = byId('comparison-section');
    section.hidden = !comparison;
    if (!comparison) return;
    const previous = state.sourceReport?.capture?.problems || [];
    const current = capture.problems || [];
    const replace = (target, ids, stateName) => {
      const values = Array.isArray(ids) ? ids : [];
      const shown = values.slice(0, 20);
      const children = shown.length ? shown.map(value => comparisonItem(value, current, previous, stateName)) : [Object.assign(document.createElement('li'), { textContent: t('comparison_none') })];
      if (values.length > shown.length) children.push(Object.assign(document.createElement('li'), { textContent: t('comparison_more', { count: values.length - shown.length }) }));
      byId(target).replaceChildren(...children);
    };
    replace('comparison-added', comparison.added, 'added');
    replace('comparison-persisting', comparison.persisting, 'persisting');
    replace('comparison-not-observed', comparison.not_observed, 'not_observed');
    const cannot = Array.isArray(comparison.cannot_compare) ? comparison.cannot_compare : [];
    const cannotShown = cannot.slice(0, 20);
    const cannotChildren = cannotShown.length ? cannotShown.map(item => {
      const row = comparisonItem(item, current, previous, 'cannot_compare'); const problem = comparisonProblem(item?.problem_id, current, previous);
      const title = problem ? display.problemText(problem, t) : t('comparison_unknown_problem');
      const reasons = (Array.isArray(item?.reasons) ? item.reasons : []).map(reason => display.comparisonReasonText(reason, t)).join(' ');
      const reasonLine = document.createElement('span'); reasonLine.className = 'evidence-line'; reasonLine.textContent = [title, reasons].filter(Boolean).join(' — '); row.append(reasonLine); return row;
    }) : [Object.assign(document.createElement('li'), { textContent: t('comparison_none') })];
    if (cannot.length > cannotShown.length) cannotChildren.push(Object.assign(document.createElement('li'), { textContent: t('comparison_more', { count: cannot.length - cannotShown.length }) }));
    byId('comparison-cannot-compare').replaceChildren(...cannotChildren);
  }
  function reportStatus() {
    if (state.legacy) return t('legacy_report_status');
    if (currentRevisionSaved()) return t('report_saved');
    if (state.report?.id) return t('report_revision_unsaved');
    return t('report_unsaved');
  }
  function currentRevisionSaved() {
    return Boolean(state.capture && state.report?.id && state.report.source_workbench_id === state.capture.id &&
      state.report.result_revision === state.capture.result_revision);
  }
  function renderResults({ refreshEvidence = true } = {}) {
    const capture = state.capture;
    byId('results').hidden = !capture && !state.legacy;
    byId('empty-result').hidden = Boolean(capture || state.legacy);
    byId('report-actions').hidden = !capture && !state.legacy;
    if (!capture && !state.legacy) {
      setText('result-state', t('waiting')); return;
    }
    renderScopeNotice();
    const historical = Boolean(state.report && !state.liveCapture);
    byId('report-context').hidden = !historical;
    setText('report-context', historical ? t('report_context', { time: date(state.report.created_at) }) : '');
    if (state.legacy) return renderLegacy();
    setText('result-state', historical ? t('report_saved_short') : display.captureStateText(capture.status === 'interrupted' ? 'interrupted' : 'ready', t));
    setText('conclusion', display.conclusionText(capture, t));
    const mode = capture.source_kind === 'changes' ? 'changes' : 'triage';
    byId('result-scope').hidden = false;
    byId('result-scope').replaceChildren(fieldList([
      { label: t('scope'), value: scopeSummary(capture.scope_requested) },
      { label: t('protocol'), value: display.protocolText(capture.protocol, t) },
      ...(mode === 'changes' ? [{ label: t('history_window'), value: t('history_' + capture.history_hours) }] : []),
    ], 'result-meta'));
    byId('observations-summary').hidden = false;
    byId('observations-summary').replaceChildren(fieldList(display.observationCounts(capture, t), 'observation-counts'));
    const kind = ['triage', 'changes', 'recheck'].includes(capture.kind) ? capture.kind : 'triage';
    const captureKind = document.createElement('span'); captureKind.textContent = t('capture_kind_' + kind);
    const captureTime = document.createElement('time'); captureTime.textContent = date(capture.captured_at);
    byId('capture-meta').replaceChildren(captureKind, captureTime);
    if (refreshEvidence) { renderCoverage(capture); renderComparison(capture); renderProblems(capture); renderFacts(capture); }
    byId('save-report').hidden = false;
    byId('save-report').textContent = currentRevisionSaved() ? t('report_saved_short') : t('save_report');
    byId('export-link').hidden = !currentRevisionSaved();
    if (currentRevisionSaved()) byId('export-link').href = '/analysis/api/workbench/reports/' + encodeURIComponent(state.report.id) + '/export';
    byId('recheck').hidden = !currentRevisionSaved();
    setText('report-status', reportStatus());
  }
  function renderLegacy() {
    const view = display.legacyPresentation(state.legacy.document, t);
    setText('result-state', t('legacy_report'));
    setText('conclusion', view.conclusion);
    byId('result-scope').hidden = true;
    byId('observations-summary').hidden = true;
    setText('capture-meta', view.capturedAt ? date(view.capturedAt) : '');
    byId('coverage').replaceChildren(makeCoverageCard(t('coverage_status'), t('legacy_coverage')));
    byId('comparison-section').hidden = true;
    byId('priority-intro').textContent = t('legacy_problem_intro');
    byId('priority-intro').hidden = false;
    byId('queue-meta').textContent = '';
    const cards = [];
    const claimCard = (titleText, values) => {
      if (!values?.length) return;
      const card = document.createElement('article'); card.className = 'problem-card';
      const title = document.createElement('h4'); title.textContent = titleText; card.append(title);
      for (const value of values) {
        const text = document.createElement('p'); text.className = 'legacy-claim'; text.textContent = value.text || value;
        const refs = value.evidence_ids?.length ? document.createElement('span') : null;
        if (refs) { refs.className = 'references'; refs.textContent = t('recommendation_refs', { refs: value.evidence_ids.join(', ') }); text.append(refs); }
        if (value.missing_evidence?.length) {
          const missing = document.createElement('span'); missing.className = 'references'; missing.textContent = t('legacy_missing', { items: value.missing_evidence.join('; ') }); text.append(missing);
        }
        card.append(text);
      }
      cards.push(card);
    };
    claimCard(t('summary'), [{ text: view.summary, evidence_ids: view.structured.interpretation?.summary?.evidence_ids || [] }]);
    claimCard(t('hypotheses'), view.structured.interpretation?.hypotheses || []);
    const checks = view.structured.interpretation?.checks || [];
    if (checks.length) {
      const checkValues = checks.map(check => ({ text: t('check_' + check.action) + '\n' + t('check_' + check.action + '_detail'), evidence_ids: check.evidence_ids || [] }));
      claimCard(t('checks_heading'), checkValues);
    }
    claimCard(t('legacy_observations'), view.structured.observed || []);
    if (!cards.length) claimCard(t('legacy_report'), [{ text: view.summary }]);
    byId('priority-queue').replaceChildren(...cards);
    byId('remaining-section').hidden = true;
    renderFacts({ facts: view.facts });
    byId('save-report').hidden = true;
    byId('recheck').hidden = true;
    const exportLink = byId('export-link'); exportLink.hidden = !state.legacy.exportPath;
    if (state.legacy.exportPath) exportLink.href = state.legacy.exportPath;
    setText('report-status', reportStatus());
  }
  function settingsEnableAI() {
    if (typeof state.settings?.enabled === 'boolean') return state.settings.enabled;
    if (state.settings?.enabled && typeof state.settings.enabled.byok_port_analysis === 'boolean') {
      return state.settings.enabled.byok_port_analysis;
    }
    if (typeof state.settings?.capabilities?.byok_port_analysis === 'boolean') {
      return state.settings.capabilities.byok_port_analysis;
    }
    return state.enabled;
  }
  function savedConnection() {
    if (state.demo) return { demo: true, provider: 'demo', model: 'synthetic-demo', revision: 'demo' };
    const ai = state.settings?.ai;
    if (!settingsEnableAI() || ai?.configured !== true || typeof ai.model !== 'string' || !ai.model ||
        typeof ai.revision !== 'string' || !ai.revision) return null;
    if (!(state.settings?.providers || []).some(provider => provider?.id === ai.provider)) return null;
    if (ai.provider === 'custom' && !ai.base_url) return null;
    return ai;
  }
  function connectionProvider(connection) {
    if (connection?.demo) return t('demo_provider');
    const provider = (state.settings?.providers || []).find(item => item?.id === connection?.provider);
    return connection?.provider === 'custom' ? t('custom_provider') : provider?.name || connection?.provider || t('provider');
  }
  function resetConsent() {
    byId('consent').checked = false;
    state.consentedRevision = null;
  }
  async function loadSettings() {
    try {
      const previous = state.settings?.ai?.revision || null;
      const next = await api('/analysis/api/settings');
      if (signal.aborted) return false;
      state.settings = next; state.settingsError = null;
      if (previous !== (next?.ai?.revision || null)) resetConsent();
      return true;
    } catch (item) {
      if (!signal.aborted) state.settingsError = item;
      return false;
    }
  }
  function renderAI() {
    const capture = state.capture;
    const empty = byId('ai-empty'); const emptyAction = byId('ai-empty-action'); const content = byId('ai-content');
    if (!capture || state.legacy) {
      empty.hidden = false; content.hidden = true;
      empty.textContent = state.legacy ? t('ai_legacy') : state.settingsError ? t('ai_settings_unavailable')
        : !savedConnection() && !state.demo ? t('ai_connection_missing') : t('ai_empty');
      emptyAction.hidden = state.demo;
      setText('ai-state', ''); return;
    }
    empty.hidden = true; emptyAction.hidden = true; content.hidden = false;
    const preview = capture.ai_preview || {};
    const ai = capture.ai || { status: 'not_started' };
    const connection = savedConnection();
    const enabled = settingsEnableAI();
    const settingsUnavailable = !state.demo && Boolean(state.settingsError);
    setText('ai-state', display.aiStateText(ai.status, t));
    byId('ai-state').classList.toggle('is-ok', ai.status === 'completed');
    const eligible = enabled && !settingsUnavailable && Boolean(connection) && state.liveCapture && preview.eligible === true && !state.dirty && capture.status === 'ready';
    let eligibility = t('ai_not_eligible');
    if (state.dirty) eligibility = t('ai_scope_changed');
    else if (!state.liveCapture) eligibility = t('ai_saved_report');
    else if (capture.status === 'interrupted') eligibility = t('ai_interrupted_capture');
    else if (settingsUnavailable) eligibility = t('ai_settings_unavailable');
    else if (!connection) eligibility = t('ai_connection_missing');
    setText('ai-eligibility', eligibility);
    byId('ai-eligibility').hidden = ai.status !== 'not_started' || eligible || (!connection && !settingsUnavailable && state.liveCapture && !state.dirty);
    const aiError = ai.error || capture.error;
    byId('ai-error').hidden = !aiError;
    setText('ai-error', aiError ? display.failureText(aiError, t) : '');
    const payload = byId('ai-payload-details'); const payloadSummary = byId('ai-payload-summary');
    payload.hidden = !eligible;
    payloadSummary.hidden = !eligible;
    byId('ai-payload-omitted').hidden = true;
    if (eligible) {
      // The exact object remains available for consent, but should not bury the
      // consent controls under a large technical document on a new capture.
      const payloadKey = String(capture.id || '') + ':' + String(capture.result_revision || '');
      if (state.payloadKey !== payloadKey) {
        payload.open = false;
        state.payloadKey = payloadKey;
      }
      const sent = preview.payload && typeof preview.payload === 'object' ? preview.payload : {};
      const sentProblems = Array.isArray(sent.problems) ? sent.problems.length : 0;
      const sentFacts = sent.facts && typeof sent.facts === 'object' ? Object.keys(sent.facts).length : 0;
      const omitted = Number.isInteger(sent.problem_summary?.omitted_count)
        ? sent.problem_summary.omitted_count : 0;
      setText('ai-payload-summary', t('payload_summary', {
        scope: scopeSummary(capture.scope_requested), problems: sentProblems, facts: sentFacts,
        resources: sent.resource_summary?.sent_count || 0,
      }));
      const omittedResources = sent.resource_summary?.omitted_count || 0;
      byId('ai-payload-omitted').hidden = !omitted && !omittedResources;
      setText('ai-payload-omitted', t('payload_omitted', { resources: omittedResources, problems: omitted }));
      byId('ai-payload').textContent = JSON.stringify(sent, null, 2);
      setText('ai-payload-meta', t('payload_meta', {
        bytes: display.formatBytes(preview.input_bytes, locale), max: display.formatBytes(preview.max_input_bytes, locale),
      }));
    }
    const canStart = eligible && ai.status === 'not_started' && capture.status === 'ready';
    const canRecover = enabled && state.liveCapture && ['failed', 'cancelled'].includes(ai.status);
    byId('retry-ai').hidden = !canRecover;
    const connected = byId('ai-connection');
    connected.hidden = (!state.liveCapture || !enabled) && ai.status === 'not_started';
    if (!connected.hidden) {
      const shownConnection = !state.demo && ai.status !== 'not_started' && capture.provider && capture.model
        ? { provider: capture.provider, model: capture.model } : connection;
      setText('ai-connection-summary', shownConnection?.demo
        ? t('ai_demo_connection')
        : shownConnection ? t('ai_connection_saved', { provider: connectionProvider(shownConnection), model: shownConnection.model || '—' })
          : settingsUnavailable ? t('ai_settings_unavailable') : t('ai_connection_missing'));
      byId('ai-destination').hidden = !shownConnection?.base_url;
      setText('ai-destination', shownConnection?.base_url ? t('ai_destination', { url: shownConnection.base_url }) : '');
      const canManage = !state.demo && enabled && state.liveCapture && (ai.status === 'not_started' || canRecover);
      byId('ai-connection-action').hidden = !canManage;
      byId('manage-ai-settings').hidden = !canManage;
      setText('consent-label', state.demo ? t('consent_demo') : t('consent_saved', { provider: connectionProvider(connection) }));
    }
    const consent = byId('consent');
    byId('consent-field').hidden = !canStart;
    const start = byId('start-ai'); const cancel = byId('cancel-ai');
    start.hidden = !canStart; cancel.hidden = ai.status !== 'running';
    consent.disabled = state.busy || !canStart;
    byId('ai-explanation').hidden = ai.status !== 'completed';
    const resultKey = capture.id + ':' + capture.result_revision;
    if (ai.status === 'completed' && state.aiResultKey !== resultKey) {
      state.aiResultKey = resultKey;
      byId('ai-conclusion-evidence').open = false;
      const conclusion = ai.conclusion;
      setText('ai-conclusion', typeof conclusion?.text === 'string' ? conclusion.text : t('ai_conclusion_unavailable'));
      const ids = Array.isArray(conclusion?.evidence_ids) ? conclusion.evidence_ids.slice(0, 64) : [];
      byId('ai-conclusion-evidence').hidden = !ids.length;
      setText('ai-conclusion-evidence-title', t('ai_conclusion_evidence', { count: ids.length }));
      const facts = factsById(capture);
      byId('ai-conclusion-facts').replaceChildren(...ids.map(id => frozenFactNode(facts.get(id), 'current')));
      renderRecommendations(ai.recommendations || [], capture.problems || []);
    }
    byId('ai-recommendations').hidden = ai.status !== 'completed' || !Array.isArray(ai.recommendations);
    byId('ai-recommendations-empty').hidden = ai.status !== 'completed' || ai.recommendations?.length !== 0;
  }
  function renderRecommendations(recommendations, problems) {
    const distinct = display.distinctRecommendations(recommendations, state.capture);
    byId('ai-recommendation-list').replaceChildren(...distinct.slice(0, 10).map(item => {
      const row = document.createElement('div'); row.className = 'recommendation';
      const problem = (problems || []).find(value => value?.id === item?.problem_id);
      row.textContent = t('recommendation', {
        problem: problem ? display.problemText(problem, t) : t('comparison_unknown_problem'),
        action: display.actionText(item?.action, t),
      });
      const { affected } = display.problemResources(problem, state.capture);
      if (affected.length) {
        row.append(resourceLinks(affected));
      }
      const refs = comparisonFactBlock(t('view_evidence'), item?.evidence_ids, state.capture, 'current');
      if (refs) row.append(refs);
      return row;
    }));
  }
  function controls() {
    const running = state.initializing || state.busy || state.capture?.status === 'running';
    for (const id of ['scope-kind', 'single-port', 'known-port', 'add-known-port', 'manual-port', 'add-manual-port', 'bulk-ports', 'add-bulk-ports', 'range-start', 'range-end', 'protocol', 'history-hours', 'task-triage', 'task-changes']) {
      byId(id).disabled = running;
    }
    byId('capture-button').disabled = running || !state.enabled;
    byId('capture-button').textContent = t(state.capturing ? 'capturing' : state.dirty && (state.capture || state.legacy) ? 'recapture' : 'capture');
    byId('capture-form').setAttribute('aria-busy', String(state.capturing));
    byId('show-more-selected').disabled = running;
    for (const button of byId('selected-ports').querySelectorAll('button')) button.disabled = running;
    const saveable = ['ready', 'completed', 'failed', 'cancelled'].includes(state.capture?.status) && state.liveCapture;
    byId('save-report').disabled = running || !saveable || !state.reportsAvailable || currentRevisionSaved();
    byId('recheck').disabled = running || !state.enabled || !currentRevisionSaved() || state.dirty;
    const ai = state.capture?.ai || { status: 'not_started' };
    const preview = state.capture?.ai_preview || {};
    const connection = savedConnection();
    const canStart = settingsEnableAI() && !state.settingsError && state.liveCapture && preview.eligible === true && !state.dirty && ai.status === 'not_started' && state.capture?.status === 'ready' && !running && byId('consent').checked &&
      Boolean(connection) && state.consentedRevision === connection.revision;
    byId('start-ai').disabled = !canStart;
    byId('cancel-ai').disabled = state.busy || ai.status !== 'running';
    byId('retry-ai').disabled = running || !state.enabled || !state.liveCapture || state.dirty || !['failed', 'cancelled'].includes(ai.status);
    byId('consent').disabled = running || !connection || !settingsEnableAI() || ai.status !== 'not_started';
    for (const button of root.querySelectorAll('#analysis-report-list button, #analysis-legacy-report-list button')) button.disabled = running;
    enhanceSelects(root);
  }
  function updateCapture(capture, { report = state.report, sourceReport = state.sourceReport, focus = false, live = true, resetView = false } = {}) {
    if (signal.aborted || !capture) return;
    const changed = resetView || state.capture?.id !== capture.id || Boolean(state.legacy);
    clearTimeout(state.timer);
    resetConsent();
    if (changed) syncCaptureScope(capture, report, sourceReport);
    state.capture = capture; state.report = report; state.sourceReport = sourceReport; state.legacy = null; state.pollFailures = 0; state.aiUncertain = false; state.liveCapture = live;
    updateDirtyState();
    if (changed) {
      state.remainingOpen = false; state.visibleProblems = 12; state.visibleFacts = 20; state.aiResultKey = null;
      root.querySelector('.facts-details').open = false;
    }
    error(); status(); renderResults({ refreshEvidence: changed }); renderAI(); syncReportSelection(); controls(); saveVisit();
    if (live && capture.status === 'running') schedulePoll(capture.id, state.generation);
    if (focus) { byId('conclusion').focus(); byId('results-panel').scrollIntoView({ block: 'start' }); }
  }
  function discardUnavailableCapture(item) {
    clearTimeout(state.timer); state.capture = null; state.report = null; state.sourceReport = null; state.legacy = null; state.liveCapture = false;
    state.dirty = false; state.pollFailures = 0; state.aiUncertain = false; resetConsent();
    renderResults(); renderAI(); syncReportSelection(); controls(); saveVisit(); error(display.failureText(item, t));
  }
  function schedulePoll(identifier, generation, delay = 1200) {
    clearTimeout(state.timer);
    if (signal.aborted) return;
    state.timer = setTimeout(async () => {
      try {
        const capture = await api('/analysis/api/workbench/captures/' + encodeURIComponent(identifier));
        if (signal.aborted || generation !== state.generation) return;
        updateCapture(capture);
      } catch (item) {
        if (signal.aborted || generation !== state.generation) return;
        if ([401, 403, 404].includes(item.status)) { discardUnavailableCapture(item); return; }
        const recoverable = !item.status || item.status >= 500;
        state.pollFailures += 1;
        if (recoverable && state.pollFailures <= 3) {
          error(t('error_recovering', { count: state.pollFailures }));
          schedulePoll(identifier, generation, 1200 * (2 ** state.pollFailures));
          return;
        }
        discardUnavailableCapture(item);
      }
    }, delay);
  }
  async function capture() {
    if (state.initializing || !state.enabled || state.busy || state.capture?.status === 'running') return;
    let body;
    try {
      body = {
        kind: state.task, scope: currentScope(), protocol: byId('protocol').value,
        history_hours: Number(byId('history-hours').value),
      };
    } catch (item) { error(display.failureText(item, t)); return; }
    state.busy = true; state.capturing = true; state.generation += 1; const generation = state.generation; error(); status(t('capturing')); controls();
    try {
      const next = await api('/analysis/api/workbench/captures', action(body));
      if (signal.aborted || generation !== state.generation) return;
      updateCapture(next, { report: null, sourceReport: null, focus: true });
    } catch (item) { if (!signal.aborted && generation === state.generation) error(display.failureText(item, t)); }
    finally { state.busy = false; state.capturing = false; status(); controls(); }
  }
  async function startAI() {
    const current = state.capture;
    const connection = savedConnection();
    if (!current || !connection || byId('start-ai').disabled) return;
    const body = state.demo
      ? { provider: 'demo', model: 'synthetic-demo', confirmed: byId('consent').checked }
      : { connection: 'saved', config_revision: state.consentedRevision, confirmed: byId('consent').checked };
    state.busy = true; error(); controls();
    try {
      const next = await api('/analysis/api/workbench/captures/' + encodeURIComponent(current.id) + '/ai', action(body));
      updateCapture(next);
    } catch (item) {
      if (!signal.aborted) {
        if (item.code === 'configuration_changed') {
          resetConsent();
          await loadSettings();
          renderAI(); controls();
          error(t('error_configuration_changed'));
          return;
        }
        error(display.failureText(item, t));
        try { updateCapture(await api('/analysis/api/workbench/captures/' + encodeURIComponent(current.id))); }
        catch (_) {
          state.aiUncertain = true;
          state.capture = { ...current, status: 'running', ai: { ...(current.ai || {}), status: 'running' } };
          renderResults(); renderAI(); controls(); schedulePoll(current.id, state.generation);
        }
      }
    } finally { state.busy = false; controls(); }
  }
  async function cancelAI() {
    if (!state.capture || byId('cancel-ai').disabled) return;
    state.busy = true; error(); controls();
    try { updateCapture(await api('/analysis/api/workbench/captures/' + encodeURIComponent(state.capture.id) + '/cancel', action({}))); }
    catch (item) { if (!signal.aborted) error(display.failureText(item, t)); }
    finally { state.busy = false; controls(); }
  }
  function reportLabel(report) {
    const kind = ['triage', 'changes', 'recheck'].includes(report?.kind) ? report.kind : 'triage';
    return t('report_link', {
      kind: t('capture_kind_' + kind),
      scope: scopeSummary(report?.scope_summary),
      time: date(report?.created_at),
    });
  }
  function renderReportList() {
    byId('saved-empty').hidden = state.reports.length > 0;
    byId('report-list').replaceChildren(...state.reports.map(report => {
      const row = document.createElement('li'); const button = document.createElement('button'); button.type = 'button';
      button.dataset.reportId = report.id; button.setAttribute('aria-label', reportLabel(report));
      const title = document.createElement('span'); title.className = 'report-title';
      const kind = ['triage', 'changes', 'recheck'].includes(report.kind) ? report.kind : 'triage';
      title.textContent = t('capture_kind_' + kind);
      const scope = document.createElement('span'); scope.className = 'report-scope'; scope.textContent = scopeSummary(report.scope_summary);
      const time = document.createElement('span'); time.className = 'report-time'; time.textContent = date(report.created_at);
      button.append(title, scope, time);
      button.addEventListener('click', () => loadReport(report.id), { signal }); row.append(button); return row;
    }));
    syncReportSelection();
    byId('load-more-reports').hidden = !state.reportsCursor;
  }
  function syncReportSelection() {
    for (const button of byId('report-list').querySelectorAll('button')) {
      if (button.dataset.reportId === state.report?.id) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
    }
  }
  async function loadReports(reset = false) {
    const cursor = reset ? null : state.reportsCursor;
    const path = '/analysis/api/workbench/reports?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
    const data = await api(path);
    if (signal.aborted) return;
    state.reports = reset ? (data.reports || []) : [...state.reports, ...(data.reports || [])];
    state.reportsCursor = data.next_cursor || null; renderReportList(); controls();
  }
  async function loadReport(identifier) {
    if (state.busy) return;
    state.busy = true; error(); controls();
    try {
      const report = await api('/analysis/api/workbench/reports/' + encodeURIComponent(identifier));
      if (signal.aborted) return;
      const source = report.source_report_id ? await loadSourceReport(report.source_report_id).catch(() => null) : null;
      updateCapture(report.capture, { report, sourceReport: source, focus: true, live: false, resetView: true });
      return true;
    } catch (item) {
      if (!signal.aborted) error(display.failureText(item, t));
      return false;
    }
    finally { state.busy = false; controls(); }
  }
  async function loadSourceReport(identifier) {
    if (state.sourceReport?.id === identifier) return state.sourceReport;
    const source = await api('/analysis/api/workbench/reports/' + encodeURIComponent(identifier));
    if (!signal.aborted) state.sourceReport = source;
    return source;
  }
  async function saveReport() {
    if (!state.capture || byId('save-report').disabled) return;
    state.busy = true; error(); controls();
    try {
      const report = await api('/analysis/api/workbench/reports', action({ capture_id: state.capture.id }));
      if (signal.aborted) return;
      updateCapture(report.capture, { report, sourceReport: state.sourceReport, live: true });
      await loadReports(true);
    } catch (item) { if (!signal.aborted) error(display.failureText(item, t)); }
    finally { state.busy = false; controls(); }
  }
  async function recheck() {
    if (!state.enabled || !state.report?.id || byId('recheck').disabled) return;
    state.busy = true; error(); controls();
    try {
      const next = await api('/analysis/api/workbench/rechecks', action({ report_id: state.report.id }));
      if (signal.aborted) return;
      updateCapture(next, { report: null, sourceReport: state.report, focus: true, live: true });
    } catch (item) { if (!signal.aborted) error(display.failureText(item, t)); }
    finally { state.busy = false; controls(); }
  }
  async function loadLegacyReports() {
    try {
      const data = await api('/analysis/api/reports');
      if (signal.aborted || !Array.isArray(data.reports) || !data.reports.length) return;
      state.legacyReports = data.reports.slice(0, 20);
      byId('legacy-reports').hidden = false;
      byId('legacy-report-list').replaceChildren(...state.legacyReports.map(report => {
        const row = document.createElement('li'); const button = document.createElement('button'); button.type = 'button';
        button.textContent = t('legacy_report_link', { port: report.port, time: date(report.created_at) });
        button.addEventListener('click', () => loadLegacyReport(report.id), { signal }); row.append(button); return row;
      }));
    } catch (_) { /* Existing reports are a compatibility convenience, not a blocker. */ }
  }
  async function loadLegacyReport(identifier) {
    if (state.busy) return;
    state.busy = true; error(); controls();
    try {
      const document = await api('/analysis/api/reports/' + encodeURIComponent(identifier));
      if (signal.aborted) return;
      state.capture = null; state.report = null; state.sourceReport = null; state.liveCapture = false;
      state.legacy = { reportId: document.id, document, exportPath: '/analysis/api/reports/' + encodeURIComponent(document.id) + '/export' };
      state.dirty = false; renderResults(); renderAI(); syncReportSelection(); controls(); saveVisit();
    } catch (item) { if (!signal.aborted) error(display.failureText(item, t)); }
    finally { state.busy = false; controls(); }
  }
  async function loadLegacyAnalysis(identifier) {
    try {
      const document = await api('/analysis/api/analysis/' + encodeURIComponent(identifier));
      if (signal.aborted) return false;
      state.capture = null; state.report = null; state.sourceReport = null; state.liveCapture = false;
      state.legacy = { reportId: null, document, exportPath: '/analysis/api/analysis/' + encodeURIComponent(identifier) + '/report' };
      state.dirty = false; renderResults(); renderAI(); syncReportSelection(); controls(); return true;
    } catch (_) { return false; }
  }
  async function restoreResult(saved) {
    if (isPort(port) && saved?.routePort !== port) return;
    // Only visits that explicitly left a live capture may prefer the temporary
    // capture over an associated saved report. A user-selected history entry
    // remains read-only even though its report embeds a capture snapshot.
    if (saved?.liveCapture === true && saved?.captureId) {
      try {
        const capture = await api('/analysis/api/workbench/captures/' + encodeURIComponent(saved.captureId));
        let report = null; let source = null;
        if (saved.reportId) {
          try {
            report = await api('/analysis/api/workbench/reports/' + encodeURIComponent(saved.reportId));
            source = report.source_report_id ? await loadSourceReport(report.source_report_id).catch(() => null) : null;
          } catch (_) { /* A readable capture remains useful without its old receipt. */ }
        }
        updateCapture(capture, { report, sourceReport: source, live: true });
        return;
      }
      catch (item) {
        if (!saved.reportId && item.status === 404) {
          state.capture = null; state.report = null; saveVisit();
          error(display.failureText(item, t));
          return;
        }
        // The immutable report is the recovery fallback below.
      }
    }
    if (saved?.reportId && await loadReport(saved.reportId)) return;
    if (saved?.liveCapture !== true && saved?.captureId && !saved?.reportId) {
      try { updateCapture(await api('/analysis/api/workbench/captures/' + encodeURIComponent(saved.captureId))); return; }
      catch (item) {
        if (item.status === 404) {
          state.capture = null; state.report = null; saveVisit();
          error(display.failureText(item, t));
        }
      }
    }
    if (saved?.legacyReportId) { await loadLegacyReport(saved.legacyReportId); return; }
    try {
      const legacyReport = sessionStorage.getItem(LEGACY_REPORT_KEY);
      if (legacyReport) { await loadLegacyReport(legacyReport); return; }
      const legacyAnalysis = sessionStorage.getItem(LEGACY_ANALYSIS_KEY);
      if (legacyAnalysis && await loadLegacyAnalysis(legacyAnalysis)) return;
    } catch (_) { /* No browser recovery is still usable. */ }
  }

  on('capture-form', 'submit', event => { event.preventDefault(); capture(); });
  on('task-triage', 'click', () => setTask('triage'));
  on('task-changes', 'click', () => setTask('changes'));
  on('restore-scope', 'click', () => {
    if (!state.capture || state.busy) return;
    syncCaptureScope(state.capture, state.report, state.sourceReport);
    markScopeChanged();
    byId('capture-button').focus({ preventScroll: true });
  });
  for (const id of ['scope-kind', 'single-port', 'range-start', 'range-end', 'protocol', 'history-hours']) on(id, 'input', markScopeChanged);
  on('add-known-port', 'click', () => { if (!byId('known-port').disabled) addPorts([Number(byId('known-port').value)]); });
  on('add-manual-port', 'click', () => {
    const value = Number(byId('manual-port').value);
    if (!isPort(value)) { error(t('error_port_list')); return; }
    if (addPorts([value])) byId('manual-port').value = '';
  });
  on('manual-port', 'keydown', event => { if (event.key === 'Enter') { event.preventDefault(); byId('add-manual-port').click(); } });
  on('add-bulk-ports', 'click', () => {
    const values = parsedBulkPorts(byId('bulk-ports').value);
    if (!values || !addPorts(values)) { error(t('error_port_list')); return; }
    byId('bulk-ports').value = '';
  });
  on('show-more-selected', 'click', () => { state.visibleSelected += 40; renderSelectedPorts(); controls(); });
  on('show-more-problems', 'click', () => {
    const all = state.capture?.problems || []; const queueIds = new Set((state.capture?.priority_queue || []).map(item => item?.id));
    const remaining = all.filter(item => !queueIds.has(item?.id));
    if (!state.remainingOpen) state.remainingOpen = true;
    else if (state.visibleProblems < remaining.length) state.visibleProblems += 12;
    else state.remainingOpen = false;
    renderProblems(state.capture);
  });
  on('show-more-facts', 'click', () => { state.visibleFacts += 20; renderFacts(state.capture || { facts: [] }); });
  on('consent', 'input', () => {
    const connection = savedConnection();
    state.consentedRevision = byId('consent').checked && connection ? connection.revision : null;
    controls();
  });
  on('start-ai', 'click', startAI); on('cancel-ai', 'click', cancelAI);
  on('retry-ai', 'click', () => { if (!byId('retry-ai').disabled) capture(); });
  on('save-report', 'click', saveReport); on('recheck', 'click', recheck);
  on('load-more-reports', 'click', () => loadReports(false).catch(item => error(display.failureText(item, t))));

  async function init() {
    const saved = storedVisit(); restoreForm(saved);
    const [meta, options] = await Promise.all([api('/analysis/api/meta'), api('/analysis/api/workbench/options')]);
    if (signal.aborted) return;
    state.enabled = meta.enabled?.byok_port_analysis === true;
    state.reportsAvailable = meta.report_storage?.available === true;
    state.demo = meta.analysis_mode === 'demo';
    await loadSettings();
    if (signal.aborted) return;
    setText('report-storage-note', state.reportsAvailable ? t('storage_note') : t('error_storage'));
    configureOptions(options); renderAI(); controls();
    await Promise.allSettled([state.reportsAvailable ? loadReports(true) : Promise.resolve(), loadLegacyReports(), restoreResult(saved)]);
    if (saved && state.capture && saved.captureId === state.capture.id && state.capture.status !== 'running' &&
        (!isPort(port) || saved.routePort === port)) {
      restoreForm(saved); markScopeChanged();
    }
    state.initializing = false;
    controls();
  }
  controls();
  await init().catch(item => { if (!signal.aborted) error(display.failureText(item, t)); });
  return () => { clearTimeout(state.timer); };
}
