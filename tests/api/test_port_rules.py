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
