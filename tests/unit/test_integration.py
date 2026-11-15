"""Tests for desktop entry and icon integration."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from appimage_launcher.core.integration import (
    escape_desktop_value,
    install_icons,
    remove_desktop_entry,
    remove_icons,
    write_desktop_entry,
)


class TestEscapeDesktopValue:
    """Tests for escape_desktop_value function."""

    def test_basic_escaping(self):
        assert escape_desktop_value("simple") == "simple"
        assert escape_desktop_value("with spaces") == "with spaces"
        assert escape_desktop_value("with;semicolon") == "with\\;semicolon"
        assert escape_desktop_value("with,comma") == "with\\,comma"
        assert escape_desktop_value("with=equals") == "with\\=equals"
        assert escape_desktop_value("with\\backslash") == "with\\\\backslash"
        assert escape_desktop_value("with\nnewline") == "with\\nnewline"
        assert escape_desktop_value("with\rcarriage") == "with\\rcarriage"

    def test_complex_escaping(self):
        value = "path/with;special,chars=and\\backslashes"
        expected = "path/with\\;special\\,chars\\=and\\\\backslashes"
        assert escape_desktop_value(value) == expected


class TestWriteDesktopEntry:
    """Tests for write_desktop_entry function."""

    def setup_method(self):
        """Set up temporary directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def teardown_method(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_write_desktop_entry_primary(self):
        """Test writing a primary desktop entry."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            with patch("appimage_launcher.core.integration.get_config") as mock_config:
                mock_config.return_value.integration = {}

                entry_path = write_desktop_entry(
                    app_id="com.example.test",
                    display_name="Test App",
                    exec_path=Path("/home/user/Applications/TestApp"),
                    icon_name="appimage-com.example.test",
                    categories=["Utility", "Development"],
                    comment="A test application",
                    startup_wm_class="test-app",
                    version="1.0.0",
                    name_origin="auto",
                    update_policy="auto",
                )

                assert entry_path.exists()
                content = entry_path.read_text()
                assert "Name=Test App" in content
                assert "Exec=/home/user/Applications/TestApp %U" in content
                assert "TryExec=/home/user/Applications/TestApp" in content
                assert "Icon=appimage-com.example.test" in content
                assert "Categories=Utility;Development;" in content
                assert "Comment=A test application" in content
                assert "StartupWMClass=test-app" in content
                assert "X-AppImage-Managed=true" in content
                assert "X-AppImage-Id=com.example.test" in content
                assert "X-AppImage-Version=1.0.0" in content
                assert "X-AppImage-NameOrigin=auto" in content
                assert "X-AppImage-UpdatePolicy=auto" in content

    def test_write_desktop_entry_kept(self):
        """Test writing a kept version desktop entry."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            with patch("appimage_launcher.core.integration.get_config") as mock_config:
                mock_config.return_value.integration = {}

                entry_path = write_desktop_entry(
                    app_id="com.example.test",
                    display_name="Test App",
                    exec_path=Path("/home/user/Applications/TestApp"),
                    icon_name="appimage-com.example.test",
                    categories=["Utility"],
                    comment=None,
                    startup_wm_class=None,
                    version="1.0.0",
                    name_origin="auto",
                    update_policy="auto",
                    role="kept",
                    version_slug="1.0.0",
                )

                assert entry_path.exists()
                assert "com.example.test-1.0.0" in entry_path.name
                content = entry_path.read_text()
                assert "Name=Test App (1.0.0)" in content

    def test_write_desktop_entry_with_origin(self):
        """Test writing desktop entry with catalog origin."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            with patch("appimage_launcher.core.integration.get_config") as mock_config:
                mock_config.return_value.integration = {}

                entry_path = write_desktop_entry(
                    app_id="com.example.test",
                    display_name="Test App",
                    exec_path=Path("/home/user/Applications/TestApp"),
                    icon_name="appimage-com.example.test",
                    categories=[],
                    comment=None,
                    startup_wm_class=None,
                    version="1.0.0",
                    name_origin="auto",
                    update_policy="auto",
                    catalog_id="org.kde.kate",
                    origin_source="aurelis-catalog",
                )

                content = entry_path.read_text()
                assert "X-AppImage-CatalogId=org.kde.kate" in content
                assert "X-AppImage-OriginSource=aurelis-catalog" in content

    def test_write_desktop_entry_user_name_origin(self):
        """Test writing desktop entry with user name origin."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            with patch("appimage_launcher.core.integration.get_config") as mock_config:
                mock_config.return_value.integration = {"menu_name": "filename"}

                entry_path = write_desktop_entry(
                    app_id="com.example.test",
                    display_name="My Custom Name",
                    exec_path=Path("/home/user/Applications/MyCustomName"),
                    icon_name="appimage-com.example.test",
                    categories=[],
                    comment=None,
                    startup_wm_class=None,
                    version="1.0.0",
                    name_origin="user",
                    update_policy="auto",
                )

                content = entry_path.read_text()
                assert "Name=My Custom Name" in content


class TestRemoveDesktopEntry:
    """Tests for remove_desktop_entry function."""

    def setup_method(self):
        """Set up temporary directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def teardown_method(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_remove_existing_entry(self):
        """Test removing an existing desktop entry."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            # Create a dummy entry
            entry_path = (
                mock_paths.return_value.applications_data_dir / "appimage-com.example.test.desktop"
            )
            entry_path.write_text("[Desktop Entry]\nName=Test\n")

            result = remove_desktop_entry("com.example.test")
            assert result is True
            assert not entry_path.exists()

    def test_remove_nonexistent_entry(self):
        """Test removing a non-existent desktop entry."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            result = remove_desktop_entry("com.example.nonexistent")
            assert result is False

    def test_remove_kept_entry(self):
        """Test removing a kept version desktop entry."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.applications_data_dir = self.temp_path / "applications"
            mock_paths.return_value.applications_data_dir.mkdir(parents=True, exist_ok=True)

            entry_path = (
                mock_paths.return_value.applications_data_dir
                / "appimage-com.example.test-1.0.0.desktop"
            )
            entry_path.write_text("[Desktop Entry]\nName=Test\n")

            result = remove_desktop_entry("com.example.test", version_slug="1.0.0")
            assert result is True
            assert not entry_path.exists()


class TestInstallIcons:
    """Tests for install_icons function."""

    def setup_method(self):
        """Set up temporary directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def teardown_method(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_install_scalable_icon(self):
        """Test installing a scalable (SVG) icon."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.icons_data_dir = self.temp_path / "icons"
            mock_paths.return_value.icons_data_dir.mkdir(parents=True, exist_ok=True)

            icon_bytes = b"<svg>test</svg>"
            icon_path = install_icons("com.example.test", icon_bytes, "svg", "scalable")

            assert icon_path is not None
            assert icon_path.exists()
            assert icon_path.suffix == ".svg"
            assert "appimage-com.example.test.svg" in icon_path.name

    def test_install_png_icon(self):
        """Test installing a PNG icon."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.icons_data_dir = self.temp_path / "icons"
            mock_paths.return_value.icons_data_dir.mkdir(parents=True, exist_ok=True)

            icon_bytes = b"fake png data"
            icon_path = install_icons("com.example.test", icon_bytes, "png", 256)

            assert icon_path is not None
            assert icon_path.exists()
            assert icon_path.suffix == ".png"
            assert "256x256" in str(icon_path.parent)

    def test_install_icon_removes_old(self):
        """Test that installing a new icon removes old ones."""
        icons_data_dir = self.temp_path / "icons"
        icons_data_dir.mkdir(parents=True, exist_ok=True)

        # Create old icons
        old_dir1 = icons_data_dir / "128x128" / "apps"
        old_dir1.mkdir(parents=True)
        old_icon1 = old_dir1 / "appimage-com.example.test.png"
        old_icon1.write_bytes(b"old")

        old_dir2 = icons_data_dir / "scalable" / "apps"
        old_dir2.mkdir(parents=True)
        old_icon2 = old_dir2 / "appimage-com.example.test.svg"
        old_icon2.write_bytes(b"old")

        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.icons_data_dir = icons_data_dir

            # Install new icon
            icon_bytes = b"<svg>new</svg>"
            icon_path = install_icons("com.example.test", icon_bytes, "svg", "scalable")

            assert icon_path.exists()
            # Old icons in different sizes should be removed
            assert not old_icon1.exists()
            # The new icon is written to the same path as old_icon2, so it exists but with new content
            assert icon_path.read_bytes() == b"<svg>new</svg>"


class TestRemoveIcons:
    """Tests for remove_icons function."""

    def setup_method(self):
        """Set up temporary directories."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def teardown_method(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_remove_icons(self):
        """Test removing all icons for an app."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.icons_data_dir = self.temp_path / "icons"
            mock_paths.return_value.icons_data_dir.mkdir(parents=True, exist_ok=True)

            # Create icons in various sizes
            for size in ["128x128", "256x256", "scalable"]:
                size_dir = mock_paths.return_value.icons_data_dir / size / "apps"
                size_dir.mkdir(parents=True)
                if size == "scalable":
                    (size_dir / "appimage-com.example.test.svg").write_bytes(b"svg")
                else:
                    (size_dir / "appimage-com.example.test.png").write_bytes(b"png")

            count = remove_icons("com.example.test")
            assert count == 3

            # Verify all removed
            for icon in mock_paths.return_value.icons_data_dir.rglob("appimage-com.example.test.*"):
                assert not icon.exists()

    def test_remove_icons_nonexistent(self):
        """Test removing icons for non-existent app."""
        with patch("appimage_launcher.core.integration.get_paths") as mock_paths:
            mock_paths.return_value.icons_data_dir = self.temp_path / "icons"
            mock_paths.return_value.icons_data_dir.mkdir(parents=True, exist_ok=True)

            count = remove_icons("com.example.nonexistent")
            assert count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
