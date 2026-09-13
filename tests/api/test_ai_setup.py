from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(('url', 'source'), [
    ('/ai-setup.md', 'docs/ai-setup.md'),
    ('/skill.md', 'skills/port-light/SKILL.md'),
])
def test_setup_documents_are_the_shipped_sources(monkeypatch, url, source):
    monkeypatch.delenv('AUTH_USER', raising=False)
    monkeypatch.delenv('AUTH_PASSWORD', raising=False)
    response = TestClient(app).get(url)
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/markdown')
    assert response.headers['cache-control'] == 'no-cache'
    assert response.text == (ROOT / source).read_text()


@pytest.mark.parametrize('url', ['/ai-setup.md', '/skill.md'])
def test_setup_documents_follow_instance_auth(monkeypatch, url):
    monkeypatch.setenv('AUTH_USER', 'operator')
    monkeypatch.setenv('AUTH_PASSWORD', 'test-password')
    client = TestClient(app)
    assert client.get(url).status_code == 401
    assert client.get(url, auth=('operator', 'test-password')).status_code == 200
