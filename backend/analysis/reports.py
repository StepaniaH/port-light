"""Explicit report storage and bounded, credential-free job receipts."""

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from . import __version__
from .baseline import baseline
from .evidence import AnalysisError
from .interpretation import (
    INTERPRETATION_VERSION,
    RENDER_VERSION,
    render_interpretation,
    validate_interpretation,
)

SCHEMA_VERSION = 2
REPORT_DOCUMENT_SCHEMA = 1
APPLICATION_ID = 0x504C4152
MAX_REPORTS = 256
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_JOB_RECEIPTS = 512
MAX_WORKBENCH_REPORTS = 128
MAX_WORKBENCH_TOTAL_BYTES = 32 * 1024 * 1024
MAX_WORKBENCH_REPORT_BYTES = 2 * 1024 * 1024
MAX_WORKBENCH_JOB_RECEIPTS = 512
RECEIPT_TTL = 24 * 3600


def owner_hash(owner):
    if not owner:
        raise AnalysisError("report_not_found", "找不到当前会话的报告或任务。", 404)
    return hashlib.sha256(b"port-light-report-owner-v1\0" + owner.encode("ascii")).hexdigest()


def storage_error():
    return AnalysisError("report_storage_unavailable", "报告存储暂不可用，已有数据未被清除。", 503)


class ReportStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        clock=time.time,
        max_reports=MAX_REPORTS,
        max_total_bytes=MAX_TOTAL_BYTES,
        max_report_bytes=MAX_REPORT_BYTES,
        max_workbench_reports=MAX_WORKBENCH_REPORTS,
        max_workbench_total_bytes=MAX_WORKBENCH_TOTAL_BYTES,
        max_workbench_report_bytes=MAX_WORKBENCH_REPORT_BYTES,
    ):
        self.path = data_dir / "state" / "analysis.sqlite3"
        self.clock = clock
        self.max_reports = max_reports
        self.max_total_bytes = max_total_bytes
        self.max_report_bytes = max_report_bytes
        self.max_workbench_reports = max_workbench_reports
        self.max_workbench_total_bytes = max_workbench_total_bytes
        self.max_workbench_report_bytes = max_workbench_report_bytes
        self.connection = None
        self.lock = threading.RLock()

    def open(self):
        """Preserve corrupt/unknown databases and leave the report API unavailable."""
        with self.lock:
            if self.connection is not None:
                return
            connection = None
            try:
                directory = self.path.parent
                if directory.is_symlink() or self.path.is_symlink():
                    raise OSError("Unsafe report storage path")
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                if not directory.is_dir():
                    raise OSError("Report state is not a directory")
                if self.path.exists():
                    info = self.path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise OSError("Unsafe report database")
                else:
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(descriptor)
                connection = sqlite3.connect(self.path, timeout=1, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("Report database integrity check failed")
                self._migrate(connection)
                directory.chmod(0o700)
                self.path.chmod(0o600)
                with connection:
                    connection.execute(
                        "UPDATE jobs SET status='interrupted', updated_at=? WHERE status='running'",
                        (int(self.clock()),),
                    )
                    connection.execute(
                        "UPDATE workbench_jobs SET status='interrupted', updated_at=? WHERE status='running'",
                        (int(self.clock()),),
                    )
                self.connection = connection
            except (OSError, sqlite3.Error, ValueError):
                if connection is not None:
                    connection.close()

    @staticmethod
    def _legacy_schema(connection):
        connection.execute(
            "SELECT id, owner_hash, source_analysis_id, port, created_at, requires_hidden_access, body, bytes FROM reports LIMIT 0"
        )
        connection.execute(
            "SELECT id, owner_hash, port, status, created_at, updated_at FROM jobs LIMIT 0"
        )

    @staticmethod
    def _workbench_schema(connection):
        connection.execute(
            "SELECT id, owner_hash, source_workbench_id, result_revision, source_capture_id, source_report_id, kind, created_at, resources, body, bytes FROM workbench_reports LIMIT 0"
        )
        connection.execute(
            "SELECT id, owner_hash, resources, status, created_at, updated_at FROM workbench_jobs LIMIT 0"
        )

    @staticmethod
    def _create_legacy_tables(connection):
        connection.execute("""CREATE TABLE reports (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL,
            source_analysis_id TEXT NOT NULL, port INTEGER NOT NULL,
            created_at INTEGER NOT NULL, requires_hidden_access INTEGER NOT NULL CHECK(requires_hidden_access IN (0,1)),
            body TEXT NOT NULL, bytes INTEGER NOT NULL,
            UNIQUE(owner_hash, source_analysis_id)
        )""")
        connection.execute("""CREATE TABLE jobs (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, port INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        )""")

    @staticmethod
    def _create_workbench_tables(connection):
        connection.execute("""CREATE TABLE workbench_reports (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL,
            source_workbench_id TEXT NOT NULL, result_revision TEXT NOT NULL,
            source_capture_id TEXT NOT NULL, source_report_id TEXT,
            kind TEXT NOT NULL, created_at INTEGER NOT NULL,
            resources TEXT NOT NULL, body TEXT NOT NULL, bytes INTEGER NOT NULL,
            UNIQUE(owner_hash, source_workbench_id, result_revision)
        )""")
        connection.execute("""CREATE TABLE workbench_jobs (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, resources TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        )""")

    @staticmethod
    def _migrate(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        if version == SCHEMA_VERSION and application == APPLICATION_ID:
            ReportStore._legacy_schema(connection)
            ReportStore._workbench_schema(connection)
            return
        if version == 1 and application == APPLICATION_ID:
            ReportStore._legacy_schema(connection)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                ReportStore._create_workbench_tables(connection)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if version != 0 or application != 0 or tables:
            raise sqlite3.DatabaseError("Unsupported report schema; preserve the original database")
        # Only an empty database has a first-install path.  A failed transaction
        # cannot leave either the legacy API or the workbench tables partially initialized.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            ReportStore._create_legacy_tables(connection)
            ReportStore._create_workbench_tables(connection)
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _database(self, *, write=False):
        with self.lock:
            if self.connection is None:
                raise storage_error()
            try:
                with self.connection:
                    if write:
                        self.connection.execute("BEGIN IMMEDIATE")
                    yield self.connection
            except (sqlite3.Error, ValueError, TypeError, KeyError):
                raise storage_error() from None

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None

    def metadata(self):
        return {
            "available": self.connection is not None,
            "max_reports": self.max_reports,
            "max_total_bytes": self.max_total_bytes,
            "max_report_bytes": self.max_report_bytes,
            "workbench": {
                "max_reports": self.max_workbench_reports,
                "max_total_bytes": self.max_workbench_total_bytes,
                "max_report_bytes": self.max_workbench_report_bytes,
            },
        }

    def begin_job(self, identifier, owner, port):
        digest, now = owner_hash(owner), int(self.clock())
        with self._database(write=True) as database:
            database.execute(
                "DELETE FROM jobs WHERE status != 'running' AND updated_at < ?",
                (now - RECEIPT_TTL,),
            )
            if database.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= MAX_JOB_RECEIPTS:
                raise AnalysisError("job_capacity", "任务状态记录已满，请稍后再试。", 429)
            database.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, 'running', ?, ?)",
                (identifier, digest, port, now, now),
            )

    def finish_job(self, identifier, owner, status):
        with self._database(write=True) as database:
            database.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=? AND owner_hash=?",
                (status, int(self.clock()), identifier, owner_hash(owner)),
            )

    def job(self, identifier, owner):
        with self._database() as database:
            row = database.execute(
                "SELECT id, port, status FROM jobs WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("not_found", "预览或任务已过期，请重新预览。", 404)
            return {
                **dict(row),
                "result_available": False,
                "error": {
                    "code": "interrupted"
                    if row["status"] == "interrupted"
                    else "result_unavailable",
                    "message": "服务已停止这项任务，不会自动重新调用模型。请查看已保存的报告，或重新预览。",
                },
            }

    def save(self, snapshot, owner):
        if snapshot.get("status") != "completed" or not snapshot.get("interpretation"):
            raise AnalysisError("not_ready", "只有已完成且仍可读取的分析才能保存。", 409)
        evidence = snapshot["evidence"]
        interpretation = validate_interpretation(json.dumps(snapshot["interpretation"]), evidence)
        digest = owner_hash(owner)
        document = {
            "id": secrets.token_urlsafe(24),
            "schema_version": REPORT_DOCUMENT_SCHEMA,
            "created_at": int(self.clock()),
            "source_analysis_id": snapshot["id"],
            "requires_hidden_access": snapshot["requires_hidden_access"],
            "evidence": evidence,
            "baseline": baseline(evidence),
            "validated_interpretation": interpretation,
            "interpretation_view": render_interpretation(interpretation, evidence),
            "versions": {
                "core": evidence["core_version"],
                "module": __version__,
                "evidence": evidence["evidence_version"],
                "prompt": snapshot["prompt_version"],
                "baseline": baseline(evidence)["version"],
                "interpretation": INTERPRETATION_VERSION,
                "render": RENDER_VERSION,
            },
            "mode": snapshot["mode"],
            "provider": snapshot["provider"],
            "model": snapshot["model"],
            "usage": {
                key: value
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if type(value := snapshot["usage"].get(key)) is int and 0 <= value <= 10**9
            },
        }
        body = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False)
        size = len(body.encode("utf-8"))
        if size > self.max_report_bytes:
            raise AnalysisError("report_too_large", "报告超过单份大小限制，未保存任何内容。", 413)
        with self._database(write=True) as database:
            previous = database.execute(
                "SELECT body FROM reports WHERE owner_hash=? AND source_analysis_id=?",
                (digest, snapshot["id"]),
            ).fetchone()
            if previous is not None:
                return json.loads(previous[0])
            count, total = database.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM reports"
            ).fetchone()
            if count >= self.max_reports or total + size > self.max_total_bytes:
                raise AnalysisError(
                    "report_limit", "报告保存空间已满；已有报告仍可查看和导出。", 409
                )
            database.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document["id"],
                    digest,
                    snapshot["id"],
                    evidence["port"],
                    document["created_at"],
                    int(document["requires_hidden_access"]),
                    body,
                    size,
                ),
            )
        return document

    def get(self, identifier, owner):
        with self._database() as database:
            row = database.execute(
                "SELECT body FROM reports WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("report_not_found", "找不到当前会话的报告。", 404)
            return json.loads(row[0])

    def list(self, owner):
        if not owner:
            return []
        with self._database() as database:
            rows = database.execute(
                "SELECT id, port, created_at, requires_hidden_access FROM reports WHERE owner_hash=? ORDER BY created_at DESC, id",
                (owner_hash(owner),),
            ).fetchall()
            return [
                {**dict(row), "requires_hidden_access": bool(row["requires_hidden_access"])}
                for row in rows
            ]

    @staticmethod
    def _workbench_resources(value):
        if not isinstance(value, list):
            raise AnalysisError("not_ready", "排障采集缺少可核对的资源集合。", 409)
        resources = []
        for item in value:
            if (
                not isinstance(item, dict)
                or type(item.get("port")) is not int
                or not 1 <= item["port"] <= 65535
                or item.get("protocol") not in {"tcp", "udp"}
            ):
                raise AnalysisError("not_ready", "排障采集资源格式无效。", 409)
            resources.append({"port": item["port"], "protocol": item["protocol"]})
        resources = sorted(
            {(item["port"], item["protocol"]): item for item in resources}.values(),
            key=lambda item: (item["port"], item["protocol"]),
        )
        if len({item["port"] for item in resources}) > 1024:
            raise AnalysisError("not_ready", "排障采集超出可保存的资源上限。", 409)
        return resources

    @staticmethod
    def _workbench_document(snapshot, *, identifier, created_at):
        if not isinstance(snapshot, dict) or snapshot.get("status") == "running":
            raise AnalysisError("not_ready", "采集仍在运行，暂时不能保存报告。", 409)
        required = {
            "id": str,
            "capture_id": str,
            "kind": str,
            "result_revision": str,
            "scope_requested": dict,
            "coverage": dict,
            "facts": dict,
            "problems": list,
            "priority_queue": list,
            "ai_preview": dict,
            "ai": dict,
        }
        if any(not isinstance(snapshot.get(key), expected) for key, expected in required.items()):
            raise AnalysisError("not_ready", "排障采集不完整，不能保存报告。", 409)
        if not snapshot["id"] or not snapshot["capture_id"] or not snapshot["result_revision"]:
            raise AnalysisError("not_ready", "排障采集标识无效。", 409)
        resources = ReportStore._workbench_resources(snapshot.get("resources"))
        capture = deepcopy(snapshot)
        # Report IDs belong to storage and must not become mutable capture data.
        capture.pop("report_id", None)
        capture.pop("report_ids", None)
        return {
            "id": identifier,
            "schema_version": REPORT_DOCUMENT_SCHEMA,
            "created_at": created_at,
            "kind": capture["kind"],
            "scope_summary": deepcopy(capture["scope_requested"]),
            "source_workbench_id": capture["id"],
            "source_capture_id": capture["capture_id"],
            "source_report_id": capture.get("source_report_id"),
            "result_revision": capture["result_revision"],
            "resources": resources,
            "capture": capture,
        }

    def save_workbench(self, snapshot, owner):
        digest, now = owner_hash(owner), int(self.clock())
        document = self._workbench_document(
            snapshot, identifier=secrets.token_urlsafe(24), created_at=now
        )
        body = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False)
        size = len(body.encode("utf-8"))
        if size > self.max_workbench_report_bytes:
            raise AnalysisError(
                "report_too_large", "排障报告超过单份大小限制，未保存任何内容。", 413
            )
        resources = json.dumps(document["resources"], sort_keys=True, separators=(",", ":"))
        with self._database(write=True) as database:
            previous = database.execute(
                "SELECT body FROM workbench_reports WHERE owner_hash=? AND source_workbench_id=? AND result_revision=?",
                (digest, document["source_workbench_id"], document["result_revision"]),
            ).fetchone()
            if previous is not None:
                return json.loads(previous[0])
            count, total = database.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM workbench_reports"
            ).fetchone()
            if count >= self.max_workbench_reports or total + size > self.max_workbench_total_bytes:
                raise AnalysisError(
                    "report_limit", "排障报告保存空间已满；已有报告仍可查看和导出。", 409
                )
            database.execute(
                "INSERT INTO workbench_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document["id"],
                    digest,
                    document["source_workbench_id"],
                    document["result_revision"],
                    document["source_capture_id"],
                    document["source_report_id"],
                    document["kind"],
                    document["created_at"],
                    resources,
                    body,
                    size,
                ),
            )
        return document

    def get_workbench(self, identifier, owner):
        with self._database() as database:
            row = database.execute(
                "SELECT body FROM workbench_reports WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("report_not_found", "找不到当前会话的排障报告。", 404)
            return json.loads(row[0])

    def list_workbench(self, owner, *, limit=20, cursor=None):
        if not owner:
            return [], None
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AnalysisError("invalid_input", "报告列表分页参数无效。", 422)
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 64):
            raise AnalysisError("invalid_input", "报告列表游标无效。", 422)
        digest = owner_hash(owner)
        with self._database() as database:
            parameters = [digest]
            where = "owner_hash=?"
            if cursor:
                row = database.execute(
                    "SELECT created_at, id FROM workbench_reports WHERE id=? AND owner_hash=?",
                    (cursor, digest),
                ).fetchone()
                if row is None:
                    raise AnalysisError("invalid_input", "报告列表游标无效。", 422)
                where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
                parameters.extend((row["created_at"], row["created_at"], row["id"]))
            rows = database.execute(
                "SELECT id, created_at, kind, source_report_id, result_revision, resources, body "
                f"FROM workbench_reports WHERE {where} "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
        next_cursor = rows[limit - 1]["id"] if len(rows) > limit else None
        rows = rows[:limit]
        records = []
        for row in rows:
            try:
                resources = json.loads(row["resources"])
                scope_summary = json.loads(row["body"])["scope_summary"]
            except (TypeError, ValueError, KeyError):
                raise storage_error() from None
            records.append(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "kind": row["kind"],
                    "source_report_id": row["source_report_id"],
                    "result_revision": row["result_revision"],
                    "scope_summary": scope_summary,
                    "resources": resources,
                }
            )
        return records, next_cursor

    def begin_workbench_job(self, identifier, owner, resources):
        digest, now = owner_hash(owner), int(self.clock())
        resources = self._workbench_resources(
            [{"port": port, "protocol": "tcp"} for port in sorted(set(resources))]
        )
        # A receipt needs only the port set.  Store a fixed protocol marker so
        # that the same authorization validator can read it after a restart.
        encoded = json.dumps(resources, sort_keys=True, separators=(",", ":"))
        with self._database(write=True) as database:
            database.execute(
                "DELETE FROM workbench_jobs WHERE status != 'running' AND updated_at < ?",
                (now - RECEIPT_TTL,),
            )
            if (
                database.execute("SELECT COUNT(*) FROM workbench_jobs").fetchone()[0]
                >= MAX_WORKBENCH_JOB_RECEIPTS
            ):
                raise AnalysisError("job_capacity", "任务状态记录已满，请稍后再试。", 429)
            database.execute(
                "INSERT INTO workbench_jobs VALUES (?, ?, ?, 'running', ?, ?)",
                (identifier, digest, encoded, now, now),
            )

    def finish_workbench_job(self, identifier, owner, status):
        with self._database(write=True) as database:
            database.execute(
                "UPDATE workbench_jobs SET status=?, updated_at=? WHERE id=? AND owner_hash=?",
                (status, int(self.clock()), identifier, owner_hash(owner)),
            )

    def workbench_job(self, identifier, owner):
        with self._database() as database:
            row = database.execute(
                "SELECT id, resources, status FROM workbench_jobs WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("not_found", "排障采集已过期，请重新采集。", 404)
            try:
                resources = json.loads(row["resources"])
            except (TypeError, ValueError):
                raise storage_error() from None
            return {
                "id": row["id"],
                "status": row["status"],
                "resources": resources,
                "result_available": False,
                "error": {
                    "code": "interrupted"
                    if row["status"] == "interrupted"
                    else "result_unavailable",
                    "message": "服务已停止这项模型检查，不会自动重新调用模型。请重新采集后再试。",
                },
            }
