from __future__ import annotations

import json
import os
import time

from backend.known_ports import KNOWN_PORTS, get_known_port


def test_known_ports_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_PORTS_FILE", str(tmp_path / "missing.json"))
    required = {"name", "description", "category", "is_access_port"}
    categories = {
        "system", "web", "database", "message", "proxy", "vpn",
        "selfhosted", "dev", "infra", "gaming",
    }
    for port, entry in KNOWN_PORTS.items():
        assert isinstance(port, int)
        assert 1 <= port <= 65535
        assert required <= set(entry)
        assert entry["category"] in categories
        assert isinstance(entry["is_access_port"], bool)
        assert entry["name"]


def test_homelab_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_PORTS_FILE", str(tmp_path / "missing.json"))
    assert get_known_port(2100)["name"] == "Port-Light"
    assert get_known_port(8123)["name"] == "Home Assistant"
    assert get_known_port(32400)["name"] == "Plex"
    assert get_known_port(2283)["name"] == "Immich"
    assert get_known_port(9696)["name"] == "Prowlarr"
    assert get_known_port(22)["is_access_port"] is True
    assert get_known_port(5432)["is_access_port"] is False
    assert get_known_port(1) is None


def test_custom_ports_override(tmp_path, monkeypatch):
    path = tmp_path / "custom_ports.json"
    path.write_text(json.dumps({
        "22": {
            "name": "Jump host",
            "description": "override",
            "category": "system",
            "is_access_port": True,
        },
        "4242": {
            "name": "Lab",
            "description": "custom",
            "category": "dev",
            "is_access_port": True,
        },
    }))
    monkeypatch.setenv("CUSTOM_PORTS_FILE", str(path))
    assert get_known_port(22)["name"] == "Jump host"
    assert get_known_port(4242)["name"] == "Lab"
    path.write_text(json.dumps({
        "4242": {
            "name": "Lab 2",
            "description": "custom",
            "category": "dev",
            "is_access_port": True,
        },
    }))
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert get_known_port(4242)["name"] == "Lab 2"
