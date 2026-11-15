"""Extended tests for naming utilities."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from appimage_launcher.core.naming import (
    classify_collision,
    generate_kept_name,
    render_template,
    sanitize_name,
)


class TestSanitizeNameExtended:
    """Extended tests for sanitize_name function."""

    def test_unicode_handling(self):
        # Test various unicode characters
        assert sanitize_name("App🎉") == "App🎉"
        assert sanitize_name("App 中文") == "App 中文"
        assert sanitize_name("App_underscore") == "App_underscore"
        assert sanitize_name("App-with-dashes") == "App-with-dashes"

    def test_max_bytes_truncation(self):
        # Test that truncation works at byte level, not character level
        # Create a string that's over 100 bytes in UTF-8
        long_name = "A" * 150
        result = sanitize_name(long_name)
        assert len(result.encode("utf-8")) <= 100

    def test_utf8_boundary_not_cut(self):
        # Ensure we don't cut in the middle of a multi-byte character
        # Each emoji is 4 bytes in UTF-8
        name = "🎉" * 30  # 120 bytes
        result = sanitize_name(name)
        # Should be valid UTF-8
        result.encode("utf-8")
        # Should be truncated to <= 100 bytes
        assert len(result.encode("utf-8")) <= 100

    def test_all_reserved_suffixes(self):
        for suffix in [".disabled", ".part", ".crdownload", ".zs-old"]:
            assert sanitize_name(f"App{suffix}") == "App"
            assert sanitize_name(f"App{suffix}extra") == f"App{suffix}extra"  # Only at end

    def test_leading_dots_and_spaces(self):
        assert sanitize_name("...App") == "App"
        assert sanitize_name("   App") == "App"
        assert sanitize_name("...   App   ...") == "App"

    def test_control_characters(self):
        assert sanitize_name("App\x00null") == "Appnull"
        assert sanitize_name("App\nnewline") == "Appnewline"
        assert sanitize_name("App\ttab") == "Apptab"
        assert sanitize_name("App\rreturn") == "Appreturn"

    def test_path_separators(self):
        assert sanitize_name("path/to/app") == "pathtoapp"
        assert sanitize_name("path\\to\\app") == "pathtoapp"

    def test_whitespace_collapse(self):
        assert sanitize_name("App   Name") == "App Name"
        # Tabs and newlines are control characters, stripped not collapsed
        assert sanitize_name("App\t\tName") == "AppName"
        assert sanitize_name("App\n\nName") == "AppName"

    def test_empty_and_edge_cases(self):
        assert sanitize_name("") == "Unnamed"
        assert sanitize_name("   ") == "Unnamed"
        assert sanitize_name("...") == "Unnamed"
        # .disabled becomes "disabled" after stripping suffix, not empty
        assert sanitize_name(".disabled") == "disabled"
        assert sanitize_name(".part") == "part"


class TestRenderTemplateExtended:
    """Extended tests for render_template function."""

    def test_all_variables(self):
        result = render_template(
            "{name} {version} {app_id}", "TestApp", "1.0.0", "com.example.test"
        )
        assert result == "TestApp 1.0.0 com.example.test"

    def test_missing_variables(self):
        result = render_template("{name} {version} {app_id}", "TestApp", None, None)
        assert result == "TestApp"

    def test_empty_variables(self):
        result = render_template("{name} {version} {app_id}", "TestApp", "", "")
        assert result == "TestApp"

    def test_sanitization_applied(self):
        result = render_template("{name}", "App/Name")
        assert result == "AppName"

    def test_special_characters_in_variables(self):
        # render_template applies sanitization to the final result, not individual variables
        result = render_template("{name}-{version}", "App@Name", "1.0.0#build")
        assert result == "App@Name-1.0.0#build"


class TestClassifyCollisionExtended:
    """Extended tests for classify_collision function."""

    def test_no_collision_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "nonexistent"
            result = classify_collision(target, "com.example.app", {})
            assert result.type == "none"

    def test_same_app_id_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "ExistingApp"
            target.touch()
            existing = {"ExistingApp": {"app_id": "com.example.app", "record": None}}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "same_app_id"
            assert result.existing_app_id == "com.example.app"

    def test_different_app_id_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "ExistingApp"
            target.touch()
            existing = {"ExistingApp": {"app_id": "com.other.app", "record": None}}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "different_app_id"
            assert result.existing_app_id == "com.other.app"
            assert result.suggested_name is not None
            assert "com.example" in result.suggested_name or "app" in result.suggested_name

    def test_directory_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "ExistingDir"
            target.mkdir()
            existing = {}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "non_appimage"

    def test_untracked_file_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "UntrackedFile"
            target.write_text("not an appimage")
            existing = {}
            result = classify_collision(target, "com.example.app", existing)
            assert result.type == "non_appimage"


class TestGenerateKeptNameExtended:
    """Extended tests for generate_kept_name function."""

    def test_basic_generation(self):
        with patch("appimage_launcher.config.get_config") as mock_config:
            mock_config.return_value.naming.keep_template = "{name} {version}"
            existing = set()
            result = generate_kept_name("TestApp", "1.0.0", existing)
            assert result == "TestApp 1.0.0"

    def test_collision_handling(self):
        with patch("appimage_launcher.config.get_config") as mock_config:
            mock_config.return_value.naming.keep_template = "{name} {version}"
            existing = {"TestApp 1.0.0"}
            result = generate_kept_name("TestApp", "1.0.0", existing)
            assert result == "TestApp 1.0.0.old"

    def test_multiple_collisions(self):
        with patch("appimage_launcher.config.get_config") as mock_config:
            mock_config.return_value.naming.keep_template = "{name} {version}"
            existing = {"TestApp 1.0.0", "TestApp 1.0.0.old", "TestApp 1.0.0.old2"}
            result = generate_kept_name("TestApp", "1.0.0", existing)
            assert result == "TestApp 1.0.0.old3"

    def test_sanitization_applied(self):
        with patch("appimage_launcher.config.get_config") as mock_config:
            mock_config.return_value.naming.keep_template = "{name} {version}"
            existing = set()
            result = generate_kept_name("App/Name", "1.0.0", existing)
            assert result == "AppName 1.0.0"

    def test_version_none(self):
        with patch("appimage_launcher.config.get_config") as mock_config:
            mock_config.return_value.naming.keep_template = "{name} {version}"
            existing = set()
            result = generate_kept_name("TestApp", None, existing)
            assert result == "TestApp"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
