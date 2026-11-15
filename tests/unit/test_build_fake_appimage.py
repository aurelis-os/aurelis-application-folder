"""Tests for fake AppImage builder."""

import tempfile
from pathlib import Path

import pytest

from tests.fixtures.build_fake_appimage import build_corrupt_appimage, build_fake_appimage


class TestBuildFakeAppImage:
    """Tests for build_fake_appimage function."""

    def test_build_basic_appimage(self):
        """Test building a basic AppImage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.appimage"
            build_fake_appimage(output)

            assert output.exists()
            assert output.stat().st_size > 0
            # Check executable bit
            assert output.stat().st_mode & 0o111

    def test_build_appimage_with_custom_metadata(self):
        """Test building AppImage with custom metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "custom.appimage"
            build_fake_appimage(
                output,
                name="CustomApp",
                version="2.5.0",
                app_id="com.custom.app",
                comment="Custom comment",
                categories=["Graphics", "Photography"],
            )

            assert output.exists()

    def test_build_corrupt_bad_magic(self):
        """Test building corrupt AppImage with bad magic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "bad_magic.appimage"
            build_corrupt_appimage(output, "bad_magic")

            assert output.exists()
            # Should not be a valid AppImage
            data = output.read_bytes()
            assert data[8:10] == b"XX"

    def test_build_corrupt_truncated(self):
        """Test building truncated AppImage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "truncated.appimage"
            build_corrupt_appimage(output, "truncated")

            assert output.exists()
            data = output.read_bytes()
            assert data == b"\x7fELF"

    def test_build_corrupt_bad_squashfs(self):
        """Test building AppImage with bad squashfs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "bad_squashfs.appimage"
            build_corrupt_appimage(output, "bad_squashfs")

            assert output.exists()

    def test_build_type1_appimage(self):
        """Test building type 1 AppImage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "type1.appimage"
            build_corrupt_appimage(output, "type1")

            assert output.exists()
            data = output.read_bytes()
            assert data[8:11] == b"AI\x01"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
