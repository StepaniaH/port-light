import asyncio
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.analysis.evidence import AnalysisError
from backend.analysis.reports import APPLICATION_ID, ReportStore, owner_hash
from backend.analysis.workbench import Workbench, build_capture
from tests.analysis.observation_fixture import seed_legacy_report
from tests.analysis.test_workbench import batch_document


def make_capture(identifier=None, *, port=8080, capture_id="obs-report-1"):
    return build_capture(
        batch_document(ports=[port], capture_id=capture_id, protocol="tcp"),
        identifier=identifier or secrets.token_urlsafe(24),
        kind="triage",
        scope_requested={
            "kind": "single_port",
            "scope": {"kind": "single_port", "port": port},
            "protocol": "tcp",
            "history_hours": 6,
        },
    )


def test_explicit_save_survives_reopen_is_owned_idempotent_and_deletable(tmp_path):
    owner = secrets.token_urlsafe(24)
    other = secrets.token_urlsafe(24)
    reports = ReportStore(tmp_path)
    reports.open()
    assert reports.metadata()["used_workbench_reports"] == 0
    assert reports.metadata()["used_workbench_bytes"] == 0
    assert reports.list_workbench(owner) == ([], None)

    capture = make_capture()
    saved = reports.save_workbench(capture, owner)
    assert reports.save_workbench(capture, owner) == saved
    meta = reports.metadata()
    assert meta["used_workbench_reports"] == 1
    assert meta["used_workbench_bytes"] > 0

    listed, cursor = reports.list_workbench(owner)
    assert cursor is None and len(listed) == 1
    assert listed[0]["id"] == saved["id"]
    assert saved["capture"]["id"] == capture["id"]
    assert (
        reports.connection.execute("SELECT owner_hash FROM workbench_reports").fetchone()[0]
        == owner_hash(owner)
    )
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.get_workbench(saved["id"], other)
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.delete_workbench(saved["id"], other)

    reports.close()
    assert owner.encode() not in reports.path.read_bytes()
    assert reports.path.stat().st_mode & 0o777 == 0o600
    assert reports.path.parent.stat().st_mode & 0o777 == 0o700

    reopened = ReportStore(tmp_path)
    reopened.open()
    assert reopened.get_workbench(saved["id"], owner) == saved
    reopened.delete_workbench(saved["id"], owner)
    assert reopened.metadata()["used_workbench_reports"] == 0
    assert reopened.metadata()["used_workbench_bytes"] == 0
    with pytest.raises(AnalysisError, match="report_not_found"):
        reopened.get_workbench(saved["id"], owner)
    reopened.close()


def test_single_report_size_limit_rejects_without_evicting_existing_reports(tmp_path):
    reports = ReportStore(tmp_path)
    reports.open()
    saved = reports.save_workbench(make_capture(), "owner-a")
    reports.max_workbench_report_bytes = 128
    with pytest.raises(AnalysisError, match="report_too_large"):
        reports.save_workbench(make_capture(), "owner-a")
    assert reports.get_workbench(saved["id"], "owner-a") == saved
    assert reports.metadata()["used_workbench_reports"] == 1
    reports.close()


def test_lru_eviction_reclaims_oldest_reports_by_count_and_bytes_across_sessions(tmp_path):
    now = [1_700_000_000]
    reports = ReportStore(tmp_path, clock=lambda: now[0], max_workbench_reports=3)
    reports.open()
    first = reports.save_workbench(make_capture(port=8001), "orphaned-session-1")
    now[0] += 10
    second = reports.save_workbench(make_capture(port=8002), "orphaned-session-2")
    now[0] += 10
    third = reports.save_workbench(make_capture(port=8003), "active-session")
    assert reports.metadata()["used_workbench_reports"] == 3

    # Re-saving the same capture revision is idempotent and does not evict anything.
    now[0] += 10
    assert (
        reports.save_workbench(third["capture"], "active-session")["id"] == third["id"]
    )
    assert reports.get_workbench(first["id"], "orphaned-session-1")["id"] == first["id"]

    # Saving a 4th distinct report evicts the oldest (`first`), even from an orphaned session.
    now[0] += 10
    fourth = reports.save_workbench(make_capture(port=8004), "active-session")
    assert reports.metadata()["used_workbench_reports"] == 3
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.get_workbench(first["id"], "orphaned-session-1")
    assert reports.get_workbench(second["id"], "orphaned-session-2")["id"] == second["id"]
    assert reports.get_workbench(third["id"], "active-session")["id"] == third["id"]
    assert reports.get_workbench(fourth["id"], "active-session")["id"] == fourth["id"]

    # Tightening byte quota evicts multiple oldest rows (`second` and `third`) to fit a new save.
    single_bytes = reports.connection.execute(
        "SELECT bytes FROM workbench_reports WHERE id=?", (fourth["id"],)
    ).fetchone()[0]
    reports.max_workbench_reports = 128
    reports.max_workbench_total_bytes = single_bytes * 2 + 64
    now[0] += 10
    fifth = reports.save_workbench(make_capture(port=8005), "active-session")
    meta = reports.metadata()
    assert meta["used_workbench_reports"] == 2
    assert meta["used_workbench_bytes"] <= reports.max_workbench_total_bytes
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.get_workbench(second["id"], "orphaned-session-2")
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.get_workbench(third["id"], "active-session")
    assert reports.get_workbench(fourth["id"], "active-session")["id"] == fourth["id"]
    assert reports.get_workbench(fifth["id"], "active-session")["id"] == fifth["id"]
    reports.close()


def test_delete_all_clears_only_calling_owner_for_workbench_and_legacy(tmp_path):
    store = ReportStore(tmp_path)
    store.open()
    owner_a = secrets.token_urlsafe(24)
    owner_b = secrets.token_urlsafe(24)
    store.save_workbench(make_capture(port=8080), owner_a)
    store.save_workbench(make_capture(port=8081), owner_a)
    kept_wb = store.save_workbench(make_capture(port=8082), owner_b)
    seed_legacy_report(store, owner_a, report_id="leg-a1", source_analysis_id="src-a1")
    kept_leg = seed_legacy_report(store, owner_b, report_id="leg-b1", source_analysis_id="src-b1")

    assert store.delete_all_workbench("") == 0
    assert store.delete_all("") == 0
    assert store.delete_all_workbench(owner_a) == 2
    assert store.delete_all(owner_a) == 1
    assert store.list_workbench(owner_a) == ([], None)
    assert store.list(owner_a) == []
    assert store.get_workbench(kept_wb["id"], owner_b)["id"] == kept_wb["id"]
    assert store.get(kept_leg["id"], owner_b)["id"] == kept_leg["id"]
    assert store.metadata()["used_workbench_reports"] == 1
    store.close()


def test_parallel_connections_enforce_the_shared_lru_cap(tmp_path):
    stores = [ReportStore(tmp_path, max_workbench_reports=1) for _ in range(2)]
    for store in stores:
        store.open()
    barrier = threading.Barrier(2)

    def save(index):
        barrier.wait()
        return stores[index].save_workbench(
            make_capture(port=8080 + index), f"owner-{index}"
        )["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert len(results) == 2
    assert stores[0].metadata()["used_workbench_reports"] == 1
    for store in stores:
        store.close()


def test_write_failure_rolls_back_eviction_and_insert_atomically(tmp_path):
    store = ReportStore(tmp_path, max_workbench_reports=1)
    store.open()
    existing = store.save_workbench(make_capture(port=8080), "owner-old")
    store.connection.set_authorizer(
        lambda action, name, *_: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT"
            else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(AnalysisError, match="report_storage_unavailable"):
        store.save_workbench(make_capture(port=8081), "owner-new")
    store.connection.set_authorizer(None)
    assert store.get_workbench(existing["id"], "owner-old")["id"] == existing["id"]
    assert store.list_workbench("owner-new") == ([], None)
    store.close()


@pytest.mark.parametrize("kind", ["corrupt", "future", "unversioned", "linked"])
def test_unsupported_or_unsafe_storage_preserves_original_bytes(tmp_path, kind):
    state = tmp_path / "state"
    state.mkdir()
    path = state / "analysis.sqlite3"
    if kind == "corrupt":
        path.write_bytes(b"Synthetic damaged database; retain for recovery")
    elif kind == "linked":
        original = tmp_path / "original.sqlite3"
        original.write_bytes(b"Synthetic original database")
        path.symlink_to(original)
    else:
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE existing_data (value TEXT)")
            database.execute("INSERT INTO existing_data VALUES ('synthetic saved data')")
            if kind == "future":
                database.execute("PRAGMA user_version=99")
                database.execute(f"PRAGMA application_id={APPLICATION_ID}")
    before = path.read_bytes()
    store = ReportStore(tmp_path)
    store.open()
    assert store.metadata()["available"] is False
    with pytest.raises(AnalysisError, match="report_storage_unavailable"):
        store.list("owner")
    store.close()
    assert path.read_bytes() == before


def test_first_schema_migration_rolls_back_and_can_retry(tmp_path, monkeypatch):
    original = ReportStore._migrate

    def fail_migration(connection):
        connection.set_authorizer(
            lambda action, name, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_TABLE and name == "jobs"
                else sqlite3.SQLITE_OK
            )
        )
        original(connection)

    monkeypatch.setattr(ReportStore, "_migrate", staticmethod(fail_migration))
    store = ReportStore(tmp_path)
    store.open()
    assert not store.metadata()["available"]
    with sqlite3.connect(store.path) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )
    monkeypatch.setattr(ReportStore, "_migrate", staticmethod(original))
    store.open()
    assert store.metadata()["available"]
    store.close()


def test_restart_marks_workbench_receipt_interrupted_without_persisting_content(tmp_path):
    store = ReportStore(tmp_path)
    store.open()
    owner, identifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    store.begin_workbench_job(identifier, owner, [8080])
    store.close()
    store.open()
    receipt = store.workbench_job(identifier, owner)
    assert receipt["status"] == "interrupted" and receipt["result_available"] is False
    assert not {"facts", "ai", "model", "provider", "key"} & receipt.keys()
    assert store.list_workbench(owner) == ([], None)
    store.close()
    assert owner.encode() not in store.path.read_bytes()


def test_module_shutdown_interrupts_attempts_without_replay_or_credentials(tmp_path):
    async def scenario():
        calls = []
        owner, key = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        store = ReportStore(tmp_path)
        store.open()

        async def gateway(*_args, **_kwargs):
            calls.append(1)
            await asyncio.Event().wait()

        workbench = Workbench(gateway, reports=store)
        capture = workbench.create(
            batch_document(ports=[8080]),
            owner=owner,
            kind="triage",
            scope_requested={
                "kind": "single_port",
                "scope": {"kind": "single_port", "port": 8080},
                "protocol": "all",
                "history_hours": 6,
            },
        )
        workbench.start_ai(capture["id"], owner, "openai", "synthetic-model", key)
        await asyncio.sleep(0)
        await workbench.close()
        store.close()
        assert (
            key.encode() not in store.path.read_bytes()
            and owner.encode() not in store.path.read_bytes()
        )
        store.open()
        restored = Workbench(gateway, reports=store)
        assert restored.get(capture["id"], owner)["status"] == "interrupted"
        with pytest.raises(AnalysisError, match="not_found"):
            restored.start_ai(capture["id"], owner, "openai", "synthetic-model", key)
        assert len(calls) == 1 and store.list_workbench(owner) == ([], None)
        await restored.close()
        store.close()

    asyncio.run(scenario())
