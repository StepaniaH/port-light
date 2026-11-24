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

from .evidence import AnalysisError

SCHEMA_VERSION = 3
REPORT_DOCUMENT_SCHEMA = 1
APPLICATION_ID = 0x504C4152
MAX_REPORTS = 256
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_WORKBENCH_REPORTS = 128
MAX_WORKBENCH_TOTAL_BYTES = 32 * 1024 * 1024
MAX_WORKBENCH_REPORT_BYTES = 2 * 1024 * 1024
MAX_WORKBENCH_JOB_RECEIPTS = 512
RECEIPT_TTL = 24 * 3600


def owner_hash(owner):
    if not owner:
        raise AnalysisError("report_not_found", "Report not found for current session.", 404)
    return hashlib.sha256(b"port-light-report-owner-v1\0" + owner.encode("ascii")).hexdigest()


def storage_error():
    return AnalysisError(
        "report_storage_unavailable",
        "Report storage is unavailable; existing data was preserved.",
        503,
    )


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
    def _workbench_schema_v2(connection):
        connection.execute(
            "SELECT id, owner_hash, source_workbench_id, result_revision, source_capture_id, source_report_id, kind, created_at, resources, body, bytes FROM workbench_reports LIMIT 0"
        )
        connection.execute(
            "SELECT id, owner_hash, resources, status, created_at, updated_at FROM workbench_jobs LIMIT 0"
        )

    @staticmethod
    def _workbench_schema(connection):
        connection.execute(
            "SELECT id, owner_hash, source_workbench_id, result_revision, source_capture_id, source_report_id, kind, created_at, resources, body, bytes, scope_summary FROM workbench_reports LIMIT 0"
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
            scope_summary TEXT NOT NULL,
            UNIQUE(owner_hash, source_workbench_id, result_revision)
        )""")
        connection.execute("""CREATE TABLE workbench_jobs (
            id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, resources TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        )""")

    @staticmethod
    def _backfill_scope_summary(connection):
        rows = connection.execute("SELECT id, body FROM workbench_reports").fetchall()
        for row in rows:
            document = json.loads(row["body"])
            if not isinstance(document, dict) or not isinstance(
                document.get("scope_summary"), dict
            ):
                raise ValueError("Invalid workbench report scope_summary")
            encoded = json.dumps(
                document["scope_summary"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            connection.execute(
                "UPDATE workbench_reports SET scope_summary=? WHERE id=?",
                (encoded, row["id"]),
            )

    @staticmethod
    def _migrate(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        if version == SCHEMA_VERSION and application == APPLICATION_ID:
            ReportStore._legacy_schema(connection)
            ReportStore._workbench_schema(connection)
            return
        if version == 2 and application == APPLICATION_ID:
            ReportStore._legacy_schema(connection)
            ReportStore._workbench_schema_v2(connection)
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "ALTER TABLE workbench_reports ADD COLUMN scope_summary TEXT NOT NULL DEFAULT '{}'"
                )
                ReportStore._backfill_scope_summary(connection)
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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
        with self.lock:
            used_reports = 0
            used_bytes = 0
            available = self.connection is not None
            if available:
                try:
                    used_reports, used_bytes = self.connection.execute(
                        "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM workbench_reports"
                    ).fetchone()
                except sqlite3.Error:
                    available = False
            return {
                "available": available,
                "max_reports": self.max_reports,
                "max_total_bytes": self.max_total_bytes,
                "max_report_bytes": self.max_report_bytes,
                "used_workbench_reports": int(used_reports),
                "used_workbench_bytes": int(used_bytes),
                "workbench": {
                    "max_reports": self.max_workbench_reports,
                    "max_total_bytes": self.max_workbench_total_bytes,
                    "max_report_bytes": self.max_workbench_report_bytes,
                },
            }

    def get(self, identifier, owner):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
            raise AnalysisError("report_not_found", "Report not found for current session.", 404)
        with self._database() as database:
            row = database.execute(
                "SELECT body FROM reports WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("report_not_found", "Report not found for current session.", 404)
            return json.loads(row[0])

    def list(self, owner):
        if not owner:
            with self._database():
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

    def delete(self, identifier, owner):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
            raise AnalysisError("report_not_found", "Report not found for current session.", 404)
        digest = owner_hash(owner)
        with self._database(write=True) as database:
            cursor = database.execute(
                "DELETE FROM reports WHERE id=? AND owner_hash=?",
                (identifier, digest),
            )
            if cursor.rowcount == 0:
                raise AnalysisError(
                    "report_not_found", "Report not found for current session.", 404
                )

    def delete_all(self, owner) -> int:
        with self._database(write=True) as database:
            if not owner:
                return 0
            cursor = database.execute(
                "DELETE FROM reports WHERE owner_hash=?",
                (owner_hash(owner),),
            )
            return cursor.rowcount

    @staticmethod
    def _workbench_resources(value):
        if not isinstance(value, list):
            raise AnalysisError("not_ready", "Capture is missing verifiable resources.", 409)
        resources = []
        for item in value:
            if (
                not isinstance(item, dict)
                or type(item.get("port")) is not int
                or not 1 <= item["port"] <= 65535
                or item.get("protocol") not in {"tcp", "udp"}
            ):
                raise AnalysisError("not_ready", "Capture resource entry is invalid.", 409)
            resources.append({"port": item["port"], "protocol": item["protocol"]})
        resources = sorted(
            {(item["port"], item["protocol"]): item for item in resources}.values(),
            key=lambda item: (item["port"], item["protocol"]),
        )
        if len({item["port"] for item in resources}) > 1024:
            raise AnalysisError("not_ready", "Capture exceeds the savable resource limit.", 409)
        return resources

    @staticmethod
    def _workbench_document(snapshot, *, identifier, created_at):
        if not isinstance(snapshot, dict) or snapshot.get("status") == "running":
            raise AnalysisError(
                "not_ready", "Capture is still running and cannot be saved yet.", 409
            )
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
            raise AnalysisError("not_ready", "Capture is incomplete and cannot be saved.", 409)
        if not snapshot["id"] or not snapshot["capture_id"] or not snapshot["result_revision"]:
            raise AnalysisError("not_ready", "Capture identifier is invalid.", 409)
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
                "report_too_large",
                "Report exceeds the single-report size limit; nothing was saved.",
                413,
            )
        resources = json.dumps(document["resources"], sort_keys=True, separators=(",", ":"))
        scope_summary = json.dumps(
            document["scope_summary"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._database(write=True) as database:
            previous = database.execute(
                "SELECT body FROM workbench_reports WHERE owner_hash=? AND source_workbench_id=? AND result_revision=?",
                (digest, document["source_workbench_id"], document["result_revision"]),
            ).fetchone()
            if previous is not None:
                return json.loads(previous[0])
            if self.max_workbench_reports < 1 or size > self.max_workbench_total_bytes:
                raise AnalysisError(
                    "report_limit",
                    "Report storage limit reached; existing reports remain readable.",
                    409,
                )
            while True:
                count, total = database.execute(
                    "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM workbench_reports"
                ).fetchone()
                if (
                    count < self.max_workbench_reports
                    and total + size <= self.max_workbench_total_bytes
                ):
                    break
                oldest = database.execute(
                    "SELECT id FROM workbench_reports ORDER BY created_at ASC, id ASC LIMIT 1"
                ).fetchone()
                if oldest is None:
                    raise AnalysisError(
                        "report_limit",
                        "Report storage limit reached; existing reports remain readable.",
                        409,
                    )
                database.execute("DELETE FROM workbench_reports WHERE id=?", (oldest["id"],))
            database.execute(
                "INSERT INTO workbench_reports ("
                "id, owner_hash, source_workbench_id, result_revision, "
                "source_capture_id, source_report_id, kind, created_at, "
                "resources, body, bytes, scope_summary"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    scope_summary,
                ),
            )
        return document

    def get_workbench(self, identifier, owner):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
            raise AnalysisError("report_not_found", "Report not found for current session.", 404)
        with self._database() as database:
            row = database.execute(
                "SELECT body FROM workbench_reports WHERE id=? AND owner_hash=?",
                (identifier, owner_hash(owner)),
            ).fetchone()
            if row is None:
                raise AnalysisError("report_not_found", "Report not found for current session.", 404)
            return json.loads(row[0])

    def delete_workbench(self, identifier, owner):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
            raise AnalysisError("report_not_found", "Report not found for current session.", 404)
        digest = owner_hash(owner)
        with self._database(write=True) as database:
            cursor = database.execute(
                "DELETE FROM workbench_reports WHERE id=? AND owner_hash=?",
                (identifier, digest),
            )
            if cursor.rowcount == 0:
                raise AnalysisError(
                    "report_not_found", "Report not found for current session.", 404
                )

    def delete_all_workbench(self, owner) -> int:
        with self._database(write=True) as database:
            if not owner:
                return 0
            cursor = database.execute(
                "DELETE FROM workbench_reports WHERE owner_hash=?",
                (owner_hash(owner),),
            )
            return cursor.rowcount

    def list_workbench(self, owner, *, limit=20, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise AnalysisError("invalid_input", "Invalid report pagination parameters.", 422)
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 64):
            raise AnalysisError("invalid_input", "Invalid report list cursor.", 422)
        if not owner:
            with self._database():
                return [], None
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
                    raise AnalysisError("invalid_input", "Invalid report list cursor.", 422)
                where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
                parameters.extend((row["created_at"], row["created_at"], row["id"]))
            rows = database.execute(
                "SELECT id, created_at, kind, source_report_id, result_revision, resources, scope_summary "
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
                scope_summary = json.loads(row["scope_summary"])
                if not isinstance(scope_summary, dict):
                    raise ValueError("invalid scope_summary")
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
                raise AnalysisError(
                    "job_capacity", "Job receipt capacity is full; try again shortly.", 429
                )
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
                raise AnalysisError("not_found", "Capture has expired; run a new capture.", 404)
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
                    "message": (
                        "The service stopped this model check without retrying. "
                        "Run a new capture to try again."
                    ),
                },
            }
