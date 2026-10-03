import asyncio
import json
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.analysis.observation_fixture import observation
from backend.analysis.analysis import Analysis
from backend.analysis.evidence import AnalysisError, prepare_evidence
from backend.analysis.reports import APPLICATION_ID, ReportStore, owner_hash
from tests.analysis.test_analysis import interpretation


@pytest.fixture
def snapshot():
    return {
        "id": secrets.token_urlsafe(24),
        "status": "completed",
        "mode": "demo",
        "provider": "demo",
        "model": "synthetic-demo",
        "prompt_version": "port-analysis.v3",
        "requires_hidden_access": False,
        "evidence": prepare_evidence(observation(), core_version="0.8.4", include_history=True),
        "interpretation": interpretation(),
        "usage": {"total_tokens": 80},
    }


def test_explicit_save_survives_reopen_is_owned_and_idempotent(snapshot, tmp_path):
    owner = secrets.token_urlsafe(24)
    reports = ReportStore(tmp_path)
    reports.open()
    assert reports.list(owner) == []
    saved = reports.save(snapshot, owner)
    assert reports.save(snapshot, owner) == saved
    assert reports.list(owner) == [
        {
            "id": saved["id"],
            "port": 8080,
            "created_at": saved["created_at"],
            "requires_hidden_access": False,
        }
    ]
    assert saved["evidence"] == snapshot["evidence"]
    assert saved["validated_interpretation"] == snapshot["interpretation"]
    assert saved["versions"]["core"] == "0.8.4"
    assert saved["versions"]["interpretation"] == "port-interpretation.v1"
    assert saved["versions"]["render"] == "port-interpretation-zh.v1"
    assert "不能确认应用" in saved["interpretation_view"]["unknowns"][0]["text"]
    assert reports.connection.execute("SELECT owner_hash FROM reports").fetchone()[0] == owner_hash(
        owner
    )
    with pytest.raises(AnalysisError, match="report_not_found"):
        reports.get(saved["id"], secrets.token_urlsafe(24))
    reports.close()
    assert owner.encode() not in reports.path.read_bytes()
    assert reports.path.stat().st_mode & 0o777 == 0o600
    assert reports.path.parent.stat().st_mode & 0o777 == 0o700
    reopened = ReportStore(tmp_path)
    reopened.open()
    assert reopened.get(saved["id"], owner) == saved
    reopened.close()


@pytest.mark.parametrize("limit", ["count", "total", "single"])
def test_storage_limits_do_not_remove_existing_reports(snapshot, tmp_path, limit):
    reports = ReportStore(tmp_path, max_reports=1 if limit == "count" else 256)
    reports.open()
    saved = reports.save(snapshot, "owner")
    size = reports.connection.execute("SELECT bytes FROM reports").fetchone()[0]
    if limit == "total":
        reports.max_total_bytes = size * 2 - 1
    if limit == "single":
        reports.max_report_bytes = 1
    with pytest.raises(AnalysisError, match="report_limit|report_too_large"):
        reports.save({**snapshot, "id": secrets.token_urlsafe(24)}, "owner")
    assert reports.get(saved["id"], "owner") == saved
    assert len(reports.list("owner")) == 1
    reports.close()


def test_parallel_connections_enforce_the_shared_limit(snapshot, tmp_path):
    stores = [ReportStore(tmp_path, max_reports=1) for _ in range(2)]
    for store in stores:
        store.open()
    barrier = threading.Barrier(2)

    def save(index):
        barrier.wait()
        try:
            stores[index].save({**snapshot, "id": secrets.token_urlsafe(24)}, f"owner-{index}")
            return "saved"
        except AnalysisError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(results) == ["report_limit", "saved"]
    assert stores[0].connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    for store in stores:
        store.close()


def test_write_failure_rolls_back_without_a_partial_report(snapshot, tmp_path):
    store = ReportStore(tmp_path)
    store.open()
    store.connection.set_authorizer(
        lambda action, name, *_: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and name == "COMMIT"
            else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(AnalysisError, match="report_storage_unavailable"):
        store.save(snapshot, "owner")
    store.connection.set_authorizer(None)
    assert store.list("owner") == []
    assert store.save(snapshot, "owner")["source_analysis_id"] == snapshot["id"]
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


def test_restart_marks_receipt_interrupted_without_persisting_content(tmp_path):
    store = ReportStore(tmp_path)
    store.open()
    owner, identifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    store.begin_job(identifier, owner, 8080)
    store.close()
    store.open()
    receipt = store.job(identifier, owner)
    assert receipt["status"] == "interrupted" and receipt["result_available"] is False
    assert not {"evidence", "interpretation", "model", "provider", "key"} & receipt.keys()
    assert not store.list(owner)
    store.close()
    assert owner.encode() not in store.path.read_bytes()


def test_module_shutdown_interrupts_attempts_without_replay_or_credentials(snapshot, tmp_path):
    async def scenario():
        calls = []
        owner, key = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        store = ReportStore(tmp_path)
        store.open()

        async def gateway(*_args, **_kwargs):
            calls.append(1)
            await asyncio.Event().wait()

        analysis = Analysis(gateway, reports=store)
        preview = analysis.preview(snapshot["evidence"], owner)
        analysis.start(preview["id"], owner, "openai", "synthetic-model", key)
        await asyncio.sleep(0)
        await analysis.close()
        store.close()
        assert (
            key.encode() not in store.path.read_bytes()
            and owner.encode() not in store.path.read_bytes()
        )
        store.open()
        restored = Analysis(gateway, reports=store)
        assert restored.get(preview["id"], owner)["status"] == "interrupted"
        with pytest.raises(AnalysisError, match="not_found"):
            restored.start(preview["id"], owner, "openai", "synthetic-model", key)
        assert len(calls) == 1 and not store.list(owner)
        await restored.close()
        store.close()

    asyncio.run(scenario())


def test_save_rejects_free_text_even_if_snapshot_claims_completion(snapshot, tmp_path):
    reports = ReportStore(tmp_path)
    reports.open()
    snapshot["interpretation"]["text"] = "The service needs to be restarted."
    snapshot["interpretation_view"] = {"summary": {"text": "forged local template"}}
    with pytest.raises(AnalysisError, match="invalid_output"):
        reports.save(snapshot, "owner")
    assert reports.list("owner") == []
    reports.close()


def test_saved_view_is_generated_from_selections_instead_of_the_snapshot(snapshot, tmp_path):
    reports = ReportStore(tmp_path)
    reports.open()
    snapshot["interpretation_view"] = {"summary": {"text": "forged local template"}}
    saved = reports.save(snapshot, "owner")
    assert "forged local template" not in json.dumps(saved)
    assert "8080" in saved["interpretation_view"]["summary"]["text"]
    reports.close()
