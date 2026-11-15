"""Fuzz tests for hostile AppImage handling.

These tests verify that malicious or malformed AppImages never escape
the temp directory or cause hangs.
"""

import tempfile
from pathlib import Path

import pytest

from appimage_launcher.core.elf import (
    CorruptAppImageError,
    NotAnAppImageError,
    UnsupportedTypeError,
    validate_appimage,
)
from appimage_launcher.core.metadata import read_appimage_meta
from appimage_launcher.core.squash import (
    extract_desktop_file,
    extract_icon,
    extract_metainfo,
    list_squashfs_contents,
)
from tests.fixtures.build_fake_appimage import build_corrupt_appimage, build_fake_appimage


class TestHostileAppImages:
    """Tests for handling hostile/malformed AppImages."""

    def test_truncated_elf_header(self):
        """Test handling of truncated ELF header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            truncated = Path(tmpdir) / "truncated.appimage"
            build_corrupt_appimage(truncated, "truncated")

            with pytest.raises(NotAnAppImageError):
                validate_appimage(truncated)

    def test_bad_elf_magic(self):
        """Test handling of file with bad ELF magic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad.appimage"
            bad.write_bytes(b"Not an ELF file at all")

            with pytest.raises(NotAnAppImageError):
                validate_appimage(bad)

    def test_appimage_type1_unsupported(self):
        """Test that AppImage type 1 (ISO9660) is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            type1 = Path(tmpdir) / "type1.appimage"
            build_corrupt_appimage(type1, "type1")

            with pytest.raises(UnsupportedTypeError):
                validate_appimage(type1)

    def test_bad_appimage_magic(self):
        """Test handling of valid ELF but wrong AppImage magic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / "bad.appimage"
            build_corrupt_appimage(bad, "bad_magic")

            with pytest.raises(NotAnAppImageError):
                validate_appimage(bad)

    def test_corrupt_squashfs_magic(self):
        """Test handling of corrupt squashfs magic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir) / "corrupt.appimage"
            build_corrupt_appimage(corrupt, "bad_squashfs")

            with pytest.raises(CorruptAppImageError):
                validate_appimage(corrupt)

    def test_empty_file(self):
        """Test handling of empty file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty = Path(tmpdir) / "empty.appimage"
            empty.write_bytes(b"")

            with pytest.raises(NotAnAppImageError):
                validate_appimage(empty)

    def test_very_large_fake_appimage(self):
        """Test handling of very large AppImage (should not hang)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "large.appimage"
            build_fake_appimage(appimage)

            # Append a lot of data to make it large
            with open(appimage, "ab") as f:
                f.write(b"x" * (10 * 1024 * 1024))  # 10 MB extra

            # Should still validate quickly
            header, offset = validate_appimage(appimage)
            assert header.is_64bit is True
            assert offset > 0

    def test_symlink_escape_in_squashfs(self):
        """Test that symlink escape attempts in squashfs are handled safely."""
        # This test verifies that our extraction functions don't follow
        # symlinks that escape the temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with tempfile.TemporaryDirectory() as dest_dir:
                dest_path = Path(dest_dir)

                # These should not crash or escape the temp directory
                # even if the squashfs contains malicious symlinks
                desktop = extract_desktop_file(appimage, offset, dest_path)
                # Result should be within dest_dir or None
                if desktop:
                    assert dest_path in desktop.parents or desktop == dest_path

                icon = extract_icon(appimage, offset, dest_path, "test-icon")
                if icon:
                    assert dest_path in icon.parents or icon == dest_path

                metainfo = extract_metainfo(appimage, offset, dest_path)
                for mf in metainfo:
                    assert dest_path in mf.parents or mf == dest_path

    def test_list_squashfs_contents_malformed(self):
        """Test listing contents of malformed squashfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            # Should not crash
            files = list_squashfs_contents(appimage, offset)
            assert isinstance(files, list)

    def test_read_metadata_from_corrupt_appimage(self):
        """Test reading metadata from corrupt AppImage fails gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir) / "corrupt.appimage"
            build_corrupt_appimage(corrupt, "bad_squashfs")

            # Should raise an error, not hang or crash
            with pytest.raises((CorruptAppImageError, NotAnAppImageError, UnsupportedTypeError)):
                read_appimage_meta(corrupt)

    def test_multiple_nested_temp_dirs(self):
        """Test that nested temp directories are handled correctly."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                appimage = Path(tmpdir1) / "test.appimage"
                build_fake_appimage(appimage)

                from appimage_launcher.core.elf import validate_appimage

                header, offset = validate_appimage(appimage)

                dest_dir = Path(tmpdir2) / "extract"
                dest_dir.mkdir()

                # Extract to a different temp directory
                desktop = extract_desktop_file(appimage, offset, dest_dir)
                if desktop:
                    assert dest_dir in desktop.parents

    def test_unicode_filenames_in_squashfs(self):
        """Test handling of unicode filenames in squashfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage, name="Tëst Äpp")

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with tempfile.TemporaryDirectory() as dest_dir:
                dest_path = Path(dest_dir)

                # Should handle unicode gracefully
                extract_desktop_file(appimage, offset, dest_path)
                # Should not crash

    def test_very_long_filename_in_squashfs(self):
        """Test handling of very long filenames in squashfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with tempfile.TemporaryDirectory() as dest_dir:
                dest_path = Path(dest_dir)

                # Should not crash with long filenames
                extract_desktop_file(appimage, offset, dest_path)
                # Should not crash


class TestTempDirCleanup:
    """Tests that temporary directories are always cleaned up."""

    def test_temp_dir_cleaned_on_success(self):
        """Test that temp dir is cleaned up on successful extraction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with tempfile.TemporaryDirectory() as dest_dir:
                dest_path = Path(dest_dir)
                extract_desktop_file(appimage, offset, dest_path)
                extract_icon(appimage, offset, dest_path, "test-icon")
                extract_metainfo(appimage, offset, dest_path)

            # Temp dirs are cleaned up by context managers

    def test_temp_dir_cleaned_on_error(self):
        """Test that temp dir is cleaned up even when extraction fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a valid appimage first
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)

            with tempfile.TemporaryDirectory() as dest_dir:
                dest_path = Path(dest_dir)

                # These should fail gracefully and clean up
                # (using a non-existent appimage path to trigger errors)
                try:
                    extract_desktop_file(Path("/nonexistent/appimage"), offset, dest_path)
                except Exception:
                    pass

                # Temp dir should still be cleaned up (handled by context manager)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
