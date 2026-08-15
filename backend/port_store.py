"""Port store — persistent user data: manual ports, hidden ports, machines.

All data is stored in a single JSON file under the data directory
(typically a Docker volume). No personal info is baked into the code;
everything is user-created at runtime.

File format::

    {
      "manual_ports": [
        {"port": 1234, "label": "My Service", "machine": "localhost"}
      ],
      "hidden_ports": [1234, 5678],
      "machines": [
        {"name": "localhost", "host": "127.0.0.1", "note": "This machine"},
        {"name": "nas", "host": "192.168.x.x", "note": "Example: Synology NAS"}
      ]
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.Lock()


def _data_dir() -> Path:
    return Path(os.environ.get("PORT_LIGHT_DATA_DIR", "/data"))


def _data_file() -> Path:
    return _data_dir() / "port_light.json"


def _load() -> dict:
    """Load the full data structure from disk."""
    f = _data_file()
    if not f.exists():
        return {"manual_ports": [], "hidden_ports": [], "machines": []}
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        corrupt = f.parent / (f.name + ".corrupt")
        try:
            os.replace(f, corrupt)
        except OSError:
            pass
        return {"manual_ports": [], "hidden_ports": [], "machines": []}
    except OSError:
        return {"manual_ports": [], "hidden_ports": [], "machines": []}


def _save(data: dict) -> None:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = _data_file()
    fd, tmp_name = tempfile.mkstemp(prefix=".port_light.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── Manual ports ──────────────────────────────────────────────

def get_manual_ports() -> list[dict]:
    data = _load()
    return data.get("manual_ports", [])


def add_manual_port(port: int, label: str = "", machine: str = "localhost") -> dict:
    with _LOCK:
        data = _load()
        mp = data.setdefault("manual_ports", [])
        # Remove existing entry for same port+machine
        mp[:] = [e for e in mp if not (e["port"] == port and e.get("machine") == machine)]
        entry = {"port": port, "label": label, "machine": machine}
        mp.append(entry)
        _save(data)
        return entry


def remove_manual_port(port: int, machine: str = "localhost") -> bool:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        before = len(mp)
        mp[:] = [e for e in mp if not (e["port"] == port and e.get("machine") == machine)]
        if len(mp) < before:
            _save(data)
            return True
        return False


# ── Hidden ports ──────────────────────────────────────────────

def get_hidden_ports() -> list[int]:
    data = _load()
    return data.get("hidden_ports", [])


def add_hidden_port(port: int) -> bool:
    with _LOCK:
        data = _load()
        hp = data.setdefault("hidden_ports", [])
        if port not in hp:
            hp.append(port)
            _save(data)
            return True
        return False


def remove_hidden_port(port: int) -> bool:
    with _LOCK:
        data = _load()
        hp = data.get("hidden_ports", [])
        if port in hp:
            hp.remove(port)
            _save(data)
            return True
        return False


def update_manual_port(port: int, label: str, machine: str = "localhost") -> dict | None:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        for entry in mp:
            if entry["port"] == port and entry.get("machine", "localhost") == machine:
                entry["label"] = label
                _save(data)
                return entry
        return None


def get_stored_settings() -> dict:
    data = _load()
    raw = data.get("settings")
    return dict(raw) if isinstance(raw, dict) else {}


def update_stored_settings(patch: dict) -> dict:
    """Merge *patch* into stored settings. ``None`` values delete a key."""
    with _LOCK:
        data = _load()
        current = dict(data.get("settings") or {})
        for key, value in patch.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        data["settings"] = current
        _save(data)
        return current


# ── Machines ──────────────────────────────────────────────────

def get_machines() -> list[dict]:
    data = _load()
    machines = data.get("machines", [])
    # Ensure localhost always exists
    if not any(m["name"] == "localhost" for m in machines):
        machines.insert(0, {"name": "localhost", "host": "127.0.0.1", "note": "This machine"})
    return machines


def add_machine(name: str, host: str, note: str = "") -> dict:
    with _LOCK:
        data = _load()
        machines = data.setdefault("machines", [])
        machines[:] = [m for m in machines if m["name"] != name]
        entry = {"name": name, "host": host, "note": note}
        machines.append(entry)
        _save(data)
        return entry


def remove_machine(name: str) -> bool:
    with _LOCK:
        data = _load()
        machines = data.get("machines", [])
        before = len(machines)
        machines[:] = [m for m in machines if m["name"] != name]
        if len(machines) < before:
            _save(data)
            return True
        return False
