"""The bundled feature must preserve the existing application's access and uptime."""

from fastapi.testclient import TestClient

from backend import main

ACTION = {"X-Port-Light-Analysis": "1"}


def test_workbench_is_available_without_a_model_key(empty_scan):
    with TestClient(main.app) as client:
        meta = client.get("/api/meta").json()
        link = next(item for item in meta["ui_links"] if item["key"] == "port-analysis")
        assert link["path"] == "/analysis/"
        assert len(link["workspace"]["revision"]) == 64
        settings = client.get("/analysis/api/settings").json()
        assert settings["enabled"] is True
        assert settings["ai"]["configured"] is False
        assert "cloud" not in settings and "official_ai" not in settings
        capture = client.post(
            "/analysis/api/workbench/captures",
            headers=ACTION,
            json={
                "kind": "triage",
                "scope": {"kind": "single_port", "port": 8080},
                "protocol": "all",
            },
        )
        assert capture.status_code == 201, capture.text
        saved = client.post(
            "/analysis/api/workbench/reports",
            headers=ACTION,
            json={"capture_id": capture.json()["id"]},
        )
        assert saved.status_code == 201, saved.text
        assert client.get("/api/ports").status_code == 200
    with TestClient(main.app) as restarted:
        restarted.cookies.update(client.cookies)
        report = restarted.get("/analysis/api/workbench/reports/" + saved.json()["id"])
        assert report.status_code == 200
        assert (
            len(
                [
                    link
                    for link in restarted.get("/api/meta").json()["ui_links"]
                    if link["key"] == "port-analysis"
                ]
            )
            == 1
        )


def test_invalid_ai_configuration_does_not_stop_existing_dashboard(
    empty_scan, monkeypatch
):
    monkeypatch.setenv("PORT_LIGHT_BYOK_BASE_URL", "not-a-url")
    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/ports").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/analysis/api/meta").status_code == 503


def test_corrupt_report_store_preserves_bytes_and_scanning(empty_scan, tmp_path):
    store = tmp_path / "analysis" / "state" / "analysis.sqlite3"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"invalid database; must not be replaced")
    with TestClient(main.app) as client:
        assert client.get("/api/ports").status_code == 200
        assert client.get("/analysis/api/meta").status_code == 200
        assert store.read_bytes() == b"invalid database; must not be replaced"


def test_analysis_inherits_basic_auth_and_same_origin_protection(
    empty_scan, monkeypatch
):
    monkeypatch.setenv("AUTH_USER", "fixture")
    monkeypatch.setenv("AUTH_PASSWORD", "fixture-password")
    with TestClient(main.app) as client:
        assert client.get("/analysis/api/settings").status_code == 401
        client.auth = ("fixture", "fixture-password")
        assert client.get("/analysis/api/settings").status_code == 200
        result = client.put(
            "/analysis/api/settings/ai",
            headers={**ACTION, "Origin": "https://example.invalid"},
            json={"provider": "openai", "model": "fixture"},
        )
        assert result.status_code == 403
