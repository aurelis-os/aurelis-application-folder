"""Extended tests for identity computation."""

import tempfile
from pathlib import Path

import pytest

from appimage_launcher.core.identity import compute_app_id, compute_fingerprint, compute_full_sha256
from tests.fixtures.build_fake_appimage import build_fake_appimage


class TestComputeAppIdExtended:
    """Extended tests for compute_app_id function."""

    def test_from_appstream_id(self):
        """Test app_id from AppStream metainfo."""
        desktop_data = {"Name": "TestApp"}
        # Create a temporary metainfo file
        with tempfile.TemporaryDirectory() as tmpdir:
            metainfo_path = Path(tmpdir) / "test.metainfo.xml"
            metainfo_path.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.fromappstream</id>
  <name>TestApp</name>
</component>""")
            result = compute_app_id(desktop_data, [metainfo_path], "fallback")
            assert result == "com.example.fromappstream"

    def test_from_name_various_formats(self):
        """Test app_id generation from various name formats."""
        test_cases = [
            ("Simple App", "simple-app"),
            ("App With Spaces", "app-with-spaces"),
            ("UPPERCASE", "uppercase"),
            ("Special@Chars#1", "specialchars1"),
            ("Already-slugified", "already-slugified"),
            ("Multiple   Spaces", "multiple-spaces"),
            ("Trailing Spaces ", "trailing-spaces"),
            ("Leading Spaces", "leading-spaces"),
            ("Dots.And.Dashes", "dots.and.dashes"),
            ("", "unnamed"),  # Empty name falls back to fallback
        ]

        for name, expected in test_cases:
            desktop_data = {"Name": name} if name else {}
            result = compute_app_id(desktop_data, [], "fallback")
            if name:
                assert result == expected, (
                    f"Failed for name='{name}': got '{result}', expected '{expected}'"
                )
            else:
                assert result == "fallback"

    def test_fallback_used_when_no_name(self):
        """Test that fallback is used when no name in desktop data."""
        desktop_data = {}
        result = compute_app_id(desktop_data, [], "my-fallback")
        assert result == "my-fallback"

    def test_appstream_id_preferred_over_name(self):
        """Test that AppStream ID is preferred over Name."""
        desktop_data = {"Name": "Different Name"}
        with tempfile.TemporaryDirectory() as tmpdir:
            metainfo_path = Path(tmpdir) / "test.metainfo.xml"
            metainfo_path.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop">
  <id>com.example.appstream</id>
  <name>Different Name</name>
</component>""")
            result = compute_app_id(desktop_data, [metainfo_path], "fallback")
            assert result == "com.example.appstream"


class TestComputeFingerprintExtended:
    """Extended tests for compute_fingerprint function."""

    def test_fingerprint_consistency(self):
        """Test that fingerprint is consistent for same file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)
            size = appimage.stat().st_size

            fp1 = compute_fingerprint(appimage, offset, size)
            fp2 = compute_fingerprint(appimage, offset, size)
            assert fp1 == fp2
            assert len(fp1) == 64  # SHA-256 hex

    def test_different_files_different_fingerprints(self):
        """Test that different files have different fingerprints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage1 = Path(tmpdir) / "test1.appimage"
            appimage2 = Path(tmpdir) / "test2.appimage"
            build_fake_appimage(appimage1, name="App1")
            build_fake_appimage(appimage2, name="App2")

            from appimage_launcher.core.elf import validate_appimage

            header1, offset1 = validate_appimage(appimage1)
            header2, offset2 = validate_appimage(appimage2)
            size1 = appimage1.stat().st_size
            size2 = appimage2.stat().st_size

            fp1 = compute_fingerprint(appimage1, offset1, size1)
            fp2 = compute_fingerprint(appimage2, offset2, size2)
            assert fp1 != fp2

    def test_fingerprint_changes_with_content(self):
        """Test that fingerprint changes when file content changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage, name="App1")

            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)
            size = appimage.stat().st_size
            fp1 = compute_fingerprint(appimage, offset, size)

            # Modify the file
            with open(appimage, "ab") as f:
                f.write(b"extra data")

            size2 = appimage.stat().st_size
            fp2 = compute_fingerprint(appimage, offset, size2)
            assert fp1 != fp2


class TestComputeFullSha256Extended:
    """Extended tests for compute_full_sha256 function."""

    def test_sha256_consistency(self):
        """Test that SHA-256 is consistent for same file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            hash1 = compute_full_sha256(appimage)
            hash2 = compute_full_sha256(appimage)
            assert hash1 == hash2
            assert len(hash1) == 64

    def test_different_files_different_hashes(self):
        """Test that different files have different hashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage1 = Path(tmpdir) / "test1.appimage"
            appimage2 = Path(tmpdir) / "test2.appimage"
            build_fake_appimage(appimage1, name="App1")
            build_fake_appimage(appimage2, name="App2")

            hash1 = compute_full_sha256(appimage1)
            hash2 = compute_full_sha256(appimage2)
            assert hash1 != hash2

    def test_large_file_hashing(self):
        """Test hashing a larger file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            # Append more data to make it larger
            with open(appimage, "ab") as f:
                f.write(b"x" * 100000)

            hash1 = compute_full_sha256(appimage)
            assert len(hash1) == 64


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
