/* Format the validated workbench contract and localized labels. */
export function translator(catalogs, locale, namespace = 'workbench') {
  const messages = catalogs[locale] || catalogs.en || {};
  const fallback = catalogs.en || {};
  return (key, values = {}) => String(messages[namespace]?.[key] ?? messages[key] ?? fallback[namespace]?.[key] ?? fallback[key] ?? key).replace(/\{(\w+)\}/g, (_, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : '{' + name + '}');
}

const known = (value, values) => values.includes(value) ? value : null;

export function formatDate(value, locale) {
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleString(locale);
}

export function formatBytes(value, locale = 'en') {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return new Intl.NumberFormat(locale).format(value) + ' B';
  if (value < 1048576) return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1024) + ' KiB';
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value / 1048576) + ' MiB';
}

export function protocolText(protocol, t) {
  return t('protocol_' + (known(protocol, ['tcp', 'udp', 'all']) || 'all'));
}

export function matchesCaptureRequest(capture, request) {
  const saved = capture?.scope_requested;
  if (!saved || !request) return false;
  const mode = capture.source_kind || (capture.kind === 'changes' ? 'changes' : 'triage');
  if (mode !== request.kind || (capture.protocol ?? saved.protocol) !== request.protocol) return false;
  if (mode === 'changes' && Number(capture.history_hours ?? saved.history_hours) !== request.history_hours) return false;
  const before = saved.scope || saved;
  const after = request.scope;
  if (!after || before.kind !== after.kind) return false;
  if (after.kind === 'all_known') return true;
  if (after.kind === 'single_port') return before.port === after.port;
  if (after.kind === 'port_range') return before.start === after.start && before.end === after.end;
  if (after.kind !== 'selected_ports') return false;
  const ports = values => [...new Set(values || [])].sort((a, b) => a - b).join(',');
  return ports(before.ports) === ports(after.ports);
}

export function problemResources(problem, capture) {
  const resources = Array.isArray(problem?.resources) ? problem.resources : [];
  if (problem?.kind !== 'project_declaration_without_live_mapping') return { affected: resources, related: [] };
  const facts = Array.isArray(capture?.facts) ? capture.facts : Object.values(capture?.facts || {});
  const affected = resources.filter(resource => facts.some(fact => fact?.kind === 'current' &&
    fact.resource?.port === resource.port && fact.resource?.protocol === resource.protocol &&
    fact.data?.compose_relation === 'declared_without_live_mapping'));
  return { affected, related: resources.filter(resource => !affected.includes(resource)) };
}

export function distinctRecommendations(recommendations, capture) {
  const seen = new Set();
  return (recommendations || []).filter(item => {
    const problem = (capture?.problems || []).find(value => value?.id === item?.problem_id);
    if (!problem) return true;
    const { affected } = problemResources(problem, capture);
    const signature = JSON.stringify([problem.kind, item.action, item.relation,
      affected.map(resource => resource.port + ':' + resource.protocol).sort(),
      [...(item.evidence_ids || [])].sort()]);
    if (seen.has(signature)) return false;
    seen.add(signature); return true;
  });
}

export function conclusionText(captureOrCode, t) {
  const capture = captureOrCode && typeof captureOrCode === 'object' ? captureOrCode : null;
  if (capture?.data_status === 'empty') return t('conclusion_empty');
  const code = capture?.conclusion || captureOrCode;
  const value = known(code, [
    'action_required', 'limited_coverage', 'no_actionable_problem', 'changes_recorded',
  ]) || 'limited_coverage';
  if (value === 'no_actionable_problem' && capture?.source_kind === 'changes') return t('conclusion_no_changes');
  if (value !== 'action_required' || !capture) return t('conclusion_' + value);
  const summaryCount = Number.isInteger(capture.summary?.problem_count) ? capture.summary.problem_count : null;
  const problems = Array.isArray(capture.problems) ? capture.problems : [];
  const count = summaryCount ?? problems.length;
  return t('conclusion_action_required', { count });
}

export function observationCounts(capture, t) {
  const entries = Object.values(capture.facts || {}).filter(fact => fact?.kind === 'current');
  const count = (key, field) => Number.isInteger(capture.summary?.[key])
    ? capture.summary[key] : entries.filter(fact => fact.data?.[field] === true).length;
  const values = [
    { label: t('fact_label_listener'), value: count('listening_count', 'listening') },
    { label: t('fact_label_docker'), value: count('mapped_count', 'docker_live') },
    { label: t('fact_label_compose'), value: count('declared_count', 'compose_declared') },
  ];
  if (capture.source_kind === 'changes') {
    values.unshift({ label: t('observations_events'), value: Number.isInteger(capture.summary?.event_count) ? capture.summary.event_count
      : Object.values(capture.facts || {}).filter(fact => fact?.kind === 'event').length });
  }
  return values;
}

/** Keep protocols separate and make every member of a displayed range available. */
export function resourceGroups(resources) {
  const groups = [];
  for (const protocol of ['tcp', 'udp', 'all']) {
    const ports = [...new Set((resources || []).filter(item => item?.protocol === protocol &&
      Number.isInteger(item.port) && item.port >= 1 && item.port <= 65535).map(item => item.port))].sort((a, b) => a - b);
    for (const port of ports) {
      const previous = groups.at(-1);
      if (previous?.protocol === protocol && previous.ports.at(-1) === port - 1) previous.ports.push(port);
      else groups.push({ protocol, ports: [port] });
    }
  }
  return groups;
}

export function problemText(problem, t) {
  const kind = known(problem?.kind, [
    'scan_quality', 'overlapping_compose_bindings', 'project_declaration_without_live_mapping',
    'recorded_change', 'same_capture_changes', 'observation_degraded',
  ]);
  return t('problem_' + (kind || 'unknown'));
}

export function relationText(relation, t) {
  const kind = known(relation?.kind, [
    'overlapping_bind', 'same_compose_project', 'same_capture', 'source_quality', 'independent',
  ]);
  return t('relation_' + (kind || 'independent'));
}

export function actionText(action, t) {
  const value = known(action, [
    'scan_status', 'inspect_port', 'inspect_compose', 'inspect_mapping', 'inspect_history',
    'inspect_deployment_record',
  ]);
  return t('action_' + (value || 'scan_status'));
}

export function purposeText(purpose, t) {
  const value = known(purpose, [
    'restore_coverage', 'complete_current_scan', 'verify_overlapping_bind',
    'distinguish_live_mapping', 'verify_missing_live_mapping', 'compare_project_members',
    'verify_recorded_transition', 'correlate_deployment_record', 'verify_same_capture',
    'check_shared_change_context', 'separate_degradation_from_state_change',
  ]);
  return t('purpose_' + (value || 'restore_coverage'));
}

export function comparisonReasonText(reason, t) {
  const value = known(reason, [
    'scope_not_comparable', 'coverage_incomplete', 'source_not_observed', 'source_disabled',
    'scan_stale', 'resource_missing', 'history_window_moved',
    'historical_event_not_current_condition', 'protocol_changed', 'no_new_observation',
  ]);
  return t('comparison_reason_' + (value || 'coverage_incomplete'));
}

export function captureStateText(status, t) {
  const value = known(status, ['ready', 'running', 'completed', 'failed', 'cancelled', 'interrupted']);
  return t('capture_' + (value || 'ready'));
}

export function aiStateText(status, t) {
  const value = known(status, ['not_started', 'running', 'completed', 'failed', 'cancelled']);
  return t('ai_' + (value || 'not_started'));
}

export function resourceText(resource, t) {
  const port = Number.isInteger(resource?.port) && resource.port >= 1 && resource.port <= 65535 ? resource.port : '—';
  return t('resource', { port, protocol: protocolText(resource?.protocol, t) });
}

function booleanText(value, t) {
  return typeof value === 'boolean' ? t(value ? 'yes' : 'no') : t('fact_no_value');
}

function stateText(value, t) {
  const state = known(value, ['used', 'configured', 'free', 'unknown']);
  return state ? t('status_' + state) : t('fact_no_value');
}

function composeRelationText(value, t) {
  const relation = known(value, [
    'not_declared', 'declared_and_live', 'declared_without_live_mapping',
  ]);
  return t('fact_relation_' + (relation || 'not_declared'));
}

function bindingFields(binding, t) {
  const family = known(binding?.family, ['ipv4', 'ipv6', 'unknown']);
  const source = known(binding?.source, ['listen', 'docker', 'compose', 'manual']);
  const scope = known(binding?.scope, ['all_interfaces', 'loopback', 'specific_interface', 'unknown']);
  return {
    label: t('fact_family_' + (family || 'unknown')),
    value: [source === 'listen' ? t('source_listening')
      : source === 'docker' ? t('source_docker_live')
        : source === 'compose' ? t('source_compose_declared')
          : source === 'manual' ? t('source_manual') : t('fact_no_value'),
    scope ? t('scope_' + scope) : t('fact_no_value')],
  };
}

function eventSideFields(side, t) {
  const values = [];
  if (known(side?.status, ['used', 'configured', 'free', 'unknown'])) {
    values.push({ label: t('fact_label_state'), value: stateText(side.status, t) });
  }
  if (Object.prototype.hasOwnProperty.call(side || {}, 'bind_scope')) {
    const scope = known(side.bind_scope, ['public', 'lan', 'link', 'localhost']);
    const binding = scope ? t('bind_' + scope) : t('fact_no_value');
    values.push({ label: t('fact_label_binding'), value: binding });
  }
  if (typeof side?.compose_conflict === 'boolean') {
    values.push({ label: t('fact_label_conflict'), value: booleanText(side.compose_conflict, t) });
  }
  if (known(side?.quality, ['complete', 'degraded'])) {
    values.push({ label: t('fact_label_quality'), value: t('fact_quality_' + side.quality) });
  }
  return values;
}

function sourceFields(source, t) {
  const name = known(source?.name, ['listen', 'docker', 'compose', 'manual', 'occupancy']);
  const state = known(source?.state, ['ok', 'failed', 'disabled', 'unknown']);
  return { label: name ? t('fact_source_' + name) : t('fact_no_value'),
    value: t('fact_source_' + (state || 'unknown')) };
}

/** Render only the frozen, closed fact shapes supplied by the workbench API. */
export function factSummary(fact, t) {
  const kind = known(fact?.kind, ['scan', 'current', 'event']);
  const data = fact?.data && typeof fact.data === 'object' ? fact.data : {};
  if (kind === 'current') {
    const bindings = Array.isArray(data.bind) ? data.bind.slice(0, 8).map(binding => bindingFields(binding, t)) : [];
    return {
      title: resourceText(fact.resource, t),
      sections: [{ fields: [
        { label: t('fact_label_state'), value: stateText(data.status, t) },
        { label: t('fact_label_listener'), value: booleanText(data.listening, t) },
        { label: t('fact_label_docker'), value: booleanText(data.docker_live, t) },
        { label: t('fact_label_compose'), value: booleanText(data.compose_declared, t) },
        { label: t('fact_label_relation'), value: composeRelationText(data.compose_relation, t) },
        { label: t('fact_label_conflict'), value: booleanText(data.compose_conflict, t) },
      ] }, ...(bindings.length ? [{ title: t('fact_label_bindings'), fields: bindings }] : [])],
    };
  }
  if (kind === 'event') {
    const event = known(data.kind, [
      'state_changed', 'bind_scope_changed', 'configuration_mismatch',
      'observation_degraded', 'observation_recovered',
    ]);
    return {
      title: fact.resource ? resourceText(fact.resource, t) : t('fact_scan'),
      sections: [
        { fields: [{ label: t('fact_label_event'), value: event ? t('event_' + event) : t('fact_unknown') }] },
        { title: t('fact_before'), fields: eventSideFields(data.before, t) },
        { title: t('fact_after'), fields: eventSideFields(data.after, t) },
      ],
    };
  }
  if (kind === 'scan') {
    const sources = Array.isArray(data.sources) ? data.sources.slice(0, 8).map(source => sourceFields(source, t)) : [];
    const status = (value, yes, no) => typeof value === 'boolean' ? t(value ? yes : no) : t('fact_no_value');
    return {
      title: t('fact_scan'),
      sections: [{ fields: [
        { label: t('fact_label_scan'), value: status(data.ready, 'fact_ready', 'fact_not_ready') },
        { label: t('fact_label_coverage'), value: status(data.complete, 'fact_complete', 'fact_incomplete') },
        { label: t('fact_label_freshness'), value: status(data.stale, 'fact_stale', 'fact_fresh') },
      ] }, ...(sources.length ? [{ title: t('fact_label_sources'), fields: sources }] : [])],
    };
  }
  return { title: t('fact_unknown'), sections: [] };
}

/** Compare only closed current-fact fields; callers keep the frozen records available separately. */
export function currentFactChanges(beforeFact, afterFact, t) {
  if (beforeFact?.kind !== 'current' || afterFact?.kind !== 'current') return [];
  const before = beforeFact.data && typeof beforeFact.data === 'object' ? beforeFact.data : {};
  const after = afterFact.data && typeof afterFact.data === 'object' ? afterFact.data : {};
  const changes = [];
  if (before.status !== after.status) changes.push(t('comparison_change_state', {
    before: stateText(before.status, t), after: stateText(after.status, t),
  }));
  for (const [field, key] of [
    ['listening', 'listener'], ['docker_live', 'docker'], ['compose_declared', 'declaration'], ['compose_conflict', 'conflict'],
  ]) {
    if (before[field] !== after[field]) changes.push(t('comparison_change_' + key, {
      before: booleanText(before[field], t), after: booleanText(after[field], t),
    }));
  }
  if (before.compose_relation !== after.compose_relation) changes.push(t('comparison_change_relation', {
    before: composeRelationText(before.compose_relation, t), after: composeRelationText(after.compose_relation, t),
  }));
  return changes;
}

export function groupText(group, t) {
  if (!group || !Number.isInteger(group.observed_members) || !Number.isInteger(group.affected_members)) return '';
  return t('group_observed_members', {
    observed: group.observed_members,
    affected: group.affected_members,
  });
}

/* Older saved single-port reports use the saved interpretation schema. */
export function presentation(snapshot, t) {
  const evidence = snapshot?.evidence || {};
  const observation = evidence.observation;
  const rules = snapshot?.baseline;
  const observed = [];
  if (!observation?.current?.entries) return {
    observed, conclusion: t('legacy'), interpretation: null,
  };
  const state = value => t('status_' + value);
  const sources = { listen: 'listening', docker: 'docker_live', compose: 'compose_declared', manual: 'manual' };
  for (const entry of observation.current.entries) {
    const flags = ['listening', 'docker_live', 'compose_declared', 'manual', 'reservation'].filter(flag => entry[flag]);
    let text = [entry.protocol?.toUpperCase() || '—', state(entry.status), flags.length ? flags.map(flag => t('source_' + flag)).join(', ') : t('no_sources')].join(' / ');
    if (entry.compose_conflict) text += '. ' + t('conclusion_configuration_conflict');
    if (entry.compose_relation === 'declared_without_live_mapping') text += '. ' + t('conclusion_declaration_without_mapping');
    for (const binding of entry.bind || []) {
      text += '\n' + [binding.family === 'unknown' ? state('unknown') : binding.family === 'ipv4' ? 'IPv4' : 'IPv6',
        sources[binding.source] ? t('source_' + sources[binding.source]) : t('state'),
        binding.scope === 'unknown' ? state('unknown') : t('scope_' + binding.scope)].join(' / ');
    }
    observed.push({ text, evidence_ids: ['current'], observed_at: observation.captured_at });
  }
  if (evidence.question_type === 'recorded_changes') {
    for (const event of observation.events || []) {
      let text = t('event_' + event.kind);
      const { before = {}, after = {} } = event;
      if (event.kind === 'state_changed') text += ': ' + state(before.status) + ' → ' + state(after.status);
      if (event.kind === 'bind_scope_changed') {
        const bind = value => value === null ? state('unknown') : t('bind_' + value);
        text += ': ' + bind(before.bind_scope) + ' → ' + bind(after.bind_scope);
      }
      if (event.kind === 'configuration_mismatch') text += ': ' + t(before.compose_conflict ? 'yes' : 'no') + ' → ' + t(after.compose_conflict ? 'yes' : 'no');
      observed.push({ text, evidence_ids: [event.event_id], observed_at: event.observed_at });
    }
  }
  const changes = (observation.events || []).filter(event => ['state_changed', 'bind_scope_changed', 'configuration_mismatch'].includes(event.kind));
  const conclusion = rules?.version === 'port-rules.v1' ? t('conclusion_' + rules.conclusion.code, { count: changes.length }) : t('legacy');
  let interpretation = null;
  const selection = snapshot?.interpretation;
  if (snapshot?.interpretation_version === 'port-interpretation.v1' && selection?.schema_version === 1) {
    const text = selection.summary_kind === 'current_occupancy' ? conclusion : observed.filter(item => item.evidence_ids.every(id => selection.evidence_ids.includes(id))).map(item => item.text).join(' ');
    interpretation = {
      summary: { text: t('port') + ' ' + evidence.port + '\n' + text, evidence_ids: selection.evidence_ids },
      hypotheses: (selection.hypotheses || []).map(item => ({ text: t('hypothesis_' + item.kind), evidence_ids: item.evidence_ids, missing_evidence: (item.missing_evidence || []).map(kind => t('missing_' + kind)) })),
      checks: selection.checks || [],
    };
  }
  return { observed, conclusion, interpretation };
}

export function legacyPresentation(snapshot, t) {
  const evidence = snapshot?.evidence || {};
  const port = Number.isInteger(evidence.port) ? evidence.port : '—';
  const structured = presentation(snapshot, t);
  return {
    port,
    conclusion: structured.conclusion === t('legacy') ? t('legacy_conclusion') : structured.conclusion,
    summary: structured.interpretation?.summary?.text || t('legacy_summary', { port }),
    capturedAt: evidence.captured_at || evidence.observation?.captured_at || null,
    facts: [evidence],
    structured,
  };
}

export function limitationText(code, t) {
  const value = known(code, [
    'scan_incomplete', 'scan_stale', 'event_history_disabled', 'event_history_unavailable',
    'event_history_truncated', 'hidden_withheld', 'runtime_sources_unobserved',
    'scope_event_boundary', 'source_disabled',
  ]);
  return t('coverage_limit_' + (value || 'unknown'));
}

export function failureText(failure, t) {
  const aliases = {
    invalid_scope: 'scope', scope_too_large: 'scope_too_large', access_restricted: 'access',
    core_unavailable: 'core', confirmation_required: 'confirm', not_ready: 'not_ready',
    configuration_changed: 'configuration_changed',
    capture_expired: 'expired', not_found: 'expired', report_not_found: 'report_missing',
    invalid_input: 'scope', invalid_action: 'access', invalid_origin: 'access',
    unsupported_provider: 'provider', invalid_key: 'provider_key',
    provider_auth: 'provider_key', provider_limit: 'provider_limit', provider_timeout: 'provider_timeout',
    provider_error: 'provider', provider_connection: 'provider', provider_unavailable: 'provider',
    invalid_output: 'output', incomplete_output: 'incomplete_output', cancelled: 'stopped', interrupted: 'stopped',
    report_storage_unavailable: 'storage', report_too_large: 'storage_full', report_limit: 'storage_full',
    receipt_unavailable: 'receipt',
  };
  const key = aliases[failure?.code] || ([401, 403].includes(failure?.status) ? 'access' : 'request');
  return t('error_' + key);
}
