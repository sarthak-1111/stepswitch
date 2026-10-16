"""Per-project state in one SQLite database (SPEC §3, ARCHITECTURE §10).

WAL mode needs a local disk and SQLite >= 3.51.3 (or the 3.50.7 / 3.44.6 backports); older
versions have a WAL-reset corruption bug (ADR 0002). On network volumes, or with an affected
SQLite, the database falls back to the rollback journal.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from stepswitch import paths

SAFE_WAL_VERSIONS = ((3, 51, 3), (3, 50, 7), (3, 44, 6))

MIGRATIONS: list[str] = [
    # 1: Phase 1 tables
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE egress_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, method TEXT, scheme TEXT, host TEXT, port INTEGER, path TEXT,
        purpose TEXT, task_id TEXT, allowed INTEGER NOT NULL, local INTEGER NOT NULL,
        status INTEGER, bytes_out INTEGER, bytes_in INTEGER, error TEXT
    );
    CREATE TABLE probes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created REAL NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL,
        format TEXT NOT NULL, profile TEXT NOT NULL
    );
    CREATE INDEX probes_endpoint_model ON probes(endpoint, model, created);
    CREATE TABLE calibrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created REAL NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL,
        prefill_tps REAL, decode_tps REAL, ttft_ms REAL, details TEXT NOT NULL
    );
    """,
    # 2: Phase 2 tables: tasks, events (trajectories), decisions and the ledger
    """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        created REAL NOT NULL, updated REAL NOT NULL,
        status TEXT NOT NULL, stop_reason TEXT,
        track TEXT NOT NULL, instruction TEXT NOT NULL,
        spec TEXT NOT NULL, result TEXT
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL, ts REAL NOT NULL, seq INTEGER NOT NULL,
        type TEXT NOT NULL, step INTEGER, data TEXT NOT NULL
    );
    CREATE INDEX events_task ON events(task_id, seq);
    CREATE TABLE decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL, step INTEGER NOT NULL, ts REAL NOT NULL,
        step_type TEXT NOT NULL, chosen TEXT, switched INTEGER NOT NULL,
        reason TEXT NOT NULL, data TEXT NOT NULL
    );
    CREATE INDEX decisions_task ON decisions(task_id, step);
    CREATE TABLE ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL, step INTEGER NOT NULL, ts REAL NOT NULL,
        step_type TEXT NOT NULL, endpoint TEXT NOT NULL, model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
        cached_input_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL,
        wall_seconds REAL NOT NULL, gpu_seconds REAL NOT NULL,
        usage_source TEXT NOT NULL, data TEXT NOT NULL
    );
    CREATE INDEX ledger_task ON ledger(task_id, step);
    """,
]


def wal_is_safe(version: str | None = None) -> bool:
    parts = tuple(int(p) for p in (version or sqlite3.sqlite_version).split(".")[:3])
    for safe in SAFE_WAL_VERSIONS:
        if parts[:2] == safe[:2]:
            return parts >= safe
    return parts >= SAFE_WAL_VERSIONS[0]


def is_network_path(path: Path) -> bool:
    """Best-effort detection of network volumes, where WAL must not be used."""
    resolved = str(path.resolve())
    if sys.platform == "win32":
        if resolved.startswith("\\\\"):
            return True
        try:
            import ctypes

            drive = resolved[:3]
            drive_remote = 4
            return int(ctypes.windll.kernel32.GetDriveTypeW(drive)) == drive_remote
        except Exception:  # noqa: BLE001 - unknown means not network
            return False
    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    network = {"nfs", "nfs4", "cifs", "smbfs", "smb3", "sshfs", "fuse.sshfs", "9p"}
    best = ("", "")
    for line in mounts:
        fields = line.split()
        if len(fields) >= 3 and resolved.startswith(fields[1]) and len(fields[1]) > len(best[0]):
            best = (fields[1], fields[2])
    return best[1] in network


class StateDB:
    """Thin wrapper over a SQLite connection with migrations and JSON helpers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.journal_mode = self._choose_journal_mode()
        self._migrate()

    def _choose_journal_mode(self) -> str:
        wanted = "wal" if wal_is_safe() and not is_network_path(self.path.parent) else "delete"
        row = self.conn.execute(f"PRAGMA journal_mode = {wanted}").fetchone()
        return str(row[0]).lower() if row else wanted

    def _migrate(self) -> None:
        with self._lock:
            version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
            for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self.conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {index};\nCOMMIT;"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # -- domain helpers -------------------------------------------------------------------

    def record_audit(self, row: dict[str, Any]) -> None:
        columns = (
            "ts",
            "method",
            "scheme",
            "host",
            "port",
            "path",
            "purpose",
            "task_id",
            "allowed",
            "local",
            "status",
            "bytes_out",
            "bytes_in",
            "error",
        )
        values = tuple(row.get(c) for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        self.execute(
            f"INSERT INTO egress_audit ({', '.join(columns)}) VALUES ({placeholders})", values
        )

    def save_probe(self, endpoint: str, model: str, fmt: str, profile: dict[str, Any]) -> int:
        cursor = self.execute(
            "INSERT INTO probes (created, endpoint, model, format, profile) VALUES (?, ?, ?, ?, ?)",
            (time.time(), endpoint, model, fmt, json.dumps(profile, sort_keys=True)),
        )
        return int(cursor.lastrowid or 0)

    def latest_probe(self, endpoint: str, model: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT profile FROM probes WHERE endpoint = ? AND model = ?"
            " ORDER BY created DESC LIMIT 1",
            (endpoint, model),
        )
        return json.loads(rows[0]["profile"]) if rows else None

    def save_calibration(
        self,
        endpoint: str,
        model: str,
        prefill_tps: float | None,
        decode_tps: float | None,
        ttft_ms: float | None,
        details: dict[str, Any],
    ) -> None:
        self.execute(
            "INSERT INTO calibrations (created, endpoint, model, prefill_tps, decode_tps, ttft_ms,"
            " details) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), endpoint, model, prefill_tps, decode_tps, ttft_ms, json.dumps(details)),
        )


def open_project_state(root: Path) -> StateDB:
    return StateDB(paths.state_db_path(root))
