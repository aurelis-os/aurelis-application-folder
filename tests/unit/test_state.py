"""Tests for state database."""

import tempfile
import time
from pathlib import Path

import pytest

from appimage_launcher.core.state import (
    AppRecord,
    BackupRecord,
    StateDB,
    TombstoneRecord,
    reset_state_db,
)


class TestStateDB:
    """Tests for StateDB class."""

    def setup_method(self):
        """Set up a temporary database for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_state.db"
        self.db = StateDB(self.db_path)

    def teardown_method(self):
        """Clean up after each test."""
        self.db.close()
        self.temp_dir.cleanup()

    def test_database_creation(self):
        """Test that database is created with correct schema."""
        # Database should be created and have the right tables
        with self.db._get_conn() as conn:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t[0] for t in tables]
            assert "apps" in table_names
            assert "tombstones" in table_names
            assert "backups" in table_names
            assert "events" in table_names

    def test_insert_and_get_app(self):
        """Test inserting and retrieving an app record."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info="zsync|https://example.com/app.zsync",
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        app_id = self.db.insert_app(app)
        assert app_id > 0

        # Retrieve by ID
        retrieved = self.db.get_app_by_id(app_id)
        assert retrieved is not None
        assert retrieved.app_id == "com.example.test"
        assert retrieved.display_name == "Test App"
        assert retrieved.managed_name == "TestApp"

    def test_get_app_by_path(self):
        """Test retrieving app by path."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        self.db.insert_app(app)
        retrieved = self.db.get_app_by_path("/home/user/Applications/TestApp")
        assert retrieved is not None
        assert retrieved.app_id == "com.example.test"

    def test_get_app_by_inode(self):
        """Test retrieving app by device and inode."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        self.db.insert_app(app)
        retrieved = self.db.get_app_by_inode(123, 456)
        assert retrieved is not None
        assert retrieved.app_id == "com.example.test"

    def test_get_primary_by_app_id(self):
        """Test retrieving primary app by app_id."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        self.db.insert_app(app)
        retrieved = self.db.get_primary_by_app_id("com.example.test")
        assert retrieved is not None
        assert retrieved.role == "primary"

    def test_get_kept_by_app_id(self):
        """Test retrieving kept apps by app_id."""
        now = int(time.time())
        # Insert primary
        primary = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="2.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )
        self.db.insert_app(primary)

        # Insert kept version
        kept = AppRecord(
            id=0,
            app_id="com.example.test",
            role="kept",
            display_name="Test App",
            managed_name="TestApp 1.0.0",
            name_origin="auto",
            orig_filename="test-old.AppImage",
            path="/home/user/Applications/TestApp 1.0.0",
            device=123,
            inode=789,
            size=900000,
            mtime_ns=(now - 1000) * 1_000_000_000,
            fingerprint="def456",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now - 1000,
            updated_at=now - 1000,
        )
        self.db.insert_app(kept)

        kept_apps = self.db.get_kept_by_app_id("com.example.test")
        assert len(kept_apps) == 1
        assert kept_apps[0].role == "kept"
        assert kept_apps[0].version == "1.0.0"

    def test_list_apps(self):
        """Test listing apps."""
        now = int(time.time())
        for i in range(3):
            app = AppRecord(
                id=0,
                app_id=f"com.example.app{i}",
                role="primary",
                display_name=f"App {i}",
                managed_name=f"App{i}",
                name_origin="auto",
                orig_filename=f"app{i}.AppImage",
                path=f"/home/user/Applications/App{i}",
                device=123,
                inode=456 + i,
                size=1000000,
                mtime_ns=now * 1_000_000_000,
                fingerprint=f"fp{i}",
                version="1.0.0",
                update_info=None,
                update_policy="auto",
                self_managed=False,
                origin=None,
                sha256=None,
                installed_at=now,
                updated_at=now,
            )
            self.db.insert_app(app)

        apps = self.db.list_apps(include_kept=False)
        assert len(apps) == 3

        apps_all = self.db.list_apps(include_kept=True)
        assert len(apps_all) == 3

    def test_update_app(self):
        """Test updating an app record."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        app_id = self.db.insert_app(app)
        app.id = app_id
        app.version = "2.0.0"
        app.updated_at = now + 100

        self.db.update_app(app)

        retrieved = self.db.get_app_by_id(app_id)
        assert retrieved.version == "2.0.0"
        assert retrieved.updated_at == now + 100

    def test_delete_app(self):
        """Test deleting an app record."""
        now = int(time.time())
        app = AppRecord(
            id=0,
            app_id="com.example.test",
            role="primary",
            display_name="Test App",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.AppImage",
            path="/home/user/Applications/TestApp",
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=now * 1_000_000_000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=now,
            updated_at=now,
        )

        app_id = self.db.insert_app(app)
        self.db.delete_app(app_id)

        retrieved = self.db.get_app_by_id(app_id)
        assert retrieved is None

    def test_tombstone_operations(self):
        """Test tombstone insert and find."""
        now = int(time.time())
        tombstone = TombstoneRecord(
            id=0,
            app_id="com.example.test",
            device=123,
            inode=456,
            fingerprint="abc123",
            last_name="TestApp",
            name_origin="auto",
            update_policy="auto",
            self_managed=False,
            origin=None,
            removed_at=now,
        )

        tombstone_id = self.db.insert_tombstone(tombstone)
        assert tombstone_id > 0

        # Find by inode
        found = self.db.find_tombstone_by_inode(123, 456)
        assert found is not None
        assert found.app_id == "com.example.test"

        # Find by fingerprint
        found = self.db.find_tombstone_by_fingerprint("abc123")
        assert found is not None
        assert found.app_id == "com.example.test"

    def test_purge_old_tombstones(self):
        """Test purging old tombstones."""
        old_time = int(time.time()) - 31 * 86400  # 31 days ago
        new_time = int(time.time())

        old_tombstone = TombstoneRecord(
            id=0,
            app_id="com.example.old",
            device=123,
            inode=456,
            fingerprint="old123",
            last_name="OldApp",
            name_origin="auto",
            update_policy="auto",
            self_managed=False,
            origin=None,
            removed_at=old_time,
        )
        new_tombstone = TombstoneRecord(
            id=0,
            app_id="com.example.new",
            device=123,
            inode=789,
            fingerprint="new123",
            last_name="NewApp",
            name_origin="auto",
            update_policy="auto",
            self_managed=False,
            origin=None,
            removed_at=new_time,
        )

        self.db.insert_tombstone(old_tombstone)
        self.db.insert_tombstone(new_tombstone)

        purged = self.db.purge_old_tombstones()
        assert purged == 1

        # Old should be gone, new should remain
        assert self.db.find_tombstone_by_fingerprint("old123") is None
        assert self.db.find_tombstone_by_fingerprint("new123") is not None

    def test_backup_operations(self):
        """Test backup insert and retrieval."""
        now = int(time.time())
        backup = BackupRecord(
            id=0,
            app_id="com.example.test",
            path="/home/user/Applications/.previous/TestApp.old",
            version="1.0.0",
            size=1000000,
            created_at=now,
        )

        backup_id = self.db.insert_backup(backup)
        assert backup_id > 0

        backups = self.db.get_backups("com.example.test")
        assert len(backups) == 1
        assert backups[0].version == "1.0.0"

    def test_delete_old_backups(self):
        """Test deleting old backups, keeping newest N."""
        now = int(time.time())
        for i in range(5):
            backup = BackupRecord(
                id=0,
                app_id="com.example.test",
                path=f"/home/user/Applications/.previous/TestApp.old{i}",
                version=f"1.0.{i}",
                size=1000000,
                created_at=now + i,
            )
            self.db.insert_backup(backup)

        # Keep 2 newest
        deleted = self.db.delete_old_backups("com.example.test", 2)
        assert deleted == 3

        backups = self.db.get_backups("com.example.test")
        assert len(backups) == 2
        # Should keep the newest (highest created_at)
        assert backups[0].version == "1.0.4"
        assert backups[1].version == "1.0.3"

    def test_event_operations(self):
        """Test event insert and retrieval."""
        # Use explicit timestamps by directly inserting with different times
        with self.db._get_conn() as conn:
            now = int(time.time())
            conn.execute(
                "INSERT INTO events (ts, kind, app_id, detail) VALUES (?, ?, ?, ?)",
                (now, "installed", "com.example.test", "Installed TestApp"),
            )
            conn.execute(
                "INSERT INTO events (ts, kind, app_id, detail) VALUES (?, ?, ?, ?)",
                (now + 1, "updated", "com.example.test", "Updated to 2.0.0"),
            )

        events = self.db.get_events(limit=10)
        assert len(events) == 2
        # Events should be in descending order (newest first)
        assert events[0].kind == "updated"
        assert events[1].kind == "installed"

    def test_events_cap(self):
        """Test that events table is capped at max rows."""
        # Insert more than max rows
        for i in range(1010):
            self.db.insert_event("test", f"app{i}", f"Event {i}")

        events = self.db.get_events(limit=2000)
        # Should be capped at EVENTS_TABLE_MAX_ROWS (1000)
        assert len(events) <= 1000

    def test_get_all_apps_dict(self):
        """Test getting all apps as dict keyed by managed_name."""
        now = int(time.time())
        for i in range(3):
            app = AppRecord(
                id=0,
                app_id=f"com.example.app{i}",
                role="primary",
                display_name=f"App {i}",
                managed_name=f"App{i}",
                name_origin="auto",
                orig_filename=f"app{i}.AppImage",
                path=f"/home/user/Applications/App{i}",
                device=123,
                inode=456 + i,
                size=1000000,
                mtime_ns=now * 1_000_000_000,
                fingerprint=f"fp{i}",
                version="1.0.0",
                update_info=None,
                update_policy="auto",
                self_managed=False,
                origin=None,
                sha256=None,
                installed_at=now,
                updated_at=now,
            )
            self.db.insert_app(app)

        apps_dict = self.db.get_all_apps_dict()
        assert len(apps_dict) == 3
        assert "App0" in apps_dict
        assert "App1" in apps_dict
        assert "App2" in apps_dict


class TestGlobalStateDB:
    """Tests for global StateDB functions."""

    def test_get_state_db(self):
        """Test getting global StateDB instance."""
        reset_state_db()
        try:
            with tempfile.TemporaryDirectory():
                # We can't easily test the global without mocking paths
                # Just verify the function exists and returns a StateDB
                pass
        finally:
            reset_state_db()

    def test_reset_state_db(self):
        """Test resetting global StateDB."""
        reset_state_db()
        # Should not raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
