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

def _entry_port(entry) -> int | None:
    if not isinstance(entry, dict):
        return None
    try:
        port = int(entry.get("port"))
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def _entry_machine(entry) -> str:
    if not isinstance(entry, dict):
        return "localhost"
    return str(entry.get("machine") or "localhost")


def _hidden_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    if port < 1 or port > 65535:
        return None
    return port


def get_manual_ports() -> list[dict]:
    data = _load()
    out: list[dict] = []
    for entry in data.get("manual_ports") or []:
        port = _entry_port(entry)
        if port is None:
            continue
        out.append({
            "port": port,
            "label": str(entry.get("label") or ""),
            "machine": _entry_machine(entry),
        })
    return out


def add_manual_port(port: int, label: str = "", machine: str = "localhost") -> dict:
    with _LOCK:
        data = _load()
        mp = data.setdefault("manual_ports", [])
        kept: list[dict] = []
        for entry in mp:
            if _entry_port(entry) is None:
                continue
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                continue
            kept.append(entry)
        entry = {"port": port, "label": label, "machine": machine}
        kept.append(entry)
        data["manual_ports"] = kept
        _save(data)
        return entry


def remove_manual_port(port: int, machine: str = "localhost") -> bool:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        kept: list[dict] = []
        removed = False
        for entry in mp:
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
                removed = True
                continue
            if _entry_port(entry) is not None:
                kept.append(entry)
        if removed:
            data["manual_ports"] = kept
            _save(data)
            return True
        return False


# ── Hidden ports ──────────────────────────────────────────────

def get_hidden_ports() -> list[int]:
    data = _load()
    out: list[int] = []
    seen: set[int] = set()
    for raw in data.get("hidden_ports") or []:
        port = _hidden_port(raw)
        if port is None or port in seen:
            continue
        seen.add(port)
        out.append(port)
    return out


def add_hidden_port(port: int) -> bool:
    with _LOCK:
        data = _load()
        hp = [_hidden_port(p) for p in data.get("hidden_ports") or []]
        hp = [p for p in hp if p is not None]
        if port in hp:
            if data.get("hidden_ports") != hp:
                data["hidden_ports"] = hp
                _save(data)
            return False
        hp.append(port)
        data["hidden_ports"] = hp
        _save(data)
        return True


def remove_hidden_port(port: int) -> bool:
    with _LOCK:
        data = _load()
        hp = [_hidden_port(p) for p in data.get("hidden_ports") or []]
        hp = [p for p in hp if p is not None]
        if port not in hp:
            return False
        hp.remove(port)
        data["hidden_ports"] = hp
        _save(data)
        return True


def update_manual_port(port: int, label: str, machine: str = "localhost") -> dict | None:
    with _LOCK:
        data = _load()
        mp = data.get("manual_ports", [])
        for entry in mp:
            if not isinstance(entry, dict):
                continue
            if _entry_port(entry) == port and _entry_machine(entry) == machine:
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
    machines = [m for m in data.get("machines", []) if isinstance(m, dict) and m.get("name")]
    if not any(m.get("name") == "localhost" for m in machines):
        machines.insert(0, {"name": "localhost", "host": "127.0.0.1", "note": "This machine"})
    return machines


def add_machine(name: str, host: str, note: str = "") -> dict:
    with _LOCK:
        data = _load()
        machines = data.setdefault("machines", [])
        machines[:] = [m for m in machines if isinstance(m, dict) and m.get("name") != name]
        entry = {"name": name, "host": host, "note": note}
        machines.append(entry)
        _save(data)
        return entry


def remove_machine(name: str) -> bool:
    with _LOCK:
        data = _load()
        machines = data.get("machines", [])
        before = len(machines)
        machines[:] = [m for m in machines if isinstance(m, dict) and m.get("name") != name]
        if len(machines) < before:
            _save(data)
            return True
        return False
