"""Tests for AppImage metadata parsing."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from appimage_launcher.core.metadata import (
    AppImageMeta,
    extract_version_from_filename,
    read_appimage_meta,
    read_icon_bytes,
)
from tests.fixtures.build_fake_appimage import build_fake_appimage


class TestExtractVersionFromFilename:
    """Tests for extract_version_from_filename function."""

    def test_semantic_version(self):
        assert extract_version_from_filename("App-1.2.3.AppImage") == "1.2.3"
        assert extract_version_from_filename("App-1.2.3-rc1.AppImage") == "1.2.3-rc1"
        assert extract_version_from_filename("App-1.2.3+build.AppImage") == "1.2.3+build"

    def test_two_part_version(self):
        assert extract_version_from_filename("App-1.2.AppImage") == "1.2"
        assert extract_version_from_filename("App-1.2-beta.AppImage") == "1.2-beta"

    def test_v_prefix_version(self):
        # The regex captures the version without the 'v' prefix
        assert extract_version_from_filename("App-v1.2.3.AppImage") == "1.2.3"

    def test_date_version(self):
        assert extract_version_from_filename("App-2024.01.15.AppImage") == "2024.01.15"
        assert extract_version_from_filename("App-2024.01.AppImage") == "2024.01"

    def test_no_version(self):
        assert extract_version_from_filename("App.AppImage") is None
        assert extract_version_from_filename("App-latest.AppImage") is None

    def test_complex_filenames(self):
        # The regex captures the first version-like pattern
        assert (
            extract_version_from_filename("GIMP-8.9.3-34-nightly_x86-64.appimage")
            == "8.9.3-34-nightly_x86-64"
        )
        assert extract_version_from_filename("App-1.0.0-x86_64.AppImage") == "1.0.0-x86_64"


class TestReadIconBytes:
    """Tests for read_icon_bytes function."""

    def test_read_png_icon(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "test.png"
            icon_path.write_bytes(b"fake png data")

            data, ext = read_icon_bytes(icon_path)
            assert data == b"fake png data"
            assert ext == "png"

    def test_read_svg_icon(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "test.svg"
            icon_path.write_bytes(b"<svg>test</svg>")

            data, ext = read_icon_bytes(icon_path)
            assert data == b"<svg>test</svg>"
            assert ext == "svg"

    def test_read_jpg_icon(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "test.jpg"
            icon_path.write_bytes(b"fake jpg data")

            data, ext = read_icon_bytes(icon_path)
            assert data == b"fake jpg data"
            assert ext == "jpg"

    def test_icon_too_large(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "test.png"
            # Create icon larger than MAX_ICON_BASE64_BYTES (512 KiB)
            icon_path.write_bytes(b"x" * (600 * 1024))

            data, ext = read_icon_bytes(icon_path)
            assert data is None
            assert ext is None

    def test_unknown_extension(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            icon_path = Path(tmpdir) / "test.xyz"
            icon_path.write_bytes(b"data")

            data, ext = read_icon_bytes(icon_path)
            assert data == b"data"
            assert ext == "png"  # Default fallback

    def test_missing_file(self):
        data, ext = read_icon_bytes(Path("/nonexistent/icon.png"))
        assert data is None
        assert ext is None


class TestReadAppImageMeta:
    """Tests for read_appimage_meta function."""

    def test_read_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage, name="TestApp", version="1.0.0")

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with (
                patch(
                    "appimage_launcher.core.metadata.validate_appimage",
                    return_value=(header, offset),
                ),
                patch("appimage_launcher.core.metadata.read_upd_info", return_value=None),
                patch("appimage_launcher.core.metadata.read_sha256_sig", return_value=None),
                patch("appimage_launcher.core.metadata.extract_desktop_file") as mock_desktop,
                patch("appimage_launcher.core.metadata.extract_icon") as mock_icon,
                patch("appimage_launcher.core.metadata.extract_metainfo") as mock_metainfo,
                patch("appimage_launcher.core.metadata.parse_desktop_file") as mock_parse_desktop,
                patch("appimage_launcher.core.metadata.parse_appstream") as mock_parse_appstream,
                patch("appimage_launcher.core.metadata.read_icon_bytes") as mock_read_icon,
                patch("appimage_launcher.core.metadata.compute_app_id") as mock_compute_app_id,
                patch("appimage_launcher.core.metadata.compute_fingerprint") as mock_compute_fp,
            ):
                mock_desktop.return_value = Path(tmpdir) / "test.desktop"
                mock_parse_desktop.return_value = {
                    "Name": "TestApp",
                    "Comment": "A test app",
                    "Categories": "Utility;Development;",
                    "Icon": "test-icon",
                    "X-AppImage-Version": "1.0.0",
                    "StartupWMClass": "test-app",
                }
                mock_icon.return_value = Path(tmpdir) / "test-icon.png"
                mock_read_icon.return_value = (b"icon data", "png")
                mock_metainfo.return_value = []
                mock_parse_appstream.return_value = {}
                mock_compute_app_id.return_value = "com.example.testapp"
                mock_compute_fp.return_value = "abc123"

                meta = read_appimage_meta(appimage)

                assert isinstance(meta, AppImageMeta)
                assert meta.name == "TestApp"
                assert meta.version == "1.0.0"
                assert meta.comment == "A test app"
                assert meta.categories == ["Utility", "Development"]
                assert meta.app_id == "com.example.testapp"
                assert meta.fingerprint == "abc123"

    def test_read_metadata_fallback_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with (
                patch(
                    "appimage_launcher.core.metadata.validate_appimage",
                    return_value=(header, offset),
                ),
                patch("appimage_launcher.core.metadata.read_upd_info", return_value=None),
                patch("appimage_launcher.core.metadata.read_sha256_sig", return_value=None),
                patch("appimage_launcher.core.metadata.extract_desktop_file") as mock_desktop,
                patch("appimage_launcher.core.metadata.extract_icon") as mock_icon,
                patch("appimage_launcher.core.metadata.extract_metainfo") as mock_metainfo,
                patch("appimage_launcher.core.metadata.parse_desktop_file") as mock_parse_desktop,
                patch("appimage_launcher.core.metadata.parse_appstream") as mock_parse_appstream,
                patch("appimage_launcher.core.metadata.read_icon_bytes") as mock_read_icon,
                patch("appimage_launcher.core.metadata.compute_app_id") as mock_compute_app_id,
                patch("appimage_launcher.core.metadata.compute_fingerprint") as mock_compute_fp,
            ):
                mock_desktop.return_value = Path(tmpdir) / "test.desktop"
                mock_parse_desktop.return_value = {}  # No Name field
                mock_icon.return_value = None
                mock_read_icon.return_value = (None, None)
                mock_metainfo.return_value = []
                mock_parse_appstream.return_value = {}
                mock_compute_app_id.return_value = "test"
                mock_compute_fp.return_value = "abc123"

                meta = read_appimage_meta(appimage)

                # Should fall back to filename stem
                assert meta.name == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
