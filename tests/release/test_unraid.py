from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient
import pytest

from backend import main
from backend.compose_scanner import scan_compose_tree
from backend.port_scanner import ListeningPort

TEMPLATE = Path(__file__).resolve().parents[2] / 'deploy/unraid/port-light.xml'


@pytest.fixture
def unraid_install(monkeypatch, tmp_path):
    for key in ('AUTH_USER', 'AUTH_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    template = ET.parse(TEMPLATE).getroot()
    for field in template.findall("Config[@Type='Variable']"):
        # Docker Manager passes variable entries even when their value is empty.
        value = field.text or field.attrib.get('Default', '')
        monkeypatch.setenv(field.attrib['Target'], value)
    monkeypatch.setenv('PORT_LIGHT_DATA_DIR', str(tmp_path / 'appdata'))
    monkeypatch.setenv('PORT_LIGHT_SETTINGS_SOURCE', 'auto')
    monkeypatch.setenv('COMPOSE_SCAN_DIR', str(tmp_path / 'compose'))
    monkeypatch.setattr(main, 'scan_containers', lambda: [])
    monkeypatch.setattr(main, 'scan_listening_ports', lambda **_kw: [
        ListeningPort(port=42100, protocol='tcp', ip='127.0.0.1'),
    ])
    compose = Mock(wraps=scan_compose_tree)
    monkeypatch.setattr(main, 'scan_compose_tree', compose)
    return compose


def test_default_install_works_without_compose_or_authentication(unraid_install):
    client = TestClient(main.app)
    assert client.get('/').status_code == 200
    listener = client.get('/api/ports/42100')
    assert listener.status_code == 200
    assert listener.json()['status'] == 'used'
    health = client.get('/api/health').json()
    assert health['auth_required'] is False
    assert health['occupancy']['ready'] is True
    assert health['occupancy']['sources'] == {
        'listen': 'ok', 'docker': 'ok', 'compose': 'disabled',
    }
    unraid_install.assert_not_called()


def test_compose_requires_explicit_enablement(unraid_install, tmp_path):
    compose_dir = tmp_path / 'compose'
    compose_dir.mkdir()
    (compose_dir / 'compose.yaml').write_text(
        'services:\n  web:\n    image: nginx:alpine\n    ports: ["43210:80"]\n',
    )
    client = TestClient(main.app)
    assert client.get('/api/ports/43210').json()['status'] == 'free'
    unraid_install.assert_not_called()

    response = client.put('/api/settings', json={
        'local_scanners': ['listen', 'docker', 'compose'],
    })
    assert response.status_code == 200
    assert client.get('/api/ports/43210').json()['status'] == 'configured'
    assert client.get('/api/health').json()['occupancy']['sources']['compose'] == 'ok'
    assert unraid_install.called


def test_authentication_can_be_added_and_removed(unraid_install, monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setenv('AUTH_USER', 'test-operator')
    monkeypatch.setenv('AUTH_PASSWORD', 'test-password')
    assert client.get('/').status_code == 401
    assert client.get('/', auth=('test-operator', 'test-password')).status_code == 200

    monkeypatch.setenv('AUTH_PASSWORD', '')
    response = client.get('/')
    assert response.status_code == 503
    assert response.json()['code'] == 'authentication_misconfigured'
    assert client.get('/api/health').status_code == 200

    for key in ('AUTH_USER', 'AUTH_PASSWORD'):
        monkeypatch.delenv(key)
    assert client.get('/').status_code == 200


def test_saved_settings_override_template_defaults(unraid_install):
    client = TestClient(main.app)
    saved = client.put('/api/settings', json={
        'local_scanners': ['listen', 'docker', 'compose'], 'locale': 'de',
    })
    assert saved.status_code == 200
    assert client.post('/api/manual-ports', json={
        'port': 43211, 'label': 'Saved service',
    }).status_code == 200

    main._monitor.reset()
    reopened = TestClient(main.app)
    settings = reopened.get('/api/settings').json()
    assert settings['values']['local_scanners'] == ['listen', 'docker', 'compose']
    assert settings['origins']['local_scanners'] == 'file'
    assert settings['values']['locale'] == 'de'
    assert reopened.get('/api/ports/43211').json()['manual_label'] == 'Saved service'
    # A saved, enabled Compose source must still fail if its mount is missing.
    health = reopened.get('/api/health').json()['occupancy']
    assert health['sources']['compose'] == 'failed'
    assert health['ready'] is False
