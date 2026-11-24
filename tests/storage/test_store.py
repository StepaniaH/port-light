from __future__ import annotations

import secrets

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
        assert client.post("/api/reservations", json={"require_count": False, **{'scope': 'all'}}, headers={"Idempotency-Key": secrets.token_urlsafe(32)}).status_code == 503
        assert client.post("/api/manual-ports", json={"port": 42000}).status_code == 503
    assert stored.read_text() == original


def test_occupancy_user_state_is_one_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    port_store.add_manual_port(9, "lab")
    port_store.add_hidden_port(22)
    port_store.set_port_rules([{"name": "web", "start": 8000, "end": 8099}])
    state = port_store.occupancy_user_state()
    manuals, hidden = state
    assert manuals[0]["port"] == 9
    assert hidden == [22]
    assert state.rules[0]["name"] == "web"


def test_manual_port_field_bounds_and_entry_cap(empty_scan, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        port_store.add_manual_port(4100, label="x" * 257)
    with pytest.raises(ValueError):
        port_store.add_manual_port(4100, machine="m" * 65)

    (tmp_path / "port_light.json").write_text(json.dumps({
        "manual_ports": [
            {"port": p, "label": "cap", "machine": "localhost"}
            for p in range(1, port_store.MAX_MANUAL_PORTS + 1)
        ]
    }), encoding="utf-8")

    with pytest.raises(ValueError):
        port_store.add_manual_port(50000, "overflow")

    updated = port_store.add_manual_port(1, "in-place-update")
    assert updated["label"] == "in-place-update"

    with TestClient(main.app) as client:
        assert client.post("/api/manual-ports", json={"port": 50001, "label": "x" * 257}).status_code == 422
        assert client.post("/api/manual-ports", json={"port": 50001, "machine": "m" * 65}).status_code == 422
        assert client.patch("/api/manual-ports/1", json={"label": "x" * 257}).status_code == 422
        assert client.post("/api/manual-ports/batch", json={"start": 50001, "end": 50002, "label": "x" * 257}).status_code == 422
        assert client.post("/api/manual-ports", json={"port": 50001, "label": "overflow"}).status_code in (400, 409)
        assert client.post("/api/manual-ports/batch", json={"start": 50001, "end": 50002}).status_code in (400, 409)
        assert client.post(
            "/api/reservations",
            json={"start": 50001, "end": 50002, "require_count": False},
            headers={"Idempotency-Key": secrets.token_urlsafe(32)},
        ).status_code in (400, 409)


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


def test_file_memo_survives_same_tick_same_size_writes_and_external_inode_swap(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    target = tmp_path / "port_light.json"
    real_stat = Path.stat
    fake_ino = [1001]

    def frozen_tick_stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        if self == target:
            return os.stat_result((
                st.st_mode,
                fake_ino[0],
                7,
                st.st_nlink,
                st.st_uid,
                st.st_gid,
                st.st_size,
                1000,
                1000,
                1000,
            ))
        return st

    monkeypatch.setattr(Path, "stat", frozen_tick_stat)
    port_store.add_manual_port(8001, "aaaa")
    port_store.update_manual_port(8001, "bbbb")
    port_store.add_hidden_port(8002)
    manuals = port_store.get_manual_ports()
    assert manuals == [{"port": 8001, "label": "bbbb", "machine": "localhost"}]
    assert port_store.get_hidden_ports() == [8002]

    # Simulate an external same-tick, same-size atomic replacement with a new inode.
    updated_payload = json.dumps({
        "manual_ports": [{"port": 8001, "label": "cccc", "machine": "localhost"}],
        "hidden_ports": [8002],
        "peers": [],
    }, indent=2) + "\n"
    target.write_text(updated_payload, encoding="utf-8")
    fake_ino[0] = 2002
    assert port_store.get_manual_ports()[0]["label"] == "cccc"


def test_concurrent_reads_and_writes_share_lock_without_clobbering(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    port_store.replace_peers([{"id": "peer0001", "name": "NAS", "url": "http://10.0.0.2:2100"}])

    def worker(idx: int):
        port_store.add_hidden_port(20000 + idx)
        assert isinstance(port_store.get_hidden_ports(), list)
        port_store.update_stored_settings({f"k{idx}": idx})
        assert isinstance(port_store.get_stored_settings(), dict)
        assert port_store.peers_stored() is True
        assert len(port_store.get_peers()) == 1
        port_store.add_manual_port(21000 + idx, f"svc-{idx}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, range(12)))

    assert set(port_store.get_hidden_ports()) == {20000 + i for i in range(12)}
    assert {entry["port"] for entry in port_store.get_manual_ports()} == {21000 + i for i in range(12)}
    settings = port_store.get_stored_settings()
    for i in range(12):
        assert settings[f"k{i}"] == i


def test_empty_reservation_picks_not_persisted_and_inactive_receipts_evict_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(port_store, "MAX_RESERVATION_REQUESTS", 3)
    clock = [1000]
    monkeypatch.setattr(port_store, "_now", lambda: clock[0])

    def params(start: int, end: int, *, require_count: bool = True) -> dict:
        return {
            "count": 1,
            "start": start,
            "end": end,
            "label": "ci",
            "ttl": 300,
            "scope": "self",
            "require_count": require_count,
        }

    empty_res, created = port_store.allocate_reservation(
        {45000}, 45000, 45000, params(45000, 45000), "e" * 43, "self",
    )
    assert created is True
    assert empty_res["ports"] == []
    assert empty_res["reservations"] == []
    assert not (tmp_path / "port_light.json").exists()

    keys = [f"{ch}" * 43 for ch in ("a", "b", "c", "d", "f")]
    tokens: list[tuple[int, str]] = []
    for idx, key in enumerate(keys[:3]):
        clock[0] = 1000 + idx
        res, ok = port_store.allocate_reservation(
            set(), 45000 + idx, 45000 + idx, params(45000 + idx, 45000 + idx), key, "self",
        )
        assert ok is True
        tokens.append((res["ports"][0], res["reservations"][0]["token"]))

    assert port_store.release_reservation(*tokens[0]) is True
    assert port_store.release_reservation(*tokens[1]) is True

    clock[0] = 1010
    res4, ok4 = port_store.allocate_reservation(
        set(), 45010, 45010, params(45010, 45010), keys[3], "self",
    )
    assert ok4 is True
    assert res4["ports"] == [45010]
    stored = json.loads((tmp_path / "port_light.json").read_text(encoding="utf-8"))
    assert len(stored["reservation_requests"]) == 3
    assert port_store._request_hash(keys[0]) not in stored["reservation_requests"]
    assert port_store._request_hash(keys[1]) in stored["reservation_requests"]
    assert port_store._request_hash(keys[2]) in stored["reservation_requests"]

    clock[0] = 1011
    res5, ok5 = port_store.allocate_reservation(
        set(), 45011, 45011, params(45011, 45011), keys[4], "self",
    )
    assert ok5 is True
    assert res5["ports"] == [45011]

    clock[0] = 1012
    with pytest.raises(port_store.ReservationConflict):
        port_store.allocate_reservation(
            set(), 45012, 45012, params(45012, 45012), "g" * 43, "self",
        )


def test_non_localhost_manual_port_cannot_bypass_reservation_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT_LIGHT_DATA_DIR", str(tmp_path))
    res, created = port_store.allocate_reservation(
        set(),
        45100,
        45100,
        {
            "count": 1,
            "start": 45100,
            "end": 45100,
            "label": "reserved",
            "ttl": 300,
            "scope": "self",
            "require_count": True,
        },
        "r" * 43,
        "self",
    )
    assert created is True
    assert res["ports"] == [45100]

    with pytest.raises(port_store.ReservationConflict):
        port_store.add_manual_port(45100, "spoof", machine="other-box")
    with pytest.raises(port_store.ReservationConflict):
        port_store.add_manual_port(45100, "spoof", machine="   ")

    manuals = port_store.get_manual_ports()
    assert len(manuals) == 1
    assert manuals[0]["machine"] == "localhost"
    assert manuals[0]["is_reservation"] is True
