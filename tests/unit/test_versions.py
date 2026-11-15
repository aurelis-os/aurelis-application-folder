"""Tests for version comparison."""

import pytest

from appimage_launcher.core.versions import VersionRelation, compare_versions, parse_version


class TestParseVersion:
    """Tests for parse_version function."""

    def test_semantic_version(self):
        v = parse_version("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3

    def test_semantic_version_with_prerelease(self):
        v = parse_version("1.2.3-rc1")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.pre_release == "rc1"

    def test_semantic_version_with_build(self):
        v = parse_version("1.2.3+build123")
        assert v.build == "build123"

    def test_two_part_version(self):
        v = parse_version("1.2")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 0

    def test_date_version(self):
        v = parse_version("2024.01.15")
        assert v.major == 2024
        assert v.minor == 1
        assert v.patch == 15

    def test_date_version_two_part(self):
        v = parse_version("2024.01")
        assert v.major == 2024
        assert v.minor == 1
        assert v.patch == 0

    def test_single_number(self):
        v = parse_version("5")
        assert v.major == 5

    def test_nightly(self):
        v = parse_version("nightly")
        assert v.pre_release == "nightly"

    def test_git_hash(self):
        v = parse_version("abc123def")
        assert v.pre_release == "abc123def"

    def test_none_version(self):
        v = parse_version(None)
        assert v.raw == ""

    def test_empty_version(self):
        v = parse_version("")
        assert v.raw == ""


class TestCompareVersions:
    """Tests for compare_versions function."""

    def test_upgrade(self):
        assert compare_versions("1.0.0", "2.0.0") == VersionRelation.UPGRADE
        assert compare_versions("1.0.0", "1.1.0") == VersionRelation.UPGRADE
        assert compare_versions("1.0.0", "1.0.1") == VersionRelation.UPGRADE

    def test_downgrade(self):
        assert compare_versions("2.0.0", "1.0.0") == VersionRelation.DOWNGRADE
        assert compare_versions("1.1.0", "1.0.0") == VersionRelation.DOWNGRADE
        assert compare_versions("1.0.1", "1.0.0") == VersionRelation.DOWNGRADE

    def test_same(self):
        assert compare_versions("1.0.0", "1.0.0") == VersionRelation.SAME
        assert compare_versions("1.2.3-rc1", "1.2.3-rc1") == VersionRelation.SAME

    def test_prerelease_vs_release(self):
        assert compare_versions("1.0.0-rc1", "1.0.0") == VersionRelation.UPGRADE
        assert compare_versions("1.0.0", "1.0.0-rc1") == VersionRelation.DOWNGRADE

    def test_unknown_versions(self):
        assert compare_versions(None, "1.0.0") == VersionRelation.UNKNOWN
        assert compare_versions("1.0.0", None) == VersionRelation.UNKNOWN
        assert compare_versions("", "1.0.0") == VersionRelation.UNKNOWN

    def test_date_versions(self):
        assert compare_versions("2024.01.01", "2024.01.15") == VersionRelation.UPGRADE
        assert compare_versions("2024.01.15", "2024.01.01") == VersionRelation.DOWNGRADE

    def test_nightly_comparison(self):
        # Nightly versions compared lexicographically
        assert compare_versions("nightly-20240101", "nightly-20240115") == VersionRelation.UPGRADE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
