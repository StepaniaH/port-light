from __future__ import annotations

import json

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
    port_store.update_stored_settings({"theme": None})
    assert "theme" not in port_store.get_stored_settings()


def test_corrupt_json_is_quarantined(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    bad = tmp_path / "port_light.json"
    bad.write_text("{not json", encoding="utf-8")
    assert port_store.get_manual_ports() == []
    assert (tmp_path / "port_light.json.corrupt").read_text(encoding="utf-8") == "{not json"
    port_store.add_manual_port(7, "lab")
    data = json.loads((tmp_path / "port_light.json").read_text(encoding="utf-8"))
    assert data["manual_ports"][0]["port"] == 7
