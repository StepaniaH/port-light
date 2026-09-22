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
    assert.notEqual(catalogs[locale].workbench.comparison_meta, catalogs.en.workbench.comparison_meta, locale + ':comparison_meta');
    assert.notEqual(catalogs[locale].workbench.group_observed_members, catalogs.en.workbench.group_observed_members, locale + ':group_observed_members');
  }
});

test('all seven locales keep the AI settings surface complete and parameter-safe', () => {
  const keys = Object.keys(catalogs.en.settings).sort();
  assert.ok(keys.includes('ai_title'));
  assert.ok(keys.includes('connection_saved'));
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
  assert.equal(display.resourceText(problem.resources[0], t), '端口 3000 · TCP');
  assert.equal(t('report_link', { kind: '先查哪里', scope: '端口 3000', time: 'now' }), '先查哪里 · 端口 3000 · now');
  assert.match(display.groupText(problem.relation.group, t), /可见范围内观察到 8 个成员，其中 2 个/);
  assert.match(display.conclusionText({ conclusion: 'action_required', summary: { problem_count: 2 }, priority_queue: [problem] }, t), /本次观察发现 2 项需处理问题；先检查/);
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
  assert.match(currentFact.title, /端口 3000 · TCP/);
  assert.match(currentFact.text, /Docker 运行中映射：是/);
  assert.match(currentFact.text, /Compose 冲突：否/);
  assert.match(currentFact.text, /IPv4/);
  const factChanges = display.currentFactChanges(
    { kind: 'current', data: { status: 'configured', listening: false, docker_live: false, compose_declared: true, compose_conflict: true, compose_relation: 'declared_without_live_mapping' } },
    { kind: 'current', data: { status: 'configured', listening: false, docker_live: false, compose_declared: true, compose_conflict: false, compose_relation: 'declared_without_live_mapping' } }, t,
  );
  assert.deepEqual(factChanges, ['Compose 冲突 是 → 否']);
  const eventFact = display.factSummary({
    kind: 'event', resource: { port: 3000, protocol: 'tcp' }, observed_at: 1700000001,
    data: { kind: 'bind_scope_changed', before: { status: 'used', bind_scope: 'localhost', compose_conflict: false }, after: { status: 'used', bind_scope: 'public', compose_conflict: false } },
  }, t);
  assert.match(eventFact.text, /绑定类别变化/);
  assert.match(eventFact.text, /本机类别/);
  assert.match(eventFact.text, /公共地址类别/);
  assert.doesNotMatch(JSON.stringify({ problem, output: display.problemText(problem, t) }), /UNTRUSTED/);
  assert.doesNotMatch(JSON.stringify({ currentFact, eventFact }), /UNTRUSTED/);
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
    assert.equal(display.failureText({ status: 503, message: 'UNTRUSTED' }, t), catalogs[locale].workbench.error_request);
  }
});
