from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend import degradations, doctor, main
from backend.main import app


def _facts() -> dict:
    return {
        "version": "test",
        "settings_source": "auto",
        "settings_readonly": False,
        "data_dir_writable": True,
        "enabled_scanners": ["listen", "docker", "compose"],
        "monitor": {
            "ready": True,
            "initialized": True,
            "stale": False,
            "scan_age_seconds": 1.5,
            "sources": {"listen": "ok", "docker": "failed", "compose": "ok"},
            "compose_files_scanned": 4,
            "compose_incomplete": False,
            "compose_truncated": False,
        },
        "listen_source": "host_proc",
        "listen_trusted": True,
        "docker_library_available": True,
        "docker_transport": "socket_denied",
        "compose_root_available": True,
        "compose_root_readable": True,
        "peer_count": 2,
        "auth_required": True,
        "hidden_unlock_required": True,
        "degradations": [{
            "source": "compose",
            "scope": "/private/projects/secret/compose.yml",
            "reason": "invalid compose file",
            "ts": 1_700_000_000,
        }],
    }


def test_doctor_report_is_allowlisted_and_excludes_scopes():
    document = doctor.build_diagnostics(_facts(), generated_at="2026-09-04T00:00:00+00:00")
    text = doctor.report_text(document)
    assert document["overall"] == "attention"
    assert document["counts"]["fail"] == 1
    assert '"scope"' not in text
    assert "/private/projects/secret" not in text
    assert "1700000000" not in text
    assert "invalid compose file" in text
    assert json.loads(text) == document

    facts = _facts()
    facts["degradations"] = [{
        "source": "private-module", "reason": "failed at never-copy-this-secret",
        "scope": "/also/private",
    }]
    redacted = doctor.report_text(doctor.build_diagnostics(facts, generated_at="2026-09-04T00:00:00+00:00"))
    assert "never-copy-this-secret" not in redacted
    assert "private-module" not in redacted
    assert '"source": "unknown"' in redacted
    assert '"reason": "redacted"' in redacted


def test_doctor_api_never_exposes_environment_values_or_peer_details(tmp_path, monkeypatch):
    secret = "never-copy-this-secret"
    private_path = tmp_path / "private-projects" / secret
    private_path.mkdir(parents=True)
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMPOSE_SCAN_DIR", str(private_path))
    monkeypatch.setenv("AUTH_USER", "private-user")
    monkeypatch.setenv("AUTH_PASSWORD", secret)
    monkeypatch.setenv("DOCKER_HOST", "tcp://private-host.internal:2376")
    monkeypatch.setattr(main.hosts, "list_public_peers", lambda: [{
        "id": "secret-id", "name": "private-host", "url": "http://10.0.0.8:2100",
    }])
    monkeypatch.setattr(main._monitor, "status", lambda: _facts()["monitor"])
    degradations.reset()
    degradations.report("compose", str(private_path), "invalid compose file")
    try:
        client = TestClient(app)
        response = client.get("/api/doctor", auth=("private-user", secret))
        assert response.status_code == 200
        body = response.json()
        assert body["context"]["peer_count"] == 1
        serialized = response.text
        for private in (
            secret, str(private_path), "private-user", "private-host",
            "10.0.0.8", "2100", "2376", "DOCKER_HOST",
        ):
            assert private not in serialized
        assert json.loads(body["report"])["schema_version"] == 1

        download = client.get("/api/doctor/report", auth=("private-user", secret))
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")
        assert secret not in download.text
        assert str(private_path) not in download.text
    finally:
        degradations.reset()


def test_doctor_marks_disabled_scanners_as_information():
    facts = _facts()
    facts["enabled_scanners"] = ["listen"]
    facts["monitor"]["sources"] = {"listen": "ok", "docker": "disabled", "compose": "disabled"}
    facts["degradations"] = []
    document = doctor.build_diagnostics(facts, generated_at="2026-09-04T00:00:00+00:00")
    checks = {row["id"]: row for row in document["checks"]}
    assert checks["docker"]["status"] == "info"
    assert checks["compose"]["status"] == "info"
