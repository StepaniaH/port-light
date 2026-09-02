from __future__ import annotations

import errno
import json
import tempfile

import pytest

from backend import port_store


def test_manual_and_hidden_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    entry = port_store.add_manual_port(2100, "ui")
    assert entry["port"] == 2100
    assert port_store.get_manual_ports() == [entry]
    assert port_store.add_hidden_port(2100) is True
    assert port_store.add_hidden_port(2100) is False
    assert port_store.get_hidden_ports() == [2100]
    assert port_store.remove_manual_port(2100) is True
    assert port_store.remove_hidden_port(2100) is True
    assert port_store.get_manual_ports() == []
    port_store.update_stored_settings({"theme": "light"})
    assert port_store.get_stored_settings()["theme"] == "light"
    gen = port_store.store_generation()
    assert gen >= 1
    port_store.update_stored_settings({"theme": "dark"})
    assert port_store.store_generation() == gen + 1
    port_store.update_stored_settings({"theme": None})
    assert "theme" not in port_store.get_stored_settings()


def test_load_picks_up_external_file_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    assert port_store.get_stored_settings() == {}
    (tmp_path / "port_light.json").write_text(json.dumps({
        "settings": {"locale": "de"},
        "manual_ports": [{"port": 5, "label": "ext"}],
    }), encoding="utf-8")
    assert port_store.get_stored_settings()["locale"] == "de"
    assert port_store.get_manual_ports()[0]["port"] == 5


@pytest.mark.parametrize("contents", ["{not json", "[]", '{"manual_ports": {}}', '{"settings": []}'])
def test_invalid_store_is_preserved_and_blocks_writes(tmp_path, monkeypatch, contents):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    bad = tmp_path / "port_light.json"
    bad.write_text(contents, encoding="utf-8")
    with pytest.raises(port_store.StoreReadError):
        port_store.get_manual_ports()
    with pytest.raises(port_store.StoreReadError):
        port_store.add_manual_port(7, "lab")
    assert bad.read_text() == contents


def test_unreadable_store_cannot_be_replaced_by_a_mutation(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    stored = tmp_path / "port_light.json"
    original = '{"manual_ports":[{"port":43000}],"hidden_ports":[43000]}'
    stored.write_text(original)
    with monkeypatch.context() as patch:
        def denied(*args, **kwargs):
            raise PermissionError("read denied")
        patch.setattr(Path, "read_text", denied)
        with pytest.raises(port_store.StoreReadError):
            port_store.add_manual_port(43001)
    assert stored.read_text() == original


def test_legacy_port_values_and_machine_data_survive_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    stored = tmp_path / "port_light.json"
    stored.write_text(json.dumps({
        "manual_ports": [{"port": "9", "label": "legacy"}],
        "hidden_ports": ["22"],
        "machines": ["x", {"name": "nas", "host": "10.0.0.1"}],
    }))
    assert port_store.get_manual_ports()[0]["port"] == 9
    assert port_store.get_hidden_ports() == [22]
    port_store.add_manual_port(11, "lab")
    assert {e["port"] for e in port_store.get_manual_ports()} == {9, 11}
    assert json.loads(stored.read_text())["machines"][0] == "x"


@pytest.mark.parametrize("data", [
    {"manual_ports": ["junk"]}, {"manual_ports": [{"label": "missing port"}]},
    {"hidden_ports": ["bad"]}, {"peers": [{"id": "peer0001", "name": "Peer"}]},
])
def test_malformed_rows_are_preserved_and_block_all_scope_allocation(empty_scan, tmp_path, data):
    from fastapi.testclient import TestClient
    from backend import main

    stored = tmp_path / "port_light.json"
    original = json.dumps(data)
    stored.write_text(original)
    with TestClient(main.app) as client:
        assert client.get("/api/ports/suggest?scope=all&reserve=true").status_code == 503
        assert client.post("/api/manual-ports", json={"port": 42000}).status_code == 503
    assert stored.read_text() == original


def test_occupancy_user_state_is_one_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    port_store.add_manual_port(9, "lab")
    port_store.add_hidden_port(22)
    manuals, hidden = port_store.occupancy_user_state()
    assert manuals[0]["port"] == 9
    assert hidden == [22]


def test_hidden_port_rejects_out_of_range(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    assert port_store.add_hidden_port(0) is False
    assert port_store.add_hidden_port(70000) is False
    assert port_store.get_hidden_ports() == []


def test_save_permission_denied_is_store_write_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))

    def boom(*_a, **_k):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", boom)
    with pytest.raises(port_store.StoreWriteError) as caught:
        port_store.replace_peers([])
    message = str(caught.value).lower()
    assert "permission denied" in message
    assert "writable" in message
    assert ".tmp" not in message


def test_invalid_store_returns_503_and_recovers_after_repair(empty_scan, tmp_path):
    from fastapi.testclient import TestClient
    from backend import main

    stored = tmp_path / "port_light.json"
    stored.write_text("{invalid")
    with TestClient(main.app) as client:
        assert client.get("/api/health").status_code == 200
        for path in ("/api/manual-ports", "/api/ports", "/api/settings"):
            assert client.get(path).status_code == 503
        assert client.post("/api/manual-ports", json={"port": 42000}).status_code == 503
        assert stored.read_text() == "{invalid"
        stored.write_text('{"manual_ports":[{"port":42001}],"hidden_ports":[42001]}')
        assert client.post("/api/manual-ports", json={"port": 42000}).status_code == 200
        assert {p["port"] for p in port_store.get_manual_ports()} == {42000, 42001}
        assert port_store.get_hidden_ports() == [42001]
