import json
import stat

import pytest
from fastapi.testclient import TestClient

from backend.analysis.evidence import AnalysisError
from backend.analysis.provider import ChatGateway
from backend.analysis.settings import MAX_CONNECTIONS, BYOKStore
from tests.analysis.test_custom_connections import ACTION, BASE_URL, KEY, host, save
from tests.analysis.test_settings import host as settings_host

SECOND_KEY = "fixture-second-key-not-a-secret"
PATH = "/analysis/api/settings/ai"


def create(client, model="second-model", key=SECOND_KEY):
    response = client.post(
        PATH + "/connections", headers={**ACTION, "X-Port-Light-Model-Key": key},
        json={"provider": "custom", "model": model, "base_url": BASE_URL},
    )
    assert response.status_code == 201
    document = response.json()
    return document, next(row for row in document["connections"] if row["id"] == document["saved_connection_id"])


def activate(client, row):
    return client.post(PATH + "/active", headers=ACTION,
                       json={"profile_id": row["id"], "config_revision": row["revision"]})


def remove(client, row):
    return client.request("DELETE", PATH + "/connections/" + row["id"], headers=ACTION,
                          json={"config_revision": row["revision"]})


def test_legacy_profile_survives_multiple_connections_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        original = save(client).json()
        original_row = original["connections"][0]
        assert original_row["id"] == original["active_connection_id"]
        document, second = create(client)
        assert document["ai"] == original["ai"]
        assert document["connections"] == [original_row, second]
        assert document["active_connection_id"] == original_row["id"]
        assert KEY not in json.dumps(document) and SECOND_KEY not in json.dumps(document)

        # The old endpoint must not erase profiles created by the new UI.
        edited = save(client, model="legacy-edited", key=None).json()
        assert edited["connections"][1] == second
        assert edited["active_connection_id"] == original_row["id"]
        assert edited["ai"]["model"] == "legacy-edited"

    restarted = BYOKStore(tmp_path).load_connections()
    assert restarted.active.identifier == original_row["id"]
    assert restarted.active.key == KEY
    assert restarted.find(second["id"]).key == SECOND_KEY
    assert stat.S_IMODE((tmp_path / "byok" / "profile.json").stat().st_mode) == 0o600
    with TestClient(host(tmp_path)) as client:
        assert client.get("/analysis/api/settings").json()["connections"] == edited["connections"]


def test_switching_connections_invalidates_previous_consent_even_when_switching_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        original = save(client).json()
        first = original["connections"][0]
        _, second = create(client)
        selected = activate(client, second)
        assert selected.status_code == 200
        assert selected.json()["active_connection_id"] == second["id"]
        assert BYOKStore(tmp_path).load().key == SECOND_KEY
        switched_back = activate(client, first)
        assert switched_back.status_code == 200
        assert switched_back.json()["active_connection_id"] == first["id"]
        assert switched_back.json()["ai"]["revision"] != first["revision"]
        assert BYOKStore(tmp_path).load().key == KEY
        with pytest.raises(AnalysisError) as error:
            BYOKStore(tmp_path).resolve(first["revision"], allowed_providers={"custom"}, demo=False)
        assert error.value.code == "configuration_changed"
        rejected = activate(client, first)
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "configuration_changed"


def test_editing_inactive_profile_uses_its_own_key_and_preserves_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        original = save(client).json()
        _, second = create(client)
        values = {"provider": "custom", "model": "edited-second", "base_url": BASE_URL,
                  "config_revision": second["revision"]}
        path = PATH + "/connections/" + second["id"]
        edited = client.put(path, headers=ACTION, json=values)
        assert edited.status_code == 200
        assert edited.json()["ai"] == original["ai"]
        row = edited.json()["connections"][1]
        assert row["id"] == second["id"] and row["revision"] != second["revision"]
        assert BYOKStore(tmp_path).load_connections().find(second["id"]).key == SECOND_KEY
        assert client.put(path, headers=ACTION, json=values).status_code == 409
        changed_address = {**values, "config_revision": row["revision"], "base_url": BASE_URL + "/other"}
        rejected = client.put(path, headers=ACTION, json=changed_address)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "key_required"
        assert client.get("/analysis/api/settings").json()["connections"][1] == row


def test_inactive_saved_and_draft_probes_never_change_the_active_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    async def probe(_self, _provider, model, key, **_options):
        calls.append((model, key))

    monkeypatch.setattr(ChatGateway, "probe", probe)
    with TestClient(host(tmp_path)) as client:
        save(client)
        before, second = create(client)
        body = {"profile_id": second["id"], "config_revision": second["revision"], "confirmed": True}
        tested = client.post(PATH + "/test", headers=ACTION, json=body)
        assert tested.status_code == 200
        assert tested.json()["config_revision"] == second["revision"]
        draft = {"provider": "custom", "model": "draft-second", "base_url": BASE_URL}
        assert client.post(PATH + "/test", headers=ACTION, json={**body, "draft": draft}).status_code == 200
        assert calls == [("second-model", SECOND_KEY), ("draft-second", SECOND_KEY)]
        after = client.get("/analysis/api/settings").json()
        assert after["ai"] == before["ai"]
        assert after["connections"] == before["connections"]


def test_delete_only_the_requested_profile_and_do_not_automatically_select_another(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        first = save(client).json()["connections"][0]
        _, second = create(client)
        deleted = remove(client, second)
        assert deleted.status_code == 200
        assert deleted.json()["connections"] == [first]
        assert deleted.json()["active_connection_id"] == first["id"]
        _, second = create(client)
        deleted = remove(client, first)
        assert deleted.status_code == 200
        assert deleted.json()["ai"]["configured"] is False
        assert deleted.json()["active_connection_id"] is None
        assert deleted.json()["connections"] == [second]
        raw = (tmp_path / "byok" / "profile.json").read_text()
        assert KEY not in raw and SECOND_KEY in raw
        assert activate(client, second).status_code == 200


@pytest.mark.parametrize("operation", ["create", "edit", "select", "delete"])
def test_profile_mutations_respect_readonly_and_same_origin(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    readonly = False
    with TestClient(settings_host(tmp_path, {}, settings_reader=lambda: readonly)) as client:
        first = save(client).json()["connections"][0]
        body = {"config_revision": first["revision"]}
        path, method = PATH + "/connections/" + first["id"], "PUT"
        if operation == "create":
            path, method = PATH + "/connections", "POST"
            body = {"provider": "custom", "model": "new-model", "base_url": BASE_URL}
        elif operation == "edit":
            body.update(provider="custom", model="new-model", base_url=BASE_URL)
        elif operation == "select":
            path, method = PATH + "/active", "POST"
            body["profile_id"] = first["id"]
        else:
            method = "DELETE"
        original = client.get("/analysis/api/settings").json()["connections"]
        for headers in ({}, {**ACTION, "Origin": "https://example.invalid"}):
            assert client.request(method, path, json=body, headers=headers).status_code == 403
        readonly = True
        rejected = client.request(method, path, json=body, headers=ACTION)
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "settings_readonly"
        assert client.get("/analysis/api/settings").json()["connections"] == original


def test_connections_are_bounded_and_a_model_cannot_expose_any_saved_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        first = save(client).json()["connections"][0]
        create(client)
        rejected = client.put(PATH + "/connections/" + first["id"], headers=ACTION,
                              json={"provider": "custom", "model": SECOND_KEY, "base_url": BASE_URL,
                                    "config_revision": first["revision"]})
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "invalid_model"
        assert KEY not in rejected.text and SECOND_KEY not in rejected.text
        for number in range(2, MAX_CONNECTIONS):
            create(client, model=f"model-{number}")
        rejected = client.post(PATH + "/connections", headers={**ACTION, "X-Port-Light-Model-Key": KEY},
                               json={"provider": "custom", "model": "overflow", "base_url": BASE_URL})
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "connection_limit"
        assert len(client.get("/analysis/api/settings").json()["connections"]) == MAX_CONNECTIONS


def test_mixed_saved_key_is_hidden_and_cannot_be_used_in_a_probe_or_analysis(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    calls = []

    async def probe(_self, *_args, **_kwargs):
        calls.append(True)

    monkeypatch.setattr(ChatGateway, "probe", probe)
    with TestClient(host(tmp_path)) as client:
        first = save(client).json()["connections"][0]
        create(client)
        path = tmp_path / "byok" / "profile.json"
        document = json.loads(path.read_text())
        document["profiles"][0]["model"] = SECOND_KEY
        path.write_text(json.dumps(document))
        response = client.get("/analysis/api/settings")
        assert KEY not in response.text and SECOND_KEY not in response.text
        assert response.json()["ai"]["model"] == ""
        for body in (
            {"profile_id": first["id"], "config_revision": first["revision"], "confirmed": True},
            {"confirmed": True, "draft": {"provider": "custom", "model": SECOND_KEY, "base_url": BASE_URL}},
        ):
            tested = client.post(PATH + "/test", headers={**ACTION, "X-Port-Light-Model-Key": KEY}, json=body)
            assert tested.status_code == 422
            assert tested.json()["error"]["code"] == "invalid_model"
            assert SECOND_KEY not in tested.text
        assert calls == []
        with pytest.raises(AnalysisError) as error:
            BYOKStore(tmp_path).resolve(first["revision"], allowed_providers={"custom"}, demo=False)
        assert error.value.code == "invalid_model"


@pytest.mark.parametrize("damage", ["duplicate_id", "unknown_active"])
def test_invalid_collection_never_changes_saved_credentials(tmp_path, monkeypatch, damage):
    monkeypatch.setenv("PORT_LIGHT_ANALYSIS_DEMO", "0")
    with TestClient(host(tmp_path)) as client:
        create(client)
        _, second = create(client, model="third-model")
        path = tmp_path / "byok" / "profile.json"
        document = json.loads(path.read_text())
        if damage == "duplicate_id":
            document["profiles"][1]["id"] = document["profiles"][0]["id"]
        else:
            document["active_id"] = "x" * 32
        path.write_text(json.dumps(document))
        original = path.read_bytes()
        unavailable = client.get("/analysis/api/settings")
        assert unavailable.status_code == 503
        assert SECOND_KEY not in unavailable.text
        rejected = remove(client, second)
        assert rejected.status_code == 503
        assert path.read_bytes() == original
