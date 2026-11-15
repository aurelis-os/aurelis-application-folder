"""SQLite state database for AppImage Launcher.

Schema, migrations, and repository classes for apps, tombstones, backups, and events.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from appimage_launcher.constants import (
    DB_USER_VERSION,
    EVENTS_TABLE_MAX_ROWS,
    TOMBSTONE_RETENTION_DAYS,
)
from appimage_launcher.logging import get_logger
from appimage_launcher.paths import get_paths

logger = get_logger(__name__)


@dataclass
class AppRecord:
    """Application record from database."""

    id: int
    app_id: str
    role: str  # primary | kept
    display_name: str
    managed_name: str
    name_origin: str  # auto | user
    orig_filename: str | None
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    fingerprint: str
    version: str | None
    update_info: str | None
    update_policy: str  # auto | manual | pinned
    self_managed: bool
    origin: str | None  # JSON string
    sha256: str | None
    installed_at: int
    updated_at: int


@dataclass
class TombstoneRecord:
    """Tombstone record for removed apps."""

    id: int
    app_id: str
    device: int
    inode: int
    fingerprint: str
    last_name: str
    name_origin: str
    update_policy: str
    self_managed: bool
    origin: str | None
    removed_at: int


@dataclass
class BackupRecord:
    """Backup record for previous versions."""

    id: int
    app_id: str
    path: str
    version: str | None
    size: int
    created_at: int


@dataclass
class EventRecord:
    """Audit event record."""

    id: int
    ts: int
    kind: str
    app_id: str | None
    detail: str | None


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('primary','kept')) DEFAULT 'primary',
    display_name TEXT NOT NULL,
    managed_name TEXT NOT NULL,
    name_origin TEXT NOT NULL CHECK (name_origin IN ('auto','user')),
    orig_filename TEXT,
    path TEXT NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    version TEXT,
    update_info TEXT,
    update_policy TEXT NOT NULL CHECK (update_policy IN ('auto','manual','pinned')) DEFAULT 'auto',
    self_managed INTEGER NOT NULL DEFAULT 0,
    origin TEXT,
    sha256 TEXT,
    installed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS apps_primary_uq ON apps(app_id) WHERE role = 'primary';
CREATE UNIQUE INDEX IF NOT EXISTS apps_inode_uq ON apps(device, inode);
CREATE UNIQUE INDEX IF NOT EXISTS apps_path_uq ON apps(path);

CREATE TABLE IF NOT EXISTS tombstones (
    id INTEGER PRIMARY KEY,
    app_id TEXT,
    device INTEGER,
    inode INTEGER,
    fingerprint TEXT,
    last_name TEXT,
    name_origin TEXT,
    update_policy TEXT,
    self_managed INTEGER,
    origin TEXT,
    removed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY,
    app_id TEXT NOT NULL,
    path TEXT NOT NULL,
    version TEXT,
    size INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    app_id TEXT,
    detail TEXT
);

-- Cap events table
CREATE TRIGGER IF NOT EXISTS events_cap AFTER INSERT ON events
BEGIN
    DELETE FROM events WHERE id IN (
        SELECT id FROM events ORDER BY ts DESC LIMIT -1 OFFSET {EVENTS_TABLE_MAX_ROWS}
    );
END;
"""


MIGRATIONS = [
    # Version 1: Initial schema (already in SCHEMA_SQL)
    "",
    # Version 2: Add origin column to tombstones table
    """
    ALTER TABLE tombstones ADD COLUMN origin TEXT;
    """,
]


class StateDB:
    """SQLite database for application state."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database and run migrations."""
        with self._get_conn() as conn:
            # Enable WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            # Check user_version
            cursor = conn.execute("PRAGMA user_version")
            current_version = cursor.fetchone()[0]

            if current_version == 0:
                # Fresh database - create schema
                conn.executescript(SCHEMA_SQL)
                conn.execute(f"PRAGMA user_version = {DB_USER_VERSION}")
                logger.info("Created new database schema", version=DB_USER_VERSION)
            elif current_version < DB_USER_VERSION:
                # Run migrations
                self._run_migrations(conn, current_version)
            elif current_version > DB_USER_VERSION:
                logger.warning(
                    "Database version newer than code",
                    db_version=current_version,
                    code_version=DB_USER_VERSION,
                )

    def _run_migrations(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Run database migrations."""
        for version in range(from_version + 1, DB_USER_VERSION + 1):
            if version - 1 < len(MIGRATIONS) and MIGRATIONS[version - 1]:
                conn.executescript(MIGRATIONS[version - 1])
                logger.info("Applied migration", version=version)
        conn.execute(f"PRAGMA user_version = {DB_USER_VERSION}")

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get database connection with row factory."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        try:
            yield self._conn
        except Exception:
            self._conn.rollback()
            raise

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # App operations

    def insert_app(self, app: AppRecord) -> int:
        """Insert a new app record."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO apps (
                    app_id, role, display_name, managed_name, name_origin,
                    orig_filename, path, device, inode, size, mtime_ns,
                    fingerprint, version, update_info, update_policy,
                    self_managed, origin, sha256, installed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    app.app_id,
                    app.role,
                    app.display_name,
                    app.managed_name,
                    app.name_origin,
                    app.orig_filename,
                    app.path,
                    app.device,
                    app.inode,
                    app.size,
                    app.mtime_ns,
                    app.fingerprint,
                    app.version,
                    app.update_info,
                    app.update_policy,
                    int(app.self_managed),
                    app.origin,
                    app.sha256,
                    app.installed_at,
                    app.updated_at,
                ),
            )
            return cursor.lastrowid

    def update_app(self, app: AppRecord) -> None:
        """Update an existing app record."""
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE apps SET
                    app_id = ?, role = ?, display_name = ?, managed_name = ?,
                    name_origin = ?, orig_filename = ?, path = ?, device = ?,
                    inode = ?, size = ?, mtime_ns = ?, fingerprint = ?,
                    version = ?, update_info = ?, update_policy = ?,
                    self_managed = ?, origin = ?, sha256 = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    app.app_id,
                    app.role,
                    app.display_name,
                    app.managed_name,
                    app.name_origin,
                    app.orig_filename,
                    app.path,
                    app.device,
                    app.inode,
                    app.size,
                    app.mtime_ns,
                    app.fingerprint,
                    app.version,
                    app.update_info,
                    app.update_policy,
                    int(app.self_managed),
                    app.origin,
                    app.sha256,
                    app.updated_at,
                    app.id,
                ),
            )

    def delete_app(self, app_id: int) -> None:
        """Delete an app record by ID."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))

    def get_app_by_id(self, id: int) -> AppRecord | None:
        """Get app by database ID."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM apps WHERE id = ?", (id,)).fetchone()
            return self._row_to_app(row) if row else None

    def get_app_by_path(self, path: str) -> AppRecord | None:
        """Get app by file path."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM apps WHERE path = ?", (path,)).fetchone()
            return self._row_to_app(row) if row else None

    def get_app_by_inode(self, device: int, inode: int) -> AppRecord | None:
        """Get app by device and inode."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM apps WHERE device = ? AND inode = ?",
                (device, inode),
            ).fetchone()
            return self._row_to_app(row) if row else None

    def get_primary_by_app_id(self, app_id: str) -> AppRecord | None:
        """Get primary app by app_id."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM apps WHERE app_id = ? AND role = 'primary'",
                (app_id,),
            ).fetchone()
            return self._row_to_app(row) if row else None

    def get_kept_by_app_id(self, app_id: str) -> list[AppRecord]:
        """Get all kept apps by app_id."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM apps WHERE app_id = ? AND role = 'kept' ORDER BY version DESC",
                (app_id,),
            ).fetchall()
            return [self._row_to_app(row) for row in rows]

    def list_apps(self, include_kept: bool = False) -> list[AppRecord]:
        """List all apps."""
        with self._get_conn() as conn:
            if include_kept:
                rows = conn.execute("SELECT * FROM apps ORDER BY display_name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM apps WHERE role = 'primary' ORDER BY display_name"
                ).fetchall()
            return [self._row_to_app(row) for row in rows]

    def get_app_by_sha256(self, sha256: str) -> AppRecord | None:
        """Get app by SHA-256 hash."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM apps WHERE sha256 = ? AND role = 'primary'",
                (sha256,),
            ).fetchone()
            return self._row_to_app(row) if row else None

    def get_all_apps_dict(self) -> dict[str, AppRecord]:
        """Get all apps as dict keyed by managed_name."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM apps").fetchall()
            return {self._row_to_app(row).managed_name: self._row_to_app(row) for row in rows}

    # Tombstone operations

    def insert_tombstone(self, tombstone: TombstoneRecord) -> int:
        """Insert a tombstone record."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tombstones (
                    app_id, device, inode, fingerprint, last_name,
                    name_origin, update_policy, self_managed, origin, removed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone.app_id,
                    tombstone.device,
                    tombstone.inode,
                    tombstone.fingerprint,
                    tombstone.last_name,
                    tombstone.name_origin,
                    tombstone.update_policy,
                    int(tombstone.self_managed),
                    tombstone.origin,
                    tombstone.removed_at,
                ),
            )
            return cursor.lastrowid

    def find_tombstone_by_inode(self, device: int, inode: int) -> TombstoneRecord | None:
        """Find tombstone by device and inode."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tombstones WHERE device = ? AND inode = ?",
                (device, inode),
            ).fetchone()
            return self._row_to_tombstone(row) if row else None

    def find_tombstone_by_fingerprint(self, fingerprint: str) -> TombstoneRecord | None:
        """Find tombstone by fingerprint."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tombstones WHERE fingerprint = ? ORDER BY removed_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
            return self._row_to_tombstone(row) if row else None

    def delete_tombstone(self, tombstone_id: int) -> None:
        """Delete a tombstone by ID."""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM tombstones WHERE id = ?", (tombstone_id,))

    def purge_old_tombstones(self) -> int:
        """Purge tombstones older than retention period."""
        cutoff = int(time.time()) - TOMBSTONE_RETENTION_DAYS * 86400
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM tombstones WHERE removed_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    # Backup operations

    def insert_backup(self, backup: BackupRecord) -> int:
        """Insert a backup record."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO backups (app_id, path, version, size, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (backup.app_id, backup.path, backup.version, backup.size, backup.created_at),
            )
            return cursor.lastrowid

    def get_backups(self, app_id: str) -> list[BackupRecord]:
        """Get backups for an app_id."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM backups WHERE app_id = ? ORDER BY created_at DESC",
                (app_id,),
            ).fetchall()
            return [self._row_to_backup(row) for row in rows]

    def delete_old_backups(self, app_id: str, keep: int) -> int:
        """Delete old backups, keeping the newest N."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM backups WHERE app_id = ? AND id NOT IN (
                    SELECT id FROM backups WHERE app_id = ? ORDER BY created_at DESC LIMIT ?
                )
                """,
                (app_id, app_id, keep),
            )
            return cursor.rowcount

    # Event operations

    def insert_event(self, kind: str, app_id: str | None = None, detail: str | None = None) -> int:
        """Insert an audit event."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO events (ts, kind, app_id, detail) VALUES (?, ?, ?, ?)",
                (int(time.time()), kind, app_id, detail),
            )
            return cursor.lastrowid

    def get_events(self, limit: int = 100) -> list[EventRecord]:
        """Get recent events."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_event(row) for row in rows]

    # Row conversion

    def _row_to_app(self, row: sqlite3.Row) -> AppRecord:
        return AppRecord(
            id=row["id"],
            app_id=row["app_id"],
            role=row["role"],
            display_name=row["display_name"],
            managed_name=row["managed_name"],
            name_origin=row["name_origin"],
            orig_filename=row["orig_filename"],
            path=row["path"],
            device=row["device"],
            inode=row["inode"],
            size=row["size"],
            mtime_ns=row["mtime_ns"],
            fingerprint=row["fingerprint"],
            version=row["version"],
            update_info=row["update_info"],
            update_policy=row["update_policy"],
            self_managed=bool(row["self_managed"]),
            origin=row["origin"],
            sha256=row["sha256"],
            installed_at=row["installed_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_tombstone(self, row: sqlite3.Row) -> TombstoneRecord:
        return TombstoneRecord(
            id=row["id"],
            app_id=row["app_id"],
            device=row["device"],
            inode=row["inode"],
            fingerprint=row["fingerprint"],
            last_name=row["last_name"],
            name_origin=row["name_origin"],
            update_policy=row["update_policy"],
            self_managed=bool(row["self_managed"]),
            origin=row["origin"],
            removed_at=row["removed_at"],
        )

    def _row_to_backup(self, row: sqlite3.Row) -> BackupRecord:
        return BackupRecord(
            id=row["id"],
            app_id=row["app_id"],
            path=row["path"],
            version=row["version"],
            size=row["size"],
            created_at=row["created_at"],
        )

    def _row_to_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=row["id"],
            ts=row["ts"],
            kind=row["kind"],
            app_id=row["app_id"],
            detail=row["detail"],
        )


# Global instance
_db: StateDB | None = None


def get_state_db() -> StateDB:
    """Get global StateDB instance."""
    global _db
    if _db is None:
        paths = get_paths()
        _db = StateDB(paths.state_db)
    return _db


def reset_state_db() -> None:
    """Reset global StateDB (for testing)."""
    global _db
    if _db:
        _db.close()
    _db = None
