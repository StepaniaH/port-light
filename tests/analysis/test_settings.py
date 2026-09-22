import os
import stat
import time
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from tests.analysis.observation_fixture import observation
from backend.analysis.evidence import AnalysisError
from backend.analysis.interpretation import demo_selection
from backend.analysis.module import create_module
from backend.analysis.settings import (
    MAX_PROFILE_BYTES,
    PROFILE_FILE,
    AIConnectionInput,
    BYOKStore,
)
from pydantic import ValidationError

ACTION = {"X-Port-Light-Analysis": "1"}
SAVED_KEY = "fixture-saved-key-not-a-real-secret"
REPLACEMENT_KEY = "fixture-replacement-key-not-a-real-secret"


def host(data_dir, state, *, settings_reader=...):
    @asynccontextmanager
    async def lifespan(_app):
        async with module.router.lifespan_context(module):
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/meta")
    def metadata():
        return {"capabilities": {"port_observation": 1}}

    @app.get("/api/observations/ports/{port}")
    def port(port: int, request: Request):
        return observation(port)

    context = {
        "module_api": 1,
        "core_app": app,
        "core_version": "0.8.4",
        "data_dir": data_dir,
    }
    if settings_reader is not ...:
        context["settings_readonly"] = settings_reader
    module = create_module(context)
    app.mount("/analysis", module)
    return app


def save_profile(client, *, provider="openai", model="fixture-model", key=SAVED_KEY, headers=None):
    request_headers = {**ACTION, **(headers or {})}
    if key is not None:
        request_headers["X-Port-Light-Model-Key"] = key
    return client.put(
        "/analysis/api/settings/ai",
        json={"provider": provider, "model": model},
        headers=request_headers,
    )


def preview(client):
    response = client.post(
        "/analysis/api/analysis/previews", json={"port": 8080}, headers=ACTION
    )
    assert response.status_code == 201, response.text
    return "/analysis/api/analysis/" + response.json()["id"]


def completed(client, path):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200
        if response.json()["status"] != "running":
            return response.json()
        time.sleep(0.01)
    raise AssertionError("Synthetic provider did not finish")


def test_bundled_settings_descriptor(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_ANALYSIS_DEMO", raising=False)
    state = {
        "capabilities": {"byok_port_analysis": True},
    }
    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as client:
        module = next(
            route.app for route in client.app.routes if getattr(route, "path", None) == "/analysis"
        )
        assert module.state.ui_link["settings"] == {
            "api": 1,
            "entry": "/analysis/assets/settings.js",
            "stylesheet": "/analysis/assets/settings.css",
        }
        settings = client.get("/analysis/api/settings")
        assert settings.status_code == 200
        assert settings.json()["capabilities"] == {
            "byok_port_analysis": True,
        }


def test_settings_document_persists_one_profile_without_disclosing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_ANALYSIS_DEMO", raising=False)
    state = {
        "capabilities": {"byok_port_analysis": True},
    }
    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as client:
        initial = client.get("/analysis/api/settings")
        assert initial.status_code == 200
        assert initial.json() == {
            "schema_version": 1,
            "analysis_mode": "byok",
            "readonly": False,
            "enabled": True,
            "capabilities": {"byok_port_analysis": True},
            "providers": initial.json()["providers"],
            "ai": {
                "provider": None,
                "model": None,
                "configured": False,
                "key_saved": False,
                "revision": None,
            },
        }
        assert {item["id"] for item in initial.json()["providers"]} == {
            "openai",
            "deepseek",
            "opencode-go",
        }
        assert all(
            set(item) in ({"id", "name"}, {"id", "name", "notice"})
            and isinstance(item["id"], str)
            and isinstance(item["name"], str)
            for item in initial.json()["providers"]
        )
        legacy_meta = client.get("/analysis/api/meta").json()
        assert legacy_meta["capabilities"] == {
            "byok_port_analysis": 1,
            "troubleshooting_workbench": 1,
        }
        assert legacy_meta["enabled"] == {"byok_port_analysis": True}

        saved = save_profile(client)
        assert saved.status_code == 200
        document = saved.json()
        assert set(document["ai"]) == {
            "provider",
            "model",
            "configured",
            "key_saved",
            "revision",
        }
        assert document["ai"].items() >= {
            "provider": "openai",
            "model": "fixture-model",
            "configured": True,
            "key_saved": True,
        }.items()
        first_revision = document["ai"]["revision"]
        assert isinstance(first_revision, str) and len(first_revision) >= 32
        assert SAVED_KEY not in saved.text and SAVED_KEY not in client.get(
            "/analysis/api/settings"
        ).text

        profile_file = tmp_path / "byok" / PROFILE_FILE
        assert stat.S_IMODE((tmp_path / "byok").stat().st_mode) == 0o700
        assert stat.S_IMODE(profile_file.stat().st_mode) == 0o600
        restarted = BYOKStore(tmp_path).load()
        assert restarted is not None
        assert (restarted.provider, restarted.model, restarted.key) == (
            "openai",
            "fixture-model",
            SAVED_KEY,
        )

        retained = save_profile(client, model="next-model", key=None)
        assert retained.status_code == 200
        assert retained.json()["ai"]["revision"] != first_revision
        assert BYOKStore(tmp_path).load().key == SAVED_KEY
        missing_key = save_profile(client, provider="deepseek", key=None)
        assert missing_key.status_code == 422
        assert missing_key.json()["error"]["code"] == "key_required"
        changed = save_profile(
            client, provider="deepseek", model="deepseek-fixture", key=REPLACEMENT_KEY
        )
        assert changed.status_code == 200
        assert BYOKStore(tmp_path).load().key == REPLACEMENT_KEY
        assert SAVED_KEY not in changed.text and REPLACEMENT_KEY not in changed.text

    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as restarted_client:
        restored = restarted_client.get("/analysis/api/settings")
        assert restored.status_code == 200
        assert restored.json()["ai"] == {
            "provider": "deepseek",
            "model": "deepseek-fixture",
            "configured": True,
            "key_saved": True,
            "revision": changed.json()["ai"]["revision"],
        }
        assert SAVED_KEY not in restored.text and REPLACEMENT_KEY not in restored.text


def test_settings_writes_require_action_and_origin_and_allow_clear(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_ANALYSIS_DEMO", raising=False)
    state = {"capabilities": {"byok_port_analysis": False}}
    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as client:
        missing_action = client.put(
            "/analysis/api/settings/ai",
            json={"provider": "openai", "model": "fixture-model"},
        )
        assert missing_action.status_code == 403
        cross_origin = save_profile(client, headers={"Origin": "https://example.invalid"})
        assert cross_origin.status_code == 403
        assert save_profile(client).status_code == 200

        assert save_profile(client).status_code == 200
        invalid_delete = client.request(
            "DELETE", "/analysis/api/settings/ai", content=b"{}", headers=ACTION
        )
        assert invalid_delete.status_code == 422

        cleared = client.delete("/analysis/api/settings/ai", headers=ACTION)
        assert cleared.status_code == 200
        assert cleared.json()["ai"] == {
            "provider": None,
            "model": None,
            "configured": False,
            "key_saved": False,
            "revision": None,
        }
        assert not (tmp_path / "byok" / PROFILE_FILE).exists()


def test_settings_readonly_fails_closed_without_an_exact_false_host_value(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    state = {"capabilities": {"byok_port_analysis": True}}

    def broken():
        raise RuntimeError("fixture")

    readers = [
        ...,  # Missing from the host context.
        lambda: True,
        lambda: None,
        lambda: 0,
        lambda: 1,
        lambda: "false",
        broken,
    ]
    for index, reader in enumerate(readers):
        with TestClient(host(tmp_path / str(index), state, settings_reader=reader)) as client:
            assert client.get("/analysis/api/settings").json()["readonly"] is True
            denied = save_profile(client, provider="demo", model="synthetic-demo", key="")
            assert denied.status_code == 403

    with TestClient(host(tmp_path / "writable", state, settings_reader=lambda: False)) as client:
        assert client.get("/analysis/api/settings").json()["readonly"] is False
        assert save_profile(client, provider="demo", model="synthetic-demo", key="").status_code == 200


def test_saved_connection_uses_exact_revision_and_legacy_key_still_works(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT_LIGHT_ANALYSIS_DEMO", raising=False)
    calls = []

    async def gateway(provider, model, key, evidence, **_kwargs):
        calls.append((provider, model, key))
        return demo_selection(evidence), {}

    monkeypatch.setattr("backend.analysis.routes.ChatGateway", lambda **_kwargs: gateway)
    state = {"capabilities": {"byok_port_analysis": True}}
    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as client:
        saved = save_profile(client)
        revision = saved.json()["ai"]["revision"]

        saved_path = preview(client)
        started = client.post(
            saved_path + "/start",
            json={"connection": "saved", "config_revision": revision, "confirmed": True},
            headers=ACTION,
        )
        assert started.status_code == 202
        assert completed(client, saved_path)["status"] == "completed"
        assert calls == [("openai", "fixture-model", SAVED_KEY)]
        assert SAVED_KEY not in started.text

        stale_path = preview(client)
        assert save_profile(client, model="changed-model", key=None).status_code == 200
        stale = client.post(
            stale_path + "/start",
            json={"connection": "saved", "config_revision": revision, "confirmed": True},
            headers=ACTION,
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "configuration_changed"
        assert calls == [("openai", "fixture-model", SAVED_KEY)]

        legacy_path = preview(client)
        legacy = client.post(
            legacy_path + "/start",
            json={"provider": "openai", "model": "legacy-model", "confirmed": True},
            headers={**ACTION, "X-Port-Light-Model-Key": REPLACEMENT_KEY},
        )
        assert legacy.status_code == 202
        assert completed(client, legacy_path)["status"] == "completed"
        assert calls[-1] == ("openai", "legacy-model", REPLACEMENT_KEY)

        for malformed in (
            {"provider": None, "model": "fixture-model", "confirmed": True},
            {"provider": "openai", "model": None, "confirmed": True},
            {"connection": "saved", "config_revision": None, "confirmed": True},
        ):
            rejected = client.post(legacy_path + "/start", json=malformed, headers=ACTION)
            assert rejected.status_code == 422
        result = client.get(legacy_path)
        assert SAVED_KEY not in result.text and REPLACEMENT_KEY not in result.text


@pytest.mark.parametrize(
    "document",
    [
        {"provider": None, "model": "fixture-model", "confirmed": True},
        {"provider": "openai", "model": None, "confirmed": True},
        {"connection": "saved", "config_revision": None, "confirmed": True},
    ],
)
def test_connection_input_rejects_explicit_nulls(document):
    with pytest.raises(ValidationError):
        AIConnectionInput.model_validate(document)


@pytest.mark.parametrize("kind", ["corrupt", "oversized", "symlink", "hardlink", "fifo"])
def test_profile_store_rejects_corrupt_links_and_special_files_without_blocking(kind, tmp_path):
    store = BYOKStore(tmp_path)
    store.root.mkdir(mode=0o700)
    profile = store.root / PROFILE_FILE
    if kind == "corrupt":
        profile.write_text("{not-json")
        profile.chmod(0o600)
    elif kind == "oversized":
        profile.write_bytes(b"x" * (MAX_PROFILE_BYTES + 1))
        profile.chmod(0o600)
    elif kind == "symlink":
        target = tmp_path / "outside-profile"
        target.write_text("fixture")
        profile.symlink_to(target)
    elif kind == "hardlink":
        store.save(
            provider="openai",
            model="fixture-model",
            supplied_key=SAVED_KEY,
            allowed_providers={"openai"},
            demo=False,
        )
        os.link(profile, tmp_path / "linked-profile")
    else:
        os.mkfifo(profile, 0o600)

    started = time.monotonic()
    with pytest.raises(AnalysisError) as caught:
        store.load()
    assert caught.value.code == "settings_unavailable"
    assert time.monotonic() - started < 1


def test_profile_store_forces_owner_only_permissions_despite_umask(tmp_path):
    original_umask = os.umask(0o777)
    try:
        BYOKStore(tmp_path).save(
            provider="openai",
            model="fixture-model",
            supplied_key=SAVED_KEY,
            allowed_providers={"openai"},
            demo=False,
        )
    finally:
        os.umask(original_umask)
    assert stat.S_IMODE((tmp_path / "byok").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "byok" / PROFILE_FILE).stat().st_mode) == 0o600


def test_corrupt_profile_does_not_break_module_startup_or_non_saved_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "1")
    root = tmp_path / "byok"
    root.mkdir(mode=0o700)
    profile = root / PROFILE_FILE
    profile.write_text("corrupt fixture profile")
    profile.chmod(0o600)
    state = {"capabilities": {"byok_port_analysis": True}}
    with TestClient(host(tmp_path, state, settings_reader=lambda: False)) as client:
        assert client.get("/analysis/api/meta").status_code == 200
        unavailable = client.get("/analysis/api/settings")
        assert unavailable.status_code == 503
        assert "corrupt fixture profile" not in unavailable.text
        path = preview(client)
        started = client.post(
            path + "/start",
            json={"provider": "demo", "model": "synthetic-demo", "confirmed": True},
            headers=ACTION,
        )
        assert started.status_code == 202
        assert completed(client, path)["status"] == "completed"
