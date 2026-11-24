import secrets
import json

from fastapi.testclient import TestClient

from backend import main, port_store
from backend.compose_scanner import ComposePort, ComposeScan


def rule(name="development", start=20000, end=20010, projects=None):
    return {"name": name, "start": start, "end": end, "projects": projects or []}


def test_rule_allocation_retries_after_rule_edit_without_reallocating(empty_scan):
    client = TestClient(main.app)
    assert client.put('/api/port-rules', json={"rules": [rule()]}).status_code == 200
    headers = {'Idempotency-Key': secrets.token_urlsafe(32)}
    body = {"rule": "development", "count": 2, "ttl": 60}
    first = client.post('/api/reservations', json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['ports'] == [20000, 20001]
    client.put('/api/port-rules', json={"rules": [rule(start=25000, end=25010)]})
    assert client.post('/api/reservations', json=body, headers=headers).json() == first.json()
    rows = client.get('/api/reservations').json()['reservations']
    assert len(rows) == 2
    assert all('token' not in row and 'reservation_hash' not in row for row in rows)


def test_rule_validation_and_bounded_suggestions(empty_scan):
    client = TestClient(main.app)
    for rules in ([rule(end=19999)], [rule(), rule()], [rule(projects=['wiki']), rule('infra', projects=['wiki'])]):
        assert client.put('/api/port-rules', json={"rules": rules}).status_code == 422
    client.put('/api/port-rules', json={"rules": [rule()]})
    assert client.get('/api/ports/suggest?rule=missing').status_code == 400
    assert client.get('/api/ports/suggest?rule=development&start=19000').status_code == 400
    assert client.get('/api/ports/suggest?rule=development&start=20009&count=2').json()['ports'] == [20009, 20010]
    assert port_store.get_manual_ports() == []


def test_rules_only_mark_assigned_projects_and_follow_visibility(empty_scan, monkeypatch):
    scan = ComposeScan(ports=[ComposePort(port=8080, compose_file='wiki/compose.yaml', project_dir='wiki', project_name='wiki', service_name='web', container_port=80)])
    monkeypatch.setattr(main, 'scan_compose_tree', lambda *a, **kw: scan)
    client = TestClient(main.app)
    client.put('/api/port-rules', json={"rules": [rule(projects=['wiki'])]})
    response = client.get('/api/ports/8080')
    assert response.json()['rule_violations'][0]['rule'] == 'development'
    old_etag = client.get('/api/ports').headers['etag']
    client.put('/api/port-rules', json={"rules": [rule(projects=[])]})
    assert 'rule_violations' not in client.get('/api/ports/8080').json()
    assert client.get('/api/ports', headers={'If-None-Match': old_etag}).status_code == 200
    port_store.add_hidden_port(8080)
    monkeypatch.setenv('HIDDEN_UNLOCK_PASSWORD', 'test-password')
    main._monitor.state_changed()
    assert client.get('/api/ports?range_end=65535').json()['ports'] == []


def test_rules_respect_readonly_settings_and_agent_gate(empty_scan, monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setenv('SETTINGS_READONLY', 'true')
    assert client.put('/api/port-rules', json={"rules": [rule()]}).status_code == 403
    monkeypatch.delenv('SETTINGS_READONLY')
    client.put('/api/port-rules', json={"rules": [rule()]})
    monkeypatch.setenv('AGENT_TOKEN', 'secret')
    assert client.get('/api/ports/suggest?rule=development').status_code == 403
    assert client.get('/api/ports/suggest?rule=development', headers={'X-Agent-Token': 'secret'}).status_code == 200


def test_corrupt_rules_leave_store_untouched(empty_scan, tmp_path):
    path = tmp_path / 'port_light.json'
    text = json.dumps({'port_rules': [{'name': 'bad'}]})
    path.write_text(text)
    response = TestClient(main.app).get('/api/port-rules')
    assert response.status_code == 503
    assert path.read_text() == text


def test_reservation_list_hides_locked_entries(empty_scan, monkeypatch):
    client = TestClient(main.app)
    body = client.post('/api/reservations', json={'start': 24000, 'end': 24000}, headers={'Idempotency-Key': secrets.token_urlsafe(32)}).json()
    port_store.add_hidden_port(24000)
    monkeypatch.setenv('HIDDEN_UNLOCK_PASSWORD', 'test-password')
    assert client.get('/api/reservations').json()['reservations'] == []
    assert client.get('/api/reservations', headers={'X-Hidden-Unlock': 'test-password'}).json()['reservations'][0]['port'] == 24000
    token = body['reservations'][0]['token']
    assert client.delete('/api/reservations/24000', headers={'X-Reservation-Token': token}).status_code == 200


def test_rules_match_project_dir_before_project_name_and_reject_traversal(empty_scan, monkeypatch):
    scan = ComposeScan(ports=[
        ComposePort(
            port=30050, compose_file='prod/wiki/compose.yaml',
            project_dir='prod/wiki', project_name='wiki', service_name='web', container_port=80,
        ),
        ComposePort(
            port=30060, compose_file='staging/wiki/compose.yaml',
            project_dir='staging/wiki', project_name='wiki', service_name='web', container_port=80,
        ),
    ])
    monkeypatch.setattr(main, 'scan_compose_tree', lambda *a, **kw: scan)
    client = TestClient(main.app)

    for bad_ref in ('../wiki', 'prod/../wiki', '..', '.', 'prod\\wiki', '/prod/wiki', 'prod/wiki/', 'prod//wiki'):
        assert client.put('/api/port-rules', json={'rules': [rule(projects=[bad_ref])]}).status_code == 422

    saved = client.put('/api/port-rules', json={'rules': [
        rule(name='prod-web', start=30000, end=30099, projects=['prod/wiki']),
        rule(name='default-wiki', start=31000, end=31099, projects=['wiki']),
    ]})
    assert saved.status_code == 200, saved.text

    prod_row = client.get('/api/ports/30050').json()
    assert 'rule_violations' not in prod_row

    staging_row = client.get('/api/ports/30060').json()
    assert staging_row['rule_violations'] == [{
        'rule': 'default-wiki',
        'project': 'wiki',
        'service': 'web',
        'start': 31000,
        'end': 31099,
    }]

    distinct = client.put('/api/port-rules', json={'rules': [
        rule(name='prod-web', start=32000, end=32099, projects=['prod/wiki']),
        rule(name='staging-web', start=30000, end=30099, projects=['staging/wiki']),
    ]})
    assert distinct.status_code == 200
    assert client.get('/api/ports/30050').json()['rule_violations'][0]['project'] == 'prod/wiki'
    assert 'rule_violations' not in client.get('/api/ports/30060').json()
    assert client.put('/api/port-rules', json={'rules': [rule(projects=['v1..v2'])]}).status_code == 200


def test_legacy_port_rules_survive_upgrade_and_allow_api_repair(empty_scan, tmp_path):
    from backend import degradations

    scan = ComposeScan(ports=[
        ComposePort(
            port=30050, compose_file='apps/wiki/compose.yaml',
            project_dir='apps/wiki', project_name='wiki', service_name='web', container_port=80,
        ),
    ])
    import backend.main as main_mod
    original_scan = main_mod.scan_compose_tree
    main_mod.scan_compose_tree = lambda *a, **kw: scan
    try:
        path = tmp_path / 'port_light.json'
        path.write_text(json.dumps({
            'port_rules': [
                rule(name='legacy-rel', start=30000, end=30010, projects=['./apps/wiki', 'apps/blog/']),
                rule(name='legacy-abs', start=31000, end=31010, projects=['/srv/media', 'win\\app', '../legacy']),
            ],
        }))
        client = TestClient(main_mod.app)
        assert client.get('/api/ports').status_code == 200
        assert client.get('/api/settings').status_code == 200
        rules_res = client.get('/api/port-rules')
        assert rules_res.status_code == 200
        assert rules_res.json()['rules'] == [
            {'name': 'legacy-rel', 'start': 30000, 'end': 30010, 'projects': ['apps/wiki', 'apps/blog']},
            {'name': 'legacy-abs', 'start': 31000, 'end': 31010, 'projects': ['/srv/media', 'win\\app', '../legacy']},
        ]
        assert client.get('/api/ports/30050').json()['rule_violations'][0]['rule'] == 'legacy-rel'

        # Cross-rule collision during legacy normalization keeps the conflicting raw form safely.
        path.write_text(json.dumps({
            'port_rules': [
                rule(name='r1', start=30000, end=30010, projects=['./apps/wiki']),
                rule(name='r2', start=31000, end=31010, projects=['apps/wiki']),
            ],
        }))
        port_store._FILE_MEMO.clear()
        colliding = client.get('/api/port-rules')
        assert colliding.status_code == 200
        assert colliding.json()['rules'][0]['projects'] == ['./apps/wiki']

        # Order-independent alias normalization: two dirty rules claiming the same canonical
        # project keep their raw forms in either rule order.
        pair = [
            rule(name='r1', start=30000, end=30010, projects=['./apps/wiki']),
            rule(name='r2', start=31000, end=31010, projects=['apps/wiki/']),
        ]
        for ordered in (pair, list(reversed(pair))):
            path.write_text(json.dumps({'port_rules': ordered}))
            port_store._FILE_MEMO.clear()
            res = client.get('/api/port-rules')
            assert res.status_code == 200
            by_name = {item['name']: item['projects'] for item in res.json()['rules']}
            assert by_name == {'r1': ['./apps/wiki'], 'r2': ['apps/wiki/']}

        # Legacy project names with tabs load with 200 rather than 503.
        path.write_text(json.dumps({
            'port_rules': [rule(name='tabbed', start=30000, end=30010, projects=['app\tmedia'])],
        }))
        port_store._FILE_MEMO.clear()
        tabbed = client.get('/api/port-rules')
        assert tabbed.status_code == 200
        assert tabbed.json()['rules'][0]['projects'] == ['app\tmedia']
        assert client.put('/api/port-rules', json={
            'rules': [rule(name='tabbed', start=30000, end=30010, projects=['app-media'])],
        }).status_code == 200

        # Sequential UI repair when 2+ non-normalizable legacy rules exist: fixing or deleting
        # one rule preserves untouched legacy rules while new invalid paths still return 422.
        path.write_text(json.dumps({
            'port_rules': [
                rule(name='r1', start=30000, end=30010, projects=['/srv/a']),
                rule(name='r2', start=31000, end=31010, projects=['win\\b']),
            ],
        }))
        port_store._FILE_MEMO.clear()
        assert client.put('/api/port-rules', json={
            'rules': [
                rule(name='r1', start=30000, end=30010, projects=['apps/a']),
                rule(name='r2', start=31000, end=31010, projects=['win\\b']),
            ],
        }).status_code == 200
        assert client.put('/api/port-rules', json={
            'rules': [
                rule(name='r1', start=30000, end=30010, projects=['/srv/new']),
                rule(name='r2', start=31000, end=31010, projects=['win\\b']),
            ],
        }).status_code == 422
        assert client.put('/api/port-rules', json={
            'rules': [
                rule(name='r1', start=30000, end=30010, projects=['win\\b']),
                rule(name='r2', start=31000, end=31010, projects=['apps/b']),
            ],
        }).status_code == 422
        assert client.put('/api/port-rules', json={
            'rules': [rule(name='r2', start=31000, end=31010, projects=['win\\b'])],
        }).status_code == 200

        cleared = client.put('/api/port-rules', json={'rules': []})
        assert cleared.status_code == 200
        assert client.get('/api/port-rules').json()['rules'] == []

        # Truly corrupt structure still fails closed and records a store degradation.
        degradations.reset()
        corrupt = json.dumps({'port_rules': [{'name': 'bad'}]})
        path.write_text(corrupt)
        port_store._FILE_MEMO.clear()
        assert client.get('/api/port-rules').status_code == 503
        assert path.read_text() == corrupt
        assert any(d['source'] == 'store' for d in degradations.recent())
    finally:
        main_mod.scan_compose_tree = original_scan
