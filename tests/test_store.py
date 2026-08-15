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


def test_junk_store_rows_do_not_break_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    (tmp_path / "port_light.json").write_text(json.dumps({
        "manual_ports": ["nope", {"label": "x"}, {"port": 9, "label": "ok"}],
        "hidden_ports": ["22", "bad", 0],
        "machines": ["x", {"name": "nas", "host": "10.0.0.1"}],
    }), encoding="utf-8")
    assert port_store.get_manual_ports()[0]["port"] == 9
    assert port_store.get_hidden_ports() == [22]
    names = {m["name"] for m in port_store.get_machines()}
    assert "localhost" in names
    assert "nas" in names
    port_store.add_manual_port(11, "lab")
    assert {e["port"] for e in port_store.get_manual_ports()} == {9, 11}
    assert port_store.add_hidden_port(22) is False
    assert port_store.remove_manual_port(9) is True
    assert {e["port"] for e in port_store.get_manual_ports()} == {11}
    (tmp_path / "port_light.json").write_text(json.dumps({
        "manual_ports": ["nope", {"port": 12, "label": "old"}],
    }), encoding="utf-8")
    updated = port_store.update_manual_port(12, "new")
    assert updated["label"] == "new"
    saved = json.loads((tmp_path / "port_light.json").read_text(encoding="utf-8"))
    assert saved["manual_ports"] == [{"port": 12, "label": "new"}]
