from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from backend import degradations, port_store, supporter
from backend.main import app
from tests.storage.test_supporter import FLEET_KEY, PERSONAL_KEY, TEST_PUB1_PEM


def _trust_test_key(monkeypatch) -> None:
    monkeypatch.setattr(supporter, "TRUSTED_PUBLIC_KEYS", (supporter.parse_public_key_pem(TEST_PUB1_PEM),))
    monkeypatch.setattr(supporter, "_WARNED_INVALID_SIGNATURES", set())


def _clean_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    for var in (
        "PORT_LIGHT_SUPPORTER_KEY",
        "PORT_LIGHT_SUPPORTER_SHOW_HEADER",
        "PORT_LIGHT_SETTINGS_SOURCE",
        "SETTINGS_READONLY",
    ):
        monkeypatch.delenv(var, raising=False)
    _trust_test_key(monkeypatch)


def test_supporter_default_inactive_and_activation_lifecycle(tmp_path, monkeypatch):
    _clean_env(monkeypatch, tmp_path)
    client = TestClient(app)

    meta0 = client.get("/api/meta").json()
    assert meta0["supporter"] == {
        "active": False,
        "tier": None,
        "issued_at": None,
        "show_in_header": True,
    }
    doc0 = client.get("/api/settings").json()
    assert doc0["supporter"] == {
        "active": False,
        "status": "inactive",
        "tier": None,
        "issued_at": None,
        "key_preview": None,
        "order_preview": None,
        "origin": "none",
        "env_configured": False,
        "can_edit": True,
        "can_clear": False,
        "show_in_header": True,
    }
    assert "supporter_key" not in doc0["values"]

    # Rejects bad keys synchronously without writing to disk
    bad_fmt = client.put("/api/supporter-key", json={"key": "not-a-key"})
    assert bad_fmt.status_code == 400
    assert bad_fmt.json()["detail"] == "supporter_key_format"
    assert port_store.get_supporter_key() is None

    tampered = PERSONAL_KEY[:-1] + ("A" if PERSONAL_KEY[-1] != "A" else "B")
    bad_sig = client.put("/api/supporter-key", json={"key": tampered})
    assert bad_sig.status_code == 400
    assert bad_sig.json()["detail"] == "supporter_key_invalid"
    assert port_store.get_supporter_key() is None

    # Valid personal key activates and returns strictly masked metadata
    ok = client.put("/api/supporter-key", json={"key": f"  {PERSONAL_KEY}  "})
    assert ok.status_code == 200
    assert PERSONAL_KEY not in ok.text
    assert PERSONAL_KEY.split(".")[2] not in ok.text
    body = ok.json()["supporter"]
    assert body["active"] is True
    assert body["status"] == "active"
    assert body["tier"] == "personal"
    assert body["issued_at"] == "2026-11-25"
    assert body["key_preview"] == "PL1.eyJpYXQiOiIyMD…"
    assert body["order_preview"] == "ord_•••••333"
    assert body["origin"] == "file"
    assert body["can_edit"] is True
    assert body["can_clear"] is True

    # Toggle show_in_header via regular /api/settings PUT
    toggled = client.put("/api/settings", json={"supporter_show_header": False})
    assert toggled.status_code == 200
    assert toggled.json()["supporter"]["show_in_header"] is False
    assert PERSONAL_KEY not in toggled.text
    meta1 = client.get("/api/meta").json()
    assert meta1["supporter"] == {
        "active": True,
        "tier": "personal",
        "issued_at": "2026-11-25",
        "show_in_header": False,
    }

    # Replace with Fleet key
    fleet_res = client.put("/api/supporter-key", json={"key": FLEET_KEY})
    assert fleet_res.status_code == 200
    assert fleet_res.json()["supporter"]["tier"] == "fleet"
    assert fleet_res.json()["supporter"]["order_preview"] == "ord_•••••654"

    # Delete reverts to inactive
    cleared = client.delete("/api/supporter-key")
    assert cleared.status_code == 200
    assert cleared.json()["supporter"]["active"] is False
    assert cleared.json()["supporter"]["status"] == "inactive"
    assert cleared.json()["supporter"]["origin"] == "none"
    assert cleared.json()["supporter"]["can_clear"] is False


def test_supporter_env_precedence_override_and_readonly_modes(tmp_path, monkeypatch):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PORT_LIGHT_SUPPORTER_KEY", FLEET_KEY)
    client = TestClient(app)

    # Writable auto mode: env activates, can_edit=True, can_clear=False
    s_env = client.get("/api/settings").json()["supporter"]
    assert s_env["active"] is True
    assert s_env["tier"] == "fleet"
    assert s_env["origin"] == "env"
    assert s_env["env_configured"] is True
    assert s_env["can_edit"] is True
    assert s_env["can_clear"] is False

    # Writable Web UI can override env key into port_light.json
    over = client.put("/api/supporter-key", json={"key": PERSONAL_KEY})
    assert over.status_code == 200
    assert over.json()["supporter"]["tier"] == "personal"
    assert over.json()["supporter"]["origin"] == "file"
    assert over.json()["supporter"]["can_clear"] is True

    # SETTINGS_READONLY=1 locks writes (403) while still reading file key (matching settings.py)
    monkeypatch.setenv("SETTINGS_READONLY", "1")
    ro_doc = client.get("/api/settings").json()["supporter"]
    assert ro_doc["active"] is True
    assert ro_doc["tier"] == "personal"
    assert ro_doc["origin"] == "file"
    assert ro_doc["can_edit"] is False
    assert ro_doc["can_clear"] is False
    assert client.put("/api/supporter-key", json={"key": FLEET_KEY}).status_code == 403
    assert client.delete("/api/supporter-key").status_code == 403

    # PORT_LIGHT_SETTINGS_SOURCE=env ignores file and reads env only
    monkeypatch.delenv("SETTINGS_READONLY", raising=False)
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "env")
    env_only = client.get("/api/settings").json()["supporter"]
    assert env_only["active"] is True
    assert env_only["tier"] == "fleet"
    assert env_only["origin"] == "env"
    assert env_only["can_edit"] is False
    assert env_only["can_clear"] is False
    assert client.put("/api/supporter-key", json={"key": PERSONAL_KEY}).status_code == 403
    assert client.delete("/api/supporter-key").status_code == 403

    # Back to writable auto: DELETE removes file override and falls back to env Fleet key
    monkeypatch.setenv("PORT_LIGHT_SETTINGS_SOURCE", "auto")
    reverted = client.delete("/api/supporter-key")
    assert reverted.status_code == 200
    assert reverted.json()["supporter"]["active"] is True
    assert reverted.json()["supporter"]["tier"] == "fleet"
    assert reverted.json()["supporter"]["origin"] == "env"
    assert reverted.json()["supporter"]["can_clear"] is False


def test_invalid_env_and_invalid_file_keys_never_degrade_doctor_or_health(tmp_path, monkeypatch, caplog):
    _clean_env(monkeypatch, tmp_path)
    degradations.reset()
    bad_env = "PL1.invalidpayload.invalidsignature"
    monkeypatch.setenv("PORT_LIGHT_SUPPORTER_KEY", bad_env)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING, logger="port-light"):
        doc = client.get("/api/settings").json()
        meta = client.get("/api/meta").json()
        health = client.get("/api/health").json()
        doctor_doc = client.get("/api/doctor").json()
        doctor_report = client.get("/api/doctor/report").text

    assert doc["supporter"]["active"] is False
    assert doc["supporter"]["status"] == "invalid"
    assert doc["supporter"]["origin"] == "env"
    assert doc["supporter"]["can_edit"] is True
    assert doc["supporter"]["can_clear"] is False
    assert meta["supporter"]["active"] is False

    # Must NOT write to degradations or pollute health/doctor
    assert degradations.recent(10) == []
    assert health["degradations"] == []
    deg_check = next(c for c in doctor_doc["checks"] if c["id"] == "degradations")
    assert deg_check["status"] == "pass"
    # Raw key must never appear anywhere (responses, doctor report, logs)
    for blob in (json.dumps(doc), json.dumps(meta), json.dumps(health), doctor_report, caplog.text):
        assert bad_env not in blob

    # Hand-edited invalid key in port_light.json takes precedence over valid env (no silent fallback)
    monkeypatch.setenv("PORT_LIGHT_SUPPORTER_KEY", FLEET_KEY)
    data_file = tmp_path / "port_light.json"
    data_file.write_text(json.dumps({"manual_ports": [], "hidden_ports": [], "supporter_key": "PL1.corrupt.file"}), encoding="utf-8")
    inv_file = client.get("/api/settings").json()["supporter"]
    assert inv_file["active"] is False
    assert inv_file["status"] == "invalid"
    assert inv_file["origin"] == "file"
    assert inv_file["can_clear"] is True
    assert inv_file["can_edit"] is True

    # Removing the invalid saved key cleanly recovers to the valid env key
    recovered = client.delete("/api/supporter-key")
    assert recovered.status_code == 200
    assert recovered.json()["supporter"]["active"] is True
    assert recovered.json()["supporter"]["tier"] == "fleet"
    assert recovered.json()["supporter"]["origin"] == "env"


def test_raw_supporter_key_never_leaks_across_meta_settings_health_or_doctor(tmp_path, monkeypatch):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("PORT_LIGHT_SUPPORTER_KEY", PERSONAL_KEY)
    client = TestClient(app)

    for path in ("/api/meta", "/api/settings", "/api/health", "/api/doctor", "/api/doctor/report"):
        res = client.get(path)
        assert res.status_code == 200
        assert PERSONAL_KEY not in res.text
        assert PERSONAL_KEY.split(".")[2] not in res.text
        assert "ord_555444333" not in res.text


def test_null_or_non_string_supporter_key_never_breaks_core_endpoints_and_can_be_cleared(tmp_path, monkeypatch):
    _clean_env(monkeypatch, tmp_path)
    data_file = tmp_path / "port_light.json"
    client = TestClient(app)

    # Case 1: supporter_key explicitly set to null -> treated as unset, all endpoints stay 200
    data_file.write_text(json.dumps({"manual_ports": [], "hidden_ports": [], "supporter_key": None}), encoding="utf-8")
    for path in ("/api/ports", "/api/settings", "/api/meta", "/api/doctor"):
        res = client.get(path)
        assert res.status_code == 200, f"{path} failed on supporter_key=null: {res.text}"
    sup = client.get("/api/settings").json()["supporter"]
    assert sup["active"] is False and sup["status"] == "inactive"
    assert client.delete("/api/supporter-key").status_code == 200

    # Case 2: supporter_key is non-string junk (e.g., int/object) -> core endpoints stay 200, status=invalid, clearable
    data_file.write_text(json.dumps({"manual_ports": [], "hidden_ports": [], "supporter_key": 12345}), encoding="utf-8")
    for path in ("/api/ports", "/api/settings", "/api/meta", "/api/doctor"):
        res = client.get(path)
        assert res.status_code == 200, f"{path} failed on non-string supporter_key: {res.text}"
    sup = client.get("/api/settings").json()["supporter"]
    assert sup["active"] is False
    assert sup["status"] == "invalid"
    assert sup["origin"] == "file"
    assert sup["can_clear"] is True
    cleared = client.delete("/api/supporter-key")
    assert cleared.status_code == 200
    assert cleared.json()["supporter"]["status"] == "inactive"
    assert "supporter_key" not in json.loads(data_file.read_text(encoding="utf-8"))


def test_put_supporter_key_never_echoes_raw_non_object_payload(tmp_path, monkeypatch):
    _clean_env(monkeypatch, tmp_path)
    client = TestClient(app)
    secret_marker = "PL1.secret-input-must-never-echo.sig"
    res = client.put(
        "/api/supporter-key",
        content=json.dumps(secret_marker),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400
    assert res.json() == {"detail": "supporter_key_format"}
    assert secret_marker not in res.text
