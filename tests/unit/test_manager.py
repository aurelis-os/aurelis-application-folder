"""Tests for Manager class."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from appimage_launcher.config import Config, reset_config
from appimage_launcher.core.manager import InstallResult, Manager
from appimage_launcher.core.state import AppRecord, StateDB
from appimage_launcher.paths import Paths, reset_paths
from tests.fixtures.build_fake_appimage import build_fake_appimage


class TestManager:
    """Tests for Manager class."""

    def setup_method(self):
        """Set up a temporary database and manager for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Set up paths
        self.applications_dir = self.temp_path / "Applications"
        self.applications_dir.mkdir()
        self.config_dir = self.temp_path / "config"
        self.config_dir.mkdir()
        self.state_dir = self.temp_path / "state"
        self.state_dir.mkdir()
        self.data_dir = self.temp_path / "data"
        self.data_dir.mkdir()

        # Create a test config
        self.test_config = Config()
        self.test_config.applications_dir = str(self.applications_dir)
        self.test_config.naming.template = "{name}"
        self.test_config.naming.keep_template = "{name} {version}"
        self.test_config.install.conflict_policy = "replace"
        self.test_config.install.source_action = "copy"
        self.test_config.update.previous_versions_keep = 1

        # Create test paths
        self.test_paths = Paths.from_config(self.test_config)
        self.test_paths.ensure_dirs()

        # Create database
        self.db_path = self.state_dir / "state.db"
        self.db = StateDB(self.db_path)

        # Create manager with test db
        self.manager = Manager(db=self.db)

        # Override manager's config and paths with test instances
        self.manager.config = self.test_config
        self.manager.paths = self.test_paths

    def teardown_method(self):
        """Clean up."""
        self.db.close()
        self.temp_dir.cleanup()
        reset_paths()
        reset_config()

    def _create_fake_appimage(self, name="TestApp", version="1.0.0"):
        """Create a fake AppImage for testing."""
        appimage = self.temp_path / f"{name}.appimage"
        build_fake_appimage(appimage, name=name, version=version)
        return appimage

    def _create_mock_meta(self, name="TestApp", version="1.0.0", app_id="com.example.testapp"):
        """Create a mock AppImageMeta."""
        meta = MagicMock()
        meta.name = name
        meta.version = version
        meta.app_id = app_id
        meta.categories = ["Utility"]
        meta.comment = "A test app"
        meta.icon_bytes = b"icon data"
        meta.icon_ext = "png"
        meta.fingerprint = "abc123"
        meta.update_info = "zsync|https://example.com/app.zsync"
        return meta

    def test_install_new_app(self):
        """Test installing a new app."""
        appimage = self._create_fake_appimage()

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="sha256hash"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            mock_read_meta.return_value = self._create_mock_meta()

            result = self.manager.install(appimage)

            assert isinstance(result, InstallResult)
            assert result.changed is True
            assert result.app.app_id == "com.example.testapp"
            assert result.app.managed_name == "TestApp"
            assert result.app.role == "primary"
            assert result.previous is None

            # Verify database record
            apps = self.db.list_apps()
            assert len(apps) == 1
            assert apps[0].app_id == "com.example.testapp"

    def test_install_identical_file_returns_existing(self):
        """Test that installing identical file returns existing app."""
        appimage = self._create_fake_appimage()

        # Pre-populate database with same hash
        existing_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256="sha256hash",
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(existing_app)

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="sha256hash"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            mock_read_meta.return_value = self._create_mock_meta()

            # Need to mock _find_by_sha256 to return the existing app
            with patch.object(self.manager, "_find_by_sha256", return_value=existing_app):
                result = self.manager.install(appimage)

                assert result.changed is False
                assert result.app.app_id == "com.example.testapp"

    def test_install_with_custom_name(self):
        """Test installing with a custom name."""
        appimage = self._create_fake_appimage()

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="sha256hash"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            mock_read_meta.return_value = self._create_mock_meta()

            result = self.manager.install(appimage, name="MyCustomName")

            assert result.app.managed_name == "MyCustomName"
            assert result.app.name_origin == "user"

    def test_install_with_origin(self):
        """Test installing with origin record."""
        appimage = self._create_fake_appimage()
        origin = json.dumps({"source": "aurelis-catalog", "catalog_id": "org.kde.kate"})

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="sha256hash"),
            patch("appimage_launcher.core.manager.write_desktop_entry") as mock_write_entry,
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            mock_read_meta.return_value = self._create_mock_meta()

            result = self.manager.install(appimage, origin=origin)

            assert result.app.origin == origin
            # Verify desktop entry was called with catalog info
            mock_write_entry.assert_called()
            call_kwargs = mock_write_entry.call_args.kwargs
            assert call_kwargs.get("catalog_id") == "org.kde.kate"
            assert call_kwargs.get("origin_source") == "aurelis-catalog"

    def test_install_conflict_replace(self):
        """Test conflict resolution with replace policy.

        This test was previously xfail due to a bug in Manager._replace_app:
        the old app's database record was not deleted before installing the new
        version at the same path, causing a UNIQUE constraint violation on
        apps.path. The bug was fixed by deleting the old app record in
        _replace_app before calling _do_install.
        """
        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        # Pre-install old version
        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(old_app)
        # Create the file
        (self.applications_dir / "TestApp").write_bytes(b"old appimage")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
            patch("appimage_launcher.core.manager.shutil.copy2") as mock_copy2,
            patch("appimage_launcher.core.manager.shutil.move") as mock_move,
        ):
            mock_read_meta.return_value = self._create_mock_meta(name="TestApp", version="2.0.0")

            # Mock the staging operations to avoid file system issues
            def mock_copy2_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage")

            mock_copy2.side_effect = mock_copy2_side_effect

            def mock_move_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage")

            mock_move.side_effect = mock_move_side_effect

            result = self.manager.install(appimage, conflict_policy="replace")

            assert result.changed is True
            assert result.app.version == "2.0.0"
            assert result.previous is not None

    def test_install_conflict_keep(self):
        """Test conflict resolution with keep policy.

        This test was previously xfail due to a bug in Manager._keep_both:
        after updating the old app to "kept" with a new managed_name, the code
        incorrectly used the new kept name for the new app's path instead of
        the original name. The bug was fixed by saving the original name before
        updating the old app and using it for the new app's target path.
        """
        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        # Pre-install old version
        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(old_app)
        (self.applications_dir / "TestApp").write_bytes(b"old appimage")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
            patch("appimage_launcher.core.manager.shutil.copy2") as mock_copy2,
            patch("appimage_launcher.core.manager.shutil.move") as mock_move,
        ):
            mock_read_meta.return_value = self._create_mock_meta(name="TestApp", version="2.0.0")

            # Mock the staging operations to avoid file system issues
            def mock_copy2_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage")

            mock_copy2.side_effect = mock_copy2_side_effect

            def mock_move_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage")

            mock_move.side_effect = mock_move_side_effect

            result = self.manager.install(appimage, conflict_policy="keep")

            assert result.changed is True
            # Old app should be kept
            apps = self.db.list_apps(include_kept=True)
            kept_apps = [a for a in apps if a.role == "kept"]
            assert len(kept_apps) == 1
            assert kept_apps[0].version == "1.0.0"

    def test_install_conflict_cancel(self):
        """Test conflict resolution with cancel policy."""
        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(old_app)
        (self.applications_dir / "TestApp").write_bytes(b"old appimage")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
        ):
            mock_read_meta.return_value = self._create_mock_meta(name="TestApp", version="2.0.0")

            from appimage_launcher.core.exceptions import InvalidArgumentError

            with pytest.raises(InvalidArgumentError, match="cancelled"):
                self.manager.install(appimage, conflict_policy="cancel")

    def test_install_conflict_error(self):
        """Test conflict resolution with error policy."""
        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(old_app)
        (self.applications_dir / "TestApp").write_bytes(b"old appimage")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
        ):
            mock_read_meta.return_value = self._create_mock_meta(name="TestApp", version="2.0.0")

            from appimage_launcher.core.exceptions import ConflictPendingError

            with pytest.raises(ConflictPendingError) as exc_info:
                self.manager.install(appimage, conflict_policy="error")

            # ConflictPendingError doesn't have conflict_info, it has conflict_id and message
            assert exc_info.value.message == "Conflict requires decision"

    def test_remove_app(self):
        """Test removing an app."""
        # Pre-install app
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)
        (self.applications_dir / "TestApp").write_bytes(b"appimage")

        with (
            patch("appimage_launcher.core.manager.remove_desktop_entry") as mock_remove_entry,
            patch("appimage_launcher.core.manager.remove_icons") as mock_remove_icons,
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.remove("TestApp", file_action="trash", keep_file=True)

            assert result is True
            # App should be removed from database
            apps = self.db.list_apps()
            assert len(apps) == 0
            # Tombstone should be created
            mock_remove_entry.assert_called_once()
            mock_remove_icons.assert_called_once()

    def test_remove_app_not_found(self):
        """Test removing non-existent app."""
        from appimage_launcher.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="App not found"):
            self.manager.remove("NonExistent")

    def test_rename_app(self):
        """Test renaming an app."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)
        (self.applications_dir / "TestApp").write_bytes(b"appimage")

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.rename("TestApp", "NewName")

            assert result.managed_name == "NewName"
            assert result.name_origin == "user"
            assert result.path == str(self.applications_dir / "NewName")

    def test_rename_app_collision(self):
        """Test renaming with collision."""
        app1 = AppRecord(
            id=0,
            app_id="com.example.app1",
            role="primary",
            display_name="App1",
            managed_name="App1",
            name_origin="auto",
            orig_filename="app1.appimage",
            path=str(self.applications_dir / "App1"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        app2 = AppRecord(
            id=0,
            app_id="com.example.app2",
            role="primary",
            display_name="App2",
            managed_name="App2",
            name_origin="auto",
            orig_filename="app2.appimage",
            path=str(self.applications_dir / "App2"),
            device=123,
            inode=789,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="def456",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app1)
        self.db.insert_app(app2)
        (self.applications_dir / "App1").write_bytes(b"appimage1")
        (self.applications_dir / "App2").write_bytes(b"appimage2")

        from appimage_launcher.core.exceptions import NameCollisionError

        with pytest.raises(NameCollisionError, match="Name collision"):
            self.manager.rename("App1", "App2")

    def test_rename_reset(self):
        """Test renaming with reset to default name."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="CustomName",
            name_origin="user",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "CustomName"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)
        (self.applications_dir / "CustomName").write_bytes(b"appimage")

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.rename("CustomName", "", reset=True)

            assert result.managed_name == "TestApp"
            assert result.name_origin == "user"

    def test_set_policy(self):
        """Test setting update policy."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.set_policy("TestApp", "pinned")

            assert result.update_policy == "pinned"

    def test_set_policy_invalid(self):
        """Test setting invalid policy."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        from appimage_launcher.core.exceptions import InvalidArgumentError

        with pytest.raises(InvalidArgumentError, match="Invalid policy"):
            self.manager.set_policy("TestApp", "invalid")

    def test_set_origin(self):
        """Test setting origin record."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        origin = json.dumps({"source": "aurelis-catalog", "catalog_id": "org.kde.kate"})

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.set_origin("TestApp", origin)

            assert result.origin == origin

    def test_set_origin_clear(self):
        """Test clearing origin record."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=json.dumps({"source": "aurelis-catalog", "catalog_id": "org.kde.kate"}),
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.set_origin("TestApp", None)

            assert result.origin is None

    def test_set_origin_invalid_json(self):
        """Test setting invalid origin JSON."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        from appimage_launcher.core.exceptions import InvalidArgumentError

        with pytest.raises(InvalidArgumentError, match="Invalid JSON"):
            self.manager.set_origin("TestApp", "not valid json")

    def test_set_origin_missing_fields(self):
        """Test setting origin with missing required fields."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        from appimage_launcher.core.exceptions import InvalidArgumentError

        with pytest.raises(InvalidArgumentError, match="must have 'source' and 'catalog_id'"):
            self.manager.set_origin("TestApp", json.dumps({"source": "test"}))

    def test_revert(self):
        """Test reverting to previous version."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="2.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)
        (self.applications_dir / "TestApp").write_bytes(b"current appimage")

        # Add backup
        from appimage_launcher.core.state import BackupRecord

        backup = BackupRecord(
            id=0,
            app_id="com.example.testapp",
            path=str(self.applications_dir / ".previous" / "TestApp-old"),
            version="1.0.0",
            size=900000,
            created_at=900,
        )
        self.db.insert_backup(backup)
        # Create the backup file (directory already exists from ensure_dirs)
        (self.applications_dir / ".previous" / "TestApp-old").write_bytes(b"old appimage")

        with (
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.refresh_caches"),
        ):
            result = self.manager.revert("TestApp")

            assert result.version == "1.0.0"
            assert result.path == str(self.applications_dir / "TestApp")

    def test_revert_no_backup(self):
        """Test reverting when no backup exists."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="2.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        from appimage_launcher.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="No previous version"):
            self.manager.revert("TestApp")

    def test_revert_backup_missing(self):
        """Test reverting when backup file is missing."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="2.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        from appimage_launcher.core.state import BackupRecord

        backup = BackupRecord(
            id=0,
            app_id="com.example.testapp",
            path=str(self.applications_dir / ".previous" / "TestApp-old"),
            version="1.0.0",
            size=900000,
            created_at=900,
        )
        self.db.insert_backup(backup)
        # Don't create the backup file

        from appimage_launcher.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError, match="Backup file not found"):
            self.manager.revert("TestApp")

    def test_find_app_by_name_exact(self):
        """Test finding app by exact name."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        found = self.manager._find_app_by_name("TestApp")
        assert found is not None
        assert found.app_id == "com.example.testapp"

    def test_find_app_by_name_case_insensitive(self):
        """Test finding app by name case-insensitive."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        found = self.manager._find_app_by_name("testapp")
        assert found is not None

    def test_find_app_by_name_prefix(self):
        """Test finding app by prefix."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        found = self.manager._find_app_by_name("Test")
        assert found is not None

    def test_find_app_by_app_id(self):
        """Test finding app by app_id."""
        app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app)

        found = self.manager._find_app_by_name("com.example.testapp")
        assert found is not None

    def test_find_app_ambiguous(self):
        """Test finding app with ambiguous name."""
        app1 = AppRecord(
            id=0,
            app_id="com.example.app1",
            role="primary",
            display_name="App",
            managed_name="App",
            name_origin="auto",
            orig_filename="app1.appimage",
            path=str(self.applications_dir / "App"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="abc123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        app2 = AppRecord(
            id=0,
            app_id="com.example.app2",
            role="primary",
            display_name="App",
            managed_name="App2",
            name_origin="auto",
            orig_filename="app2.appimage",
            path=str(self.applications_dir / "App2"),
            device=123,
            inode=789,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="def456",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        self.db.insert_app(app1)
        self.db.insert_app(app2)

        # Ambiguous prefix "App" matches both - should return first match (exact match for "App")
        found = self.manager._find_app_by_name("App")
        assert found is not None
        assert found.managed_name == "App"

    def test_exclusive_lock(self):
        """Test exclusive lock context manager."""
        with self.manager._exclusive_lock():
            # Lock acquired
            pass
        # Lock released

    def test_exclusive_lock_blocking(self):
        """Test exclusive lock blocks when already held."""
        # Acquire lock manually
        lock_path = self.manager.paths.lock_file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_path, "w")
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        try:
            from appimage_launcher.core.exceptions import BusyError

            with pytest.raises(BusyError, match="Could not acquire lock"):
                with self.manager._exclusive_lock():
                    pass
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()

    def test_dbus_service_no_raw_exceptions(self):
        """Ensure dbus_service.py uses typed DBusError exceptions, not raw Exception."""
        import re

        dbus_service_path = (
            Path(__file__).parent.parent.parent
            / "src"
            / "appimage_launcher"
            / "daemon"
            / "dbus_service.py"
        )
        content = dbus_service_path.read_text()
        matches = re.findall(r"raise Exception\(", content)
        assert not matches, f"Found {len(matches)} raw 'raise Exception(' in dbus_service.py"

    def test_replace_app_rollback_on_failure(self):
        """Test that _replace_app rolls back file and keeps old record on failure."""
        # Pre-install old version
        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        old_app.id = self.db.insert_app(old_app)
        old_file = self.applications_dir / "TestApp"
        old_file.write_bytes(b"old appimage content")

        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
            patch("appimage_launcher.core.manager.shutil.copy2"),
            patch("appimage_launcher.core.manager.shutil.move"),
        ):
            mock_read_meta.return_value = self._create_mock_meta(name="TestApp", version="2.0.0")

            # Make shutil.copy2 fail (simulating disk error during staging)
            def fail_copy2(*args, **kwargs):
                raise RuntimeError("Disk error")

            with patch("appimage_launcher.core.manager.shutil.copy2", side_effect=fail_copy2):
                with pytest.raises(RuntimeError, match="Disk error"):
                    self.manager.install(appimage, conflict_policy="replace")

                # Old app record should still exist
                apps = self.db.list_apps()
                assert len(apps) == 1
                assert apps[0].app_id == "com.example.testapp"
                assert apps[0].version == "1.0.0"

                # Old file should be restored
                assert old_file.exists()
                assert old_file.read_bytes() == b"old appimage content"

                # No backup should have been created in .previous
                previous_dir = self.applications_dir / ".previous"
                if previous_dir.exists():
                    previous_files = list(previous_dir.iterdir())
                    assert len(previous_files) == 0

    def test_replace_app_logs_correct_version_and_backup_old_version_size(self):
        """Test that _replace_app logs correct version transition and backup has old version/size."""
        # Pre-install old version
        old_app = AppRecord(
            id=0,
            app_id="com.example.testapp",
            role="primary",
            display_name="TestApp",
            managed_name="TestApp",
            name_origin="auto",
            orig_filename="test-old.appimage",
            path=str(self.applications_dir / "TestApp"),
            device=123,
            inode=456,
            size=1000000,
            mtime_ns=1000000000,
            fingerprint="old123",
            version="1.0.0",
            update_info=None,
            update_policy="auto",
            self_managed=False,
            origin=None,
            sha256=None,
            installed_at=1000,
            updated_at=1000,
        )
        old_app.id = self.db.insert_app(old_app)
        old_file = self.applications_dir / "TestApp"
        old_file.write_bytes(b"old appimage content")

        appimage = self._create_fake_appimage("TestApp", "2.0.0")

        with (
            patch("appimage_launcher.core.manager.read_appimage_meta") as mock_read_meta,
            patch("appimage_launcher.core.manager.compute_full_sha256", return_value="newsha256"),
            patch("appimage_launcher.core.manager.write_desktop_entry"),
            patch("appimage_launcher.core.manager.install_icons"),
            patch("appimage_launcher.core.manager.ensure_executable"),
            patch("appimage_launcher.core.manager.refresh_caches"),
            patch("appimage_launcher.core.manager.shutil.copy2") as mock_copy2,
            patch("appimage_launcher.core.manager.shutil.move") as mock_move,
        ):
            # Create mock meta with new version
            new_meta = self._create_mock_meta(name="TestApp", version="2.0.0")
            new_meta.size = 2000000  # New size is different
            mock_read_meta.return_value = new_meta

            # Mock the staging operations
            def mock_copy2_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage content")

            mock_copy2.side_effect = mock_copy2_side_effect

            def mock_move_side_effect(src, dst):
                Path(dst).write_bytes(b"new appimage content")

            mock_move.side_effect = mock_move_side_effect

            # Perform the replace
            result = self.manager.install(appimage, conflict_policy="replace")

            # Verify result
            assert result.changed is True
            assert result.app.version == "2.0.0"
            assert result.previous is not None

            # Verify the event log has correct old -> new version
            events = self.db.get_events()
            updated_events = [e for e in events if e.kind == "updated"]
            assert len(updated_events) == 1
            assert updated_events[0].detail == "Updated 1.0.0 -> 2.0.0"

            # Verify backup record has OLD version (1.0.0) and OLD size (1000000)
            backups = self.db.get_backups("com.example.testapp")
            assert len(backups) == 1
            backup = backups[0]
            assert backup.version == "1.0.0", (
                f"Backup should have old version 1.0.0, got {backup.version}"
            )
            assert backup.size == 1000000, f"Backup should have old size 1000000, got {backup.size}"


class TestSaveUserConfig:
    """Tests for save_user_config function."""

    def test_save_user_config_raises_on_invalid_toml(self, tmp_path):
        """Test that save_user_config raises error when existing config.toml is invalid."""
        import tomllib

        import appimage_launcher.constants as constants
        from appimage_launcher.config import Config, save_user_config

        # Create a temp config dir
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        original_config_dir = constants.DEFAULT_CONFIG_DIR
        constants.DEFAULT_CONFIG_DIR = str(config_dir)

        try:
            # Write invalid TOML to config.toml
            config_path = config_dir / "config.toml"
            config_path.write_text("invalid toml [[[")

            # Create a config with a preference to save
            test_config = Config()
            test_config.conflict_preferences = {"com.example.testapp": "replace"}

            # save_user_config should raise TOMLDecodeError
            with pytest.raises(tomllib.TOMLDecodeError):
                save_user_config(test_config)

            # Verify the file is unchanged (byte-for-byte)
            assert config_path.read_text() == "invalid toml [[["
        finally:
            constants.DEFAULT_CONFIG_DIR = original_config_dir

    def test_save_user_config_raises_on_invalid_utf8(self, tmp_path):
        """Test that save_user_config raises error when existing config.toml has invalid UTF-8."""
        import appimage_launcher.constants as constants
        from appimage_launcher.config import Config, save_user_config

        # Create a temp config dir
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        original_config_dir = constants.DEFAULT_CONFIG_DIR
        constants.DEFAULT_CONFIG_DIR = str(config_dir)

        try:
            # Write invalid UTF-8 to config.toml (BOM for UTF-16, not valid UTF-8)
            config_path = config_dir / "config.toml"
            config_path.write_bytes(b"\xff\xfe")

            # Create a config with a preference to save
            test_config = Config()
            test_config.conflict_preferences = {"com.example.testapp": "replace"}

            # save_user_config should raise UnicodeDecodeError
            with pytest.raises(UnicodeDecodeError):
                save_user_config(test_config)

            # Verify the file is unchanged (byte-for-byte)
            assert config_path.read_bytes() == b"\xff\xfe"
        finally:
            constants.DEFAULT_CONFIG_DIR = original_config_dir

    def test_save_user_config_preserves_other_keys(self, tmp_path):
        """Test that save_user_config preserves other keys in config.toml."""
        import tomllib

        import tomli_w

        import appimage_launcher.constants as constants
        from appimage_launcher.config import Config, save_user_config

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        original_config_dir = constants.DEFAULT_CONFIG_DIR
        constants.DEFAULT_CONFIG_DIR = str(config_dir)

        try:
            # Create initial config with multiple keys
            config_path = config_dir / "config.toml"
            initial_config = {
                "applications_dir": "/custom/path",
                "install": {"source_action": "move", "conflict_policy": "keep"},
                "naming": {"template": "{name}-{version}"},
                "conflict_preferences": {"existing_app": "keep"},
            }
            with open(config_path, "wb") as f:
                tomli_w.dump(initial_config, f)

            # Save new conflict preference
            test_config = Config()
            test_config.conflict_preferences = {"com.example.newapp": "replace"}
            save_user_config(test_config)

            # Verify all keys preserved
            with open(config_path, "rb") as f:
                loaded = tomllib.load(f)

            assert loaded["applications_dir"] == "/custom/path"
            assert loaded["install"]["source_action"] == "move"
            assert loaded["install"]["conflict_policy"] == "keep"
            assert loaded["naming"]["template"] == "{name}-{version}"
            assert loaded["conflict_preferences"]["existing_app"] == "keep"
            assert loaded["conflict_preferences"]["com.example.newapp"] == "replace"
        finally:
            constants.DEFAULT_CONFIG_DIR = original_config_dir


class TestConfigCLI:
    """Tests for config CLI commands."""

    def test_config_list_outputs_valid_toml(self):
        """Test that config list outputs valid TOML using tomli_w."""
        import tomllib

        import tomli_w

        from appimage_launcher.config import Config

        config = Config()
        config.applications_dir = "/custom/apps"
        config.install.source_action = "move"
        config.install.conflict_policy = "keep"
        config.naming.template = "{name}-{version}"
        config.update.previous_versions_keep = 5
        config.conflict_preferences = {"com.example.test": "replace"}

        # This is what cmd_config does for "list"
        toml_output = tomli_w.dumps(config.to_dict())

        # Verify it's valid TOML and can be parsed back
        parsed = tomllib.loads(toml_output)
        assert parsed["applications_dir"] == "/custom/apps"
        assert parsed["install"]["source_action"] == "move"
        assert parsed["install"]["conflict_policy"] == "keep"
        assert parsed["naming"]["template"] == "{name}-{version}"
        assert parsed["update"]["previous_versions_keep"] == 5
        assert parsed["conflict_preferences"]["com.example.test"] == "replace"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
