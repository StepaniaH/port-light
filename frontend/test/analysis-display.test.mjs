import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const staticRoot = new URL('../../backend/analysis/static/', import.meta.url);
const catalogs = JSON.parse(readFileSync(new URL('messages.json', staticRoot), 'utf8'));
const source = readFileSync(new URL('display.js', staticRoot), 'utf8');
const display = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('all seven locales cover every workbench control and placeholder', () => {
  assert.deepEqual(Object.keys(catalogs), ['en', 'zh-CN', 'zh-TW', 'de', 'es', 'fr', 'ja']);
  const keys = Object.keys(catalogs.en.workbench).sort();
  for (const [locale, messages] of Object.entries(catalogs)) {
    const source = JSON.parse(readFileSync(new URL('../locales/' + locale + '.json', import.meta.url), 'utf8'));
    assert.deepEqual(messages, source.analysis.messages, locale + ':bundled messages match the UI copy source');
    assert.deepEqual(Object.keys(messages.workbench).sort(), keys, locale);
    for (const [key, value] of Object.entries(messages.workbench)) {
      assert.ok(value.trim(), locale + ':' + key);
      assert.deepEqual([...value.matchAll(/\{\w+\}/g)].map(match => match[0]).sort(), [...catalogs.en.workbench[key].matchAll(/\{\w+\}/g)].map(match => match[0]).sort(), locale + ':' + key);
    }
  }
  const html = readFileSync(new URL('index.html', staticRoot), 'utf8');
  for (const [, key] of html.matchAll(/data-analysis-i18n(?:-placeholder|-aria)?="([^"]+)"/g)) {
    assert.notEqual(display.translator(catalogs, 'en')(key), key, key);
  }
  assert.doesNotMatch(html, /<(html|body|header|script)\b|开发预览|功能预览/);
  for (const locale of ['zh-TW', 'de', 'es', 'fr', 'ja']) {
    assert.notEqual(catalogs[locale].workbench.task_triage, catalogs.en.workbench.task_triage, locale + ':task_triage');
    assert.notEqual(catalogs[locale].workbench.fact_label_sources, catalogs.en.workbench.fact_label_sources, locale + ':fact_label_sources');
    assert.notEqual(catalogs[locale].workbench.group_observed_members, catalogs.en.workbench.group_observed_members, locale + ':group_observed_members');
  }
});

test('all seven locales keep the AI settings surface complete and parameter-safe', () => {
  const keys = Object.keys(catalogs.en.settings).sort();
  assert.ok(keys.includes('ai_title'));
  assert.ok(keys.includes('connection_saved'));
  const source = readFileSync(new URL('settings.js', staticRoot), 'utf8');
  const usedKeys = [...source.matchAll(/\bt\('([^']+)'/g)].map(match => match[1]);
  for (const key of [...usedKeys, 'test', 'testing']) {
    assert.notEqual(display.translator(catalogs, 'en', 'settings')(key), key, key);
  }
  for (const [locale, messages] of Object.entries(catalogs)) {
    assert.deepEqual(Object.keys(messages.settings).sort(), keys, locale);
    for (const [key, value] of Object.entries(messages.settings)) {
      assert.equal(typeof value, 'string', locale + ':' + key);
      assert.ok(value.trim(), locale + ':' + key);
      assert.deepEqual(
        [...value.matchAll(/\{\w+\}/g)].map(match => match[0]).sort(),
        [...catalogs.en.settings[key].matchAll(/\{\w+\}/g)].map(match => match[0]).sort(),
        locale + ':' + key,
      );
    }
  }
});

test('closed workbench terms render local labels and never use model prose', () => {
  const t = display.translator(catalogs, 'zh-CN');
  const problem = {
    kind: 'project_declaration_without_live_mapping', priority: 3, related_ports: [3000, 3001],
    resources: [{ port: 3000, protocol: 'tcp' }],
    relation: { kind: 'same_compose_project', group: { observed_members: 8, affected_members: 2, complete: false } },
    first_check: { action: 'inspect_mapping', purpose: 'distinguish_live_mapping' },
    confirm: { action: 'inspect_port', purpose: 'verify_missing_live_mapping' },
    evidence: [{ id: 'event-1', observed_at: 1700000000 }],
  };
  assert.equal(display.problemText(problem, t), catalogs['zh-CN'].workbench.problem_project_declaration_without_live_mapping);
  assert.equal(display.resourceText(problem.resources[0], t), '端口 3000（TCP）');
  assert.equal(t('report_link', { kind: '当前状态', scope: '端口 3000', time: 'now' }), '当前状态: 端口 3000, now');
  assert.match(display.groupText(problem.relation.group, t), /同组 8 个端口中，2 个/);
  assert.equal(display.conclusionText({ conclusion: 'action_required', summary: { problem_count: 2 }, priority_queue: [problem] }, t), '发现 2 项问题。');
  assert.equal(display.actionText(problem.first_check.action, t), catalogs['zh-CN'].workbench.action_inspect_mapping);
  assert.equal(display.purposeText(problem.first_check.purpose, t), catalogs['zh-CN'].workbench.purpose_distinguish_live_mapping);
  assert.equal(display.comparisonReasonText('scan_stale', t), catalogs['zh-CN'].workbench.comparison_reason_scan_stale);
  assert.equal(display.comparisonReasonText('no_new_observation', t), catalogs['zh-CN'].workbench.comparison_reason_no_new_observation);
  assert.equal(display.limitationText('runtime_sources_unobserved', t), catalogs['zh-CN'].workbench.coverage_limit_runtime_sources_unobserved);
  const currentFact = display.factSummary({
    kind: 'current', resource: { port: 3000, protocol: 'tcp' }, observed_at: 1700000000,
    data: { status: 'used', listening: true, docker_live: true, compose_declared: true,
      compose_relation: 'declared_and_live', compose_conflict: false,
      bind: [{ family: 'ipv4', source: 'listen', scope: 'all_interfaces' }] },
  }, t);
  assert.equal(currentFact.title, '端口 3000（TCP）');
  assert.ok(currentFact.sections[0].fields.some(field => field.label === 'Docker 映射' && field.value === '是'));
  assert.ok(currentFact.sections[0].fields.some(field => field.label === 'Compose 冲突' && field.value === '否'));
  assert.equal(currentFact.sections[1].fields[0].label, 'IPv4');
  const factChanges = display.currentFactChanges(
    { kind: 'current', data: { status: 'configured', listening: false, docker_live: false, compose_declared: true, compose_conflict: true, compose_relation: 'declared_without_live_mapping' } },
    { kind: 'current', data: { status: 'configured', listening: false, docker_live: false, compose_declared: true, compose_conflict: false, compose_relation: 'declared_without_live_mapping' } }, t,
  );
  assert.deepEqual(factChanges, ['Compose 冲突 是 → 否']);
  const eventFact = display.factSummary({
    kind: 'event', resource: { port: 3000, protocol: 'tcp' }, observed_at: 1700000001,
    data: { kind: 'bind_scope_changed', before: { status: 'used', bind_scope: 'localhost', compose_conflict: false }, after: { status: 'used', bind_scope: 'public', compose_conflict: false } },
  }, t);
  assert.equal(eventFact.sections[0].fields[0].value, '绑定类别变化');
  assert.equal(eventFact.sections[1].title, '变化前');
  assert.ok(eventFact.sections[1].fields.some(field => field.value === '本机类别'));
  assert.equal(eventFact.sections[2].title, '变化后');
  assert.ok(eventFact.sections[2].fields.some(field => field.value === '公共地址类别'));
  assert.doesNotMatch(JSON.stringify({ problem, output: display.problemText(problem, t) }), /UNTRUSTED/);
  assert.doesNotMatch(JSON.stringify({ currentFact, eventFact }), /UNTRUSTED/);
});

test('capture summary distinguishes current port state, missing data and recorded changes', () => {
  const t = display.translator(catalogs, 'zh-CN');
  const current = { source_kind: 'triage', conclusion: 'no_actionable_problem', summary: {
    listening_count: 1, mapped_count: 0, declared_count: 0,
  } };
  assert.deepEqual(display.observationCounts(current, t), [
    { label: '主机监听', value: 1 }, { label: 'Docker 映射', value: 0 }, { label: 'Compose 声明', value: 0 },
  ]);
  assert.match(display.conclusionText(current, t), /未发现配置冲突或映射不一致/);
  assert.equal(display.conclusionText({ ...current, data_status: 'empty' }, t), '没有采集到端口数据。');
  const changes = { ...current, source_kind: 'changes' };
  assert.deepEqual(display.observationCounts(changes, t)[0], { label: '变化记录', value: 0 });
  assert.match(display.conclusionText(changes, t), /没有可查看的端口变化记录/);
  assert.deepEqual(display.observationCounts({ facts: {
    mapped: { kind: 'current', data: { docker_live: true } },
    declared: { kind: 'current', data: { compose_declared: true } },
  } }, t).map(field => field.value), [0, 1, 1]);
});

test('port ranges preserve every valid member and keep TCP and UDP separate', () => {
  const tcp = port => ({ port, protocol: 'tcp' });
  assert.deepEqual(display.resourceGroups([tcp(20002), tcp(20000), tcp(20001), tcp(20002),
    tcp(20003), tcp(20005), { port: 20001, protocol: 'udp' }, tcp(0), tcp(65536), tcp(2.5),
    { port: '20004', protocol: 'tcp' }, { port: 20004, protocol: 'invalid' }, null]), [
    { protocol: 'tcp', ports: [20000, 20001, 20002, 20003] },
    { protocol: 'tcp', ports: [20005] }, { protocol: 'udp', ports: [20001] },
  ]);
  const ports = Array.from({ length: 256 }, (_, index) => 20000 + index);
  assert.deepEqual(display.resourceGroups(ports.map(tcp)), [{ protocol: 'tcp', ports }]);
});

test('scan fields distinguish missing values from negative states in every locale', () => {
  for (const locale of Object.keys(catalogs)) {
    const t = display.translator(catalogs, locale);
    const summary = display.factSummary({ kind: 'scan', data: { ready: false, complete: true,
      sources: [{ name: 'docker', state: 'disabled' }, { name: 'listen', state: 'ok' }] } }, t);
    assert.deepEqual(summary.sections[0].fields.map(field => field.value), [t('fact_not_ready'), t('fact_complete'), t('fact_no_value')]);
    assert.deepEqual(summary.sections[1].fields.map(field => field.value), [t('fact_source_disabled'), t('fact_source_ok')]);
  }
});

test('checked conditions match again after reverting a draft and ignore inactive history controls', () => {
  const request = { kind: 'triage', scope: { kind: 'selected_ports', ports: [8080, 8081] }, protocol: 'tcp', history_hours: 24 };
  const capture = { kind: 'recheck', source_kind: 'triage', scope_requested: request };
  assert.equal(display.matchesCaptureRequest(capture, { ...request, history_hours: 1 }), true);
  assert.equal(display.matchesCaptureRequest(capture, { ...request, scope: { kind: 'selected_ports', ports: [8081, 8080] } }), true);
  assert.equal(display.matchesCaptureRequest(capture, { ...request, protocol: 'udp' }), false);
  assert.equal(display.matchesCaptureRequest(capture, { ...request, kind: 'changes' }), false);
  assert.equal(display.matchesCaptureRequest(capture, { ...request, scope: { kind: 'selected_ports', ports: [8080] } }), false);
  assert.equal(display.matchesCaptureRequest(capture, request), true);
  const changes = { ...request, kind: 'changes' };
  assert.equal(display.matchesCaptureRequest({ ...capture, source_kind: 'changes' }, changes), true);
  assert.equal(display.matchesCaptureRequest({ ...capture, source_kind: 'changes' }, { ...changes, history_hours: 6 }), false);
});

test('mapping findings distinguish affected resources from other project ports using frozen facts', () => {
  const tcp = { port: 8080, protocol: 'tcp' };
  const udp = { port: 8080, protocol: 'udp' };
  const missing = { port: 8081, protocol: 'tcp' };
  const problem = { kind: 'project_declaration_without_live_mapping', resources: [tcp, udp, missing] };
  const capture = { facts: {
    tcp: { kind: 'current', resource: tcp, data: { compose_relation: 'declared_and_live' } },
    udp: { kind: 'current', resource: udp, data: { compose_relation: 'declared_without_live_mapping' } },
    missing: { kind: 'current', resource: missing, data: { compose_relation: 'declared_without_live_mapping' } },
  } };
  assert.deepEqual(display.problemResources(problem, capture), { affected: [udp, missing], related: [tcp] });
  assert.deepEqual(display.problemResources(problem, { facts: {} }), { affected: [], related: [tcp, udp, missing] });
});

test('identical visible advice is shown once without merging different protocols, checks or evidence', () => {
  const problem = { id: 'first', kind: 'overlapping_compose_bindings', resources: [{ port: 8080, protocol: 'tcp' }] };
  const duplicate = { ...problem, id: 'second' };
  const udp = { ...problem, id: 'udp', resources: [{ port: 8080, protocol: 'udp' }] };
  const item = { problem_id: 'first', action: 'inspect_compose', relation: 'overlapping_bind', evidence_ids: ['scan', 'current:8080:tcp'] };
  const repeated = { ...item, problem_id: 'second', evidence_ids: [...item.evidence_ids].reverse() };
  const otherProtocol = { ...item, problem_id: 'udp' };
  const otherCheck = { ...item, action: 'inspect_port' };
  const otherEvidence = { ...item, evidence_ids: ['current:8080:tcp'] };
  assert.deepEqual(display.distinctRecommendations([item, repeated, otherProtocol, otherCheck, otherEvidence], {
    problems: [problem, duplicate, udp],
  }), [item, otherProtocol, otherCheck, otherEvidence]);
});

test('valid legacy port-interpretation reports retain structured templates only', () => {
  const t = display.translator(catalogs, 'en');
  const snapshot = {
    evidence: { port: 8080, question_type: 'recorded_changes', history_record_limit: 5,
      limitation_codes: ['sources', 'truncated'], observation: { captured_at: 1700000000,
        current: { entries: [{ protocol: 'tcp', status: 'configured', compose_declared: true, compose_conflict: false,
          compose_relation: 'declared_without_live_mapping', bind: [{ family: 'ipv6', scope: 'loopback', source: 'compose' }] }] },
        events: [{ event_id: 'event-1', kind: 'state_changed', observed_at: 1700000001, before: { status: 'configured' }, after: { status: 'used' } }],
      } },
    baseline: { version: 'port-rules.v1', conclusion: { code: 'declaration_without_mapping' }, ai: { reason: 'available' } },
    interpretation_version: 'port-interpretation.v1',
    interpretation: { schema_version: 1, summary_kind: 'recorded_changes', evidence_ids: ['event-1'],
      hypotheses: [{ kind: 'declaration_runtime_gap', evidence_ids: ['current'], missing_evidence: ['runtime_configuration'], text: 'UNTRUSTED' }],
      unknowns: [{ kind: 'application_health', evidence_ids: ['current'] }], checks: [{ action: 'mapping', evidence_ids: ['current'] }] },
    interpretation_view: { version: 'port-interpretation-zh.v1', summary: { text: 'UNTRUSTED' } },
  };
  const output = display.presentation(snapshot, t);
  assert.ok(output.interpretation);
  assert.match(output.interpretation.summary.text, /Declared → In use/);
  assert.equal(output.interpretation.hypotheses[0].text, catalogs.en.hypothesis_declaration_runtime_gap);
  assert.doesNotMatch(JSON.stringify(output), /UNTRUSTED/);
});

test('stable failures remain localized without echoing upstream messages', () => {
  for (const locale of Object.keys(catalogs)) {
    const t = display.translator(catalogs, locale);
    assert.equal(display.failureText({ code: 'scope_too_large', message: 'UNTRUSTED' }, t), catalogs[locale].workbench.error_scope_too_large);
    assert.equal(display.failureText({ code: 'access_restricted', message: 'UNTRUSTED' }, t), catalogs[locale].workbench.error_access);
    assert.equal(display.failureText({ code: 'not_found', status: 404 }, t), catalogs[locale].error_expired);
    assert.equal(display.failureText({ code: 'incomplete_output' }, t), catalogs[locale].workbench.error_incomplete_output);
    assert.notEqual(display.failureText({ code: 'incomplete_output' }, t), display.failureText({ code: 'invalid_output' }, t));
    assert.equal(display.failureText({ status: 503, message: 'UNTRUSTED' }, t), catalogs[locale].workbench.error_request);
  }
});
