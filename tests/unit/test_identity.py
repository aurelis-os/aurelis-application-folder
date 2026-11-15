"""Tests for identity computation."""

import tempfile
from pathlib import Path

import pytest

from appimage_launcher.core.identity import compute_app_id, compute_fingerprint, compute_full_sha256
from tests.fixtures.build_fake_appimage import build_fake_appimage


class TestComputeAppId:
    """Tests for compute_app_id function."""

    def test_from_appstream(self):
        desktop_data = {"Name": "TestApp"}
        metainfo_files = []
        # Would need actual metainfo file - skip for now
        result = compute_app_id(desktop_data, metainfo_files, "testapp")
        assert result == "testapp"

    def test_from_name(self):
        desktop_data = {"Name": "My Application"}
        metainfo_files = []
        result = compute_app_id(desktop_data, metainfo_files, "fallback")
        assert result == "my-application"

    def test_special_chars_in_name(self):
        desktop_data = {"Name": "My@App#1"}
        metainfo_files = []
        result = compute_app_id(desktop_data, metainfo_files, "fallback")
        assert result == "myapp1"


class TestComputeFingerprint:
    """Tests for compute_fingerprint function."""

    def test_fingerprint_consistency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            # Validate to get offset
            from appimage_launcher.core.elf import validate_appimage

            header, offset = validate_appimage(appimage)
            size = appimage.stat().st_size

            fp1 = compute_fingerprint(appimage, offset, size)
            fp2 = compute_fingerprint(appimage, offset, size)
            assert fp1 == fp2
            assert len(fp1) == 64  # SHA-256 hex

    def test_different_files_different_fingerprints(self):
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


class TestComputeFullSha256:
    """Tests for compute_full_sha256 function."""

    def test_sha256_consistency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            appimage = Path(tmpdir) / "test.appimage"
            build_fake_appimage(appimage)

            hash1 = compute_full_sha256(appimage)
            hash2 = compute_full_sha256(appimage)
            assert hash1 == hash2
            assert len(hash1) == 64


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
