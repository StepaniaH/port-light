/* Render only local language and the closed workbench contract. */
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

export function conclusionText(captureOrCode, t) {
  const capture = captureOrCode && typeof captureOrCode === 'object' ? captureOrCode : null;
  const code = capture?.conclusion || captureOrCode;
  const value = known(code, [
    'action_required', 'limited_coverage', 'no_actionable_problem', 'changes_recorded',
  ]) || 'limited_coverage';
  if (value !== 'action_required' || !capture) return t('conclusion_' + value);
  const summaryCount = Number.isInteger(capture.summary?.problem_count) ? capture.summary.problem_count : null;
  const problems = Array.isArray(capture.problems) ? capture.problems : [];
  const count = summaryCount ?? problems.length;
  const queue = Array.isArray(capture.priority_queue) ? capture.priority_queue : [];
  const first = queue[0] || problems[0];
  return t('conclusion_action_required', { count, category: problemText(first, t) });
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

function bindingText(binding, t) {
  const family = known(binding?.family, ['ipv4', 'ipv6', 'unknown']);
  const source = known(binding?.source, ['listen', 'docker', 'compose', 'manual']);
  const scope = known(binding?.scope, ['all_interfaces', 'loopback', 'specific_interface', 'unknown']);
  return t('fact_binding_item', {
    family: t('fact_family_' + (family || 'unknown')),
    source: source === 'listen' ? t('source_listening')
      : source === 'docker' ? t('source_docker_live')
        : source === 'compose' ? t('source_compose_declared')
          : source === 'manual' ? t('source_manual') : t('fact_no_value'),
    scope: scope ? t('scope_' + scope) : t('fact_no_value'),
  });
}

function eventSideText(side, t) {
  const values = [];
  if (known(side?.status, ['used', 'configured', 'free', 'unknown'])) {
    values.push(t('fact_event_state', { state: stateText(side.status, t) }));
  }
  if (Object.prototype.hasOwnProperty.call(side || {}, 'bind_scope')) {
    const scope = known(side.bind_scope, ['public', 'lan', 'link', 'localhost']);
    const binding = scope ? t('bind_' + scope) : t('fact_no_value');
    values.push(t('fact_event_binding', { binding }));
  }
  if (typeof side?.compose_conflict === 'boolean') {
    values.push(t('fact_event_conflict', { conflict: booleanText(side.compose_conflict, t) }));
  }
  if (known(side?.quality, ['complete', 'degraded'])) {
    values.push(t('fact_event_quality', { quality: t('fact_quality_' + side.quality) }));
  }
  return values.length ? values.join(' · ') : t('fact_no_value');
}

function sourceText(source, t) {
  const name = known(source?.name, ['listen', 'docker', 'compose', 'manual', 'occupancy']);
  const state = known(source?.state, ['ok', 'failed', 'disabled', 'unknown']);
  return t('fact_scan_source', {
    source: name ? t('fact_source_' + name) : t('fact_no_value'),
    state: t('fact_source_' + (state || 'unknown')),
  });
}

/** Render only the frozen, closed fact shapes supplied by the workbench API. */
export function factSummary(fact, t) {
  const kind = known(fact?.kind, ['scan', 'current', 'event']);
  const data = fact?.data && typeof fact.data === 'object' ? fact.data : {};
  if (kind === 'current') {
    const bindings = Array.isArray(data.bind) ? data.bind.slice(0, 8).map(binding => bindingText(binding, t)) : [];
    return {
      title: resourceText(fact.resource, t),
      text: [
        t('fact_current_summary', {
          state: stateText(data.status, t),
          listener: booleanText(data.listening, t),
          docker: booleanText(data.docker_live, t),
          compose: booleanText(data.compose_declared, t),
        }),
        t('fact_current_relation', { relation: composeRelationText(data.compose_relation, t) }),
        t('fact_current_conflict', { conflict: booleanText(data.compose_conflict, t) }),
        bindings.length ? t('fact_binding_summary', { bindings: bindings.join(', ') }) : t('fact_no_binding'),
      ].join(' '),
    };
  }
  if (kind === 'event') {
    const event = known(data.kind, [
      'state_changed', 'bind_scope_changed', 'configuration_mismatch',
      'observation_degraded', 'observation_recovered',
    ]);
    return {
      title: fact.resource ? resourceText(fact.resource, t) : t('fact_scan'),
      text: t('fact_event_summary', {
        kind: event ? t('event_' + event) : t('fact_unknown'),
        before: eventSideText(data.before, t),
        after: eventSideText(data.after, t),
      }),
    };
  }
  if (kind === 'scan') {
    const sources = Array.isArray(data.sources) ? data.sources.slice(0, 8).map(source => sourceText(source, t)) : [];
    return {
      title: t('fact_scan'),
      text: [
        t('fact_scan_summary', {
          ready: booleanText(data.ready, t), complete: booleanText(data.complete, t), stale: booleanText(data.stale, t),
        }),
        sources.length ? t('fact_scan_sources', { sources: sources.join(' · ') }) : '',
      ].filter(Boolean).join(' '),
    };
  }
  return { title: t('fact_unknown'), text: t('fact_unknown') };
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
    observed, conclusion: t('legacy'), limitations: [t('limit_legacy')], interpretation: null, eligibility: t('legacy'),
  };
  const state = value => t('status_' + value);
  const sources = { listen: 'listening', docker: 'docker_live', compose: 'compose_declared', manual: 'manual' };
  for (const entry of observation.current.entries) {
    const flags = ['listening', 'docker_live', 'compose_declared', 'manual', 'reservation'].filter(flag => entry[flag]);
    let text = [entry.protocol?.toUpperCase() || '—', state(entry.status), flags.length ? flags.map(flag => t('source_' + flag)).join(', ') : t('no_sources')].join(' · ');
    if (entry.compose_conflict) text += '. ' + t('conclusion_configuration_conflict');
    if (entry.compose_relation === 'declared_without_live_mapping') text += '. ' + t('conclusion_declaration_without_mapping');
    for (const binding of entry.bind || []) {
      text += ' · ' + [binding.family === 'unknown' ? state('unknown') : binding.family === 'ipv4' ? 'IPv4' : 'IPv6',
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
      observed.push({ text: text + ' · ' + t('event_scope'), evidence_ids: [event.event_id], observed_at: event.observed_at });
    }
  }
  const changes = (observation.events || []).filter(event => ['state_changed', 'bind_scope_changed', 'configuration_mismatch'].includes(event.kind));
  const conclusion = rules?.version === 'port-rules.v1' ? t('conclusion_' + rules.conclusion.code, { count: changes.length }) : t('legacy');
  const limitations = (evidence.limitation_codes || ['sources', 'declarations', 'health', 'legacy']).map(code => t('limit_' + code, { count: evidence.history_record_limit }));
  let interpretation = null;
  const selection = snapshot?.interpretation;
  if (snapshot?.interpretation_version === 'port-interpretation.v1' && selection?.schema_version === 1) {
    const text = selection.summary_kind === 'current_occupancy' ? conclusion : observed.filter(item => item.evidence_ids.every(id => selection.evidence_ids.includes(id))).map(item => item.text).join(' ');
    interpretation = {
      summary: { text: t('port') + ' ' + evidence.port + ' · ' + text, evidence_ids: selection.evidence_ids },
      hypotheses: (selection.hypotheses || []).map(item => ({ text: t('hypothesis_' + item.kind), evidence_ids: item.evidence_ids, missing_evidence: (item.missing_evidence || []).map(kind => t('missing_' + kind)) })),
      unknowns: (selection.unknowns || []).map(item => ({ text: t('unknown_' + item.kind), evidence_ids: item.evidence_ids })),
      checks: selection.checks || [],
    };
  }
  return { observed, conclusion, limitations, interpretation, eligibility: rules ? t('ai_' + rules.ai.reason) : t('legacy') };
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
    capture_expired: 'expired', report_not_found: 'report_missing',
    invalid_input: 'scope', invalid_action: 'access', invalid_origin: 'access',
    unsupported_provider: 'provider', invalid_key: 'provider_key',
    provider_auth: 'provider_key', provider_limit: 'provider_limit', provider_timeout: 'provider',
    provider_error: 'provider', provider_connection: 'provider', provider_unavailable: 'provider',
    invalid_output: 'output', incomplete_output: 'output', cancelled: 'stopped', interrupted: 'stopped',
    report_storage_unavailable: 'storage', report_too_large: 'storage_full', report_limit: 'storage_full',
  };
  const key = aliases[failure?.code] || ([401, 403].includes(failure?.status) ? 'access' : 'request');
  return t('error_' + key);
}
